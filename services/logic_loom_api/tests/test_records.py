"""``/api/records`` — read-only browse of akms-records stores.

The integration tests build a REAL store with the installed ``akms_records``
package (register a template schema, create, cite, supersede) and read it back
through the API. They skip when the optional ``records`` extra is absent; the
degradation tests force the package away via ``sys.modules`` and always run.
"""

from __future__ import annotations

import hashlib
import importlib.resources
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from logic_loom_api.deps import reload_settings
from logic_loom_api.main import api


def _write_config(tmp_path: Path, body: str) -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text(body, encoding="utf-8")
    return cfg


@pytest.fixture
def make_client(tmp_path: Path):
    def _make(body: str) -> TestClient:
        cfg = _write_config(tmp_path, body)
        patcher = patch.dict(
            os.environ,
            {
                "LOGIC_LOOM_CONFIG": str(cfg),
                "LOGIC_LOOM_CACHE_PATH": str(tmp_path / "cache"),
            },
        )
        patcher.start()
        reload_settings()
        clients.append(patcher)
        return TestClient(api)

    clients: list = []
    yield _make
    for patcher in clients:
        patcher.stop()
    reload_settings()


def _toml_path(path: Path) -> str:
    return "'" + str(path) + "'"  # TOML literal string: Windows paths parse


# ── degradation (always runs) ───────────────────────────────────────────────


def test_status_without_package_is_200(make_client, tmp_path: Path) -> None:
    client = make_client(
        f"[[records_stores]]\nid = 'demo'\nscope = 'project:demo'\nrepo = {_toml_path(tmp_path)}\n"
    )
    with patch.dict(sys.modules, {"akms_records": None}):
        res = client.get("/api/records/status")
        assert res.status_code == 200
        body = res.json()
        assert body["available"] is False
        assert "records" in body["reason"]
        assert [s["id"] for s in body["stores"]] == ["demo"]

        res = client.get("/api/records/stores/demo/records")
        assert res.status_code == 503
        assert res.json()["error"]["code"] == "records_unavailable"


def test_unknown_store_is_404(make_client) -> None:
    pytest.importorskip("akms_records")
    client = make_client("")
    res = client.get("/api/records/stores/nope/graph")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "unknown_store"


def test_store_with_no_records_yet(make_client, tmp_path: Path) -> None:
    pytest.importorskip("akms_records")
    client = make_client(
        f"[[records_stores]]\nid = 'empty'\nscope = 'project:empty'\nrepo = {_toml_path(tmp_path / 'repo')}\n"
    )
    status = client.get("/api/records/status").json()
    assert status["available"] is True
    assert status["stores"][0]["exists"] is False
    assert client.get("/api/records/stores/empty/records").json()["total"] == 0
    assert client.get("/api/records/stores/empty/graph").json()["nodes"] == []
    assert not (tmp_path / "repo").exists(), "a read must not create the store"


# ── a real store ────────────────────────────────────────────────────────────


@pytest.fixture
def populated(tmp_path: Path) -> dict:
    ar = pytest.importorskip("akms_records")
    repo = tmp_path / "repo"
    repo.mkdir()
    store = ar.RecordStore.open("project:demo", repo=repo)
    schema = json.loads(
        (importlib.resources.files("akms_records") / "templates/decision.schema.json").read_text(
            encoding="utf-8"
        )
    )
    store.register_schema(schema, actor="test")
    a = store.create(
        {"schema_ref": "decision/v1", "payload": {"decision": "Keep surrogate", "rationale": "ok"}, "tags": ["surrogate"]},
        actor="test",
    ).record_id
    b = store.create(
        {
            "schema_ref": "decision/v1",
            "payload": {"decision": "Tune mesh", "rationale": "follows the surrogate call"},
            "source_refs": [
                {"kind": "record", "ref": f"{a}@1"},
                {"kind": "url", "ref": "https://example.org/paper"},
            ],
        },
        actor="test",
    ).record_id
    c = store.supersede(
        a,
        expected_revision=1,
        actor="test",
        document={"schema_ref": "decision/v1", "payload": {"decision": "Replace surrogate", "rationale": "drift"}},
    )["superseded_by"]
    store.close()
    return {"repo": repo, "a": a, "b": b, "c": c, "root": store.root}


def _tree_digest(root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(path.relative_to(root).as_posix().encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def test_browse_records_schemas_and_graph(make_client, populated: dict) -> None:
    a, b, c = populated["a"], populated["b"], populated["c"]
    before = _tree_digest(populated["root"])
    client = make_client(
        f"[[records_stores]]\nid = 'demo'\nscope = 'project:demo'\nrepo = {_toml_path(populated['repo'])}\n"
    )

    status = client.get("/api/records/status").json()
    assert status["stores"][0]["record_count"] == 3
    assert status["stores"][0]["schema_count"] == 1

    listing = client.get("/api/records/stores/demo/records").json()
    assert listing["total"] == 3
    assert listing["facets"]["lifecycle"] == {"superseded": 1, "active": 2}
    assert client.get("/api/records/stores/demo/records", params={"tag": "surrogate"}).json()["total"] == 1
    assert client.get("/api/records/stores/demo/records", params={"lifecycle": "active"}).json()["total"] == 2
    assert client.get("/api/records/stores/demo/records", params={"schema": "decision"}).json()["total"] == 3
    hits = client.get("/api/records/stores/demo/records", params={"q": "mesh"}).json()
    assert [r["record_id"] for r in hits["records"]] == [b]

    detail = client.get(f"/api/records/stores/demo/records/{a}").json()
    assert detail["record"]["payload"]["decision"] == "Keep surrogate"
    assert detail["state"]["superseded_by"] == c
    assert len(detail["history"]) == 2
    assert detail["schema"]["schema_ref"] == "decision/v1"
    assert "properties" in detail["schema"]["schema"]

    schemas = client.get("/api/records/stores/demo/schemas").json()["schemas"]
    assert schemas[0]["schema_ref"] == "decision/v1" and schemas[0]["record_count"] == 3

    graph = client.get("/api/records/stores/demo/graph").json()
    edges = {(e["source"], e["target"], e["type"]) for e in graph["edges"]}
    assert (a, c, "superseded_by") in edges
    assert (b, a, "cites") in edges
    assert (b, "ext:url:https://example.org/paper", "source_ref") in edges
    # supersede writes both superseded_by state and a `supersedes` ref: one edge
    assert sum(1 for e in graph["edges"] if e["type"] == "superseded_by") == 1

    no_ext = client.get("/api/records/stores/demo/graph", params={"include_external": False}).json()
    assert all(n["kind"] != "external" for n in no_ext["nodes"])

    focus = client.get("/api/records/stores/demo/graph", params={"focus": c, "depth": 1}).json()
    assert {n["id"] for n in focus["nodes"]} == {a, c}

    assert client.get("/api/records/stores/demo/records/rec-20990101-zzzzzzzz").status_code == 404
    assert client.get("/api/records/stores/demo/records/not-an-id").status_code == 400

    assert _tree_digest(populated["root"]) == before, "browsing must not write to the store"


def test_graph_limit_reports_truncation(make_client, populated: dict) -> None:
    client = make_client(
        f"[[records_stores]]\nid = 'demo'\nscope = 'project:demo'\nrepo = {_toml_path(populated['repo'])}\n"
    )
    graph = client.get("/api/records/stores/demo/graph", params={"limit": 2}).json()
    assert graph["truncated"] is True
    assert len(graph["nodes"]) == 2
    assert graph["total_nodes"] == 4
