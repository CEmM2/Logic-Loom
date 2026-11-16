"""Tests for established-node provenance, versioning & read-only enforcement.

Covers:
    1 (established nodes report status: established + provenance in responses),
    2 (edit attempts on established nodes are rejected),
    3 (copy produces an editable Tier-B node in the user's workspace).

Provenance/version come from ``verified_nodes/PROVENANCE.toml`` (keyed by node
id) — NOT node frontmatter (GlobalNodeFrontmatter is ``extra: forbid``). Tier A
is read-only; the editing affordance is "copy to my vault" → Tier B.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

pytest.importorskip("akms")  # graph compiler — established → graph needs AKMS

import frontmatter  # type: ignore[import-not-found,import-untyped]  # noqa: E402
import yaml  # type: ignore[import-untyped]  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from logic_loom_api.adapters import graph_adapter as ga  # noqa: E402
from logic_loom_api.config import Settings  # noqa: E402


# ── Fixture helpers ──────────────────────────────────────────────────────


def _repo_root() -> Path:
    """Repository root (…/Logic-Loom), four parents up from this test file."""
    return Path(__file__).resolve().parents[4]


def _ensure_established_library() -> Path:
    """Return the shipped established library root, running the sync if absent.

    Mirrors the helper in test_established_library_graph_source.py — the tree is a gitignored build artifact.
    """
    root = ga.established_library_root()
    if root is not None and any(root.glob("**/*.md")):
        return root

    repo = _repo_root()
    sync_script = repo / "scripts" / "sync_akms_nodes.py"
    if not sync_script.is_file():
        pytest.skip("sync_akms_nodes.py not present; cannot materialise library")
    try:
        subprocess.run(
            [sys.executable, str(sync_script)],
            cwd=str(repo),
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, OSError) as e:
        pytest.skip(f"established library sync unavailable: {e}")

    root = ga.established_library_root()
    if root is None or not any(root.glob("**/*.md")):
        pytest.skip("established library still empty after sync")
    return root


def _make_repo_scaffold(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Build a minimal AKMS repo scaffold + empty user vault + cache dir."""
    repo = tmp_path / "AKMS"
    vault = tmp_path / "vault"
    cache = tmp_path / "cache"
    (repo / "knowledge" / "graph").mkdir(parents=True)
    (repo / "knowledge" / "local-nodes").mkdir(parents=True)
    (repo / "knowledge" / "code-mirror").mkdir(parents=True)
    vault.mkdir(parents=True)
    (repo / "knowledge" / "graph" / "local_state.yaml").write_text(
        yaml.dump(
            {
                "akms_schema": "v2",
                "repo_id": "test-repo",
                "nodes": {},
                "local_edges": [],
                "session_nodes": {},
                "suppressed_edges": [],
            }
        )
    )
    return repo, vault, cache


def _make_settings(
    repo: Path,
    vault: Path,
    cache: Path,
    workspace: Path,
    *,
    nodes_editor: str | None = None,
) -> Settings:
    return Settings(
        akms_repo=repo,
        akms_global_vault=vault,
        cache_path=cache,
        akms_workspace=workspace,
        nodes_editor=nodes_editor,
    )


def _make_app(settings: Settings):
    """Build a minimal FastAPI test app with the nodes router mounted."""
    from fastapi import FastAPI

    from logic_loom_api.routes import nodes as nodes_mod

    app = FastAPI()
    app.include_router(nodes_mod.router, prefix="/api/nodes")

    # Override the settings dependency so TestClient uses our tmp settings.
    from logic_loom_api.deps import get_settings
    app.dependency_overrides[get_settings] = lambda: settings
    return app


def _load_provenance_from_repo() -> dict[str, dict[str, str]]:
    """Read PROVENANCE.toml directly from the tracked verified_nodes/ tree."""
    manifest = _repo_root() / "verified_nodes" / "PROVENANCE.toml"
    with manifest.open("rb") as f:
        data = tomllib.load(f)
    return {k: {kk: str(vv) for kk, vv in v.items()} for k, v in data.items() if isinstance(v, dict)}


# ── Tests ────────────────────────────────────────────────────────────────


