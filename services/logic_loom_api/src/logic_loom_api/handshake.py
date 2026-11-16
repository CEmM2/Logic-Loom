"""Sidecar handshake.

Bind a TCP socket on a random localhost port, persist the port + pid for the
parent (Tauri) to discover, and emit ``LOGIC_LOOM_API_READY:<port>`` on stdout so
the parent can parse it without polling. The bound socket is handed to uvicorn
via its ``fd`` parameter — no race window between bind and listen.
"""

from __future__ import annotations

import atexit
import json
import os
import signal
import socket
import sys
from pathlib import Path
from types import FrameType
from typing import Optional


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
