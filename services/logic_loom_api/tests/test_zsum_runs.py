"""RunRegistry + /api/zsum/runs end-to-end with stub commands."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from logic_loom_api.runs.models import LogEvent
from logic_loom_api.runs.registry import RunRegistry, _parse_line, build_zsum_argv


class TestBuildZsumArgv:
    """Regression coverage for the ``--config`` flag.

    Background: LogicLoom originally invoked ``uv run zsum <stage>`` with no
    ``--config``. The CLI then picked up whichever config it found on its
    own search path, which made the user's edits in the Pipeline → Config
    tab silently irrelevant for the next run. The Obsidian plugin always
    passes ``--config <abs>`` before the stage; we now do the same.
    """

    def test_includes_config_flag_before_stage(self, tmp_path: Path) -> None:
        cfg = tmp_path / "zsum.config.yaml"
        argv = build_zsum_argv("publish", cfg, None, python_cmd="uv run zsum")
        assert "--config" in argv
        assert str(cfg) in argv
        flag_idx = argv.index("--config")
        stage_idx = argv.index("publish")
        # Global option must come before the subcommand so zsum's argument
        # parser treats it as global, not stage-local.
        assert flag_idx < stage_idx
        # Layout: ["uv", "run", "zsum", "--config", "<abs>", "publish"]
        assert argv[:3] == ["uv", "run", "zsum"]
        assert argv[3:5] == ["--config", str(cfg)]
        assert argv[5] == "publish"

    def test_omits_config_flag_when_path_is_none(self) -> None:
        argv = build_zsum_argv("stats", None, None, python_cmd="uv run zsum")
        assert "--config" not in argv
        assert argv == ["uv", "run", "zsum", "stats"]

    def test_extra_args_follow_stage(self, tmp_path: Path) -> None:
        cfg = tmp_path / "zsum.config.yaml"
        argv = build_zsum_argv(
            "digests",
            cfg,
            ["--collection", "Demo", "--force"],
            python_cmd="uv run zsum",
        )
        # Extras come after the stage name, mirroring the plugin's order.
        assert argv == [
            "uv",
            "run",
            "zsum",
            "--config",
            str(cfg),
            "digests",
            "--collection",
            "Demo",
            "--force",
        ]

    def test_default_python_cmd_uses_sidecar_interpreter(self, tmp_path: Path) -> None:
        """When ``python_cmd`` is unset, the prefix points at the embedded launcher.

        Backs the embedded-launcher fix: packaged sidecar builds have no ``uv`` binary on
        ``PATH``, so the spawn must go through ``sys.executable`` + the
        in-tree ``_zsum_launcher`` module instead.
        """
        import sys

        argv = build_zsum_argv("stats", None, None)
        assert argv[:3] == [sys.executable, "-m", "logic_loom_api._zsum_launcher"]
        assert argv[3] == "stats"

    def test_shlex_splits_user_supplied_python_cmd(self) -> None:
        """Spaces and quotes in ``python_cmd`` are parsed by :mod:`shlex`."""
        argv = build_zsum_argv(
            "stats",
            None,
            None,
            python_cmd='"/opt/my python/bin/python" -m zsum.cli',
        )
        assert argv[:3] == ["/opt/my python/bin/python", "-m", "zsum.cli"]
        assert argv[3] == "stats"


class TestLegacyZsumPythonCmdMigration:
    """Pin the embedded-launcher follow-up: legacy ``"uv run zsum"`` persisted in
    ``config.toml`` predates the embedded-launcher pivot and still produces
    "spawn failed: [Errno 2]" inside Tauri's stripped GUI PATH on macOS.
    ``Settings.load`` drops the legacy literal so the new default_factory
    fires.
    """

    def test_legacy_uv_run_zsum_is_migrated_to_embedded_launcher(
        self, tmp_path: Path
    ) -> None:
        from logic_loom_api.config import Settings

        cfg = tmp_path / "config.toml"
        cfg.write_text(
            'zsum_python_cmd = "uv run zsum"\n', encoding="utf-8"
        )
        s = Settings.load(config_path=cfg)
        # Default factory uses sys.executable + the bundled launcher.
        # The interpreter path is shlex-quoted so spaced paths (and Windows
        # backslashes) survive the shlex.split() round-trip at spawn time, so
        # parse it the same way the consumers do rather than naively on spaces.
        assert s.zsum_python_cmd.endswith("-m logic_loom_api._zsum_launcher")
        assert shlex.split(s.zsum_python_cmd)[0] == sys.executable

    def test_user_custom_zsum_python_cmd_passes_through(
        self, tmp_path: Path
    ) -> None:
        """Arbitrary user customisations must not be migrated."""
        from logic_loom_api.config import Settings

        cfg = tmp_path / "config.toml"
        cfg.write_text(
            'zsum_python_cmd = "/opt/custom/python -m zsum.cli"\n',
            encoding="utf-8",
        )
        s = Settings.load(config_path=cfg)
        assert s.zsum_python_cmd == "/opt/custom/python -m zsum.cli"


class TestFrozenZsumLauncherAddressing:
    """Pin the packaged-build spawn path.

    A PyInstaller binary is not an interpreter: it has no ``-m``, and forwards
    ``sys.argv[1:]`` to our own ``__main__``. The pre-fix default therefore
    reached argparse and killed every packaged pipeline run with
    ``unrecognized arguments: -m logic_loom_api._zsum_launcher cards``.
    Frozen builds address the launcher via ``--run-module`` instead.
    """

    def test_frozen_default_uses_run_module_not_dash_m(self, monkeypatch) -> None:
        from logic_loom_api.config import _default_zsum_python_cmd

        monkeypatch.setattr(sys, "frozen", True, raising=False)
        cmd = _default_zsum_python_cmd()
        parts = shlex.split(cmd)
        assert parts == [sys.executable, "--run-module", "logic_loom_api._zsum_launcher"]
        assert "-m" not in parts  # would be swallowed by our own argparse

    def test_frozen_argv_keeps_stage_and_config_ordering(self, monkeypatch) -> None:
        """The ``--config`` before subcommand contract survives the frozen prefix."""
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        argv = build_zsum_argv("cards", "/tmp/zsum.toml", None)
        assert argv == [
            sys.executable,
            "--run-module",
            "logic_loom_api._zsum_launcher",
            "--config",
            "/tmp/zsum.toml",
            "cards",
        ]

    def test_frozen_binary_dash_m_persisted_value_is_migrated(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """A packaged build that saved Settings pre-fix persisted a dead command."""
        from logic_loom_api.config import Settings

        monkeypatch.setattr(sys, "frozen", True, raising=False)
        cfg = tmp_path / "config.toml"
        # Reproduce the exact string the pre-fix default factory produced, and
        # serialise it with json.dumps: a Windows sys.executable is full of
        # backslashes, which are invalid escapes in a TOML basic string.
        stale = f"{shlex.quote(sys.executable)} -m logic_loom_api._zsum_launcher"
        cfg.write_text(f"zsum_python_cmd = {json.dumps(stale)}\n", encoding="utf-8")
        s = Settings.load(config_path=cfg)
        assert shlex.split(s.zsum_python_cmd)[1] == "--run-module"

    def test_external_interpreter_dash_m_survives_when_frozen(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """Only the frozen binary's own ``-m`` is dead — a real python still works."""
        from logic_loom_api.config import Settings

        monkeypatch.setattr(sys, "frozen", True, raising=False)
        cfg = tmp_path / "config.toml"
        cfg.write_text(
            'zsum_python_cmd = "/opt/my-python/bin/python -m logic_loom_api._zsum_launcher"\n',
            encoding="utf-8",
        )
        s = Settings.load(config_path=cfg)
        assert s.zsum_python_cmd.startswith("/opt/my-python/bin/python -m")


