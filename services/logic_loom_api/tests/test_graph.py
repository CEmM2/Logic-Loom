"""Contract tests for /api/graph backed by AKMS graph compilation."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

import frontmatter  # type: ignore[import-not-found,import-untyped]
import pytest
import yaml  # type: ignore[import-untyped]
from fastapi.testclient import TestClient

HAS_AKMS = importlib.util.find_spec("akms") is not None


@pytest.fixture(autouse=True)
def _isolate_established_library(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the /api/graph contract tests hermetic against their tmp sources.

    The graph compile unions the shipped established node library into every compile. These
    tests assert exact node/edge sets built from their own tmp vault + repo, so
    we pin the established-library resolver to "absent" here. The dual-source
    union itself is covered by
    ``tests/bundled_nodes_and_batches/test_established_library_graph_source.py``.
    """
    if HAS_AKMS:
        from logic_loom_api.adapters import graph_adapter

        monkeypatch.setattr(graph_adapter, "established_library_root", lambda: None)


def _write_node(path: Path, metadata: dict, content: str = "# node\n") -> None:
    post = frontmatter.Post(content)
    post.metadata = metadata
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        f.write(frontmatter.dumps(post).encode("utf-8"))


@pytest.fixture
def graph_sources(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "AKMS"
    vault = tmp_path / "global-vault"
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

    _write_node(
        vault / "plasticity-von-mises.md",
        {
            "id": "plasticity-von-mises",
            "title": "Plasticity — von Mises",
            "domain": "computational-mechanics",
            "subdomain": "plasticity",
            "tags": ["plasticity", "yield"],
            "status": "established",
            "confidence": 0.93,
            "source": "human",
            "edges": [{"to": "return-mapping", "type": "requires", "weight": 1.0}],
            "akms_schema": "v2",
        },
    )
    _write_node(
        vault / "return-mapping.md",
        {
            "id": "return-mapping",
            "title": "Return mapping",
            "domain": "computational-mechanics",
            "tags": ["plasticity", "solver"],
            "status": "tentative",
            "confidence": 0.81,
            "source": "human",
            "edges": [{"to": "fft-green", "type": "feeds-into", "weight": 0.6}],
            "akms_schema": "v2",
        },
    )
    _write_node(
        vault / "fft-green.md",
        {
            "id": "fft-green",
            "title": "FFT Green operator",
            "domain": "fft-galerkin",
            "tags": ["fft", "green-operator"],
            "status": "established",
            "confidence": 0.88,
            "source": "human",
            "edges": [],
            "akms_schema": "v2",
        },
    )
    _write_node(
        repo / "knowledge" / "local-nodes" / "gpu-pitfall.md",
        {
            "id": "gpu-pitfall",
            "title": "GPU pitfall",
            "domain": "gpu-simulation",
            "tags": ["gpu", "pitfall"],
            "status": "tentative",
            "confidence": 0.74,
            "source": "agent",
            "edges": [{"to": "plasticity-von-mises", "type": "pitfall", "weight": 0.7}],
            "akms_schema": "v2",
        },
    )
    (vault / "README.md").write_text("# Nodes Vault\n", encoding="utf-8")
    return repo, vault, cache


@pytest.fixture
def client(graph_sources: tuple[Path, Path, Path]) -> Iterator[TestClient]:
    repo, vault, cache = graph_sources
    cfg = cache / "config.toml"
    with patch.dict(
        os.environ,
        {
            "LOGIC_LOOM_CONFIG": str(cfg),
            "LOGIC_LOOM_AKMS_REPO": str(repo),
            "LOGIC_LOOM_AKMS_GLOBAL_VAULT": str(vault),
            "LOGIC_LOOM_CACHE_PATH": str(cache),
        },
    ):
        from logic_loom_api.deps import reload_settings  # type: ignore[import-not-found]

        reload_settings()
        from logic_loom_api.main import api  # type: ignore[import-not-found]

        with TestClient(api) as c:
            yield c
        reload_settings()


class TestGraphStatus:
    def test_status_reports_cache_and_readiness(self, client: TestClient) -> None:
        r = client.get("/api/graph/status")
        assert r.status_code == 200
        body = r.json()
        assert body["cache_exists"] is False
        assert body["node_count"] == 0
        assert body["edge_count"] == 0
        assert body["graph_package_installed"] is HAS_AKMS
        assert body["repo_exists"] is True
        assert body["global_vault_exists"] is True
        assert body["graph_available"] is HAS_AKMS


@pytest.mark.skipif(not HAS_AKMS, reason="akms package not installed")
class TestGraphRefresh:
    def test_issue_scan_reports_problem_files(self, client: TestClient) -> None:
        r = client.post("/api/graph/issues/scan")
        assert r.status_code == 200
        body = r.json()
        assert body["issue_count"] == 1
        assert body["ignored_issue_count"] == 0
        assert body["total_issue_count"] == 1
        assert body["issues"][0]["path"].endswith("README.md")
        assert body["issues"][0]["ignored"] is False
        assert body["log_path"].endswith("source_issues.json")

    def test_ignore_issue_persists_and_allows_generation(self, client: TestClient) -> None:
        scan = client.post("/api/graph/issues/scan")
        assert scan.status_code == 200
        issue_path = scan.json()["issues"][0]["path"]

        ignore = client.post("/api/graph/issues/ignore", json={"path": issue_path, "ignored": True})
        assert ignore.status_code == 200
        ignore_body = ignore.json()
        assert ignore_body["issue_count"] == 0
        assert ignore_body["ignored_issue_count"] == 1
        assert ignore_body["total_issue_count"] == 1
        assert ignore_body["issues"][0]["ignored"] is True

        refresh = client.post("/api/graph/refresh")
        assert refresh.status_code == 200
        assert refresh.json()["cache_exists"] is True

        unignore = client.post("/api/graph/issues/ignore", json={"path": issue_path, "ignored": False})
        assert unignore.status_code == 200
        unignore_body = unignore.json()
        assert unignore_body["issue_count"] == 1
        assert unignore_body["ignored_issue_count"] == 0

    def test_refresh_requires_review_when_scan_has_issues(self, client: TestClient) -> None:
        r = client.post("/api/graph/refresh")
        assert r.status_code == 409
        body = r.json()
        assert body["error"]["code"] == "review_required"
        assert body["error"]["detail"]["issue_count"] == 1
        assert body["error"]["detail"]["ignored_issue_count"] == 0

    def test_refresh_writes_only_logic_loom_cache(self, client: TestClient, graph_sources: tuple[Path, Path, Path]) -> None:
        repo, vault, cache = graph_sources
        r = client.post("/api/graph/refresh", json={"skip_invalid_files": True})
        assert r.status_code == 200
        body = r.json()
        assert body["cache_exists"] is True
        assert Path(body["cache_path"]).is_file()
        assert Path(body["cache_path"]).is_relative_to(cache)
        assert not (repo / "knowledge" / "graph" / "graph.json").exists()
        graph_json = json.loads(Path(body["cache_path"]).read_text(encoding="utf-8"))
        assert graph_json["graph"]["global_vault"] == str(vault)

    def test_refresh_skips_schema_invalid_nodes_when_confirmed(self, client: TestClient, graph_sources: tuple[Path, Path, Path]) -> None:
        _repo, vault, _cache = graph_sources
        _write_node(
            vault / "composites" / "composite-delamination.md",
            {
                "id": "composite-delamination",
                "title": "Composite delamination",
                "domain": "composites",
                "tags": ["composites", "delamination"],
                "status": "tentative",
                "confidence": 0.77,
                "source": "human",
                "edges": [
                    {"to": "composite-laminate-theory", "type": "refines"},
                    {"to": "composite-progressive-damage", "type": "refines"},
                ],
                "akms_schema": "v2",
            },
        )

        scan = client.post("/api/graph/issues/scan")
        assert scan.status_code == 200
        scan_body = scan.json()
        assert scan_body["issue_count"] == 2
        assert scan_body["total_issue_count"] == 2
        assert any(issue["path"].endswith("composite-delamination.md") for issue in scan_body["issues"])
        assert any("Field required" in issue["reason"] for issue in scan_body["issues"])

        refresh = client.post("/api/graph/refresh", json={"skip_invalid_files": True})
        assert refresh.status_code == 200

        r = client.get("/api/graph/nodes")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 4
        assert any(node["id"] == "plasticity-von-mises" for node in body)
        assert all(node["id"] != "composite-delamination" for node in body)

    def test_nodes_require_explicit_generate_when_cache_missing(self, client: TestClient) -> None:
        r = client.get("/api/graph/nodes")
        assert r.status_code == 412
        body = r.json()
        assert body["error"]["code"] == "precondition_failed"
        assert "Graph cache missing" in body["error"]["message"]

    def test_refresh_stream_emits_progress_and_done(self, client: TestClient) -> None:
        with client.stream("GET", "/api/graph/refresh/stream?skip_invalid_files=true") as r:
            assert r.status_code == 200
            lines = [line for line in r.iter_lines() if line]

        current_event: str | None = None
        progress_payloads: list[dict] = []
        done_payload: dict | None = None

        for line in lines:
            if line.startswith("event: "):
                current_event = line.removeprefix("event: ")
                continue
            if not line.startswith("data: "):
                continue
            payload = line.removeprefix("data: ")
            if current_event == "progress":
                progress_payloads.append(json.loads(payload))
            elif current_event == "done":
                done_payload = json.loads(payload)
            current_event = None

        assert len(progress_payloads) >= 5
        assert any("Scanning vault" in item["message"] for item in progress_payloads)
        assert progress_payloads[-1]["percent"] == 100
        assert done_payload is not None
        assert done_payload["cache_exists"] is True


@pytest.mark.skipif(not HAS_AKMS, reason="akms package not installed")
class TestGraphFilters:
    def test_nodes_filter_by_query_domain_tag(self, client: TestClient) -> None:
        client.post("/api/graph/refresh", json={"skip_invalid_files": True})
        r = client.get(
            "/api/graph/nodes",
            params={"query": "plasticity", "domain": "computational-mechanics", "tag": "solver"},
        )
        assert r.status_code == 200
        body = r.json()
        assert [node["id"] for node in body] == ["return-mapping"]
        assert body[0]["file_path"].endswith("return-mapping.md")

    def test_edges_filter_deterministic(self, client: TestClient) -> None:
        client.post("/api/graph/refresh", json={"skip_invalid_files": True})
        r = client.get("/api/graph/edges", params={"source": "plasticity-von-mises"})
        assert r.status_code == 200
        body = r.json()
        assert body == [
            {
                "source": "plasticity-von-mises",
                "target": "return-mapping",
                "type": "requires",
                "weight": 1.0,
                "note": "",
                "edge_origin": "global",
            }
        ]


@pytest.mark.skipif(not HAS_AKMS, reason="akms package not installed")
class TestGraphSlices:
    def test_ego_depth_and_direction(self, client: TestClient) -> None:
        client.post("/api/graph/refresh", json={"skip_invalid_files": True})
        r = client.get(
            "/api/graph/ego",
            params={"node": "return-mapping", "depth": 1, "direction": "out"},
        )
        assert r.status_code == 200
        body = r.json()
        assert {node["id"] for node in body["nodes"]} == {"return-mapping", "fft-green"}
        assert body["meta"]["scope"] == "ego"
        assert body["meta"]["selected_node"] == "return-mapping"

    def test_cluster_by_tag_returns_internal_edges(self, client: TestClient) -> None:
        client.post("/api/graph/refresh", json={"skip_invalid_files": True})
        r = client.get("/api/graph/cluster", params={"tag": "plasticity"})
        assert r.status_code == 200
        body = r.json()
        assert {node["id"] for node in body["nodes"]} == {"plasticity-von-mises", "return-mapping"}
        assert body["edges"] == [
            {
                "source": "plasticity-von-mises",
                "target": "return-mapping",
                "type": "requires",
                "weight": 1.0,
                "note": "",
                "edge_origin": "global",
            }
        ]


class TestGraphErrors:
    def test_missing_graph_package_returns_error_envelope(self, client: TestClient) -> None:
        if HAS_AKMS:
            pytest.skip("local environment has akms installed; missing-package path not applicable")
        r = client.get("/api/graph/nodes")
        assert r.status_code == 412
        body = r.json()
        assert body["error"]["code"] == "precondition_failed"
        assert "AKMS graph support is not installed" in body["error"]["message"]

    @pytest.mark.skipif(not HAS_AKMS, reason="akms package not installed")
    def test_missing_graph_source_returns_error_envelope(self, graph_sources: tuple[Path, Path, Path]) -> None:
        """The graph *source* is the vault. A missing vault is a 412 naming it.

        (Contract change: a missing ``akms_repo`` used to be the 412 here. The
        checkout is now optional — see
        ``test_missing_akms_repo_is_not_a_graph_error`` below.)
        """
        repo, vault, cache = graph_sources
        cfg = cache / "config.toml"
        missing_vault = vault.parent / "missing-vault"
        with patch.dict(
            os.environ,
            {
                "LOGIC_LOOM_CONFIG": str(cfg),
                "LOGIC_LOOM_AKMS_REPO": str(repo),
                "LOGIC_LOOM_AKMS_GLOBAL_VAULT": str(missing_vault),
                "LOGIC_LOOM_CACHE_PATH": str(cache),
            },
        ):
            from logic_loom_api.deps import reload_settings  # type: ignore[import-not-found]

            reload_settings()
            from logic_loom_api.main import api  # type: ignore[import-not-found]

            with TestClient(api) as client:
                r = client.get("/api/graph/nodes")
            reload_settings()

        assert r.status_code == 412
        body = r.json()
        assert body["error"]["code"] == "precondition_failed"
        assert "akms_global_vault does not exist" in body["error"]["message"]

    @pytest.mark.skipif(not HAS_AKMS, reason="akms package not installed")
    def test_missing_akms_repo_is_not_a_graph_error(
        self, graph_sources: tuple[Path, Path, Path], monkeypatch
    ) -> None:
        """With akms_repo unset the graph builds from the source vault alone,
        through an empty stub root inside Logic-Loom's cache (never the vault
        or a checkout)."""
        repo, vault, cache = graph_sources
        monkeypatch.delenv("LOGIC_LOOM_AKMS_REPO", raising=False)
        with patch.dict(
            os.environ,
            {
                "LOGIC_LOOM_CONFIG": str(cache / "config.toml"),
                "LOGIC_LOOM_AKMS_GLOBAL_VAULT": str(vault),
                "LOGIC_LOOM_CACHE_PATH": str(cache),
            },
        ):
            from logic_loom_api.deps import reload_settings  # type: ignore[import-not-found]

            reload_settings()
            from logic_loom_api.main import api  # type: ignore[import-not-found]

            with TestClient(api) as client:
                before = client.get("/api/graph/nodes")
                status = client.get("/api/graph/status").json()
                built = client.post("/api/graph/refresh", json={"skip_invalid_files": True})
                nodes = client.get("/api/graph/nodes")
            reload_settings()

        # Not built yet: the 412 is about the cache, not a missing repo.
        assert before.status_code == 412
        assert "Graph cache missing" in before.json()["error"]["message"]
        assert status["graph_available"] is True
        assert status["repo_exists"] is False
        assert status["akms_repo"] is None
        assert status["repo_graph_path"] is None
        assert built.status_code == 200, built.text
        assert nodes.status_code == 200
        assert "plasticity-von-mises" in {n["id"] for n in nodes.json()}
        stub = cache / "graph" / "no-akms-repo"
        assert stub.is_dir() and not any(stub.iterdir())