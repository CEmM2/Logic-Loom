"""Tests for the Zotero picker backend.

Covers:

* :class:`ZoteroLibraryIndex.build` populates FTS5 from a BBT JSON
  fixture.
* :class:`ZoteroLibraryIndex.search` ranks title/author hits and
  applies the collection filter.
* :meth:`ZoteroLibraryIndex.ensure_fresh` skips work when the BBT
  file is unchanged.
* ``/api/assistant/zotero/search`` returns sectioned hits and reports
  ``library_reason`` when the BBT path is unconfigured.
* ``/api/assistant/zotero/preview/{citekey}`` 404s when the citekey is
  unknown in both stores.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from fastapi.testclient import TestClient

from logic_loom_api.assistant.zotero_library_index import (
    ZoteroLibraryIndex,
    open_index,
    resolve_bbt_json_path,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _bbt_payload() -> dict:
    return {
        "items": [
            {
                "citationKey": "smith2024photons",
                "title": "Photons in disordered media",
                "creators": [
                    {"creatorType": "author", "lastName": "Smith", "firstName": "Alice"},
                    {"creatorType": "author", "lastName": "Lee", "firstName": "Bo"},
                ],
                "date": "2024-03-15",
                "publicationTitle": "Phys. Rev. Lett.",
                "abstractNote": "We measure transport of photons in disordered media.",
                "DOI": "10.1103/PRL.2024.001",
                "collections": ["colA", "colB"],
                "tags": [{"tag": "scattering"}, {"tag": "experiment"}],
                "attachments": [{"path": "/Library/zotero/smith2024.pdf"}],
            },
            {
                "citationKey": "jones2023networks",
                "title": "Sparse graph neural networks",
                "creators": [
                    {"creatorType": "author", "lastName": "Jones", "firstName": "Cara"},
                ],
                "date": "2023",
                "publicationTitle": "NeurIPS",
                "abstractNote": "Sparse attention reduces neural network compute.",
                "collections": ["colB"],
            },
            {
                "citationKey": "no_attachment_paper",
                "title": "A paper without PDF",
                "creators": [],
                "date": "2020",
            },
        ]
    }


@pytest.fixture()
def bbt_file(tmp_path: Path) -> Path:
    p = tmp_path / "bbt.json"
    p.write_text(json.dumps(_bbt_payload()), encoding="utf-8")
    return p


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "assistant-workspace"
    (ws / "index").mkdir(parents=True)
    return ws


@pytest.fixture()
def client(tmp_path: Path, workspace: Path, bbt_file: Path) -> TestClient:
    cfg = tmp_path / "config.toml"
    zsum_repo = tmp_path / "zsum"
    zsum_repo.mkdir()
    # zsum config that points the picker at the BBT JSON.
    (zsum_repo / "zsum.config.yaml").write_text(
        yaml.safe_dump({"bbt_json": str(bbt_file)}), encoding="utf-8"
    )
    with patch.dict(
        os.environ,
        {
            "LOGIC_LOOM_CONFIG": str(cfg),
            "LOGIC_LOOM_CACHE_PATH": str(tmp_path / "cache"),
            "LOGIC_LOOM_ZSUM_REPO": str(zsum_repo),
            "LOGIC_LOOM_ASSISTANT_WORKSPACE": str(workspace),
            "LOGIC_LOOM_VAULT_PATH": str(tmp_path / "vault"),
        },
    ):
        from logic_loom_api.deps import reload_settings

        reload_settings()
        from logic_loom_api.main import api

        with TestClient(api) as c:
            yield c
        reload_settings()


# ---------------------------------------------------------------------------
# Library index unit tests
# ---------------------------------------------------------------------------


def test_build_indexes_every_citekey(tmp_path: Path, bbt_file: Path) -> None:
    idx = ZoteroLibraryIndex(tmp_path / "lib.sqlite")
    count = idx.build(bbt_file)
    assert count == 3
    stats = idx.stats()
    assert stats["count"] == 3
    assert stats["bbt_path"] == str(bbt_file)
    idx.close()


def test_search_finds_by_title_token(tmp_path: Path, bbt_file: Path) -> None:
    idx = ZoteroLibraryIndex(tmp_path / "lib.sqlite")
    idx.build(bbt_file)
    hits = idx.search(query="photon")
    assert any(h.citekey == "smith2024photons" for h in hits)
    idx.close()


def test_search_finds_by_author(tmp_path: Path, bbt_file: Path) -> None:
    idx = ZoteroLibraryIndex(tmp_path / "lib.sqlite")
    idx.build(bbt_file)
    hits = idx.search(query="jones")
    assert any(h.citekey == "jones2023networks" for h in hits)
    idx.close()


def test_collection_filter_keeps_only_matching(tmp_path: Path, bbt_file: Path) -> None:
    idx = ZoteroLibraryIndex(tmp_path / "lib.sqlite")
    idx.build(bbt_file)
    # colA is exclusive to smith2024photons.
    hits = idx.search(query="", collection_key="colA")
    assert [h.citekey for h in hits] == ["smith2024photons"]
    idx.close()


def test_ensure_fresh_is_noop_when_unchanged(tmp_path: Path, bbt_file: Path) -> None:
    idx = ZoteroLibraryIndex(tmp_path / "lib.sqlite")
    assert idx.build(bbt_file) == 3
    # No mtime bump → ensure_fresh returns None.
    assert idx.ensure_fresh(bbt_file) is None
    idx.close()


def test_ensure_fresh_rebuilds_when_bbt_mtime_advances(
    tmp_path: Path, bbt_file: Path
) -> None:
    idx = ZoteroLibraryIndex(tmp_path / "lib.sqlite")
    assert idx.build(bbt_file) == 3
    # Mutate the BBT file → ensure_fresh notices the new mtime and rebuilds.
    payload = json.loads(bbt_file.read_text())
    payload["items"].append(
        {"citationKey": "new2026paper", "title": "Fresh paper", "creators": []}
    )
    bbt_file.write_text(json.dumps(payload), encoding="utf-8")
    # Advance mtime so the float comparison in ensure_fresh fires reliably.
    new_mtime = bbt_file.stat().st_mtime + 5
    os.utime(bbt_file, (new_mtime, new_mtime))
    rebuilt = idx.ensure_fresh(bbt_file)
    assert rebuilt == 4
    idx.close()


def test_resolve_bbt_json_path_reads_zsum_config(
    tmp_path: Path, bbt_file: Path
) -> None:
    """The picker resolves the BBT path through the zsum config (single
    source of truth shared with the zsum BBT-live indicator)."""
    from logic_loom_api.config import Settings

    zsum_repo = tmp_path / "zsum"
    zsum_repo.mkdir()
    cfg = zsum_repo / "zsum.config.yaml"
    cfg.write_text(yaml.safe_dump({"bbt_json": str(bbt_file)}), encoding="utf-8")
    settings = Settings(zsum_repo=zsum_repo)
    assert resolve_bbt_json_path(settings) == bbt_file


# ---------------------------------------------------------------------------
# HTTP route tests
# ---------------------------------------------------------------------------


def test_search_route_returns_library_hits(client: TestClient) -> None:
    r = client.get("/api/assistant/zotero/search", params={"q": "photon"})
    assert r.status_code == 200, r.text
    body = r.json()
    citekeys = [h["citekey"] for h in body["items"]]
    assert "smith2024photons" in citekeys
    assert body["library_reason"] is None
    assert body["library_count"] >= 1


def test_search_route_empty_query_falls_back_to_recent(client: TestClient) -> None:
    r = client.get("/api/assistant/zotero/search")
    assert r.status_code == 200
    items = r.json()["items"]
    # Newest-first by year — smith2024 (2024) precedes jones2023 (2023).
    citekeys = [h["citekey"] for h in items]
    assert citekeys.index("smith2024photons") < citekeys.index("jones2023networks")


def test_search_route_collection_filter(client: TestClient) -> None:
    r = client.get(
        "/api/assistant/zotero/search", params={"collection": "colA"}
    )
    assert r.status_code == 200
    citekeys = [h["citekey"] for h in r.json()["items"]]
    assert citekeys == ["smith2024photons"]


def test_preview_returns_metadata(client: TestClient) -> None:
    r = client.get("/api/assistant/zotero/preview/smith2024photons")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "Photons in disordered media"
    assert "Smith, Alice" in body["authors"]
    assert body["year"] == 2024
    assert body["doi"] == "10.1103/PRL.2024.001"


def test_preview_unknown_citekey_404(client: TestClient) -> None:
    r = client.get("/api/assistant/zotero/preview/does-not-exist")
    assert r.status_code == 404


def test_search_reports_reason_when_bbt_missing(
    tmp_path: Path, workspace: Path
) -> None:
    """If zsum.config.yaml omits ``bbt_json``, the picker stays alive
    but reports ``library_reason`` so the UI can show a banner."""
    cfg = tmp_path / "config.toml"
    zsum_repo = tmp_path / "zsum"
    zsum_repo.mkdir()
    (zsum_repo / "zsum.config.yaml").write_text("", encoding="utf-8")
    with patch.dict(
        os.environ,
        {
            "LOGIC_LOOM_CONFIG": str(cfg),
            "LOGIC_LOOM_CACHE_PATH": str(tmp_path / "cache"),
            "LOGIC_LOOM_ZSUM_REPO": str(zsum_repo),
            "LOGIC_LOOM_ASSISTANT_WORKSPACE": str(workspace),
        },
    ):
        from logic_loom_api.deps import reload_settings

        reload_settings()
        from logic_loom_api.main import api

        with TestClient(api) as c:
            r = c.get("/api/assistant/zotero/search", params={"q": "photon"})
        reload_settings()
    assert r.status_code == 200
    body = r.json()
    assert body["library_reason"] == "bbt-path-unset"
    assert body["items"] == []
