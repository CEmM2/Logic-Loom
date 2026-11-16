"""nlm_batch_fix — launch node extraction + create notebook when id missing.

Covers:

1. Empty ``nlm_id`` preflight is OK (create-at-run-time, not a hard fail).
2. :func:`parse_nlm_notebook_id` / :func:`create_nlm_notebook` /
   :func:`resolve_nlm_id_for_batch` precedence + create+persist.
3. ``POST /api/akms/node-generation/runs`` with no nlm_id creates a notebook
   and attaches the executor with that id in the prompt.
4. ``POST /api/runs/workflow`` for ``akms.node_extraction`` attaches the
   executor (no more forever-``pending`` orphans).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from logic_loom_api.akms_dispatch import (
    AkmsDispatchError,
    create_nlm_notebook,
    parse_nlm_notebook_id,
    resolve_nlm_id_for_batch,
)
from logic_loom_api.batches import BatchStore, EditableBatch
from logic_loom_api.runs.registry import RunRegistry, init_registry


_KEY_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "AKMS_LLM_API_BASE",
    "AKMS_LEARN_NLM_NOTEBOOK_ID",
    "NLM_NOTEBOOK_ID",
)


@pytest.fixture(autouse=True)
def _scrub_key_env(monkeypatch):
    for var in _KEY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _all_binaries_on_path(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda binary: "/usr/bin/" + binary)


# ── pure helpers ─────────────────────────────────────────────────────────────


class TestParseNlmNotebookId:
    def test_json_id(self):
        assert parse_nlm_notebook_id('{"id": "nb_json_abc"}') == "nb_json_abc"

    def test_nb_token(self):
        assert parse_nlm_notebook_id("Created notebook: nb_plain_xyz") == "nb_plain_xyz"

    def test_drive_style(self):
        long_id = "a" * 24
        assert parse_nlm_notebook_id(f"id={long_id}") == long_id

    def test_empty(self):
        assert parse_nlm_notebook_id("") == ""
        assert parse_nlm_notebook_id("nothing useful") == ""


class TestCreateNlmNotebook:
    def test_success(self, monkeypatch):
        cp = SimpleNamespace(returncode=0, stdout="Created notebook: nb_new_1\n", stderr="")
        monkeypatch.setattr(
            "subprocess.run",
            lambda *a, **k: cp,
        )
        assert create_nlm_notebook("My Batch") == "nb_new_1"

    def test_nonzero_exit(self, monkeypatch):
        cp = SimpleNamespace(returncode=1, stdout="", stderr="auth required")
        monkeypatch.setattr("subprocess.run", lambda *a, **k: cp)
        with pytest.raises(AkmsDispatchError, match="exited 1"):
            create_nlm_notebook("x")

    def test_unparseable(self, monkeypatch):
        cp = SimpleNamespace(returncode=0, stdout="ok", stderr="")
        monkeypatch.setattr("subprocess.run", lambda *a, **k: cp)
        with pytest.raises(AkmsDispatchError, match="could not parse"):
            create_nlm_notebook("x")


class TestResolveNlmIdForBatch:
    def test_explicit_wins(self, tmp_path):
        nid, meta = resolve_nlm_id_for_batch(
            explicit_nlm_id="nb_explicit",
            batch_notebook_id="nb_batch",
            batch_id="b1",
            workspace=tmp_path,
        )
        assert nid == "nb_explicit"
        assert meta == {"source": "explicit", "created": False}

    def test_batch_reuse(self, tmp_path):
        nid, meta = resolve_nlm_id_for_batch(
            explicit_nlm_id=None,
            batch_notebook_id="nb_on_batch",
            batch_id="b1",
            workspace=tmp_path,
        )
        assert nid == "nb_on_batch"
        assert meta["source"] == "batch"
        assert meta["created"] is False

    def test_create_and_persist(self, tmp_path, monkeypatch):
        ws = tmp_path / "ws"
        ws.mkdir()
        store = BatchStore()
        store.add(EditableBatch(id="aniso-blocks", title="Aniso Blocks"))
        store.save(ws)

        monkeypatch.setattr(
            "logic_loom_api.akms_dispatch.create_nlm_notebook",
            lambda title, nlm_binary="nlm": "nb_created_99",
        )
        nid, meta = resolve_nlm_id_for_batch(
            explicit_nlm_id=None,
            batch_notebook_id=None,
            batch_id="aniso-blocks",
            batch_title="Aniso Blocks",
            workspace=ws,
            persist=True,
        )
        assert nid == "nb_created_99"
        assert meta["source"] == "created"
        assert meta["created"] is True
        assert meta.get("persisted") is True

        reloaded = BatchStore.load(ws).get("aniso-blocks")
        assert reloaded is not None
        assert reloaded.notebook_id == "nb_created_99"


# ── preflight ────────────────────────────────────────────────────────────────


def _app_with_routes(tmp_path, monkeypatch):
    from logic_loom_api.routes import akms_dispatch as akms_routes
    from logic_loom_api.routes import runs as runs_routes

    ws = tmp_path / "akms-workspace"
    ws.mkdir()
    monkeypatch.setenv("LOGIC_LOOM_AKMS_WORKSPACE", str(ws))
    monkeypatch.setenv("LOGIC_LOOM_CACHE_PATH", str(tmp_path / "cache"))
    monkeypatch.setenv("LOGIC_LOOM_CONFIG", str(tmp_path / "missing.toml"))

    store = BatchStore()
    store.add(EditableBatch(id="batch-1", title="Test Batch"))
    store.save(ws)

    captured: list[dict] = []

    def _capture_attach(
        self, run_id, *, args, cwd, log_path, env=None, stdin=None, on_done=None
    ):
        captured.append(
            {
                "run_id": run_id,
                "args": list(args),
                "cwd": str(cwd),
                "log_path": str(log_path),
                "env": dict(env or {}),
                "stdin": stdin,
            }
        )
        return None

    monkeypatch.setattr(RunRegistry, "attach_workflow_subprocess", _capture_attach)

    app = FastAPI()
    app.include_router(akms_routes.router, prefix="/api/akms/node-generation")
    app.include_router(runs_routes.router, prefix="/api/runs")
    cache = tmp_path / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    init_registry(cache, zsum_repo=None)
    return app, captured, ws


class TestPreflightEmptyNlmId:
    def test_empty_nlm_id_check_is_ok(self):
        """Missing nlm_id means create-at-run-time — preflight must not fail it."""
        import asyncio

        from logic_loom_api.akms_dispatch import preflight_node_generation

        result = asyncio.run(
            preflight_node_generation(nlm_id=None, plan_path=None, out_dir=None)
        )
        nlm_check = next(c for c in result["checks"] if c["name"] == "nlm_id")
        assert nlm_check["ok"] is True
        assert "new notebook" in nlm_check["detail"].lower()

    def test_explicit_nlm_id_still_reported(self):
        import asyncio

        from logic_loom_api.akms_dispatch import preflight_node_generation

        result = asyncio.run(preflight_node_generation(nlm_id="nb_known"))
        nlm_check = next(c for c in result["checks"] if c["name"] == "nlm_id")
        assert nlm_check["ok"] is True
        assert "nb_known" in nlm_check["detail"]


# ── route integration ────────────────────────────────────────────────────────


class TestNodeGenCreatesNotebookAndAttaches:
    @pytest.mark.integration
    def test_create_run_without_nlm_id_creates_and_attaches(
        self, tmp_path, monkeypatch
    ):
        app, captured, ws = _app_with_routes(tmp_path, monkeypatch)
        monkeypatch.setattr(
            "logic_loom_api.routes.akms_dispatch.resolve_nlm_id_for_batch",
            lambda **kwargs: ("nb_auto_42", {"source": "created", "created": True}),
        )
        with TestClient(app) as client:
            resp = client.post(
                "/api/akms/node-generation/runs",
                json={"batch_id": "batch-1", "run_label": "auto", "dry_run": False},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "enqueued"
        assert body.get("nlm_id") == "nb_auto_42"
        assert len(captured) == 1, "executor must be attached"
        # Prompt / argv must carry the resolved notebook id.
        joined = " ".join(str(a) for a in captured[0]["args"])
        assert "nb_auto_42" in joined

    @pytest.mark.integration
    def test_generic_runs_workflow_attaches_for_node_extraction(
        self, tmp_path, monkeypatch
    ):
        """POST /api/runs/workflow for akms.node_extraction must launch, not
        leave a forever-pending register-only run."""
        app, captured, ws = _app_with_routes(tmp_path, monkeypatch)
        monkeypatch.setattr(
            "logic_loom_api.routes.akms_dispatch.resolve_nlm_id_for_batch",
            lambda **kwargs: ("nb_via_generic", {"source": "created", "created": True}),
        )
        with TestClient(app) as client:
            resp = client.post(
                "/api/runs/workflow",
                json={
                    "batch_id": "batch-1",
                    "workflow_id": "akms.node_extraction",
                    "run_label": "gui",
                    "dry_run": False,
                },
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "enqueued"
        assert len(captured) == 1, (
            "generic /api/runs/workflow must attach the node-gen executor"
        )
        joined = " ".join(str(a) for a in captured[0]["args"])
        assert "nb_via_generic" in joined

    @pytest.mark.integration
    def test_dry_run_does_not_create_or_attach(self, tmp_path, monkeypatch):
        app, captured, ws = _app_with_routes(tmp_path, monkeypatch)
        create_spy = MagicMock(side_effect=AssertionError("must not create on dry_run"))
        monkeypatch.setattr(
            "logic_loom_api.routes.akms_dispatch.resolve_nlm_id_for_batch",
            create_spy,
        )
        with TestClient(app) as client:
            resp = client.post(
                "/api/akms/node-generation/runs",
                json={"batch_id": "batch-1", "dry_run": True},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "preview"
        assert captured == []
        create_spy.assert_not_called()
