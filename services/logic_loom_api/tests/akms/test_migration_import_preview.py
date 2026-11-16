"""Migration import endpoints + dry-run preview.

Covers:
  mode=preview returns a diff payload and writes nothing.
  mode=replace requires the preview's token.

Tier: integration. Uses FastAPI TestClient against the four
migration import endpoints with an isolated AKMS workspace under
tmp_path. Token registry is cleared in the fixture so cross-test state
cannot pollute the invariant checks.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    """A TestClient with an isolated AKMS workspace + token registry."""
    workspace = tmp_path / "akms-workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "prompt-library").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("LOGIC_LOOM_AKMS_WORKSPACE", str(workspace))

    import logic_loom_api.import_tokens as _it
    import logic_loom_api.main as m

    importlib.reload(m)
    _it.registry().clear()
    with TestClient(m.api) as c:
        c.workspace_root = workspace  # type: ignore[attr-defined]
        yield c
    _it.registry().clear()


def _make_repo(root: Path) -> Path:
    """Create a minimal fake AKMS repo tree under ``root``."""
    vault = root / "Packages" / "Nodes_Vault"
    (vault / "geom").mkdir(parents=True, exist_ok=True)
    (vault / "geom" / "node-a.md").write_text("# node A\n")
    (vault / "geom" / "node-b.md").write_text("# node B\n")
    (vault / "topology.md").write_text("# topology root\n")
    return root


_LEGACY_PLAN = """
# Generation Plan

## Batch: paper-set-a

