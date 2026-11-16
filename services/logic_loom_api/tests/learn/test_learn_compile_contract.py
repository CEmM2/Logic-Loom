"""Tests for the /api/learn/compile request/response contract.

Covers: the compile contract is implemented without editing akms_learn; the
response carries markdown preview, warnings, artifact id and provenance.

Integration tests exercise the real ``akms_learn`` compiler end-to-end.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

HAS_AKMS_LEARN = importlib.util.find_spec("akms_learn") is not None
HAS_AKMS = importlib.util.find_spec("akms") is not None

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    """TestClient with tmp-path settings so no real filesystem state is touched."""
    cfg_path = tmp_path / "config.toml"
    with patch.dict(
        os.environ,
        {
            "LOGIC_LOOM_CONFIG": str(cfg_path),
            "LOGIC_LOOM_VAULT_PATH": str(tmp_path),
            "LOGIC_LOOM_CACHE_PATH": str(tmp_path / "cache"),
        },
    ):
        from logic_loom_api.deps import reload_settings  # type: ignore[import-not-found]

        reload_settings()
        from logic_loom_api.main import api  # type: ignore[import-not-found]

        with TestClient(api) as c:
            yield c
        reload_settings()


def _fixture_graph_slice_payload() -> dict:
    """Return a minimal dict matching akms_learn.fixture_graph() serialised to GraphSlicePayload shape."""
    from akms_learn import fixture_graph  # type: ignore[import-not-found]

    gs = fixture_graph()
    return {
        "nodes": list(gs.nodes),
        "edges": list(gs.edges),
        "metadata": dict(gs.metadata),
    }


def _base_request_body(**overrides) -> dict:
    """Return a minimal valid CompileRequest body (GraphSlice path)."""
    body: dict = {
        "generation_option": "deterministic_outline",
        "topic": "j2 return mapping",
        "goal": "Understand the j2 return-mapping algorithm",
        "graph_slice": _fixture_graph_slice_payload(),
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# Compile contract tests
# ---------------------------------------------------------------------------


class TestCompileContract:
    """Integration tests for the POST /api/learn/compile request/response contract."""

    # ------------------------------------------------------------------
    # Test: compile with explicit GraphSlice produces full response
    # ------------------------------------------------------------------

    @pytest.mark.integration
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    def test_compile_with_graphslice_produces_response(self, client: TestClient) -> None:
        """Verifies: an explicit GraphSlice compile request returns a structured response.

        Requirement: 'Compile contract implemented without editing akms_learn.'
        Requirement: 'Response carries markdown preview, warnings, artifact id, provenance.'
        Passes when: the GraphSlice path delegates to the adapter and returns a
        well-formed CompileResponse carrying all required fields.
        """
        body = _base_request_body()
        r = client.post("/api/learn/compile", json=body)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

        data = r.json()

        # artifact_id — deterministic non-empty string
        assert "artifact_id" in data, "Response must carry artifact_id"
        assert isinstance(data["artifact_id"], str) and len(data["artifact_id"]) > 0

        # markdown preview — non-empty rendered lesson.md
        assert "markdown" in data, "Response must carry markdown preview"
        assert isinstance(data["markdown"], str) and len(data["markdown"]) > 0, (
            "markdown preview must be a non-empty string"
        )

        # warnings — list (may be empty for a clean graph)
        assert "warnings" in data, "Response must carry warnings list"
        assert isinstance(data["warnings"], list)

        # provenance_summary — must include key provenance fields
        assert "provenance_summary" in data, "Response must carry provenance_summary"
        prov = data["provenance_summary"]
        assert "request_hash" in prov, "provenance_summary must include request_hash"
        assert "graph_hash" in prov, "provenance_summary must include graph_hash"
        assert "topic" in prov, "provenance_summary must include topic"
        assert "generation_option" in prov, "provenance_summary must include generation_option"
        assert "node_count" in prov, "provenance_summary must include node_count"

        # Persistence is enabled: packet_path/exports are populated.
        assert data.get("packet_path") is not None, "packet_path must be populated"
        assert isinstance(data.get("exports"), list) and len(data["exports"]) >= 1, (
            "exports must be a non-empty list"
        )

        # cache_dir — must be advertised (non-empty string) and namespaced by artifact_id
        assert "cache_dir" in data, "Response must carry cache_dir"
        artifact_id = data["artifact_id"]
        cache_dir = data["cache_dir"]
        assert isinstance(cache_dir, str) and len(cache_dir) > 0, (
            "cache_dir must be a non-empty string"
        )
        assert cache_dir.endswith(artifact_id), (
            f"cache_dir '{cache_dir}' must end with artifact_id '{artifact_id}'"
        )
        # Persistence is enabled — the artifact dir exists on disk.
        from pathlib import Path
        assert Path(cache_dir).is_dir(), (
            f"cache_dir '{cache_dir}' must exist on disk — compile persists artifacts"
        )

    # ------------------------------------------------------------------
    # Test: compile with node/depth produces response
    # ------------------------------------------------------------------

    @pytest.mark.integration
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    @pytest.mark.skipif(not HAS_AKMS, reason="akms package not installed (needed to build graph cache)")
    def test_compile_with_node_depth_produces_response(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Verifies: a (node, max_depth, direction) compile request returns a structured response.

        Requirement: 'Response carries markdown preview, warnings, artifact id, provenance.'
        Passes when: node/depth slice→translate→delegate wiring produces the
        same response shape as the GraphSlice path.

        Uses monkeypatching to inject a real akms_learn fixture graph as the
        cached graph, avoiding the need for a full AKMS repo + graph build.
        """
        from akms_learn import fixture_graph  # type: ignore[import-not-found]

        # Build a tiny networkx DiGraph from the fixture_graph so ego_slice works.
        # We need the AKMS graph package's load_graph to produce a networkx graph
        # that ego_slice (graph_adapter) can traverse.
        # Strategy: monkeypatch load_cached_graph in the learn route namespace to
        # return a CachedGraph wrapping a real networkx graph built from the fixture.
        try:
            import networkx as nx  # type: ignore[import-not-found]
        except ImportError:
            pytest.skip("networkx not installed")

        gs = fixture_graph()

        # Build a minimal directed graph for traversal
        G = nx.DiGraph()
        for node in gs.nodes:
            node_id = node.get("node_id") or node.get("id")
            G.add_node(node_id, **node)
        for edge in gs.edges:
            src = edge.get("from") or edge.get("source")
            tgt = edge.get("to") or edge.get("target")
            if src and tgt:
                G.add_edge(src, tgt, **edge)

        from logic_loom_api.adapters.graph_adapter import CachedGraph  # type: ignore[import-not-found]

        mock_cached = CachedGraph(
            graph=G,
            meta={"node_count": len(gs.nodes), "edge_count": len(gs.edges)},
            cache_path=tmp_path / "graph.json",
        )

        body = {
            "generation_option": "deterministic_outline",
            "topic": "j2 return mapping",
            "goal": "Understand the return mapping algorithm",
            "node": "core_j2_return_mapping",
            "max_depth": 1,
            "direction": "out",
        }

        from logic_loom_api.routes import learn as learn_route_module  # type: ignore[import-not-found]

        with patch.object(learn_route_module, "load_cached_graph", return_value=mock_cached):
            r = client.post("/api/learn/compile", json=body)

        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

        data = r.json()

        # Same shape checks as the GraphSlice path
        assert "artifact_id" in data and len(data["artifact_id"]) > 0
        assert "markdown" in data and len(data["markdown"]) > 0
        assert "warnings" in data and isinstance(data["warnings"], list)
        assert "provenance_summary" in data
        prov = data["provenance_summary"]
        assert "request_hash" in prov
        assert "graph_hash" in prov
        assert "node_count" in prov
        # Persistence is enabled: packet_path/exports are populated.
        assert data.get("packet_path") is not None
        assert isinstance(data.get("exports"), list) and len(data["exports"]) >= 1

    # ------------------------------------------------------------------
    # Test: missing graph cache → structured 412, not 500
    # ------------------------------------------------------------------

    @pytest.mark.integration
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    def test_missing_graph_cache_returns_structured_412(self, client: TestClient) -> None:
        """Verifies: when load_cached_graph raises GraphAdapterError, the route returns
        a structured 412 precondition error (never a 500).
        """
        from logic_loom_api.adapters.graph_adapter import GraphAdapterError  # type: ignore[import-not-found]
        from logic_loom_api.routes import learn as learn_route_module  # type: ignore[import-not-found]

        body = {
            "generation_option": "deterministic_outline",
            "topic": "test topic",
            "goal": "test goal",
            "node": "some-node",
        }

        with patch.object(
            learn_route_module,
            "load_cached_graph",
            side_effect=GraphAdapterError("Graph cache missing: /some/path/graph.json"),
        ):
            r = client.post("/api/learn/compile", json=body)

        assert r.status_code == 412, f"Expected 412, got {r.status_code}: {r.text}"
        data = r.json()
        assert "error" in data
        assert data["error"]["code"] == "graph_unavailable"
        assert data["error"]["detail"]["graph_available"] is False

    # ------------------------------------------------------------------
    # Test: request with neither graph_slice nor node → 422
    # ------------------------------------------------------------------

    @pytest.mark.integration
    def test_compile_without_graph_source_returns_422(self, client: TestClient) -> None:
        """Verifies: a request with neither graph_slice nor node returns a 422
        precondition error (enforced by handler-level selector validation).
        """
        body = {
            "generation_option": "deterministic_outline",
            "topic": "test",
            "goal": "test goal",
            # neither graph_slice nor node — should be rejected
        }
        r = client.post("/api/learn/compile", json=body)
        assert r.status_code == 422, f"Expected 422, got {r.status_code}: {r.text}"

    # ------------------------------------------------------------------
    # Test: compile returns structured 503 when akms_learn is unavailable
    # ------------------------------------------------------------------

    @pytest.mark.integration
    def test_compile_returns_503_when_plugin_unavailable(self, client: TestClient) -> None:
        """Verifies: POST /api/learn/compile returns a structured 503 when
        akms_learn is not installed (degrade-gracefully path).
        """
        from logic_loom_api.adapters import learn_adapter  # type: ignore[import-not-found]

        body = _base_request_body() if HAS_AKMS_LEARN else {
            "generation_option": "deterministic_outline",
            "topic": "test",
            "goal": "test",
            "graph_slice": {"nodes": [], "edges": [], "metadata": {}},
        }

        with patch.object(learn_adapter, "akms_learn_available", return_value=False):
            r = client.post("/api/learn/compile", json=body)

        assert r.status_code == 503, f"Expected 503, got {r.status_code}: {r.text}"
        data = r.json()
        assert "error" in data
        assert data["error"]["code"] == "plugin_unavailable"
        assert data["error"]["detail"]["akms_learn_available"] is False

    # ------------------------------------------------------------------
    # Test: artifact_id is deterministic (same inputs → same id)
    # ------------------------------------------------------------------

    @pytest.mark.integration
    @pytest.mark.skipif(not HAS_AKMS_LEARN, reason="akms_learn not installed")
    def test_artifact_id_is_deterministic(self, client: TestClient) -> None:
        """Verifies: two identical compile requests produce the same artifact_id."""
        body = _base_request_body()
        r1 = client.post("/api/learn/compile", json=body)
        r2 = client.post("/api/learn/compile", json=body)
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["artifact_id"] == r2.json()["artifact_id"], (
            "artifact_id must be deterministic for identical inputs"
        )
