"""Tests for the Assistant Outputs API.

Covers:

  1. ``GET /api/assistant/outputs`` lists only runs whose ``workflow_id``
     belongs to an Assistant workflow (category filter + bundled-id
     fallback for archived workflows).
  2. ``GET /api/assistant/outputs/{run_id}/primary`` returns the
     primary output file contents (the rendered markdown body).
  3. ``POST /api/assistant/outputs/{run_id}/open-folder`` returns the
     run directory's absolute path.
  4. ``POST /api/assistant/outputs/{run_id}/export`` zips the run
     directory and returns the zip stream.
  5. Defensive: unknown run_id returns 404 across every run-scoped
     endpoint.

The endpoint module mounts under ``/api/assistant/outputs``.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from logic_loom_api.config import Settings
from logic_loom_api.main import api
from logic_loom_api.runs import registry as registry_module
from logic_loom_api.runs.registry import RunRegistry


_ASSISTANT_WORKFLOW_ID = "assistant.literature_survey"
_AKMS_WORKFLOW_ID = "akms.node_extraction"


@pytest.fixture
def isolated_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Settings.load() returns a tmp-tree workspace + the global registry
    is replaced with a fresh one rooted at tmp_path.

    Both swaps reverse on test teardown.
    """
    workspace = tmp_path / "AKMS-Workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    cache_dir = tmp_path / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    def _settings_load(*_a, **_kw):
        return Settings(
            akms_workspace=workspace,
            assistant_workspace=tmp_path / "Assistant-Workspace",
            assistant_workspace_colocate=False,
            cache_path=cache_dir,
        )

    monkeypatch.setattr(
        "logic_loom_api.config.Settings.load",
        classmethod(_settings_load),
    )
    fresh = RunRegistry(cache_dir=cache_dir, zsum_repo=None)
    monkeypatch.setattr(registry_module, "_GLOBAL_REGISTRY", fresh)
    return tmp_path


@pytest.fixture
def client(isolated_workspace: Path) -> TestClient:
    return TestClient(api)


