"""Prompt Library API.

Covers: all endpoints return the documented shapes against the
FastAPI TestClient. Tier: integration.

The mount is exercised end-to-end (the `main.py` `include_router` call must
not be skipped during the test). Workspace isolation is achieved via the
`LOGIC_LOOM_AKMS_WORKSPACE` env var pointing into ``tmp_path``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch):
    """A TestClient with an isolated AKMS workspace under tmp_path."""
    workspace = tmp_path / "akms-workspace"
    monkeypatch.setenv("LOGIC_LOOM_AKMS_WORKSPACE", str(workspace))
    # Reload main to pick up the fresh env var. The router is mounted at
    # module import; reimporting forces the mount loop to re-run against
    # the test workspace.
    import importlib

    import logic_loom_api.main as main_module

    importlib.reload(main_module)
    with TestClient(main_module.api) as c:
        yield c


class TestSlots:
    @pytest.mark.integration
    def test_get_slots_returns_eight(self, client):
        r = client.get("/api/prompts/slots")
        assert r.status_code == 200
        body = r.json()
        assert "slots" in body
        # The AKMS bundle ships 8 slots; the Assistant mode adds 6
        # more for 14 total. The endpoint returns
        # PromptSlot.all_ids() which is additive, so we assert AT
        # LEAST 8 slots are present plus the canonical AKMS one is
        # exposed.
        assert len(body["slots"]) >= 8
        assert "akms.node_gen_skill" in body["slots"]


class TestVariantCRUD:
    @pytest.mark.integration
    def test_create_then_list_variant(self, client):
        payload = {
            "slot": "akms.node_gen_skill",
            "name": "Strict Equations",
            "filename": ".claude/skills/node-gen/SKILL.md",
            "content_type": "markdown",
            "content": "---\nname: strict\n---\nhello",
        }
        r = client.post("/api/prompts/variant", json=payload)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == "strict-equations"
        assert body["slot"] == "akms.node_gen_skill"
        assert body["source"] == "user"
        assert body["content_hash"].startswith("sha256:")

        # GET variant
        r = client.get(
            "/api/prompts/variant/strict-equations",
            params={"slot": "akms.node_gen_skill"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["name"] == "Strict Equations"

        # List variants (slot-filtered)
        r = client.get("/api/prompts/variants", params={"slot": "akms.node_gen_skill"})
        assert r.status_code == 200
        ids = [v["id"] for v in r.json()["variants"]]
        assert "strict-equations" in ids

    @pytest.mark.integration
    def test_list_variants_includes_bundled(self, client):
        r = client.get("/api/prompts/variants", params={"slot": "zsum.paper_card"})
        assert r.status_code == 200
        body = r.json()
        bundled_ids = [v["id"] for v in body["bundled"]]
        # Bundled default shipped with the prompt slot registry.
        assert "default" in bundled_ids

    @pytest.mark.integration
    def test_update_then_delete_variant(self, client):
        client.post(
            "/api/prompts/variant",
            json={
                "slot": "zsum.paper_card",
                "name": "Test",
                "filename": "prompts/paper_card.txt",
                "content_type": "text",
                "content": "v1",
            },
        )
        r = client.put(
            "/api/prompts/variant/test",
            json={
                "slot": "zsum.paper_card",
                "name": "Test",
                "filename": "prompts/paper_card.txt",
                "content_type": "text",
                "content": "v2",
            },
        )
        assert r.status_code == 200
        assert r.json()["content"] == "v2"

        r = client.delete(
            "/api/prompts/variant/test", params={"slot": "zsum.paper_card"}
        )
        assert r.status_code == 200
        assert r.json()["deleted"] is True

        # Second delete is 404.
        r = client.delete(
            "/api/prompts/variant/test", params={"slot": "zsum.paper_card"}
        )
        assert r.status_code == 404

    @pytest.mark.integration
    def test_duplicate_bundled_variant_creates_user_copy(self, client):
        r = client.post(
            "/api/prompts/variant/default/duplicate",
            params={"slot": "zsum.paper_card", "new_id": "default-mine"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == "default-mine"
        assert body["source"] == "user"
        assert body["based_on"] == "bundled:zsum.paper_card.default"


class TestSlotActivate:
    @pytest.mark.integration
    def test_activate_slot_overrides_global(self, client):
        r = client.post(
            "/api/prompts/slot/zsum.paper_card/activate",
            json={"variant_ref": "bundled:zsum.paper_card.default"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["active"]["zsum.paper_card"] == "bundled:zsum.paper_card.default"

        # And it shows up under /profiles -> active.
        r = client.get("/api/prompts/profiles")
        assert r.status_code == 200
        assert (
            r.json()["active"]["active"]["zsum.paper_card"]
            == "bundled:zsum.paper_card.default"
        )

    @pytest.mark.integration
    def test_activate_rejects_unknown_slot(self, client):
        r = client.post(
            "/api/prompts/slot/zsum.does_not_exist/activate",
            json={"variant_ref": "bundled:zsum.paper_card.default"},
        )
        assert r.status_code == 400


class TestProfiles:
    @pytest.mark.integration
    def test_create_then_list_profile(self, client):
        r = client.post(
            "/api/prompts/profiles",
            json={
                "name": "AKMS Default",
                "description": "AKMS-flavoured defaults",
                "active": {"akms.node_gen_skill": "bundled:akms.node_gen_skill.default"},
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["id"] == "akms-default"

        r = client.get("/api/prompts/profiles")
        assert r.status_code == 200
        ids = [p["id"] for p in r.json()["profiles"]]
        assert "akms-default" in ids

    @pytest.mark.integration
    def test_update_profile(self, client):
        client.post(
            "/api/prompts/profiles",
            json={"name": "X", "description": "", "active": {}},
        )
        r = client.put(
            "/api/prompts/profiles/x",
            json={"name": "X2", "description": "renamed", "active": {}},
        )
        assert r.status_code == 200
        assert r.json()["name"] == "X2"


class TestValidate:
    @pytest.mark.integration
    def test_validate_accepts_safe_path(self, client):
        r = client.post(
            "/api/prompts/validate",
            json={
                "filename": "prompts/paper_card.txt",
                "content_type": "text",
                "content": "Summarize the paper.",
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True

    @pytest.mark.integration
    def test_validate_rejects_unsafe_path(self, client):
        # absolute
        r = client.post(
            "/api/prompts/validate",
            json={
                "filename": "/etc/passwd",
                "content_type": "text",
                "content": "x",
            },
        )
        assert r.status_code == 400

        # traversal
        r = client.post(
            "/api/prompts/validate",
            json={
                "filename": "prompts/../../etc/passwd",
                "content_type": "text",
                "content": "x",
            },
        )
        assert r.status_code == 400

        # wrong prefix
        r = client.post(
            "/api/prompts/validate",
            json={
                "filename": "Nodes_Vault/secret.md",
                "content_type": "text",
                "content": "x",
            },
        )
        assert r.status_code == 400

    @pytest.mark.integration
    def test_validate_warns_on_markdown_front_matter_in_text(self, client):
        """Front-matter in a text variant is a soft warning, not a 400.

        Pre-T2.2 the route returned HTTP 400 for any soft authoring issue,
        including this one and the empty-body case the user repeatedly
        hit on the Prompts page. Validate now stays 400 only for hard
        path-safety failures; soft issues come back as warnings in a
        200 response so the UI can render them as banners.
        """
        r = client.post(
            "/api/prompts/validate",
            json={
                "filename": "prompts/paper_card.txt",
                "content_type": "text",
                "content": "---\nname: x\n---\nbody",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert any("front-matter" in w for w in body["warnings"])

    @pytest.mark.integration
    def test_validate_warns_on_empty_body(self, client):
        """Empty content is a soft warning, not a 400 (T2.2)."""
        r = client.post(
            "/api/prompts/validate",
            json={
                "filename": "prompts/paper_card.txt",
                "content_type": "text",
                "content": "",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert any("empty" in w for w in body["warnings"])


class TestImportExport:
    @pytest.mark.integration
    def test_round_trip_via_import_export(self, client):
        # Create a profile referencing a user variant.
        client.post(
            "/api/prompts/variant",
            json={
                "slot": "zsum.paper_card",
                "name": "Test PR",
                "filename": "prompts/paper_card.txt",
                "content_type": "text",
                "content": "hello",
            },
        )
        client.post(
            "/api/prompts/profiles",
            json={
                "name": "Export Profile",
                "description": "",
                "active": {"zsum.paper_card": "user:zsum.paper_card.test-pr"},
            },
        )
        r = client.get("/api/prompts/export/export-profile")
        assert r.status_code == 200, r.text
        bundle = r.json()
        assert bundle["profile"]["id"] == "export-profile"
        variant_ids = [v["id"] for v in bundle["variants"]]
        assert "test-pr" in variant_ids


class TestResolveEndpoint:
    @pytest.mark.integration
    def test_resolve_returns_effective_set(self, client):
        # Pin the global active profile via the activate endpoint.
        client.post(
            "/api/prompts/slot/zsum.paper_card/activate",
            json={"variant_ref": "bundled:zsum.paper_card.default"},
        )
        r = client.post(
            "/api/prompts/resolve",
            json={
                "batch_prompt_profile_id": None,
                "batch_prompt_overrides": {},
                "request_profile_id": None,
                "request_overrides": {},
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "zsum.paper_card" in body["slot_to_variant"]
        # Bundled content materialised into contents.
        assert "zsum.paper_card" in body["contents"]
