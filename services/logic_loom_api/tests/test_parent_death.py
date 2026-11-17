"""The sidecar exits when the desktop shell dies (stdin EOF watcher)."""

from __future__ import annotations

import io
import os
import subprocess
import urllib.request
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
    """Start the sidecar; its stderr goes to ``tmp_path/stderr.log`` for diagnosis."""
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
        stderr=(tmp_path / "stderr.log").open("w"),
        text=True,
        env=env,
    )


def _stderr_tail(tmp_path: Path) -> str:
    try:
        return "\n".join((tmp_path / "stderr.log").read_text(errors="replace").splitlines()[-40:])
    except OSError:
        return "<no stderr>"


def _wait_ready(proc: subprocess.Popen[str], tmp_path: Path) -> int:
    """Return the port once READY is announced and /api/app/health answers."""
    assert proc.stdout is not None
    deadline = time.monotonic() + 60
    port = None
    while time.monotonic() < deadline and port is None:
        line = proc.stdout.readline()
        if line.startswith("LOGIC_LOOM_API_READY:"):
            port = int(line.split(":", 1)[1])
        elif not line and proc.poll() is not None:
            pytest.fail(f"sidecar exited early with {proc.returncode}\n{_stderr_tail(tmp_path)}")
    if port is None:
        pytest.fail("sidecar never announced READY")
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/app/health", timeout=2) as r:
                if r.status == 200:
                    return port
        except OSError:
            if proc.poll() is not None:
                pytest.fail(f"sidecar died before serving: {proc.returncode}\n{_stderr_tail(tmp_path)}")
            time.sleep(0.2)
    pytest.fail("sidecar never served /api/app/health")


def test_sidecar_exits_when_stdin_closes(tmp_path: Path) -> None:
    proc = _spawn(tmp_path, "--exit-on-stdin-eof")
    try:
        _wait_ready(proc, tmp_path)
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
        _wait_ready(proc, tmp_path)
        assert proc.stdin is not None
        proc.stdin.close()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            return  # still running: expected
        pytest.fail(f"sidecar exited ({proc.returncode}) without the flag\n{_stderr_tail(tmp_path)}")
    finally:
        proc.kill()
        proc.wait()
