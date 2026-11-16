"""Node-generation run routes.

Submitting a run produces a record visible at both
``/api/akms/node-generation/runs/<id>`` and ``/api/runs/<id>``
(single RunRegistry, AKMS dispatch is a specialisation of the generic
workflow run system).
"""

from __future__ import annotations

import asyncio
import os
import stat
import sys
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from logic_loom_api.batches import BatchStore, EditableBatch
from logic_loom_api.runs.registry import RunRegistry, get_registry
from logic_loom_api.routes.akms_dispatch import AKMS_NODE_EXTRACTION_WORKFLOW


def _build_test_app(ws: Path):
    """Construct a minimal FastAPI app with BOTH router prefixes.

    Verification is route-level: the AKMS dispatch routes share the
    generic RunRegistry, so we mount the existing `routes.runs.router`
    too and assert the run is visible at both endpoints.
    test_main_router_mounts.py covers the full main.py mount; this test
    stays focused on the route layer.
    """
    from fastapi import FastAPI
    from logic_loom_api.routes import akms_dispatch as akms_routes
    from logic_loom_api.routes import runs as runs_routes
    from logic_loom_api.runs.registry import init_registry

    app = FastAPI()
    app.include_router(
        akms_routes.router, prefix="/api/akms/node-generation"
    )
    app.include_router(runs_routes.router, prefix="/api/runs")
    # Manually init the registry since we're not using the production
    # lifespan.
    cache = ws.parent / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    init_registry(cache, zsum_repo=None)
    return app


@pytest.fixture()
def akms_client(tmp_path, monkeypatch):
    """TestClient with an isolated workspace + seeded batch."""
    ws = tmp_path / "akms-workspace"
    ws.mkdir()
    monkeypatch.setenv("LOGIC_LOOM_AKMS_WORKSPACE", str(ws))
    monkeypatch.setenv("LOGIC_LOOM_CACHE_PATH", str(tmp_path / "cache"))
    monkeypatch.setenv("LOGIC_LOOM_CONFIG", str(tmp_path / "missing.toml"))

    store = BatchStore()
    batch = EditableBatch(id="batch-1", title="Test Batch")
    store.add(batch)
    store.save(ws)

    app = _build_test_app(ws)
    with TestClient(app) as client:
        yield client, ws


def _create_run(client, **extra):
    # Default a notebook id so CI (no `nlm` on PATH) does not attempt
    # `nlm notebook create` — empty nlm_id means create (nlm_batch_fix).
    body = {
        "batch_id": "batch-1",
        "run_label": "ng-alpha",
        "dry_run": False,
        "nlm_id": "nb-test",
        **extra,
    }
    resp = client.post("/api/akms/node-generation/runs", json=body)
    return resp