class TestEstablishedNodeProvenance:
    """Integration tests for provenance + read-only + copy-to-vault."""

    @pytest.mark.integration
    def test_established_response_carries_provenance(self, tmp_path, monkeypatch):
        """Verifies: an established-node API response carries status:
        established plus provenance (verified_by/verified_at/source_ref) and
        library version."""
        established_root = _ensure_established_library()
        repo, vault, cache = _make_repo_scaffold(tmp_path)
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        settings = _make_settings(repo, vault, cache, workspace)

        # Build the graph — provenance stamps happen during augment.
        cg = ga.build_cached_graph(settings, skip_invalid_files=True)

        # Load expected provenance from the tracked manifest.
        expected_provenance = _load_provenance_from_repo()
        assert expected_provenance, "PROVENANCE.toml is empty — test setup error"

        # Pick one well-known established node.
        sample_id = "elastic-isotropic"
        assert sample_id in cg.graph.nodes, f"{sample_id!r} not in compiled graph"
        assert sample_id in expected_provenance, f"{sample_id!r} missing from PROVENANCE.toml"

        node_data = dict(cg.graph.nodes[sample_id])

        # (a) tier marker
        assert node_data.get("is_established") is True, (
            f"is_established not True on {sample_id}: {node_data}"
        )
        assert node_data.get("tier") == "A", (
            f"tier != 'A' on {sample_id}: {node_data}"
        )

        # (b) provenance fields
        prov = expected_provenance[sample_id]
        for field_name in ("verified_by", "verified_at", "version", "source_ref"):
            assert node_data.get(field_name) == prov[field_name], (
                f"{field_name} mismatch on {sample_id}: "
                f"got {node_data.get(field_name)!r}, expected {prov[field_name]!r}"
            )

        # (c) _graph_node_payload propagates the fields into API response shape.
        from logic_loom_api.adapters.graph_adapter import _graph_node_payload
        payload = _graph_node_payload(sample_id, node_data)
        assert payload["is_established"] is True
        assert payload["tier"] == "A"
        for field_name in ("verified_by", "verified_at", "version", "source_ref"):
            assert payload[field_name] == prov[field_name]

        # (d) ALL established ids get the marker (not just the sample).
        for node_id, prov_entry in expected_provenance.items():
            if node_id not in cg.graph.nodes:
                continue
            ndata = dict(cg.graph.nodes[node_id])
            assert ndata.get("is_established") is True, (
                f"is_established missing on established node {node_id!r}"
            )
            assert ndata.get("version") == prov_entry["version"], (
                f"version mismatch on {node_id!r}"
            )

    @pytest.mark.integration
    def test_edit_established_node_rejected(self, tmp_path, monkeypatch):
        """Verifies: an attempt to edit an established (Tier-A) node in place
        is rejected by the API.

        The /open endpoint would normally launch the host's default editor via
        subprocess.Popen. We MOCK Popen so the non-established "not-blocked" path
        can never spawn a real process (an unmocked run flooded the host with
        node cards during gate runs). The mock also records whether it was called,
        so the test asserts the *established* path is short-circuited BEFORE any
        subprocess is spawned.
        """
        from logic_loom_api.routes import nodes as nodes_mod

        spawned: list[tuple] = []

        def _fake_popen(*args, **kwargs):
            spawned.append((args, kwargs))
            return None

        monkeypatch.setattr(nodes_mod.subprocess, "Popen", _fake_popen)

        established_root = _ensure_established_library()
        repo, vault, cache = _make_repo_scaffold(tmp_path)
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        # nodes_editor set so the mocked Popen path (not os.uname/open) is taken.
        settings = _make_settings(repo, vault, cache, workspace, nodes_editor="true")

        # Pick a real established node path.
        established_md = next(established_root.glob("**/*.md"))

        client = TestClient(_make_app(settings))

        # POST /open on an established path must be rejected with 403 — and must
        # short-circuit BEFORE any subprocess is spawned.
        resp = client.post("/api/nodes/open", json={"path": str(established_md)})
        assert resp.status_code == 403, (
            f"Expected 403 for established node open, got {resp.status_code}: {resp.text}"
        )
        detail = resp.json().get("detail", "")
        assert "read-only" in detail.lower() or "established" in detail.lower(), (
            f"Error detail does not mention read-only/established: {detail!r}"
        )
        assert spawned == [], "established /open must NOT spawn a subprocess"

        # Sanity check: a regular (non-established) file is NOT blocked. The
        # editor launch is mocked, so nothing real is spawned.
        user_node = vault / "user-node.md"
        post = frontmatter.Post("# user node\n")
        post.metadata = {
            "id": "user-node",
            "title": "User Node",
            "domain": "user-domain",
            "tags": [],
            "status": "draft",
            "confidence": 0.5,
            "source": "human",
            "edges": [],
            "akms_schema": "v2",
        }
        with user_node.open("wb") as f:
            f.write(frontmatter.dumps(post).encode("utf-8"))

        resp2 = client.post("/api/nodes/open", json={"path": str(user_node)})
        # The (mocked) editor launch succeeds → 200; must NOT return 403 or 400.
        assert resp2.status_code == 200, (
            f"User-vault open should succeed (mocked), got {resp2.status_code}: {resp2.text}"
        )
        # The mocked launcher was invoked exactly once (for the user node).
        assert len(spawned) == 1, (
            f"expected exactly one mocked launch for the user node, got {len(spawned)}"
        )

    @pytest.mark.integration
    def test_established_guard_survives_resolver_monkeypatch(self, tmp_path, monkeypatch):
        """Regression (Issue 2): the read-only guard must read the resolver off
        the graph_adapter MODULE at call time, so a test that monkeypatches
        graph_adapter.established_library_root → None and then reverts it does
        NOT permanently disable the guard.
        """
        from logic_loom_api.adapters import graph_adapter as ga_mod
        from logic_loom_api.routes import nodes as nodes_mod

        established_root = _ensure_established_library()
        established_md = next(established_root.glob("**/*.md"))
        p = Path(established_md)

        # Simulate test_graph.py's autouse fixture: patch the resolver to None,
        # then revert (as pytest teardown would). If nodes bound the name by
        # value at import, it would keep the stale None and the guard would break.
        monkeypatch.setattr(ga_mod, "established_library_root", lambda: None)
        assert nodes_mod._is_established_path(p) is False, (
            "with resolver patched to None the guard should treat nothing as established"
        )
        monkeypatch.undo()

        # After revert, the guard must recognise the established path again.
        assert nodes_mod._is_established_path(p) is True, (
            "guard did not recover after the resolver monkeypatch was reverted — "
            "the resolver is bound by value instead of read at call time"
        )

    @pytest.mark.integration
    def test_copy_to_vault_creates_editable_tier_b(self, tmp_path, monkeypatch):
        """Verifies: the copy-to-vault action clones an established node into
        the user's workspace as an editable Tier-B node."""
        established_root = _ensure_established_library()
        repo, vault, cache = _make_repo_scaffold(tmp_path)
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        settings = _make_settings(repo, vault, cache, workspace)

        # Use elastic-isotropic as the sample (first .md in established root).
        sample_md = established_root / "computational-mechanics" / "elastic-isotropic.md"
        if not sample_md.is_file():
            sample_md = next(established_root.glob("**/*.md"))

        client = TestClient(_make_app(settings))

        resp = client.post("/api/nodes/copy-to-vault", json={"path": str(sample_md)})
        assert resp.status_code == 200, (
            f"copy-to-vault failed: {resp.status_code}: {resp.text}"
        )
        body = resp.json()

        # Response contract.
        assert body["status"] == "copied"
        assert body["tier"] == "B"
        assert body["node_status"] == "draft"
        copy_id = body["copy_id"]
        copy_path = Path(body["copy_path"])

        # File must exist in the workspace generated_nodes dir.
        expected_dir = settings.akms_generated_nodes_dir()
        assert copy_path.parent.resolve() == expected_dir.resolve(), (
            f"copy landed in wrong dir: {copy_path.parent} != {expected_dir}"
        )
        assert copy_path.is_file(), f"copy file not found: {copy_path}"

        # The copy id must be suffixed with -copy.
        original_id = body["original_id"]
        assert copy_id == f"{original_id}-copy", (
            f"copy id should be '{original_id}-copy', got {copy_id!r}"
        )

        # Read the written file — must be parseable and Tier B.
        copied_post = frontmatter.load(str(copy_path))
        assert copied_post.metadata.get("id") == copy_id
        assert copied_post.metadata.get("status") == "draft", (
            f"copy status should be 'draft', got {copied_post.metadata.get('status')!r}"
        )
        # Established-specific fields must NOT appear in the copy.
        for field_name in ("is_established", "tier", "verified_by", "verified_at",
                           "version", "source_ref"):
            assert field_name not in copied_post.metadata, (
                f"Field {field_name!r} should not appear in Tier-B copy"
            )

        # Non-destructive: a second copy attempt is rejected (409 Conflict).
        resp2 = client.post("/api/nodes/copy-to-vault", json={"path": str(sample_md)})
        assert resp2.status_code == 409, (
            f"Second copy should be 409, got {resp2.status_code}: {resp2.text}"
        )

        # Copying a non-established path is rejected (400).
        user_node = vault / "user-node.md"
        user_post = frontmatter.Post("# user\n")
        user_post.metadata = {"id": "user-node", "akms_schema": "v2"}
        with user_node.open("wb") as f:
            f.write(frontmatter.dumps(user_post).encode("utf-8"))
        resp3 = client.post("/api/nodes/copy-to-vault", json={"path": str(user_node)})
        assert resp3.status_code == 400, (
            f"Non-established copy should be 400, got {resp3.status_code}: {resp3.text}"
        )
