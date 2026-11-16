"""Tests for routes/learn.py (6 endpoints).

Covers: all six routes resolve and are reachable via the test client; routes
delegate to the adapter (no compiler logic in the route layer).
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from logic_loom_api.adapters import learn_adapter
from logic_loom_api.deps import reload_settings
from logic_loom_api.main import api


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    """TestClient with a tmp-path config so no real filesystem state is touched."""
    cfg_path = tmp_path / "config.toml"
    with patch.dict(
        os.environ,
        {
            "LOGIC_LOOM_CONFIG": str(cfg_path),
            "LOGIC_LOOM_VAULT_PATH": str(tmp_path),
        },
    ):
        reload_settings()
        yield TestClient(api)
        reload_settings()


class TestLearnRoutes:
    """Integration tests: learn router registration + reachability."""

    @pytest.mark.integration
    def test_all_six_routes_registered(self) -> None:
        """Verifies: status, profiles, exporters, compile, export, artifacts/{id}/{path}
        are all registered on the FastAPI app.

        Requirement: 'All six routes resolve and are reachable via the test client.'
        Passes when: every expected path+method appears in app.routes.
        """
        # Collect all (path, method) pairs from every served route. Read them
        # from the OpenAPI document: on FastAPI >= 0.14x ``include_router``
        # registers a lazy ``_IncludedRouter`` in ``api.routes`` that carries no
        # ``path``/``methods``, so walking ``api.routes`` sees nothing. OpenAPI
        # renders the path-converter ``{path:path}`` as ``{path}``.
        registered: set[tuple[str, str]] = set()
        for path, item in api.openapi()["paths"].items():
            for method in item:
                registered.add((path, method.upper()))

        expected = [
            ("/api/learn/status", "GET"),
            ("/api/learn/profiles", "GET"),
            ("/api/learn/exporters", "GET"),
            ("/api/learn/compile", "POST"),
            ("/api/learn/export", "POST"),
            ("/api/learn/artifacts/{artifact_id}/{path}", "GET"),
        ]

        for path, method in expected:
            assert (path, method) in registered, (
                f"Expected route {method} {path} not found in app routes.\n"
                f"Registered learn routes: {[r for r in registered if '/learn' in r[0]]}"
            )

    @pytest.mark.integration
    def test_status_reachable_with_and_without_plugin(
        self, client: TestClient
    ) -> None:
        """Verifies: GET /api/learn/status returns 200 whether or not akms_learn is present.

        Requirement: 'Routes delegate to the adapter; no compiler logic in the route layer.'
        Passes when: TestClient gets a well-formed status payload in both cases.
        """
        # --- Case 1: plugin available (default — the dev env has akms_learn vendored) ---
        r = client.get("/api/learn/status")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        body = r.json()
        assert "akms_learn_available" in body, "Status payload must contain akms_learn_available"
        assert "capabilities" in body, "Status payload must contain capabilities"

        # --- Case 2: plugin unavailable (monkeypatched) ---
        with patch.object(learn_adapter, "akms_learn_available", return_value=False):
            r2 = client.get("/api/learn/status")
        assert r2.status_code == 200, (
            f"Status must return 200 even when plugin absent, got {r2.status_code}: {r2.text}"
        )
        body2 = r2.json()
        assert body2["akms_learn_available"] is False, (
            "Unavailable path must report akms_learn_available=false"
        )
        assert body2["capabilities"] == [], (
            "Unavailable path must report capabilities=[]"
        )
        # reason should be a non-empty string on the unavailable path
        assert body2.get("reason"), "Unavailable path should include a non-empty reason"
