"""Shared pytest test harness for Logic-Loom API tests.

In this repo, graph-route tests exercise AKMS code paths decorated with
``@traced(...)`` from ``akms.orchestrator.telemetry``. If AKMS telemetry
auto-initializes with its default console exporter, OpenTelemetry's background
batch worker may try to write to stdout/stderr during interpreter shutdown,
which produces a noisy but non-fatal traceback:

    ValueError: I/O operation on closed file.

That warning is cosmetic, but it pollutes local test runs and CI logs. The
Logic-Loom test suite does not assert on AKMS span output, so we proactively
initialize AKMS telemetry with an in-memory exporter for the duration of the
pytest session.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

#: Environment the suite pins for the whole session so that no test can reach
#: the developer's real Logic-Loom state. Restored verbatim at unconfigure.
_ISOLATED_ENV: tuple[str, ...] = (
    "LOGIC_LOOM_CONFIG",
    "LOGIC_LOOM_AKMS_WORKSPACE",
    "LOGIC_LOOM_AKMS_REPO",
    "LOGIC_LOOM_VAULT_PATH",
)
_ISOLATION_ROOT = pytest.StashKey[Path]()
_ISOLATION_SAVED_ENV = pytest.StashKey[dict[str, str | None]]()


def pytest_configure(config: pytest.Config) -> None:
    """Hide the developer's ``config.toml`` and give the session its own AKMS
    workspace BEFORE collection.

    This has to be a configure-time hook rather than a fixture: several test
    modules import :mod:`logic_loom_api.main` at module level, and ``main``
    builds the picker sub-app from ``Settings.load()`` at import time, which
    materialises ``<akms_workspace>/.cache/generation_plan.md`` — a filesystem
    write that happens during collection, before any fixture runs. Without
    this pin that write lands in the developer's real workspace: on macOS the
    *default* ``akms_workspace`` is the live ``~/Library/Application Support``
    path, so merely hiding the TOML is not enough. The vault is pinned for the
    same reason: with no ``vault_path`` the picker falls back to AKMS's own
    default ``zsumbib.json``, which on a developer machine can be a cloud-synced
    (e.g. OneDrive) placeholder whose ``open()`` blocks collection until it
    downloads. The pinned vault holds no ``zsumbib.json``, so the picker mount
    is skipped exactly as on CI. ``akms_repo`` is pinned to a path that does
    not exist because AKMS is consumed as an installed package: no test may
    depend on a sibling AKMS source checkout (the field's default is
    ``~/Github/Personal/AKMS``). Tests that need a specific
    layout still set these variables themselves (``monkeypatch.setenv`` /
    ``patch.dict``) and win, as before.
    """
    root = Path(tempfile.mkdtemp(prefix="logic-loom-tests-"))
    config.stash[_ISOLATION_ROOT] = root
    config.stash[_ISOLATION_SAVED_ENV] = {k: os.environ.get(k) for k in _ISOLATED_ENV}
    # A path that does not exist: Settings.load() then falls through to defaults.
    os.environ["LOGIC_LOOM_CONFIG"] = str(root / "config.toml")
    os.environ["LOGIC_LOOM_AKMS_WORKSPACE"] = str(root / "akms-workspace")
    os.environ["LOGIC_LOOM_AKMS_REPO"] = str(root / "no-akms-source-checkout")
    os.environ["LOGIC_LOOM_VAULT_PATH"] = str(root / "vault")


def pytest_unconfigure(config: pytest.Config) -> None:
    for key, value in config.stash.get(_ISOLATION_SAVED_ENV, {}).items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    root = config.stash.get(_ISOLATION_ROOT, None)
    if root is not None:
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(scope="session", autouse=True)
def _isolate_failure_memory_job_journal(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[None]:
    """Point the failure-memory job journal at a session temp directory.

    The journal makes terminal job evidence durable across sidecar restarts, so
    in production it lives under the user's real cache directory. Tests must
    neither write there nor RELOAD a previous run's evidence into this run's
    assertions, so the path derivation is redirected for the whole session. The
    journal machinery itself still runs for real — attach, load, persist —
    and the restart-durability tests build their own registries against their
    own paths.
    """
    from logic_loom_api.services import failure_memory_jobs

    journal_dir = tmp_path_factory.mktemp("failure-memory-journal")
    original = failure_memory_jobs._journal_path
    failure_memory_jobs._journal_path = (
        lambda settings: journal_dir / "jobs.json"
    )
    try:
        yield
    finally:
        failure_memory_jobs._journal_path = original


@pytest.fixture(scope="session", autouse=True)
def _materialize_bundled_akms_claude_assets() -> Iterator[None]:
    """Materialise the gitignored bundled AKMS Claude asset tree for the tests
    that exercise it.

    ``src/logic_loom_api/resources/akms_claude/`` is a build artifact produced by
    ``scripts/sync_akms_claude_assets.py`` (run by the sidecar build) from the
    installed ``akms`` package. It is gitignored, so a bare dev
    checkout lacks it and the bundled Claude-asset tests
    (``tests/akms/test_claude_assets_*.py``,
    ``tests/akms/test_claude_runtime_materializer.py``) fail with ``AkmsBundledAssetsMissing`` —
    a missing build step, not a code defect.

    We materialise it once per session WHEN ``akms`` is installed (the normal
    case), so those tests run for real instead of erroring. Without the ``akms``
    extra we no-op — there is nothing to materialise, and the asset-dependent
    tests surface their own clear error.

    The sync is idempotent (it verifies-in-place when already materialised), so
    this is cheap on repeat runs and safe to run alongside a populated tree.
    """
    try:
        from logic_loom_api.akms_claude_assets import bundled_root

        bundled_root()  # already materialised → nothing to do
        yield
        return
    except Exception:
        pass

    # repo root: <root>/services/logic_loom_api/tests/conftest.py -> parents[3]
    repo_root = Path(__file__).resolve().parents[3]
    script = repo_root / "scripts" / "sync_akms_claude_assets.py"
    if importlib.util.find_spec("akms") is not None and script.is_file():
        try:
            subprocess.run(
                [sys.executable, str(script)],
                cwd=str(repo_root),
                check=False,
                capture_output=True,
            )
        except Exception:
            pass  # best-effort; asset tests will surface a clear error if needed
    yield


@pytest.fixture(scope="session", autouse=True)
def _configure_akms_test_telemetry() -> Iterator[None]:
    """Disable AKMS console span export during Logic-Loom tests.

    We keep the tracer functional by wiring it to ``InMemorySpanExporter`` via
    ``SimpleSpanProcessor``. That avoids the shutdown-time console write while
    preserving the decorated code paths used by the graph integration tests.
    """
    if importlib.util.find_spec("akms") is None:
        yield
        return

    # OpenTelemetry is an optional AKMS extra (``akms[telemetry]``) that
    # Logic-Loom deliberately does not request. Without it AKMS's tracing
    # decorators are inert no-ops and nothing can write a span to the console,
    # so there is nothing to redirect. Only wire the in-memory exporter when
    # the SDK is actually present (e.g. a dev venv that added the extra).
    try:
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter,
        )
    except ImportError:
        yield
        return

    from akms.orchestrator import telemetry

    exporter = InMemorySpanExporter()
    telemetry._provider = None
    telemetry._tracer = None
    telemetry.init_telemetry(
        service_name="logic-loom-tests",
        export_console=False,
        span_exporter=exporter,
    )

    try:
        yield
    finally:
        provider = telemetry._provider
        if provider is not None:
            try:
                provider.shutdown()
            except Exception:
                pass
        exporter.clear()
        telemetry._provider = None
        telemetry._tracer = None