class TestRunModuleReentry:
    """``__main__`` must intercept ``--run-module`` before argparse sees it."""

    def test_run_module_executes_module_as_main(self, monkeypatch, capsys) -> None:
        import runpy

        from logic_loom_api.__main__ import _run_module

        seen: dict[str, object] = {}

        def fake_run_module(mod, run_name=None, alter_sys=False):
            seen["mod"] = mod
            seen["run_name"] = run_name
            seen["argv"] = list(sys.argv)

        monkeypatch.setattr(runpy, "run_module", fake_run_module)
        monkeypatch.setattr(sys, "argv", ["logic-loom-api"])
        rc = _run_module(["logic_loom_api._zsum_launcher", "--config", "z.toml", "cards"])

        assert rc == 0
        assert seen["mod"] == "logic_loom_api._zsum_launcher"
        assert seen["run_name"] == "__main__"
        # The module's own CLI must see its args, not ours.
        assert seen["argv"][1:] == ["--config", "z.toml", "cards"]

    def test_run_module_without_name_is_an_error(self, capsys) -> None:
        from logic_loom_api.__main__ import _run_module

        assert _run_module([]) == 2

    def test_main_dispatches_run_module_before_argparse(self, monkeypatch) -> None:
        """Regression: argparse used to reject the module name outright."""
        from logic_loom_api import __main__ as entry

        monkeypatch.setattr(
            sys, "argv", ["logic-loom-api", "--run-module", "some.module", "cards"]
        )
        monkeypatch.setattr(entry, "_run_module", lambda argv: 7)

        with pytest.raises(SystemExit) as exc:
            entry.main()
        assert exc.value.code == 7


