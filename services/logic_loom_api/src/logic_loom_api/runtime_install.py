"""Runtime installer for the optional Taichi backend (MechDSL ``verify``).

The packaged sidecar ships the MechDSL **emit** bridge but deliberately excludes
Taichi (~170 MB; ``--exclude-module taichi`` in ``scripts/build_sidecar_*.sh``),
so ``emit``/``transpile`` work frozen while ``verify`` needs Taichi installed
on demand. This module installs ``taichi==1.7.4`` into a user-writable,
ABI-namespaced directory that ``__main__`` adds to ``sys.path`` at startup, so a
side-installed Taichi becomes importable in both dev (``uv run``) and frozen
(PyInstaller onefile) modes — without re-freezing or bloating the shipped binary.

Frozen onefile builds have no ``python -m pip``, so the install runs through a
hidden ``--install-runtime-pkg`` re-entry of the binary itself (handled in
``__main__``), which runs ``pip._internal`` against ``--target``. In dev we just
shell out to ``sys.executable -m pip``. Either way pip resolves the wheel for the
*running* interpreter's tags (cp312 + platform), guaranteeing an ABI match.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import platform
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from .config import Settings

logger = logging.getLogger(__name__)

# Pinned so the in-app button can never install an arbitrary/incompatible build.
TAICHI_SPEC = "taichi==1.7.4"
TAICHI_VERSION = "1.7.4"

_LOG_CAP = 200  # keep only the last N installer output lines

# Install state, guarded by _lock. status: idle|installing|installed|failed.
_lock = threading.Lock()
_state: dict[str, Any] = {"status": "idle", "log": [], "error": None}


# --------------------------------------------------------------------------- #
# Platform / target-dir resolution
# --------------------------------------------------------------------------- #
def platform_supported() -> tuple[bool, str | None]:
    """Whether a ``taichi==1.7.4`` wheel exists for this OS/arch.

    Upstream ships cp3x wheels for macOS arm64, Linux x86_64, and Windows
    amd64 only — notably **no macOS x86_64 (Intel) wheel**, so verify is
    unsupported there. Returns ``(supported, reason_if_not)``.
    """
    sysname = platform.system()
    mach = platform.machine().lower()
    if sysname == "Darwin":
        if mach in ("arm64", "aarch64"):
            return True, None
        return False, (
            "No taichi 1.7.4 wheel for macOS x86_64 (Intel) — verify requires "
            "Apple Silicon."
        )
    if sysname == "Linux":
        if mach in ("x86_64", "amd64"):
            return True, None
        return False, f"No taichi 1.7.4 wheel for Linux {mach} (need x86_64)."
    if sysname == "Windows":
        if mach in ("amd64", "x86_64"):
            return True, None
        return False, f"No taichi 1.7.4 wheel for Windows {mach} (need x86_64/AMD64)."
    return False, f"Unsupported platform for taichi: {sysname}/{mach}."


def runtime_site_dir(settings: Settings) -> Path:
    """Install target: ``<cache_path>/runtime/<pyXY>-<os>-<arch>/``.

    Namespaced by interpreter version and platform so a Python bump never loads
    a stale-ABI wheel from a previous install.
    """
    pyver = f"py{sys.version_info.major}{sys.version_info.minor}"
    plat = f"{platform.system().lower()}-{platform.machine().lower()}"
    return settings.cache_path.expanduser() / "runtime" / f"{pyver}-{plat}"


def ensure_runtime_site_on_path(settings: Settings | None = None) -> Path | None:
    """Append the runtime-site dir to ``sys.path`` (idempotent).

    Appended (not prepended) so bundled deps still win over anything side-
    installed. Called from ``__main__`` at startup *and* after an install
    completes. The dir is added even if it does not exist yet, so a later
    install + ``importlib.invalidate_caches()`` makes Taichi importable live.
    Never raises — a resolution failure just logs and is skipped.
    """
    try:
        settings = settings or Settings.load()
        target = str(runtime_site_dir(settings))
        if target not in sys.path:
            sys.path.append(target)
        return Path(target)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("runtime-site path setup skipped: %s", exc)
        return None


def taichi_available() -> bool:
    """True if ``taichi`` is importable, without paying its heavy import."""
    try:
        return importlib.util.find_spec("taichi") is not None
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Status
# --------------------------------------------------------------------------- #
def runtime_status(settings: Settings) -> dict[str, Any]:
    """Combined platform + availability + install-state descriptor."""
    supported, reason = platform_supported()
    with _lock:
        install_status = _state["status"]
        log = list(_state["log"])
        error = _state["error"]
    return {
        "taichi_version": TAICHI_VERSION,
        "taichi_available": taichi_available(),
        "platform_supported": supported,
        "platform_reason": reason,
        "install_status": install_status,
        "log": log,
        "error": error,
        "target_dir": str(runtime_site_dir(settings)),
    }


# --------------------------------------------------------------------------- #
# Install
# --------------------------------------------------------------------------- #
def _pip_available() -> bool:
    """True if ``pip`` is importable in the current interpreter."""
    try:
        return importlib.util.find_spec("pip") is not None
    except Exception:
        return False


def _pip_cmd(target: Path, spec: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--target",
        str(target),
        "--no-input",
        "--disable-pip-version-check",
        "--no-cache-dir",
        spec,
    ]


def _install_cmd(spec: str, target: Path) -> list[str]:
    """Subprocess argv to install ``spec`` into ``target``.

    Three modes:
      - **frozen** onefile binaries have no ``python -m pip``, so we re-enter the
        binary with the hidden ``--install-runtime-pkg`` flag (handled in
        ``__main__``), which runs the bundled ``pip._internal`` in-process.
      - **dev with pip** uses ``python -m pip`` directly.
      - **dev without pip** (uv-managed venvs ship without pip) falls back to
        ``uv pip install --python <this interpreter> --target``.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, "--install-runtime-pkg", spec, "--target", str(target)]
    if _pip_available():
        return _pip_cmd(target, spec)
    uv = shutil.which("uv")
    if uv:
        return [
            uv,
            "pip",
            "install",
            "--python",
            sys.executable,
            "--target",
            str(target),
            spec,
        ]
    # Neither pip nor uv — emit the pip cmd so the failure message is obvious.
    return _pip_cmd(target, spec)