class TestNodeGenerationRoutes:
    """Node-generation run routes: create, get, cancel, stream."""

    @pytest.mark.integration
    def test_dry_run_returns_preview_with_akms_workflow_id(self, akms_client):
        """A dry run goes through RunService.start_workflow with
        workflow_id pinned to akms.node_extraction."""
        client, _ws = akms_client
        resp = _create_run(client, dry_run=True)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "preview"
        assert body["workflow_id"] == AKMS_NODE_EXTRACTION_WORKFLOW

    @pytest.mark.integration
    def test_create_run_visible_in_both_endpoints(self, akms_client, monkeypatch):
        """The run is visible at both AKMS-prefixed and generic
        run endpoints because the registry is shared.

        Post-#103 ``create_run`` launches the executor; stub the attach so the
        two GET snapshots are not racing a live subprocess updating status."""
        from logic_loom_api.runs.registry import RunRegistry

        monkeypatch.setattr(
            RunRegistry, "attach_workflow_subprocess", lambda self, *a, **k: None
        )
        client, _ws = akms_client
        resp = _create_run(client)
        assert resp.status_code == 200, resp.text
        run_id = resp.json()["run_id"]

        # Visible at AKMS-prefixed endpoint.
        akms_get = client.get(f"/api/akms/node-generation/runs/{run_id}")
        assert akms_get.status_code == 200, akms_get.text
        assert akms_get.json()["id"] == run_id

        # AND at the generic run endpoint.
        gen_get = client.get(f"/api/runs/{run_id}")
        assert gen_get.status_code == 200, gen_get.text
        assert gen_get.json()["id"] == run_id

        # Both endpoints return the SAME record (single registry).
        assert akms_get.json() == gen_get.json()

    @pytest.mark.integration
    def test_create_run_propagates_workflow_inputs(self, akms_client):
        """``nlm_id`` / ``node_selection`` / ``plan_path`` ride along
        under workflow_inputs so the bundled workflow sees them."""
        client, _ws = akms_client
        resp = _create_run(
            client,
            dry_run=True,
            nlm_id="nbk-42",
            node_selection="all",
            plan_path="/tmp/plan.json",
        )
        assert resp.status_code == 200, resp.text
        wi = resp.json()["workflow_inputs"]
        assert wi["nlm_id"] == "nbk-42"
        assert wi["node_selection"] == "all"
        assert wi["plan_path"] == "/tmp/plan.json"

    @pytest.mark.integration
    def test_get_run_404_for_unknown_id(self, akms_client):
        client, _ws = akms_client
        resp = client.get("/api/akms/node-generation/runs/does-not-exist")
        assert resp.status_code == 404

    @pytest.mark.integration
    def test_cancel_404_for_unknown_id(self, akms_client):
        client, _ws = akms_client
        resp = client.post(
            "/api/akms/node-generation/runs/does-not-exist/cancel"
        )
        assert resp.status_code == 404

    @pytest.mark.integration
    def test_cancel_409_without_live_subprocess(self, akms_client, monkeypatch):
        """A run whose executor never attached a live subprocess → cancel 409.

        Post-#103 ``create_run`` launches the executor, so to isolate the 409
        cancel path we stub the attach: the run is registered (``pending``) with
        no live process, which is exactly the state cancel must reject with 409."""
        from logic_loom_api.runs.registry import RunRegistry

        monkeypatch.setattr(
            RunRegistry, "attach_workflow_subprocess", lambda self, *a, **k: None
        )
        client, _ws = akms_client
        run_id = _create_run(client).json()["run_id"]
        resp = client.post(f"/api/akms/node-generation/runs/{run_id}/cancel")
        assert resp.status_code == 409

    @pytest.mark.integration
    def test_create_run_batch_not_found(self, akms_client):
        client, _ws = akms_client
        resp = client.post(
            "/api/akms/node-generation/runs",
            json={"batch_id": "nope", "dry_run": True},
        )
        assert resp.status_code == 404

    @pytest.mark.integration
    def test_stream_delegates_to_registry_404_on_unknown(self, akms_client):
        """Stream endpoint surfaces the registry's 404 for unknown ids."""
        client, _ws = akms_client
        # TestClient does not iterate SSE — but the 404 path is emitted
        # before stream iteration starts (same pattern as
        # test_run_output_streaming.py).
        resp = client.get("/api/akms/node-generation/runs/unknown/stream")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_cancel_terminates_dispatch(self, tmp_path):
        """Cancel sends SIGTERM to a live workflow subprocess and the
        driver flips status to 'cancelled' on exit."""
        from logic_loom_api.runs.models import RunRecord  # noqa: F401

        cache = tmp_path / "cache"
        cache.mkdir()
        reg = RunRegistry(cache, zsum_repo=None)

        # Stub that sleeps long enough to be cancelled.
        #
        # On Windows the obvious `.bat` wrapper (used by
        # test_node_generation_dispatch.py::_make_stub_claude) does NOT work for *cancel* tests:
        # the .bat spawns `python.exe` as a child process, but
        # `TerminateProcess(cmd.exe, 1)` only kills the cmd.exe host;
        # the orphaned python child keeps running and holds stdout, so
        # the registry driver hangs on `proc.stdout.readline()` waiting
        # for an EOF that never comes. (Real Windows process-tree
        # cleanup needs job objects or `taskkill /T`, which is overkill
        # for a unit test.)
        #
        # Sidestep entirely: invoke `python sleeper.py` directly via the
        # args list, so the subprocess is a single python.exe that
        # TerminateProcess kills cleanly. On POSIX we can still use the
        # shebang form for parity with the rest of the test suite.
        py_stub = tmp_path / "sleeper.py"
        py_body = textwrap.dedent(
            """\
            import signal, sys, time
            while True:
                time.sleep(0.1)
            """
        )
        if os.name == "nt":
            py_stub.write_text(py_body, encoding="utf-8")
            stub_args = [sys.executable, str(py_stub)]
        else:
            py_stub.write_text(f"#!{sys.executable}\n{py_body}", encoding="utf-8")
            py_stub.chmod(
                py_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
            )
            stub_args = [str(py_stub)]

        run_id = "run_2026-05-13_0000_p93-cancel"
        log_path = tmp_path / "logs.txt"
        log_path.touch()
        reg.register_workflow_run(
            batch_id="b",
            workflow_id=AKMS_NODE_EXTRACTION_WORKFLOW,
            run_id=run_id,
            log_path=str(log_path),
            cwd=str(tmp_path),
        )
        task = reg.attach_workflow_subprocess(
            run_id, args=stub_args, cwd=tmp_path, log_path=log_path
        )

        # Wait for the driver to set status='running'.
        for _ in range(50):
            rec = reg.get(run_id)
            if rec.status == "running":
                break
            await asyncio.sleep(0.05)
        assert reg.get(run_id).status == "running"

        # Cancel.
        assert reg.cancel(run_id) is True
        await asyncio.wait_for(task, timeout=10.0)
        rec = reg.get(run_id)
        assert rec.status == "cancelled"
