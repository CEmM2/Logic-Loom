"""Tests for the sidecar handshake utilities."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from logic_loom_api import handshake


def test_bind_random_port() -> None:
    sock, port = handshake.bind_random_port()
    try:
        assert 1 <= port <= 65535
        assert sock.getsockname()[1] == port
    finally:
        sock.close()


def test_port_file_roundtrip(tmp_path: Path) -> None:
    fake_path = tmp_path / "port.json"
    with patch.object(handshake, "port_file_path", return_value=fake_path):
        path = handshake.write_port_file(17321)
        assert path == fake_path
        data = json.loads(fake_path.read_text())
        assert data["port"] == 17321
        assert isinstance(data["pid"], int) and data["pid"] > 0

        handshake.remove_port_file()
        assert not fake_path.exists()


def test_remove_port_file_when_absent_is_quiet(tmp_path: Path) -> None:
    fake_path = tmp_path / "missing.json"
    with patch.object(handshake, "port_file_path", return_value=fake_path):
        handshake.remove_port_file()  # should not raise


def test_announce_ready_format(capsys: pytest.CaptureFixture[str]) -> None:
    handshake.announce_ready(54321)
    out = capsys.readouterr().out
    assert out == "LOGIC_LOOM_API_READY:54321\n"
