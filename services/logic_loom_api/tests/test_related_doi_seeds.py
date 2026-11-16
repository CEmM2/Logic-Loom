"""Tests that DOI seeds resolve to OpenAlex W-ids on /related.

Previously the FE only emitted ``openalex:`` / ``semantic_scholar:`` seeds.
Zotero-derived batch sources usually carry a DOI but no provider id, so
the Related tab silently showed "0 seeds". This test pins the new
expansion path: ``doi:`` seeds get batched into one OpenAlex
``filter=doi:…`` lookup and substituted with the resolved ``openalex:W…``
id; unresolved DOIs are dropped.
"""

from __future__ import annotations

from typing import Any

import pytest

from logic_loom_api.assistant.related import RelatedPaperService
from logic_loom_api.config import Settings


class _StubRelatedService(RelatedPaperService):
    """Stubs ``_openalex_get`` so the DOI lookup never touches the network.

    Captures every request to ``/works`` so the test can assert the
    expected pipe-separated filter clause was used (one HTTP call for
    however many DOIs we throw at it).
    """

    def __init__(self, settings: Settings, *, results: list[dict[str, Any]]) -> None:
        super().__init__(settings)
        self._scripted = results
        self.openalex_calls: list[dict[str, Any]] = []

    async def _openalex_get(self, path: str, *, params: dict[str, str]):
        self.openalex_calls.append({"path": path, "params": dict(params)})
        return {"results": list(self._scripted)}


@pytest.mark.asyncio
async def test_doi_seeds_resolve_to_openalex_ids(tmp_path) -> None:
    svc = _StubRelatedService(
        Settings(cache_path=tmp_path, akms_workspace=tmp_path),
        results=[
            {
                "id": "https://openalex.org/W123",
                "doi": "https://doi.org/10.1234/abc",
            },
            {
                "id": "https://openalex.org/W456",
                "doi": "https://doi.org/10.5678/def",
            },
        ],
    )
    expanded = await svc.expand_doi_seeds(
        ["doi:10.1234/abc", "doi:10.5678/def", "openalex:W999"]
    )
    # Order preserved; DOI seeds substituted; non-DOI seeds untouched.
    assert expanded == ["openalex:W123", "openalex:W456", "openalex:W999"]
    # One round-trip for both DOIs (the filter clause is pipe-separated).
    assert len(svc.openalex_calls) == 1
    assert "doi:10.1234/abc|10.5678/def" in svc.openalex_calls[0]["params"]["filter"]


@pytest.mark.asyncio
async def test_unresolved_doi_seeds_are_dropped(tmp_path) -> None:
    svc = _StubRelatedService(
        Settings(cache_path=tmp_path, akms_workspace=tmp_path),
        results=[
            {
                "id": "https://openalex.org/W123",
                "doi": "https://doi.org/10.1234/abc",
            }
        ],
    )
    expanded = await svc.expand_doi_seeds(
        ["doi:10.1234/abc", "doi:10.0000/missing"]
    )
    # Only the resolved DOI survives; the missing one is silently dropped
    # so the related endpoint still has at least one usable seed.
    assert expanded == ["openalex:W123"]


@pytest.mark.asyncio
async def test_no_dois_means_no_network_call(tmp_path) -> None:
    """When the seed list has no DOIs we must not call OpenAlex."""
    svc = _StubRelatedService(
        Settings(cache_path=tmp_path, akms_workspace=tmp_path), results=[]
    )
    expanded = await svc.expand_doi_seeds(
        ["openalex:W123", "semantic_scholar:abc"]
    )
    assert expanded == ["openalex:W123", "semantic_scholar:abc"]
    assert svc.openalex_calls == []


@pytest.mark.asyncio
async def test_empty_seed_list_is_no_op(tmp_path) -> None:
    svc = _StubRelatedService(
        Settings(cache_path=tmp_path, akms_workspace=tmp_path), results=[]
    )
    assert await svc.expand_doi_seeds([]) == []
    assert svc.openalex_calls == []


# ---------------------------------------------------------------------------
# Follow-up: ``zotero:<citekey>`` → ``doi:<doi>`` via BBT FTS index.
# ---------------------------------------------------------------------------


def _write_bbt_fixture(tmp_path):
    """Write a minimal BBT JSON + zsum.config + workspace and return Settings."""
    import json

    import yaml

    bbt = tmp_path / "bbt.json"
    bbt.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "citationKey": "smith2024",
                        "title": "A paper",
                        "DOI": "10.1234/abc",
                    },
                    {
                        "citationKey": "no_doi_paper",
                        "title": "Preprint only",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    zsum_repo = tmp_path / "zsum"
    zsum_repo.mkdir()
    (zsum_repo / "zsum.config.yaml").write_text(
        yaml.safe_dump({"bbt_json": str(bbt)}), encoding="utf-8"
    )
    workspace = tmp_path / "ws"
    (workspace / "index").mkdir(parents=True)
    return Settings(
        zsum_repo=zsum_repo,
        akms_workspace=workspace,
        assistant_workspace=workspace,
        cache_path=tmp_path / "cache",
    )


def test_zotero_seeds_resolve_to_doi_seeds(tmp_path) -> None:
    settings = _write_bbt_fixture(tmp_path)
    svc = _StubRelatedService(settings, results=[])
    out = svc.expand_zotero_seeds(
        ["zotero:smith2024", "doi:already-doi", "openalex:W999"]
    )
    # smith2024 → doi:10.1234/abc; other seeds pass through.
    assert out == ["doi:10.1234/abc", "doi:already-doi", "openalex:W999"]


def test_zotero_seeds_with_no_doi_are_dropped(tmp_path) -> None:
    """A BBT row without a DOI can't be expanded; the seed is dropped."""
    settings = _write_bbt_fixture(tmp_path)
    svc = _StubRelatedService(settings, results=[])
    out = svc.expand_zotero_seeds(["zotero:no_doi_paper", "zotero:smith2024"])
    assert out == ["doi:10.1234/abc"]


def test_zotero_seeds_unknown_citekey_dropped(tmp_path) -> None:
    settings = _write_bbt_fixture(tmp_path)
    svc = _StubRelatedService(settings, results=[])
    out = svc.expand_zotero_seeds(["zotero:does_not_exist"])
    assert out == []


def test_no_zotero_seeds_is_no_op(tmp_path) -> None:
    settings = _write_bbt_fixture(tmp_path)
    svc = _StubRelatedService(settings, results=[])
    out = svc.expand_zotero_seeds(["openalex:W123", "doi:10.1/x"])
    assert out == ["openalex:W123", "doi:10.1/x"]
