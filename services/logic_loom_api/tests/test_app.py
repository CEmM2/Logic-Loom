"""Smoke + contract tests for /api/app/* and the cross-cutting middleware."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from logic_loom_api.config import Settings
from logic_loom_api.deps import get_settings, reload_settings
from logic_loom_api.main import api


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    """Override settings to point at tmp_path for any file writes."""
    cfg_path = tmp_path / "config.toml"
    with patch.dict(os.environ, {"LOGIC_LOOM_CONFIG": str(cfg_path), "LOGIC_LOOM_VAULT_PATH": str(tmp_path)}):
        reload_settings()
        yield TestClient(api)
        reload_settings()


class TestHealth:
    def test_returns_ok(self, client: TestClient) -> None:
        r = client.get("/api/app/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert "version" in body and "started_at" in body

    def test_correlation_id_in_response(self, client: TestClient) -> None:
        r = client.get("/api/app/health")
        assert r.headers["X-Correlation-ID"]

    def test_correlation_id_passthrough(self, client: TestClient) -> None:
        r = client.get("/api/app/health", headers={"X-Correlation-ID": "test-cid-123"})
        assert r.headers["X-Correlation-ID"] == "test-cid-123"


class TestVersion:
    def test_returns_versions(self, client: TestClient) -> None:
        r = client.get("/api/app/version")
        assert r.status_code == 200
        body = r.json()
        assert "logic_loom_api" in body
        # akms_nodes_gen and zsum may or may not have __version__; just present in dict.
        assert "akms_nodes_gen" in body
        assert "zsum" in body


class TestConfig:
    def test_get_returns_paths(self, client: TestClient) -> None:
        r = client.get("/api/app/config")
        assert r.status_code == 200
        body = r.json()
        for k in ("logic_loom_root", "akms_repo", "zsum_repo", "cache_path", "search_backend"):
            assert k in body
        assert "akms_global_vault" in body
        assert "akms_global_vault_effective" in body
        assert "graph_cache_path" in body

    def test_post_persists_to_toml(self, client: TestClient, tmp_path: Path) -> None:
        cfg_path = Path(os.environ["LOGIC_LOOM_CONFIG"])
        r = client.post("/api/app/config", json={"search_backend": "qmd"})
        assert r.status_code == 200
        assert cfg_path.exists()
        # Read back and confirm
        import tomllib

        with cfg_path.open("rb") as f:
            data = tomllib.load(f)
        assert data["search_backend"] == "qmd"

    def test_post_invalid_backend_rejected(self, client: TestClient) -> None:
        r = client.post("/api/app/config", json={"search_backend": "bogus"})
        assert r.status_code == 422
        body = r.json()
        assert "error" in body
        assert body["error"]["code"] == "validation_error"

    def test_first_run_reports_setup_state(self, client: TestClient) -> None:
        r = client.get("/api/app/config/first-run")
        assert r.status_code == 200
        body = r.json()
        for k in ("needs_setup", "missing_or_placeholder", "configured", "detected", "config_path"):
            assert k in body
        assert "akms_global_vault" in body["configured"]
        assert "akms_global_vault" in body["detected"]

    def test_first_run_seeds_template(self, client: TestClient) -> None:
        cfg_path = Path(os.environ["LOGIC_LOOM_CONFIG"])
        # Make sure config.toml does not exist yet.
        if cfg_path.exists():
            cfg_path.unlink()
        client.get("/api/app/config/first-run")
        assert cfg_path.exists()
        text = cfg_path.read_text()
        assert "<AKMS_REPO_PATH>" in text  # template placeholders preserved

    def test_first_run_save_persists(self, client: TestClient, tmp_path: Path) -> None:
        cfg_path = Path(os.environ["LOGIC_LOOM_CONFIG"])
        payload = {
            "akms_repo": str(tmp_path),
            "akms_global_vault": str(tmp_path / "global-vault"),
            "zsum_repo": str(tmp_path),
            "vault_path": str(tmp_path),
            "search_backend": "ripgrep",
        }
        r = client.post("/api/app/config/first-run", json=payload)
        assert r.status_code == 200
        import tomllib

        with cfg_path.open("rb") as f:
            data = tomllib.load(f)
        assert data["akms_repo"] == str(tmp_path)
        assert data["akms_global_vault"] == str(tmp_path / "global-vault")
        assert data["search_backend"] == "ripgrep"


class TestAkmsStatus:
    def test_reports_graph_capability_fields(self, client: TestClient) -> None:
        r = client.get("/api/app/akms-status")
        assert r.status_code == 200
        body = r.json()
        for key in (
            "available",
            "package_installed",
            "picker_package_installed",
            "graph_package_installed",
            "graph_available",
            "repo_exists",
            "akms_global_vault_exists",
            "akms_repo",
            "akms_global_vault",
        ):
            assert key in body


class TestErrorEnvelope:
    def test_404_uses_envelope(self, client: TestClient) -> None:
        r = client.get("/api/app/does-not-exist")
        assert r.status_code == 404
        body = r.json()
        assert set(body["error"].keys()) == {"code", "message", "detail"}
        assert body["error"]["code"] == "not_found"


class TestCors:
    def test_allowed_origin(self, client: TestClient) -> None:
        r = client.options(
            "/api/app/health",
            headers={
                "Origin": "tauri://localhost",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert r.headers.get("access-control-allow-origin") == "tauri://localhost"

    def test_disallowed_origin_no_cors_header(self, client: TestClient) -> None:
        r = client.options(
            "/api/app/health",
            headers={
                "Origin": "https://evil.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        # Starlette's CORS middleware does not echo the Allow-Origin header for unlisted origins.
        assert r.headers.get("access-control-allow-origin") != "https://evil.example.com"

    def test_patch_preflight_is_allowed(self, client: TestClient) -> None:
        """PATCH must survive the CORS preflight.

        Regression: PATCH was missing from ``allow_methods`` so WebKit's
        preflight for the batch-defaults endpoints failed with a bare
        "Load failed" and every default-workflow / prompt-profile save
        broke. The browser sends an OPTIONS preflight asking permission
        for the PATCH method; the response must echo it in
        ``access-control-allow-methods``.
        """
        r = client.options(
            "/api/assistant/batches/some-id",
            headers={
                "Origin": "tauri://localhost",
                "Access-Control-Request-Method": "PATCH",
            },
        )
        assert r.status_code == 200, r.text
        assert r.headers.get("access-control-allow-origin") == "tauri://localhost"
        allowed = r.headers.get("access-control-allow-methods", "")
        assert "PATCH" in allowed


class TestSettingsLoader:
    def test_env_overrides(self, tmp_path: Path) -> None:
        with patch.dict(os.environ, {"LOGIC_LOOM_SEARCH_BACKEND": "qmd"}):
            s = Settings.load(config_path=tmp_path / "missing.toml")
            assert s.search_backend == "qmd"

    def test_env_beats_toml_for_the_same_key(self, tmp_path: Path) -> None:
        """A key set in BOTH the TOML and the environment resolves to the env value.

        ``test_env_overrides`` only covers env against a missing file. This is
        the conflicting-key case: ``BaseSettings`` ranks constructor kwargs
        above env, so if the TOML layer is passed through as kwargs unfiltered
        the file silently shadows ``LOGIC_LOOM_*`` and the documented
        precedence inverts. That is what let the batch/migration tests write
        into the user's real workspace on any machine whose config.toml sets
        ``akms_workspace``.
        """
        cfg = tmp_path / "config.toml"
        toml_ws = tmp_path / "from-toml"
        env_ws = tmp_path / "from-env"
        # TOML literal strings ('...') take Windows paths verbatim; in a basic
        # string ("...") the backslashes in C:\Users\... parse as escapes.
        cfg.write_text(f"akms_workspace = '{toml_ws}'\nsearch_backend = 'ripgrep'\n")
        with patch.dict(
            os.environ,
            {"LOGIC_LOOM_AKMS_WORKSPACE": str(env_ws), "LOGIC_LOOM_SEARCH_BACKEND": "qmd"},
        ):
            s = Settings.load(config_path=cfg)
        assert s.akms_workspace == env_ws
        assert s.search_backend == "qmd"

    def test_toml_still_applies_for_keys_env_does_not_set(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = tmp_path / "config.toml"
        toml_ws = tmp_path / "from-toml"
        cfg.write_text(f"akms_workspace = '{toml_ws}'\n")
        # The session conftest pins LOGIC_LOOM_AKMS_WORKSPACE for isolation;
        # this test is specifically about a key the environment does NOT set.
        monkeypatch.delenv("LOGIC_LOOM_AKMS_WORKSPACE", raising=False)
        monkeypatch.setenv("LOGIC_LOOM_SEARCH_BACKEND", "qmd")
        s = Settings.load(config_path=cfg)
        assert s.akms_workspace == toml_ws
        assert s.search_backend == "qmd"

    def test_explicit_override_beats_env_and_toml(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.toml"
        toml_ws = tmp_path / "from-toml"
        cfg.write_text(f"akms_workspace = '{toml_ws}'\n")
        kw_ws = tmp_path / "from-kwarg"
        with patch.dict(os.environ, {"LOGIC_LOOM_AKMS_WORKSPACE": str(tmp_path / "from-env")}):
            s = Settings.load(config_path=cfg, akms_workspace=kw_ws)
        assert s.akms_workspace == kw_ws

    def test_placeholder_drops_through(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.toml"
        cfg.write_text('akms_repo = "<AKMS_REPO_PATH>"\n')
        s = Settings.load(config_path=cfg)
        # Placeholder is dropped, so the default is used (not the placeholder string).
        assert "<" not in str(s.akms_repo)
