"""Tests for the Assistant WorkflowLibrary view.

Covers:
  1. ``GET /api/assistant/workflows`` lists the six bundled
     Assistant workflows.
  2. AKMS ``Node Extraction`` workflow is NOT listed in the Assistant
     view — the category filter excludes it.
  3. Duplicate / archive delegated calls produce the expected
     side effects on the shared workflow library and stay scoped to
     Assistant-category workflows.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from logic_loom_api.config import Settings
from logic_loom_api.main import api
from logic_loom_api.routes.assistant_workflows import ASSISTANT_CATEGORIES


_ASSISTANT_WORKFLOW_IDS = (
    "assistant.literature_survey",
    "assistant.paper_introduction",
    "assistant.related_work",
    "assistant.proposal_ideas",
    "assistant.method_comparison",
    "assistant.annotated_bibliography",
)


@pytest.fixture
def isolated_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point Settings.load() at a fresh tmp workspace for each test."""
    workspace = tmp_path / "AKMS-Workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    def _settings_load(*_a, **_kw):
        return Settings(
            akms_workspace=workspace,
            assistant_workspace=tmp_path / "Assistant-Workspace",
            assistant_workspace_colocate=False,
            cache_path=tmp_path / ".cache",
        )

    # Settings.load() is referenced from routes.workflows (_user_workflows_dir)
    # and main.py (lifespan). Patch the class attribute so every caller
    # sees the tmp tree.
    monkeypatch.setattr("logic_loom_api.config.Settings.load", classmethod(_settings_load))
    return workspace


@pytest.fixture
def client(isolated_workspace: Path) -> TestClient:
    """TestClient with the isolated workspace already in place."""
    return TestClient(api)


class TestAssistantWorkflowLibrary:
    """Integration tests for the Assistant WorkflowLibrary view."""

    @pytest.mark.integration
    def test_list_assistant_workflows_returns_six(self, client: TestClient) -> None:
        """Verifies: GET /api/assistant/workflows returns the six
        bundled Assistant workflows.
        """
        resp = client.get("/api/assistant/workflows")
        assert resp.status_code == 200
        payload = resp.json()
        ids = {wf["id"] for wf in payload["workflows"]}
        for wid in _ASSISTANT_WORKFLOW_IDS:
            assert wid in ids, f"Assistant view missing {wid}"
        # Exactly six bundled Assistant workflows ship.
        assistant_only = [wf for wf in payload["workflows"] if wf["id"] in _ASSISTANT_WORKFLOW_IDS]
        assert len(assistant_only) == 6

    @pytest.mark.integration
    def test_list_excludes_akms_node_extraction(self, client: TestClient) -> None:
        """Verifies: akms.node_extraction is NOT in the listing.

        Also excludes the AKMS-bundle's other bundled demos
        (research.literature_survey, writing.paper_introduction,
        research.proposal_ideas) — those carry non-Assistant categories
        in the AKMS bundle (Research Synthesis / Paper Writing / Proposal
        Writing — note the second + third happen to be in the Assistant
        set but the AKMS-bundle workflow ids start with `writing.` and
        `research.` not `assistant.`, so the category filter is the only
        gate). Actually `writing.paper_introduction` has category
        "Paper Writing" which IS in the Assistant set, so the test only
        excludes `akms.node_extraction` and `research.literature_survey`
        which has category "Research Synthesis" (outside Assistant set).
        """
        resp = client.get("/api/assistant/workflows")
        assert resp.status_code == 200
        ids = {wf["id"] for wf in resp.json()["workflows"]}
        assert "akms.node_extraction" not in ids, "AKMS Node Extraction must be filtered out"
        # Sanity: every listed workflow has a category in the Assistant set.
        for wf in resp.json()["workflows"]:
            assert wf["category"] in ASSISTANT_CATEGORIES, (
                f"workflow {wf['id']} has category {wf['category']!r} "
                "which is outside the Assistant set"
            )

    @pytest.mark.integration
    def test_get_single_assistant_workflow_passes_through(
        self, client: TestClient
    ) -> None:
        """Verifies: GET /api/assistant/workflows/{id} returns the
        workflow for an Assistant-category id.
        """
        resp = client.get("/api/assistant/workflows/assistant.literature_survey")
        assert resp.status_code == 200
        wf = resp.json()
        assert wf["id"] == "assistant.literature_survey"
        assert wf["category"] == "Literature Review"

    @pytest.mark.integration
    def test_get_non_assistant_workflow_returns_404(
        self, client: TestClient
    ) -> None:
        """Verifies the same exclusion from the lookup side: requesting an
        AKMS-category workflow through the Assistant view returns 404.
        """
        resp = client.get("/api/assistant/workflows/akms.node_extraction")
        assert resp.status_code == 404

    @pytest.mark.integration
    def test_duplicate_endpoint_delegates_to_shared_api(
        self, client: TestClient, isolated_workspace: Path
    ) -> None:
        """Verifies: POST /api/assistant/workflows/{id}/duplicate delegates
        to the shared workflow library and produces a new user-side
        workflow entry visible via subsequent listings.
        """
        resp = client.post(
            "/api/assistant/workflows/assistant.literature_survey/duplicate"
        )
        assert resp.status_code == 200
        dup = resp.json()
        # Duplicated id is suffixed with a numeric.
        assert dup["id"].startswith("assistant.literature_survey")
        assert dup["id"] != "assistant.literature_survey"
        assert dup["source"] == "user"
        assert dup["status"] == "active"
        # New duplicate appears in the listing.
        listing = client.get("/api/assistant/workflows").json()["workflows"]
        ids = {wf["id"] for wf in listing}
        assert dup["id"] in ids

    @pytest.mark.integration
    def test_archive_endpoint_delegates_to_shared_api(
        self, client: TestClient, isolated_workspace: Path
    ) -> None:
        """Verifies: POST /api/assistant/workflows/{id}/archive flips a
        user workflow's status to "archived" via the shared route.

        Archive is only valid on user workflows (built-ins return 409 per
        shared API; this test duplicates first, then archives the dup).
        """
        # Duplicate first (built-ins cannot be archived directly).
        dup_resp = client.post(
            "/api/assistant/workflows/assistant.literature_survey/duplicate"
        )
        assert dup_resp.status_code == 200
        dup_id = dup_resp.json()["id"]

        # Archive the user duplicate.
        arch_resp = client.post(f"/api/assistant/workflows/{dup_id}/archive")
        assert arch_resp.status_code == 200
        assert arch_resp.json()["status"] == "archived"

        # Default listing hides archived entries; verify it's hidden.
        listing = client.get("/api/assistant/workflows").json()["workflows"]
        ids = {wf["id"] for wf in listing}
        assert dup_id not in ids

        # Include_archived=true surfaces it.
        listing_all = client.get(
            "/api/assistant/workflows?include_archived=true"
        ).json()["workflows"]
        ids_all = {wf["id"] for wf in listing_all}
        assert dup_id in ids_all

    @pytest.mark.unit
    def test_category_filter_set_is_exactly_four_documented_values(self) -> None:
        """Verifies the Assistant categories constant matches the
        documented closed set.

        Pinning the constant ensures deliberate changes surface in the
        diff (same closed-set convention as RELATED_MODES).
        """
        assert ASSISTANT_CATEGORIES == frozenset(
            {"Literature Review", "Paper Writing", "Proposal Writing", "Analysis"}
        )