def _append_log(line: str) -> None:
    with _lock:
        _state["log"].append(line.rstrip())
        if len(_state["log"]) > _LOG_CAP:
            del _state["log"][: len(_state["log"]) - _LOG_CAP]


def _install_worker(spec: str, target: Path) -> None:
    """Run the install subprocess, streaming output into the capped log."""
    target.mkdir(parents=True, exist_ok=True)
    cmd = _install_cmd(spec, target)
    _append_log(f"$ {' '.join(cmd)}")
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            _append_log(line)
        code = proc.wait()
    except Exception as exc:  # subprocess failed to launch / crashed
        with _lock:
            _state["status"] = "failed"
            _state["error"] = f"{type(exc).__name__}: {exc}"
        logger.warning("taichi install failed to run: %s", exc)
        return

    if code != 0:
        with _lock:
            _state["status"] = "failed"
            _state["error"] = f"pip exited with code {code}"
        logger.warning("taichi install failed (exit %s)", code)
        return

    # Make the freshly-installed package importable live (no restart): the dir
    # is on sys.path already (appended at startup, even when it was empty), so
    # invalidating the finder cache lets the next `import taichi` find it.
    ensure_runtime_site_on_path()
    importlib.invalidate_caches()
    ok = taichi_available()
    with _lock:
        _state["status"] = "installed" if ok else "failed"
        _state["error"] = (
            None
            if ok
            else "install completed but taichi is still not importable — "
            "restart the app and try a MechDSL verify."
        )
    logger.info("taichi install finished; importable=%s", ok)


def start_taichi_install(settings: Settings) -> dict[str, Any]:
    """Kick off a background Taichi install if one isn't already running.

    Returns the current runtime status immediately (non-blocking) — the caller
    polls ``runtime_status`` for progress. A second call while installing is a
    no-op (idempotent).
    """
    with _lock:
        if _state["status"] == "installing":
            pass  # already running — fall through to return current status
        else:
            _state["status"] = "installing"
            _state["log"] = []
            _state["error"] = None
            target = runtime_site_dir(settings)
            thread = threading.Thread(
                target=_install_worker,
                args=(TAICHI_SPEC, target),
                name="taichi-install",
                daemon=True,
            )
            thread.start()
    return runtime_status(settings)


# --------------------------------------------------------------------------- #
# Frozen re-entry: `logic-loom-api --install-runtime-pkg <spec> --target <dir>`
# --------------------------------------------------------------------------- #
def _cli_install_runtime_pkg(argv: list[str]) -> int:
    """Run ``pip install`` in-process for the frozen binary (no ``-m pip``).

    Invoked from ``__main__.main`` when ``--install-runtime-pkg`` is present.
    ``argv`` is ``sys.argv[1:]``. Returns a process exit code.
    """
    spec: str | None = None
    target: str | None = None
    it = iter(argv)
    for tok in it:
        if tok == "--install-runtime-pkg":
            spec = next(it, None)
        elif tok == "--target":
            target = next(it, None)
    if not spec or not target:
        sys.stderr.write("usage: --install-runtime-pkg <spec> --target <dir>\n")
        return 2
    try:
        from pip._internal.cli.main import main as pip_main
    except Exception as exc:  # pip not bundled — build regression
        sys.stderr.write(f"pip unavailable in frozen sidecar: {exc}\n")
        return 3
    return pip_main(
        [
            "install",
            "--target",
            target,
            "--no-input",
            "--disable-pip-version-check",
            "--no-cache-dir",
            spec,
        ]
    )
