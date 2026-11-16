"""Tests for the Taichi runtime installer (MechDSL ``verify`` unblock).

Covers the pure helpers (platform gating, target-dir namespacing, dev-vs-frozen
install command) and the two new routes (``GET /runtime``, ``POST
/runtime/install-taichi``). The install worker is stubbed so no test ever shells
out to pip or downloads ~170 MB.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from logic_loom_api import runtime_install
from logic_loom_api.config import Settings
from logic_loom_api.deps import reload_settings
from logic_loom_api.main import api


@pytest.fixture(autouse=True)
def _reset_install_state():
    runtime_install._state = {"status": "idle", "log": [], "error": None}
    yield
    runtime_install._state = {"status": "idle", "log": [], "error": None}


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    with patch.dict(
        os.environ,
        {
            "LOGIC_LOOM_CONFIG": str(tmp_path / "config.toml"),
            "LOGIC_LOOM_VAULT_PATH": str(tmp_path),
            "LOGIC_LOOM_CACHE_PATH": str(tmp_path / "cache"),
        },
    ):
        reload_settings()
        yield TestClient(api)
        reload_settings()


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
class TestPlatformSupported:
    def test_macos_arm_supported(self, monkeypatch) -> None:
        monkeypatch.setattr(runtime_install.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(runtime_install.platform, "machine", lambda: "arm64")
        ok, reason = runtime_install.platform_supported()
        assert ok is True and reason is None

    def test_macos_intel_unsupported(self, monkeypatch) -> None:
        monkeypatch.setattr(runtime_install.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(runtime_install.platform, "machine", lambda: "x86_64")
        ok, reason = runtime_install.platform_supported()
        assert ok is False
        assert reason is not None and "Intel" in reason

    def test_linux_x86_supported(self, monkeypatch) -> None:
        monkeypatch.setattr(runtime_install.platform, "system", lambda: "Linux")
        monkeypatch.setattr(runtime_install.platform, "machine", lambda: "x86_64")
        ok, reason = runtime_install.platform_supported()
        assert ok is True and reason is None

    def test_linux_arm_unsupported(self, monkeypatch) -> None:
        monkeypatch.setattr(runtime_install.platform, "system", lambda: "Linux")
        monkeypatch.setattr(runtime_install.platform, "machine", lambda: "aarch64")
        ok, reason = runtime_install.platform_supported()
        assert ok is False and reason is not None


def test_runtime_site_dir_is_abi_namespaced() -> None:
    settings = Settings.load()
    d = runtime_install.runtime_site_dir(settings)
    assert d.parent.name == "runtime"
    assert d.name.startswith(f"py{sys.version_info.major}{sys.version_info.minor}-")


def test_install_cmd_dev_uses_pip_module(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(runtime_install, "_pip_available", lambda: True)
    cmd = runtime_install._install_cmd("taichi==1.7.4", tmp_path)
    assert cmd[:3] == [sys.executable, "-m", "pip"]
    assert "--target" in cmd and str(tmp_path) in cmd and "taichi==1.7.4" in cmd


def test_install_cmd_dev_falls_back_to_uv(monkeypatch, tmp_path: Path) -> None:
    # uv-managed venvs ship without pip; the installer must use `uv pip` then.
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(runtime_install, "_pip_available", lambda: False)
    monkeypatch.setattr(runtime_install.shutil, "which", lambda name: "/usr/bin/uv")
    cmd = runtime_install._install_cmd("taichi==1.7.4", tmp_path)
    assert cmd[:2] == ["/usr/bin/uv", "pip"]
    assert "--python" in cmd and sys.executable in cmd
    assert "--target" in cmd and "taichi==1.7.4" in cmd


def test_install_cmd_frozen_reenters_binary(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    cmd = runtime_install._install_cmd("taichi==1.7.4", tmp_path)
    assert cmd[0] == sys.executable
    assert "--install-runtime-pkg" in cmd and "taichi==1.7.4" in cmd
    assert "-m" not in cmd  # frozen binary has no `python -m pip`


def test_ensure_on_path_appends_idempotently(tmp_path: Path) -> None:
    settings = Settings.load(cache_path=tmp_path)
    target = str(runtime_install.runtime_site_dir(settings))
    try:
        runtime_install.ensure_runtime_site_on_path(settings)
        runtime_install.ensure_runtime_site_on_path(settings)  # idempotent
        assert sys.path.count(target) == 1
    finally:
        while target in sys.path:
            sys.path.remove(target)


def test_cli_requires_spec_and_target() -> None:
    assert runtime_install._cli_install_runtime_pkg(["--install-runtime-pkg"]) == 2
    assert runtime_install._cli_install_runtime_pkg([]) == 2


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
def test_status_reports_taichi_fields(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(runtime_install, "taichi_available", lambda: False)
    body = client.get("/api/mechdsl/status").json()
    assert body["taichi_available"] is False
    assert body["verify_available"] is False  # no taichi → no verify
    assert "platform_supported" in body


def test_runtime_endpoint_shape(client: TestClient) -> None:
    r = client.get("/api/mechdsl/runtime")
    assert r.status_code == 200
    body = r.json()
    for key in (
        "taichi_version",
        "taichi_available",
        "platform_supported",
        "install_status",
        "log",
        "target_dir",
    ):
        assert key in body
    assert body["install_status"] == "idle"


def test_install_412_on_unsupported_platform(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        runtime_install, "platform_supported", lambda: (False, "no wheel here")
    )
    r = client.post("/api/mechdsl/runtime/install-taichi")
    assert r.status_code == 412
    assert "no wheel here" in r.json()["error"]["message"]


def test_install_starts_and_is_idempotent(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(runtime_install, "platform_supported", lambda: (True, None))
    # Stub the worker so the spawned thread does nothing (no pip, no download);
    # status stays "installing" so we can assert the idempotency guard.
    monkeypatch.setattr(runtime_install, "_install_worker", lambda spec, target: None)
    r1 = client.post("/api/mechdsl/runtime/install-taichi")
    assert r1.status_code == 200 and r1.json()["install_status"] == "installing"
    r2 = client.post("/api/mechdsl/runtime/install-taichi")
    assert r2.json()["install_status"] == "installing"  # no-op second call
