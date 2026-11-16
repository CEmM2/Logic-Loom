"""Regression tests: the graph route surfaces provenance.

Previously ``routes/graph.py::GraphNode`` — the
Pydantic response model — did NOT declare the provenance / tier fields that
``graph_adapter`` stamps onto established nodes. Because it is a strict
``BaseModel`` (no ``extra="allow"``), FastAPI silently dropped
``is_established``/``tier``/``verified_by``/``verified_at``/``version``/
``source_ref`` from every ``/api/graph/*`` response, so established nodes
rendered as Draft/Generated in the graph inspector.

The existing provenance tests missed this because they asserted ``_graph_node_payload()``
directly (dict builder) or hand-built FE props — NEVER the actual route
serialization seam. This is that missing route-level (TestClient) assertion:
it hits ``/api/graph/nodes`` (and a slice route) and asserts the SERIALIZED
JSON carries the provenance an established node must have — and that a
non-established user node does not.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

pytest.importorskip("akms")  # graph compiler — established → graph needs AKMS

import frontmatter  # type: ignore[import-not-found,import-untyped]  # noqa: E402
import yaml  # type: ignore[import-untyped]  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from logic_loom_api.adapters import graph_adapter as ga  # noqa: E402
from logic_loom_api.config import Settings  # noqa: E402


# ── Fixture helpers (mirror the graph-source / provenance tests) ─────────


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _ensure_established_library() -> Path:
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
    except (subprocess.CalledProcessError, OSError) as e:  # pragma: no cover
        pytest.skip(f"established library sync unavailable: {e}")

    root = ga.established_library_root()
    if root is None or not any(root.glob("**/*.md")):
        pytest.skip("established library still empty after sync")
    return root


def _make_repo(tmp_path: Path) -> tuple[Path, Path, Path]:
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


def _write_user_node(vault: Path, node_id: str) -> None:
    """A plain user (Tier-B) node — NOT in PROVENANCE.toml, status draft."""
    post = frontmatter.Post(f"# {node_id}\n")
    post.metadata = {
        "id": node_id,
        "title": node_id.replace("-", " ").title(),
        "domain": "user-domain",
        "tags": ["user"],
        "status": "draft",
        "confidence": 0.5,
        "source": "human",
        "edges": [],
        "akms_schema": "v2",
    }
    with (vault / f"{node_id}.md").open("wb") as f:
        f.write(frontmatter.dumps(post).encode("utf-8"))


def _settings(repo: Path, vault: Path, cache: Path) -> Settings:
    return Settings(akms_repo=repo, akms_global_vault=vault, cache_path=cache)


def _make_graph_app(settings: Settings) -> FastAPI:
    from logic_loom_api.deps import get_settings
    from logic_loom_api.routes import graph as graph_mod

    app = FastAPI()
    app.include_router(graph_mod.router, prefix="/api/graph")
    app.dependency_overrides[get_settings] = lambda: settings
    return app


def _load_provenance_from_repo() -> dict[str, dict[str, str]]:
    manifest = _repo_root() / "verified_nodes" / "PROVENANCE.toml"
    with manifest.open("rb") as f:
        data = tomllib.load(f)
    return {
        k: {kk: str(vv) for kk, vv in v.items()}
        for k, v in data.items()
        if isinstance(v, dict)
    }


_PROVENANCE_FIELDS = ("verified_by", "verified_at", "version", "source_ref")


# ── Tests ─────────────────────────────────────────────────────────────────


class TestGraphRouteProvenance:
    """Route-level (TestClient) regression for the GraphNode serialization seam."""

    @pytest.mark.integration
    def test_graph_nodes_route_carries_provenance(self, tmp_path):
        """The SERIALIZED ``/api/graph/nodes`` JSON carries is_established/tier/
        provenance for an established node, and marks a user node NOT established.

        This is the assertion the earlier provenance tests lacked — it crosses the GraphNode
        Pydantic serialization boundary that was silently dropping the fields.
        """
        _ensure_established_library()
        repo, vault, cache = _make_repo(tmp_path)
        _write_user_node(vault, "user-draft-node")
        settings = _settings(repo, vault, cache)

        # Build the cache so the read-only /nodes route can load it.
        ga.build_cached_graph(settings, skip_invalid_files=True)

        expected = _load_provenance_from_repo()
        assert expected, "PROVENANCE.toml empty — test setup error"
        sample_id = "elastic-isotropic"
        assert sample_id in expected, f"{sample_id!r} missing from PROVENANCE.toml"

        client = TestClient(_make_graph_app(settings))
        resp = client.get("/api/graph/nodes")
        assert resp.status_code == 200, resp.text
        by_id = {n["id"]: n for n in resp.json()}

        # Established node: the serialized response MUST carry provenance.
        assert sample_id in by_id, f"{sample_id!r} not in /api/graph/nodes response"
        est = by_id[sample_id]
        assert est["is_established"] is True, f"is_established not surfaced: {est}"
        assert est["tier"] == "A", f"tier not surfaced: {est}"
        prov = expected[sample_id]
        for field_name in _PROVENANCE_FIELDS:
            assert est[field_name] == prov[field_name], (
                f"{field_name} not surfaced through the route: "
                f"got {est.get(field_name)!r}, expected {prov[field_name]!r}"
            )

        # User (Tier-B) node: present, but NOT marked established.
        assert "user-draft-node" in by_id, "user node missing from route response"
        user = by_id["user-draft-node"]
        assert user["is_established"] is False, f"user node wrongly established: {user}"
        assert user["tier"] is None, f"user node wrongly tiered: {user}"

    @pytest.mark.integration
    def test_graph_ego_slice_route_carries_provenance(self, tmp_path):
        """A SLICE route (/api/graph/ego) also carries provenance — the fields
        must survive serialization on the GraphSlice → GraphNode path too."""
        _ensure_established_library()
        repo, vault, cache = _make_repo(tmp_path)
        settings = _settings(repo, vault, cache)
        ga.build_cached_graph(settings, skip_invalid_files=True)

        sample_id = "elastic-isotropic"
        client = TestClient(_make_graph_app(settings))
        resp = client.get("/api/graph/ego", params={"node": sample_id, "depth": 0})
        assert resp.status_code == 200, resp.text
        nodes = {n["id"]: n for n in resp.json()["nodes"]}
        assert sample_id in nodes, f"{sample_id!r} not in ego slice"
        assert nodes[sample_id]["is_established"] is True
        assert nodes[sample_id]["tier"] == "A"
        for field_name in _PROVENANCE_FIELDS:
            assert nodes[sample_id][field_name], (
                f"{field_name} empty in ego slice response for {sample_id!r}"
            )