### Nodes
| Node ID | Title |
|---|---|
| n.geom.1 | First node |
""".strip()


class TestMigrationImportPreview:
    """Migration import endpoints with preview/merge/replace modes."""

    # ------------------------------------------------------------------
    # Preview writes nothing
    # ------------------------------------------------------------------

    @pytest.mark.integration
    def test_preview_writes_nothing(self, client, tmp_path):
        """preview must return a diff payload AND not touch the destination."""
        repo = _make_repo(tmp_path / "fake-akms")
        dest = client.workspace_root / "AKMS_Sources" / "new"  # type: ignore[attr-defined]
        # Pre-state: dest empty (workspace fresh).
        before = sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()) if dest.exists() else []

        resp = client.post(
            "/api/workspace/import-akms-repo",
            json={"mode": "preview", "source_repo": str(repo)},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["mode"] == "preview"
        assert "diff" in body and isinstance(body["diff"], dict)
        # All three files should be classified `new` against an empty dest.
        assert set(body["diff"]["new"]) == {"geom/node-a.md", "geom/node-b.md", "topology.md"}
        assert body["diff"]["identical"] == []
        assert body["diff"]["conflict"] == []
        # Token is minted.
        assert isinstance(body["preview_token"], str) and len(body["preview_token"]) > 0

        # Dest must remain untouched.
        after = sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()) if dest.exists() else []
        assert after == before

    # ------------------------------------------------------------------
    # mode=replace requires the preview's token
    # ------------------------------------------------------------------

    @pytest.mark.integration
    def test_replace_without_token_returns_400(self, client, tmp_path):
        """replace with no preview_token → HTTP 400 and no writes."""
        repo = _make_repo(tmp_path / "fake-akms")
        dest = client.workspace_root / "AKMS_Sources" / "new"  # type: ignore[attr-defined]

        resp = client.post(
            "/api/workspace/import-akms-repo",
            json={"mode": "replace", "source_repo": str(repo)},
        )
        assert resp.status_code == 400, resp.text
        # No files should have been written.
        files = list(dest.rglob("*")) if dest.exists() else []
        assert all(not p.is_file() for p in files)

    @pytest.mark.integration
    def test_replace_with_unknown_token_returns_400(self, client, tmp_path):
        """A bogus token that was never minted is rejected."""
        repo = _make_repo(tmp_path / "fake-akms")
        resp = client.post(
            "/api/workspace/import-akms-repo",
            json={
                "mode": "replace",
                "source_repo": str(repo),
                "preview_token": "totally-fake-token-not-from-preview",
            },
        )
        assert resp.status_code == 400
        assert "preview_token" in resp.text.lower() or "token" in resp.text.lower()

    @pytest.mark.integration
    def test_replace_token_from_different_endpoint_rejected(self, client, tmp_path):
        """Token minted by ``import-plan`` cannot authorise ``import-akms-repo``."""
        repo = _make_repo(tmp_path / "fake-akms")
        # Mint a preview token on import-plan.
        plan_resp = client.post(
            "/api/batches/import-plan",
            json={"text": _LEGACY_PLAN, "mode": "preview"},
        )
        assert plan_resp.status_code == 200, plan_resp.text
        wrong_token = plan_resp.json()["preview_token"]

        resp = client.post(
            "/api/workspace/import-akms-repo",
            json={
                "mode": "replace",
                "source_repo": str(repo),
                "preview_token": wrong_token,
            },
        )
        assert resp.status_code == 400
        assert "minted for" in resp.text or "endpoint" in resp.text.lower()

    @pytest.mark.integration
    def test_replace_with_valid_token_succeeds(self, client, tmp_path):
        """Valid preview → replace round trip writes files and consumes token."""
        repo = _make_repo(tmp_path / "fake-akms")
        dest = client.workspace_root / "AKMS_Sources" / "new"  # type: ignore[attr-defined]

        prev = client.post(
            "/api/workspace/import-akms-repo",
            json={"mode": "preview", "source_repo": str(repo)},
        )
        assert prev.status_code == 200
        token = prev.json()["preview_token"]

        resp = client.post(
            "/api/workspace/import-akms-repo",
            json={
                "mode": "replace",
                "source_repo": str(repo),
                "preview_token": token,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["mode"] == "replace"
        assert set(body["written"]) == {"geom/node-a.md", "geom/node-b.md", "topology.md"}
        # Files materialised.
        assert (dest / "geom" / "node-a.md").read_text() == "# node A\n"
        assert (dest / "topology.md").read_text() == "# topology root\n"

        # Token consumed: replaying it on a second replace must 400.
        replay = client.post(
            "/api/workspace/import-akms-repo",
            json={
                "mode": "replace",
                "source_repo": str(repo),
                "preview_token": token,
            },
        )
        assert replay.status_code == 400

    # ------------------------------------------------------------------
    # merge preserves user edits
    # ------------------------------------------------------------------

    @pytest.mark.integration
    def test_merge_preserves_user_local_edits(self, client, tmp_path):
        """mode=merge must skip conflicts (user-modified content)."""
        repo = _make_repo(tmp_path / "fake-akms")
        dest = client.workspace_root / "AKMS_Sources" / "new"  # type: ignore[attr-defined]
        # Pre-populate one of the dest files with user edits (different bytes).
        (dest / "geom").mkdir(parents=True, exist_ok=True)
        (dest / "geom" / "node-a.md").write_text("# USER EDIT — DO NOT OVERWRITE\n")

        resp = client.post(
            "/api/workspace/import-akms-repo",
            json={"mode": "merge", "source_repo": str(repo)},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["mode"] == "merge"
        # node-a.md should be a conflict (different content) and thus skipped.
        assert "geom/node-a.md" in body["skipped_conflicts"]
        # node-b.md and topology.md were new — both written.
        assert set(body["written"]) >= {"geom/node-b.md", "topology.md"}
        # User content preserved.
        assert (dest / "geom" / "node-a.md").read_text() == "# USER EDIT — DO NOT OVERWRITE\n"

    # ------------------------------------------------------------------
    # The other three endpoints share the same token-invariant via the
    # registry — a single representative case proves the wiring on each.
    # ------------------------------------------------------------------

    @pytest.mark.integration
    def test_import_plan_replace_requires_token(self, client):
        """``/api/batches/import-plan`` honours the same invariant."""
        bad = client.post(
            "/api/batches/import-plan",
            json={"text": _LEGACY_PLAN, "mode": "replace"},
        )
        assert bad.status_code == 400

        prev = client.post(
            "/api/batches/import-plan",
            json={"text": _LEGACY_PLAN, "mode": "preview"},
        )
        assert prev.status_code == 200
        token = prev.json()["preview_token"]

        ok = client.post(
            "/api/batches/import-plan",
            json={"text": _LEGACY_PLAN, "mode": "replace", "preview_token": token},
        )
        assert ok.status_code == 200, ok.text

    @pytest.mark.integration
    def test_import_claude_assets_replace_requires_token(self, client, tmp_path):
        """``/api/prompts/import-akms-claude-assets`` honours the invariant."""
        src = tmp_path / "fake-claude-src"
        (src / ".claude").mkdir(parents=True)
        (src / ".claude" / "settings.json").write_text("{}\n")

        bad = client.post(
            "/api/prompts/import-akms-claude-assets",
            json={"mode": "replace", "source_dir": str(src)},
        )
        assert bad.status_code == 400

        prev = client.post(
            "/api/prompts/import-akms-claude-assets",
            json={"mode": "preview", "source_dir": str(src)},
        )
        assert prev.status_code == 200
        token = prev.json()["preview_token"]
        ok = client.post(
            "/api/prompts/import-akms-claude-assets",
            json={"mode": "replace", "source_dir": str(src), "preview_token": token},
        )
        assert ok.status_code == 200, ok.text

    @pytest.mark.integration
    def test_import_zsum_prompts_replace_requires_token(self, client, tmp_path):
        """``/api/prompts/import-zsum-prompts`` honours the invariant."""
        src = tmp_path / "fake-zsum-prompts"
        src.mkdir()
        (src / "summary.md").write_text("Summarise: {{paper}}\n")

        bad = client.post(
            "/api/prompts/import-zsum-prompts",
            json={"mode": "replace", "source_dir": str(src)},
        )
        assert bad.status_code == 400

        prev = client.post(
            "/api/prompts/import-zsum-prompts",
            json={"mode": "preview", "source_dir": str(src)},
        )
        assert prev.status_code == 200
        token = prev.json()["preview_token"]
        ok = client.post(
            "/api/prompts/import-zsum-prompts",
            json={"mode": "replace", "source_dir": str(src), "preview_token": token},
        )
        assert ok.status_code == 200, ok.text