def _stage_run_dir(
    workspace_root: Path,
    *,
    batch_id: str,
    workflow_id: str,
    run_id: str,
    primary_body: str | None = "# Primary output\n\nSome assistant body.",
) -> Path:
    """Create the assistant run dir + the four reproducibility artefacts
    + an optional primary markdown file. Returns the run-dir path."""
    run_dir = (
        workspace_root
        / "Assistant-Workspace"
        / "assistant"
        / "outputs"
        / batch_id
        / workflow_id
        / run_id
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "metadata.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": run_id,
                "batch_id": batch_id,
                "workflow_id": workflow_id,
                "kind": "workflow",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "source_snapshot.json").write_text(
        json.dumps({"schema_version": 1, "batch_id": batch_id}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    snap = run_dir / "prompt_snapshot"
    snap.mkdir(parents=True, exist_ok=True)
    (snap / "effective_prompts.json").write_text(
        json.dumps({"schema_version": 1, "slots": {}}, indent=2) + "\n",
        encoding="utf-8",
    )
    (snap / "main.md").write_text("# prompt", encoding="utf-8")
    (run_dir / "logs.txt").write_text("", encoding="utf-8")
    if primary_body is not None:
        (run_dir / "output.md").write_text(primary_body, encoding="utf-8")
    return run_dir


def _register_run(
    *,
    batch_id: str,
    workflow_id: str,
    run_id: str,
    log_path: Path,
) -> None:
    """Register a workflow run in the global registry."""
    registry_module.get_registry().register_workflow_run(
        batch_id=batch_id,
        workflow_id=workflow_id,
        run_id=run_id,
        log_path=str(log_path),
    )


class TestAssistantOutputsApi:
    """Outputs API for Assistant."""

    def test_list_filters_to_assistant_workflow_ids_only(
        self, client: TestClient, isolated_workspace: Path
    ) -> None:
        """Verifies: a mixed registry returns only Assistant-family runs."""
        # Stage one Assistant run + one AKMS-bundle workflow run.
        a_dir = _stage_run_dir(
            isolated_workspace,
            batch_id="b-assistant",
            workflow_id=_ASSISTANT_WORKFLOW_ID,
            run_id="run_2026-05-15_1000_one",
        )
        k_dir = _stage_run_dir(
            isolated_workspace,
            batch_id="b-akms",
            workflow_id=_AKMS_WORKFLOW_ID,
            run_id="run_2026-05-15_1001_two",
        )
        _register_run(
            batch_id="b-assistant",
            workflow_id=_ASSISTANT_WORKFLOW_ID,
            run_id="run_2026-05-15_1000_one",
            log_path=a_dir / "logs.txt",
        )
        _register_run(
            batch_id="b-akms",
            workflow_id=_AKMS_WORKFLOW_ID,
            run_id="run_2026-05-15_1001_two",
            log_path=k_dir / "logs.txt",
        )

        res = client.get("/api/assistant/outputs")
        assert res.status_code == 200, res.text
        body = res.json()
        run_ids = [r["id"] for r in body["runs"]]
        assert run_ids == ["run_2026-05-15_1000_one"]
        assert all(
            r["workflow_id"].startswith("assistant.") for r in body["runs"]
        )

    def test_primary_returns_markdown_body(
        self, client: TestClient, isolated_workspace: Path
    ) -> None:
        """Verifies: primary endpoint returns the run's primary output body."""
        run_id = "run_2026-05-15_1100_three"
        body_text = "# Lit review\n\nThis is the markdown body."
        run_dir = _stage_run_dir(
            isolated_workspace,
            batch_id="b1",
            workflow_id=_ASSISTANT_WORKFLOW_ID,
            run_id=run_id,
            primary_body=body_text,
        )
        _register_run(
            batch_id="b1",
            workflow_id=_ASSISTANT_WORKFLOW_ID,
            run_id=run_id,
            log_path=run_dir / "logs.txt",
        )

        res = client.get(f"/api/assistant/outputs/{run_id}/primary")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["run_id"] == run_id
        assert body["filename"] == "output.md"
        assert body["content"] == body_text

    def test_primary_404_when_no_markdown_output(
        self, client: TestClient, isolated_workspace: Path
    ) -> None:
        """Negative case: 404 when no executor markdown exists yet."""
        run_id = "run_2026-05-15_1200_four"
        run_dir = _stage_run_dir(
            isolated_workspace,
            batch_id="b1",
            workflow_id=_ASSISTANT_WORKFLOW_ID,
            run_id=run_id,
            primary_body=None,
        )
        _register_run(
            batch_id="b1",
            workflow_id=_ASSISTANT_WORKFLOW_ID,
            run_id=run_id,
            log_path=run_dir / "logs.txt",
        )

        res = client.get(f"/api/assistant/outputs/{run_id}/primary")
        assert res.status_code == 404
        # The repo's errors.install_handlers wraps HTTPException(detail=msg)
        # into envelope shape {"error": {code, message, detail}}; the raised
        # HTTPException.detail becomes the envelope's ``message``.
        assert "no primary output" in res.json()["error"]["message"]

    def test_open_folder_returns_absolute_path(
        self, client: TestClient, isolated_workspace: Path
    ) -> None:
        """Verifies: open-folder returns ``{path: <abs>}`` for the run dir."""
        run_id = "run_2026-05-15_1300_five"
        run_dir = _stage_run_dir(
            isolated_workspace,
            batch_id="b1",
            workflow_id=_ASSISTANT_WORKFLOW_ID,
            run_id=run_id,
        )
        _register_run(
            batch_id="b1",
            workflow_id=_ASSISTANT_WORKFLOW_ID,
            run_id=run_id,
            log_path=run_dir / "logs.txt",
        )

        res = client.post(f"/api/assistant/outputs/{run_id}/open-folder")
        assert res.status_code == 200
        body = res.json()
        assert Path(body["path"]).is_absolute()
        assert Path(body["path"]) == run_dir.resolve()

    def test_export_zips_run_dir(
        self, client: TestClient, isolated_workspace: Path
    ) -> None:
        """Verifies: export endpoint produces a zip containing every
        reproducibility artefact (metadata.json, source_snapshot.json,
        prompt_snapshot/, logs.txt, primary output)."""
        run_id = "run_2026-05-15_1400_six"
        run_dir = _stage_run_dir(
            isolated_workspace,
            batch_id="b1",
            workflow_id=_ASSISTANT_WORKFLOW_ID,
            run_id=run_id,
        )
        _register_run(
            batch_id="b1",
            workflow_id=_ASSISTANT_WORKFLOW_ID,
            run_id=run_id,
            log_path=run_dir / "logs.txt",
        )

        res = client.post(f"/api/assistant/outputs/{run_id}/export")
        assert res.status_code == 200, res.text
        assert res.headers["content-type"] == "application/zip"
        assert f'filename="{run_id}.zip"' in res.headers.get(
            "content-disposition", ""
        )

        zf = zipfile.ZipFile(io.BytesIO(res.content))
        names = set(zf.namelist())
        expected_files = {
            f"{run_id}/metadata.json",
            f"{run_id}/source_snapshot.json",
            f"{run_id}/prompt_snapshot/effective_prompts.json",
            f"{run_id}/prompt_snapshot/main.md",
            f"{run_id}/logs.txt",
            f"{run_id}/output.md",
        }
        missing = expected_files - names
        assert not missing, f"missing zip entries: {missing}"

    def test_404_on_unknown_run_id(
        self, client: TestClient, isolated_workspace: Path
    ) -> None:
        """Defensive: unknown run_id surfaces as 404 across the three
        run-scoped endpoints (primary / open-folder / export)."""
        for route in ("primary",):
            res = client.get(f"/api/assistant/outputs/missing/{route}")
            assert res.status_code == 404, route
        for route in ("open-folder", "export"):
            res = client.post(f"/api/assistant/outputs/missing/{route}")
            assert res.status_code == 404, route

    def test_404_when_run_is_not_assistant_family(
        self, client: TestClient, isolated_workspace: Path
    ) -> None:
        """Negative case: AKMS-family runs are 404 on the run-scoped endpoints."""
        run_id = "run_2026-05-15_1500_seven"
        run_dir = _stage_run_dir(
            isolated_workspace,
            batch_id="b-akms",
            workflow_id=_AKMS_WORKFLOW_ID,
            run_id=run_id,
        )
        _register_run(
            batch_id="b-akms",
            workflow_id=_AKMS_WORKFLOW_ID,
            run_id=run_id,
            log_path=run_dir / "logs.txt",
        )

        res = client.get(f"/api/assistant/outputs/{run_id}/primary")
        assert res.status_code == 404
        res = client.post(f"/api/assistant/outputs/{run_id}/open-folder")
        assert res.status_code == 404
