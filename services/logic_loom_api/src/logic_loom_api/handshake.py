"""Sidecar handshake.

Bind a TCP socket on a random localhost port, persist the port + pid for the
parent (Tauri) to discover, and emit ``LOGIC_LOOM_API_READY:<port>`` on stdout so
the parent can parse it without polling. The bound socket is handed to uvicorn
via ``Server.run(sockets=[...])`` — no race window between bind and listen.
"""

from __future__ import annotations

import atexit
import json
import os
import signal
import socket
import sys
import threading
import time
from pathlib import Path
from types import FrameType
from typing import BinaryIO, Callable, Optional


def port_file_path() -> Path:
    return Path.home() / ".cache" / "logic-loom" / "port.json"


def bind_random_port(host: str = "127.0.0.1") -> tuple[socket.socket, int]:
    """Create a listening socket on ``host:0``. Caller is responsible for the fd."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    return sock, port


def write_port_file(port: int) -> Path:
    path = port_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"port": port, "pid": os.getpid()}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload))
    tmp.replace(path)
    return path


def remove_port_file() -> None:
    try:
        port_file_path().unlink(missing_ok=True)
    except OSError:
        pass


def announce_ready(port: int) -> None:
    """Emit the line the Tauri parent parses. Must be flushed."""
    sys.stdout.write(f"LOGIC_LOOM_API_READY:{port}\n")
    sys.stdout.flush()


def install_shutdown_handlers() -> None:
    """Clean up the port file on SIGTERM/SIGINT/normal exit."""
    atexit.register(remove_port_file)

    def _handler(signum: int, _frame: Optional[FrameType]) -> None:
        remove_port_file()
        # Re-raise default behavior: exit non-zero on SIGTERM/SIGINT.
        sys.exit(128 + signum)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            pass


# Grace period between asking the server to stop (SIGTERM, which uvicorn turns
# into a graceful shutdown) and forcing the process out.
EXIT_GRACE_SECONDS = 10.0


def _request_exit() -> None:
    """Ask the server to shut down, then force it out after the grace period."""
    remove_port_file()
    try:
        os.kill(os.getpid(), signal.SIGTERM)
    except OSError:
        os._exit(0)
    time.sleep(EXIT_GRACE_SECONDS)
    os._exit(0)


def _detach_stdin() -> BinaryIO:
    """Move the parent pipe to a private, non-inheritable fd; put /dev/null on fd 0.

    Child processes the sidecar spawns (agent CLIs, zsum) inherit fd 0 unless
    told otherwise. Left on the pipe, a tool that reads stdin would block until
    the app quits instead of seeing EOF, and every child would keep the pipe's
    read end open. ``os.dup`` returns a non-inheritable fd, so only this
    process watches the pipe.
    """
    private = os.dup(0)
    devnull = os.open(os.devnull, os.O_RDONLY)
    try:
        os.dup2(devnull, 0)
    finally:
        os.close(devnull)
    return os.fdopen(private, "rb", buffering=0)


def watch_parent_stdin(
    stream: Optional[BinaryIO] = None,
    on_eof: Callable[[], None] = _request_exit,
) -> threading.Thread:
    """Exit when the parent's end of our stdin pipe closes.

    The desktop shell spawns the sidecar with a piped stdin and never writes to
    it. The pipe's write end lives in the shell process, so the OS closes it
    when the shell exits for ANY reason (normal quit, crash, ``kill -9``, force
    quit), and our read returns EOF. That is the one parent-death signal that
    works on macOS, Linux and Windows alike, and it reaches the Python process
    through the PyInstaller bootloader because stdin is inherited.

    Only enabled with ``--exit-on-stdin-eof``, so running the sidecar by hand
    in a terminal is unaffected.
    """
    src = stream if stream is not None else _detach_stdin()

    def _run() -> None:
        try:
            while src.read(4096):
                pass  # the shell never writes; ignore anything that arrives
        except (OSError, ValueError):
            pass
        on_eof()

    thread = threading.Thread(target=_run, name="parent-stdin-watch", daemon=True)
    thread.start()
    return thread

