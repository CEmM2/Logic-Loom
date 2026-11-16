"""Search backends + /api/vault/search."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from logic_loom_api.search.ripgrep import RipgrepBackend


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "Collections").mkdir(parents=True)
    (root / "Papers").mkdir()
    (root / "Collections" / "Plasticity.md").write_text(
        "# Plasticity\nThe plastic deformation of metals follows von Mises yield criterion.\n",
    )
    (root / "Papers" / "@smith2020.md").write_text(
        "# Smith 2020\nDiscusses crystal plasticity in austenitic steel.\n",
    )
    return root


@pytest.fixture
def client(vault: Path) -> TestClient:
    cfg = vault.parent / "logic-loom-config.toml"
    with patch.dict(
        os.environ,
        {"LOGIC_LOOM_CONFIG": str(cfg), "LOGIC_LOOM_VAULT_PATH": str(vault)},
    ):
        from logic_loom_api.deps import reload_settings

        reload_settings()
        from logic_loom_api.main import api

        with TestClient(api) as c:
            yield c
        reload_settings()


@pytest.mark.skipif(not shutil.which("rg"), reason="ripgrep not installed")
class TestRipgrepBackend:
    @pytest.mark.asyncio
    async def test_finds_known_phrase(self, vault: Path) -> None:
        backend = RipgrepBackend(vault)
        hits = await backend.search("plasticity", limit=10)
        assert len(hits) > 0
        # All paths should be vault-relative.
        for h in hits:
            assert not h.path.startswith("/")
            assert "plasticity" in h.snippet.lower()

    @pytest.mark.asyncio
    async def test_empty_query_returns_empty(self, vault: Path) -> None:
        backend = RipgrepBackend(vault)
        assert await backend.search("", limit=10) == []

    @pytest.mark.asyncio
    async def test_no_matches(self, vault: Path) -> None:
        backend = RipgrepBackend(vault)
        hits = await backend.search("zzz_nonsense_string_qx", limit=10)
        assert hits == []


@pytest.mark.skipif(not shutil.which("rg"), reason="ripgrep not installed")
class TestSearchRoute:
    def test_default_backend_works(self, client: TestClient) -> None:
        r = client.get("/api/vault/search", params={"q": "plasticity"})
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        assert len(body) > 0
        assert "path" in body[0] and "snippet" in body[0]

    def test_explicit_ripgrep(self, client: TestClient) -> None:
        r = client.get("/api/vault/search", params={"q": "plastic", "backend": "ripgrep"})
        assert r.status_code == 200
        body = r.json()
        assert len(body) > 0
        # All paths are vault-relative.
        assert all(not h["path"].startswith("/") for h in body)

    def test_unknown_backend(self, client: TestClient) -> None:
        r = client.get("/api/vault/search", params={"q": "x", "backend": "nope"})
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "bad_request"

    def test_qmd_without_collection_returns_412(self, client: TestClient) -> None:
        r = client.get("/api/vault/search", params={"q": "plasticity", "backend": "qmd"})
        # If qmd is installed: 412 (collection unset). If not: also 412 (qmd not found).
        assert r.status_code == 412
        assert r.json()["error"]["code"] == "precondition_failed"


class TestQmdBackendUnit:
    @pytest.mark.skipif(not shutil.which("qmd"), reason="qmd CLI not installed")
    @pytest.mark.asyncio
    async def test_no_collection_raises(self, tmp_path: Path) -> None:
        from logic_loom_api.search.qmd import QmdBackend

        backend = QmdBackend(tmp_path, collection=None)
        with pytest.raises(RuntimeError, match="vault_qmd_collection"):
            await backend.search("anything", limit=10)