class TestParseLine:
    def test_zsum_format(self) -> None:
        evt = _parse_line("12:34:56 [INFO] zsum.cli: Running stage skeleton")
        assert evt.level == "INFO"

    def test_warning_format(self) -> None:
        evt = _parse_line("00:00:00 [WARNING] zsum.publisher: collision")
        assert evt.level == "WARNING"

    def test_unmatched_falls_back_to_info(self) -> None:
        evt = _parse_line("not a log format")
        assert evt.level == "INFO"
        assert "not a log format" in evt.line


@pytest.mark.asyncio
async def test_submit_streams_lines(tmp_path: Path) -> None:
    reg = RunRegistry(cache_dir=tmp_path, zsum_repo=tmp_path)
    rec = await reg.submit(
        stage="stub",
        cmd_override=[sys.executable, "-c", "import sys; print('hi'); print('bye')"],
        cwd_override=tmp_path,
    )
    # Drain the stream.
    events: list[LogEvent] = []
    async for evt in reg.stream(rec.id):
        events.append(evt)
    final = reg.get(rec.id)
    assert final is not None
    assert final.status == "succeeded"
    assert final.exit_code == 0
    assert any("hi" in e.line for e in events)
    assert any("bye" in e.line for e in events)


@pytest.mark.asyncio
async def test_persistence(tmp_path: Path) -> None:
    reg = RunRegistry(cache_dir=tmp_path, zsum_repo=tmp_path)
    rec = await reg.submit(
        stage="stub",
        cmd_override=[sys.executable, "-c", "print('persisted')"],
        cwd_override=tmp_path,
    )
    # Wait for completion.
    while reg.get(rec.id).status in ("pending", "running"):
        await asyncio.sleep(0.05)
    # Reload registry from disk.
    reg2 = RunRegistry(cache_dir=tmp_path, zsum_repo=tmp_path)
    rehydrated = reg2.get(rec.id)
    assert rehydrated is not None
    assert rehydrated.status == "succeeded"
    assert rehydrated.exit_code == 0


@pytest.mark.asyncio
async def test_cancel(tmp_path: Path) -> None:
    reg = RunRegistry(cache_dir=tmp_path, zsum_repo=tmp_path)
    rec = await reg.submit(
        stage="stub",
        cmd_override=[sys.executable, "-c", "import time; time.sleep(30)"],
        cwd_override=tmp_path,
    )
    # Wait until running.
    for _ in range(50):
        if reg.get(rec.id).status == "running":
            break
        await asyncio.sleep(0.05)
    assert reg.cancel(rec.id) is True
    # Wait for it to actually exit.
    for _ in range(50):
        st = reg.get(rec.id)
        if st.status not in ("running", "pending"):
            break
        await asyncio.sleep(0.1)
    final = reg.get(rec.id)
    assert final.status == "cancelled"


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    cfg = tmp_path / "config.toml"
    with patch.dict(
        os.environ,
        {
            "LOGIC_LOOM_CONFIG": str(cfg),
            "LOGIC_LOOM_CACHE_PATH": str(tmp_path / "cache"),
            "LOGIC_LOOM_ZSUM_REPO": str(tmp_path),
            "LOGIC_LOOM_VAULT_PATH": str(tmp_path),
        },
    ):
        from logic_loom_api.deps import reload_settings

        reload_settings()
        from logic_loom_api.main import api

        with TestClient(api) as c:
            yield c
        reload_settings()


def test_runs_list_empty(client: TestClient) -> None:
    r = client.get("/api/zsum/runs")
    assert r.status_code == 200
    body = r.json()
    assert "runs" in body and isinstance(body["runs"], list)


def test_status_endpoint(client: TestClient) -> None:
    r = client.get("/api/zsum/status")
    assert r.status_code == 200
    body = r.json()
    assert "zsum_repo" in body and "vault_path" in body and "cache_path" in body


def test_unknown_run_404(client: TestClient) -> None:
    r = client.get("/api/zsum/runs/does-not-exist")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
