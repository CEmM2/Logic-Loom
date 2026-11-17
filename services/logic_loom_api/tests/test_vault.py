"""Vault routes: tree, file, resolve. Path-traversal hardening covered."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Build a small fake vault."""
    root = tmp_path / "vault"
    (root / "Collections").mkdir(parents=True)
    (root / "Papers").mkdir()
    (root / "Sub" / "Deep").mkdir(parents=True)

    (root / "Library Index.md").write_text("# Library Index\n")
    (root / "Collections" / "Plasticity.md").write_text(
        "---\ntitle: Plasticity\naliases:\n  - PLT\n---\n# Plasticity\n",
    )
    (root / "Collections" / "Phase field.md").write_text("---\n---\n# Phase field\n")
    (root / "Papers" / "@smith2020.md").write_text("---\nciteky: smith2020\n---\nbody\n")
    (root / "Papers" / "@jones_2019.md").write_text("body\n")
    (root / "Sub" / "Deep" / "Note.md").write_text("# Deep\n")
    # Excluded dirs
    (root / ".obsidian").mkdir()
    (root / ".obsidian" / "config.json").write_text("{}")
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "ci.yml").write_text("on: push\n")
    (root / ".gitignore").write_text("*.tmp\n")
    return root


@pytest.fixture
def client(vault: Path) -> TestClient:
    cfg = vault.parent / "logic-loom-config.toml"
    with patch.dict(
        os.environ,
        {
            "LOGIC_LOOM_CONFIG": str(cfg),
            "LOGIC_LOOM_VAULT_PATH": str(vault),
        },
    ):
        from logic_loom_api.deps import reload_settings

        reload_settings()
        from logic_loom_api.main import api

        yield TestClient(api)
        reload_settings()


class TestTree:
    def test_top_level(self, client: TestClient) -> None:
        r = client.get("/api/vault/tree?depth=1")
        assert r.status_code == 200
        names = {n["name"] for n in r.json()}
        assert "Collections" in names
        assert "Papers" in names
        assert ".obsidian" not in names
        # Every dot-entry is hidden, not just a fixed list (QA: .github showed).
        assert ".github" not in names
        assert ".gitignore" not in names

    def test_depth_two_loads_children(self, client: TestClient) -> None:
        r = client.get("/api/vault/tree?depth=2")
        assert r.status_code == 200
        nodes = r.json()
        coll = next(n for n in nodes if n["name"] == "Collections")
        assert coll["kind"] == "dir"
        assert coll["children"] is not None
        child_names = {c["name"] for c in coll["children"]}
        assert "Plasticity.md" in child_names

    def test_subroot_walk(self, client: TestClient) -> None:
        r = client.get("/api/vault/tree?root=Sub&depth=2")
        assert r.status_code == 200
        nodes = r.json()
        deep = next(n for n in nodes if n["name"] == "Deep")
        assert deep["children"] is not None
        assert any(c["name"] == "Note.md" for c in deep["children"])


class TestFile:
    def test_loads_with_frontmatter(self, client: TestClient) -> None:
        r = client.get("/api/vault/file", params={"path": "Collections/Plasticity.md"})
        assert r.status_code == 200
        body = r.json()
        assert body["path"] == "Collections/Plasticity.md"
        assert body["frontmatter"]["title"] == "Plasticity"
        assert body["frontmatter"]["aliases"] == ["PLT"]
        assert "# Plasticity" in body["content"]
        assert body["size"] > 0
        assert body["mtime"]

    def test_404_on_missing(self, client: TestClient) -> None:
        r = client.get("/api/vault/file", params={"path": "Collections/Missing.md"})
        assert r.status_code == 404


class TestPathTraversal:
    def test_relative_traversal_blocked(self, client: TestClient) -> None:
        r = client.get("/api/vault/file", params={"path": "../logic-loom-config.toml"})
        assert r.status_code == 403

    def test_absolute_path_blocked(self, client: TestClient) -> None:
        r = client.get("/api/vault/file", params={"path": "/etc/passwd"})
        assert r.status_code == 403

    def test_tree_traversal_blocked(self, client: TestClient) -> None:
        r = client.get("/api/vault/tree", params={"root": "../"})
        assert r.status_code == 403


class TestResolve:
    def test_paper_wikilink(self, client: TestClient) -> None:
        r = client.get("/api/vault/resolve", params={"wikilink": "@smith2020"})
        assert r.status_code == 200
        body = r.json()
        assert body["exists"] is True
        assert body["path"] == "Papers/@smith2020.md"

    def test_paper_without_at_prefix(self, client: TestClient) -> None:
        r = client.get("/api/vault/resolve", params={"wikilink": "smith2020"})
        body = r.json()
        # Should still resolve via the @ prefix candidate.
        assert body["exists"] is True
        assert body["path"] == "Papers/@smith2020.md"

    def test_collection_wikilink(self, client: TestClient) -> None:
        r = client.get("/api/vault/resolve", params={"wikilink": "Plasticity"})
        body = r.json()
        assert body["exists"] is True
        assert body["path"] == "Collections/Plasticity.md"

    def test_unknown_returns_not_exists(self, client: TestClient) -> None:
        r = client.get("/api/vault/resolve", params={"wikilink": "DoesNotExist"})
        body = r.json()
        assert body["exists"] is False
        assert body["path"] is None

    def test_unsafe_chars_normalized(self, client: TestClient) -> None:
        # "Phase field" sanitises to "Phase field" (space preserved, no unsafe chars).
        r = client.get("/api/vault/resolve", params={"wikilink": "Phase field"})
        body = r.json()
        assert body["exists"] is True
        assert body["path"] == "Collections/Phase field.md"


class TestVaultUnsetPrecondition:
    def test_412_when_vault_unset(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.toml"
        with patch.dict(os.environ, {"LOGIC_LOOM_CONFIG": str(cfg)}, clear=False):
            os.environ.pop("LOGIC_LOOM_VAULT_PATH", None)
            from logic_loom_api.deps import reload_settings

            reload_settings()
            from logic_loom_api.main import api

            client = TestClient(api)
            r = client.get("/api/vault/tree")
            assert r.status_code == 412
            body = r.json()
            assert body["error"]["code"] == "precondition_failed"
        from logic_loom_api.deps import reload_settings as _reload

        _reload()
