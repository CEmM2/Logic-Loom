"""End-to-end tests for the four endpoints ported from the Obsidian plugin:
``GET /api/zsum/index``, ``GET /api/zsum/bbt-status``,
``GET/POST /api/zsum/config-doc``.

The Settings model is loaded from a per-test ``LOGIC_LOOM_CONFIG`` so each
test runs against an isolated zsum.config.yaml + cache_dir.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, Path, Path]]:
    """Return a TestClient pointing at a fresh ``zsum.config.yaml`` + ``cache/``
    inside ``tmp_path``. Yields ``(client, cfg_path, cache_dir)``."""
    cache = tmp_path / "cache"
    cache.mkdir()
    cfg = tmp_path / "zsum.config.yaml"
    cfg.write_text(
        '# top comment\nbbt_json: "~/Zotero/zot.json"\ncache_dir: ./cache\n'
        "llm:\n  routing:\n    cards: anthropic  # inline\n",
        encoding="utf-8",
    )
    logic_loom_cfg = tmp_path / "logic-loom.toml"
    # Use as_posix() so Windows paths render as `C:/Users/...` instead of
    # `C:\Users\...`. TOML basic strings interpret `\U` as a Unicode escape
    # (`tomllib.TOMLDecodeError: Invalid hex value`); forward slashes
    # bypass the escape interpretation and Python's file APIs accept them
    # on every OS.
    logic_loom_cfg.write_text(
        f'zsum_config_path = "{cfg.as_posix()}"\nzsum_repo = "{tmp_path.as_posix()}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("LOGIC_LOOM_CONFIG", str(logic_loom_cfg))

    from logic_loom_api.deps import reload_settings
    from logic_loom_api.main import api

    reload_settings()
    yield TestClient(api), cfg, cache


def test_index_missing_when_no_index_json(client):
    c, _, _ = client
    r = c.get("/api/zsum/index").json()
    assert r["kind"] == "missing"
    assert "index.json not found" in r["message"]


def test_index_ok_with_minimal_index(client):
    c, _, cache = client
    (cache / "index.json").write_text(
        json.dumps(
            {
                "collections": {
                    "c1": {
                        "name": "Demo",
                        "parent": None,
                        "path": "Demo",
                        "item_count": 1,
                        "digest_state": "ok",
                        "redigest_state": "ok",
                    }
                },
                "items": {
                    "@x": {
                        "citekey": "@x",
                        "year": 2024,
                        "venue": "Nature",
                        "collections": ["c1"],
                        "card_state": "cached",
                    }
                },
            }
        )
    )
    r = c.get("/api/zsum/index").json()
    assert r["kind"] == "ok"
    assert r["stats"]["total_papers"] == 1
    assert r["stats"]["cards_cached"] == 1
    assert r["stats"]["digests_ok"] == 1


def test_bbt_status_reports_missing(client):
    c, _, _ = client
    # bbt_json points at ~/Zotero/zot.json which (very likely) doesn't exist
    # in the test environment — verify the response shape, not the boolean.
    r = c.get("/api/zsum/bbt-status").json()
    assert r["is_live"] is False
    assert "bbt_json" in r


def test_config_doc_get_returns_parsed_dict(client):
    c, _, _ = client
    r = c.get("/api/zsum/config-doc").json()
    assert r["exists"] is True
    assert r["parse_error"] is None
    assert r["parsed"]["llm"]["routing"]["cards"] == "anthropic"


def test_config_doc_post_preserves_comments_and_creates_new_keys(client):
    c, cfg, _ = client
    r = c.post(
        "/api/zsum/config-doc",
        json={
            "updates": [
                {"path": "llm.routing.cards", "value": "openai_compat"},
                {"path": "llm.routing.redigest", "value": "anthropic"},
                {"path": "extraction.max_first_pages", "value": 5},
            ]
        },
    ).json()
    assert r["parsed"]["llm"]["routing"]["cards"] == "openai_compat"
    assert r["parsed"]["llm"]["routing"]["redigest"] == "anthropic"
    assert r["parsed"]["extraction"]["max_first_pages"] == 5
    on_disk = cfg.read_text(encoding="utf-8")
    assert "# top comment" in on_disk, "round-trip must preserve top-of-file comment"
    assert "# inline" in on_disk, "round-trip must preserve inline comment on cards"


def test_config_doc_post_rejects_invalid_dotted_path(client):
    c, _, _ = client
    r = c.post(
        "/api/zsum/config-doc",
        json={"updates": [{"path": "", "value": 1}]},
    )
    assert r.status_code == 400


def test_local_model_backfill_applies_to_blank_fields(client):
    """After installing a local model, blank openai_compat + routing fields
    in zsum.config.yaml are backfilled so the next run uses the new model
    without the user having to visit Settings."""
    from logic_loom_api.routes.zsum import _apply_local_model_to_zsum_config

    _, cfg, _ = client
    defaults = {
        "model": "qwen2.5-coder:7b",
        "base_url": "http://localhost:11434/v1",
        "api_key": "ollama",
        "routing_provider": "openai_compat",
    }
    applied = _apply_local_model_to_zsum_config(cfg, defaults)
    applied_paths = {u.path for u in applied}

    # Fixture seeds llm.routing.cards = "anthropic" — must NOT be overwritten.
    assert "llm.routing.cards" not in applied_paths
    # Blank fields get backfilled.
    assert "llm.openai_compat.model" in applied_paths
    assert "llm.openai_compat.base_url" in applied_paths
    assert "llm.openai_compat.api_key" in applied_paths
    assert "llm.routing.digests" in applied_paths
    assert "llm.routing.redigest" in applied_paths

    # Verify the YAML on disk reflects the writes.
    on_disk = cfg.read_text(encoding="utf-8")
    assert "qwen2.5-coder:7b" in on_disk
    assert "http://localhost:11434/v1" in on_disk
    assert "# top comment" in on_disk, "comment-preserving write should keep the header"
    # User-set field stays.
    assert "cards: anthropic" in on_disk


def test_local_model_backfill_is_noop_when_no_model(client):
    from logic_loom_api.routes.zsum import _apply_local_model_to_zsum_config

    _, cfg, _ = client
    applied = _apply_local_model_to_zsum_config(
        cfg, {"model": None, "base_url": "x", "api_key": "y", "routing_provider": "openai_compat"}
    )
    assert applied == []


def test_local_model_backfill_skips_when_all_set(client, tmp_path: Path):
    """If every backfill path already has a value, nothing is written."""
    from logic_loom_api.routes.zsum import _apply_local_model_to_zsum_config

    cfg = tmp_path / "filled.yaml"
    cfg.write_text(
        "llm:\n"
        "  routing:\n"
        "    cards: anthropic\n"
        "    digests: anthropic\n"
        "    redigest: anthropic\n"
        "  openai_compat:\n"
        "    model: existing\n"
        "    base_url: existing\n"
        "    api_key: existing\n",
        encoding="utf-8",
    )
    applied = _apply_local_model_to_zsum_config(
        cfg,
        {
            "model": "new-model",
            "base_url": "new-url",
            "api_key": "new-key",
            "routing_provider": "openai_compat",
        },
    )
    assert applied == []
    assert "existing" in cfg.read_text(encoding="utf-8")
    assert "new-model" not in cfg.read_text(encoding="utf-8")
