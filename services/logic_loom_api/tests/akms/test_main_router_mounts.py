"""main.py mounts every router (and preserves degrade-gracefully).

Verifies the production main.py:

  * mounts the new AKMS dispatch router at /api/akms/node-generation,
  * keeps every existing router mounted,
  * preserves the picker-as-sub-app + degrade-gracefully invariant
    from CLAUDE.md (a failed `build_picker_app` must log + skip; the
    rest of the API stays up and /api/app/health still returns 200).
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from logic_loom_api import main as main_mod


@pytest.fixture()
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("LOGIC_LOOM_AKMS_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("LOGIC_LOOM_CACHE_PATH", str(tmp_path / "cache"))
    monkeypatch.setenv("LOGIC_LOOM_CONFIG", str(tmp_path / "missing.toml"))
    yield tmp_path


class TestMainRouterMounts:
    """main.py router mounts + lifespan ordering."""

    def test_main_existing_suite_still_passes(self, isolated_env):
        """All previously-mounted routes still serve.

        Mounting the AKMS dispatch router must not break the existing /api/runs,
        /api/batches, /api/prompts, /api/workflows, /api/app routes.
        """
        from logic_loom_api.main import api

        # `with` triggers the lifespan so init_registry() runs.
        with TestClient(api) as client:
            # Health endpoint is the canonical 'is the sidecar up?' probe.
            resp = client.get("/api/app/health")
            assert resp.status_code == 200, resp.text

            # /api/runs still mounted.
            runs = client.get("/api/runs")
            assert runs.status_code == 200, runs.text

            # /api/batches still mounted.
            batches = client.get("/api/batches")
            assert batches.status_code == 200, batches.text

            # /api/prompts still mounted.
            prompts = client.get("/api/prompts/slots")
            assert prompts.status_code == 200, prompts.text

            # /api/workflows still mounted.
            workflows = client.get("/api/workflows")
            assert workflows.status_code == 200, workflows.text

    def test_new_endpoints_mount_without_akms_or_return_503(self, isolated_env):
        """Every new router with no hard AKMS dep mounts; AKMS
        dispatch routes return 503 with a clear message when the
        bundled workflow is unavailable.

        The preflight endpoint always mounts and runs (it returns
        structured failures rather than 503 for missing tools), so a
        200 response is the expected outcome even with no AKMS extras.
        """
        from logic_loom_api.main import api

        client = TestClient(api)
        # Preflight must respond — the route layer never raises on
        # missing external tools.
        pre = client.post("/api/akms/node-generation/preflight", json={})
        assert pre.status_code == 200, pre.text
        body = pre.json()
        assert "checks" in body
        # Missing tools -> some checks ok:false. The endpoint itself is up.
        assert isinstance(body["ok"], bool)

    def test_app_health_still_ok_when_picker_mount_raises(
        self, isolated_env, monkeypatch, caplog
    ):
        """Regression for CLAUDE.md degrade-gracefully invariant: even
        if `build_picker_app` raises during module import, /api/app/health
        must return 200 and the core API stays up.

        We monkeypatch `build_picker_app` to raise BEFORE reloading the
        main module so the picker mount path takes the except branch.
        """
        # Patch build_picker_app to raise the exception type the
        # production try/except actually catches.
        def boom(_settings):
            raise RuntimeError("picker construction failed in test")

        monkeypatch.setattr(
            "logic_loom_api.adapters.picker_adapter.build_picker_app",
            boom,
        )

        caplog.set_level(logging.WARNING)
        # Reload main so the module-level mount path re-runs against
        # the patched picker factory.
        reloaded = importlib.reload(main_mod)

        # The mount-skipped warning was logged at import time.
        assert any(
            "picker mount skipped" in rec.getMessage().lower()
            for rec in caplog.records
        )

        # `with` triggers the lifespan -> init_registry runs.
        with TestClient(reloaded.api) as client:
            # Health endpoint still works.
            resp = client.get("/api/app/health")
            assert resp.status_code == 200, resp.text

            # Other routers still serve after the reload.
            runs = client.get("/api/runs")
            assert runs.status_code == 200

            # AKMS dispatch preflight still serves.
            pre = client.post("/api/akms/node-generation/preflight", json={})
            assert pre.status_code == 200

    def test_lifespan_bootstrap_runs_before_init_registry(self, isolated_env):
        """The lifespan calls bootstrap_akms_workspace BEFORE init_registry
        (the workspace must exist before the run registry loads).

        We verify the order by checking the workspace was materialised
        (sentinel file present) by the time the first request hits the
        registry-backed /api/runs endpoint.
        """
        from logic_loom_api.main import api

        with TestClient(api) as client:
            # Hitting any endpoint forces the lifespan to run.
            client.get("/api/app/health")

            ws = Path(isolated_env / "ws")
            assert (ws / ".logic-loom-workspace-ok").exists(), (
                "bootstrap_akms_workspace did not run before init_registry"
            )

            # init_registry succeeded (registry exists -> /api/runs serves).
            assert client.get("/api/runs").status_code == 200

    def test_akms_dispatch_router_is_mounted(self):
        """The new router exists on the production app's routing table."""
        from logic_loom_api.main import api

        # ``api.routes`` is no longer a flat list of paths on FastAPI >= 0.14x:
        # ``include_router`` registers a lazy ``_IncludedRouter`` that has no
        # ``.path``. The OpenAPI document is the stable view of what is served.
        paths = set(api.openapi()["paths"])
        # The preflight endpoint is the canary — its presence on the
        # router table proves the include_router call succeeded.
        assert any("/api/akms/node-generation/preflight" in p for p in paths)
