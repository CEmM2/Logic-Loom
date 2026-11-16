"""Integration tests for the AKMS Claude asset routes (FastAPI).

Each test mounts a fresh FastAPI ``TestClient`` so request-scoped state and
the lifespan-managed Settings don't leak between cases. ``XDG_CONFIG_HOME``
and ``XDG_CACHE_HOME`` are redirected to ``tmp_path`` so the routes never
touch the real user config.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from logic_loom_api import akms_claude_assets as aca
from logic_loom_api.main import api


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Provide a TestClient with isolated config/cache directories."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "config" / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache" / "AppData" / "Local"))
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "cache").mkdir(parents=True, exist_ok=True)
    with TestClient(api) as c:
        yield c


PREFIX = "/api/akms/claude-assets"


class TestClaudeAssetsRoutes:
    """Asset routes: status, install, read, write, reset, folder, materialize."""

    @pytest.mark.integration
    def test_status_route(self, client):
        """GET /status returns a non-500 body with installed/path/manifest."""
        r = client.get(f"{PREFIX}/status")
        assert r.status_code == 200
        body = r.json()
        assert set(body.keys()) >= {"installed", "path", "manifest"}
        assert body["installed"] is False
        assert body["manifest"] is None

    @pytest.mark.integration
    def test_install_route(self, client):
        """POST /install creates the editable user assets dir."""
        r = client.post(f"{PREFIX}/install")
        assert r.status_code == 200
        body = r.json()
        assert body["path"]
        assert body["overwrite"] is False
        assert Path(body["path"]).exists()

    @pytest.mark.integration
    def test_install_route_overwrite_returns_backup_warning(self, client):
        """When overwrite=true and a prior tree exists, response carries the
        backup path and a UI-facing warning string (the reset/backup audit
        guarantee).
        """
        client.post(f"{PREFIX}/install")
        r = client.post(f"{PREFIX}/install", params={"overwrite": "true"})
        assert r.status_code == 200
        body = r.json()
        assert body["overwrite"] is True
        assert body["backup_exists"] is True
        assert body["warning"] and "backup" in body["warning"].lower()

    @pytest.mark.integration
    def test_read_route(self, client):
        """GET /file?path=.claude/.../SKILL.md returns the bundled content."""
        client.post(f"{PREFIX}/install")
        r = client.get(
            f"{PREFIX}/file",
            params={"path": ".claude/skills/node-gen/SKILL.md"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["path"] == ".claude/skills/node-gen/SKILL.md"
        assert isinstance(body["content"], str) and body["content"]

    @pytest.mark.integration
    def test_write_route(self, client):
        """PUT /file?path=.claude/... writes the user copy."""
        client.post(f"{PREFIX}/install")
        r = client.put(
            f"{PREFIX}/file",
            params={"path": ".claude/skills/node-gen/SKILL.md"},
            json={"content": "USER OVERRIDE"},
        )
        assert r.status_code == 200
        # Round-trip via the read route to confirm precedence.
        r2 = client.get(
            f"{PREFIX}/file",
            params={"path": ".claude/skills/node-gen/SKILL.md"},
        )
        assert r2.json()["content"] == "USER OVERRIDE"
        # Manifest now marks user_modified=True.
        st = client.get(f"{PREFIX}/status").json()
        assert st["manifest"]["user_modified"] is True

    @pytest.mark.integration
    def test_reset_route(self, client):
        """POST /reset?path=.claude/... restores bundled content."""
        client.post(f"{PREFIX}/install")
        rel = ".claude/skills/node-gen/SKILL.md"
        bundled = client.get(f"{PREFIX}/file", params={"path": rel}).json()["content"]
        client.put(f"{PREFIX}/file", params={"path": rel}, json={"content": "edited"})
        r = client.post(f"{PREFIX}/reset", params={"path": rel})
        assert r.status_code == 200
        assert r.json() == {"status": "reset", "path": rel}
        after = client.get(f"{PREFIX}/file", params={"path": rel}).json()["content"]
        assert after == bundled

    @pytest.mark.integration
    def test_folder_route(self, client):
        """GET /folder returns the absolute user_assets_dir path."""
        r = client.get(f"{PREFIX}/folder")
        assert r.status_code == 200
        body = r.json()
        assert body["path"]
        assert Path(body["path"]).is_absolute()

    @pytest.mark.integration
    def test_materialize_route(self, client):
        """POST /materialize-runtime returns the materialized runtime path."""
        r = client.post(f"{PREFIX}/materialize-runtime")
        assert r.status_code == 200
        body = r.json()
        path = Path(body["path"])
        assert path.is_dir()
        assert (path / ".claude" / "skills" / "node-gen" / "SKILL.md").is_file()
        assert (path / ".logic-loom-runtime-ok").is_file()

    @pytest.mark.integration
    def test_read_route_default_only_skips_user_copy(self, client):
        """GET /file?default=true returns bundled content even when a user copy exists.

        Backs the Settings → Claude Assets "Show default" button: the UI
        needs the bundled text regardless of the user's edits so the
        viewer modal can display the unmodified reference.
        """
        client.post(f"{PREFIX}/install")
        rel = ".claude/skills/node-gen/SKILL.md"
        bundled = client.get(f"{PREFIX}/file", params={"path": rel}).json()["content"]
        client.put(f"{PREFIX}/file", params={"path": rel}, json={"content": "edited"})
        # Without default=true the user override wins.
        user_body = client.get(f"{PREFIX}/file", params={"path": rel}).json()
        assert user_body["content"] == "edited"
        # With default=true the bundled content is returned and the source flag flips.
        default_body = client.get(
            f"{PREFIX}/file", params={"path": rel, "default": "true"}
        ).json()
        assert default_body["content"] == bundled
        assert default_body["source"] == "bundled"

    @pytest.mark.integration
    def test_path_traversal_returns_400(self, client):
        """GET /file?path=../../etc/passwd returns 400 via the error envelope."""
        r = client.get(f"{PREFIX}/file", params={"path": "../../etc/passwd"})
        assert r.status_code == 400
        body = r.json()
        # Uniform error envelope from errors.install_handlers.
        assert "error" in body
        assert body["error"]["code"] == "bad_request"
        # The same guard fires for PUT and POST /reset.
        r2 = client.put(
            f"{PREFIX}/file",
            params={"path": "/etc/passwd"},
            json={"content": "x"},
        )
        assert r2.status_code == 400
        r3 = client.post(f"{PREFIX}/reset", params={"path": "../etc/passwd"})
        assert r3.status_code == 400
