"""The sidecar exits when the desktop shell dies (stdin EOF watcher)."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from logic_loom_api.handshake import watch_parent_stdin


def test_watcher_calls_on_eof_when_stream_closes() -> None:
    fired = threading.Event()
    watch_parent_stdin(io.BytesIO(b"ignored bytes"), on_eof=fired.set)
    assert fired.wait(5)


def test_watcher_waits_while_the_pipe_is_open() -> None:
    r, w = os.pipe()
    fired = threading.Event()
    watch_parent_stdin(os.fdopen(r, "rb", buffering=0), on_eof=fired.set)
    assert not fired.wait(0.3)
    os.close(w)  # the parent "dies"
    assert fired.wait(5)


def _spawn(tmp_path: Path, *extra: str) -> subprocess.Popen[str]:
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "USERPROFILE": str(tmp_path),
        "LOGIC_LOOM_CONFIG": str(tmp_path / "config.toml"),
        "LOGIC_LOOM_CACHE_PATH": str(tmp_path / "cache"),
        "LOGIC_LOOM_AKMS_WORKSPACE": str(tmp_path / "ws"),
    }
    env.pop("LOGIC_LOOM_API_TOKEN", None)
    return subprocess.Popen(
        [sys.executable, "-m", "logic_loom_api", "--auto-port", *extra],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=env,
    )


def _wait_ready(proc: subprocess.Popen[str]) -> None:
    assert proc.stdout is not None
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if line.startswith("LOGIC_LOOM_API_READY:"):
            return
        if not line and proc.poll() is not None:
            pytest.fail(f"sidecar exited early with {proc.returncode}")
    pytest.fail("sidecar never announced READY")


def test_sidecar_exits_when_stdin_closes(tmp_path: Path) -> None:
    proc = _spawn(tmp_path, "--exit-on-stdin-eof")
    try:
        _wait_ready(proc)
        assert proc.stdin is not None
        proc.stdin.close()  # what the OS does when the shell process dies
        proc.wait(timeout=20)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
            pytest.fail("sidecar kept running after its stdin closed")


def test_sidecar_without_flag_ignores_stdin_eof(tmp_path: Path) -> None:
    proc = _spawn(tmp_path)
    try:
        _wait_ready(proc)
        assert proc.stdin is not None
        proc.stdin.close()
        with pytest.raises(subprocess.TimeoutExpired):
            proc.wait(timeout=2)
    finally:
        proc.kill()
        proc.wait()
