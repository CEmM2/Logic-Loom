"""Prompt slot registry + variant/profile schemas.

Covers:
  Variant/profile round-trip through JSON.
  The eight slots are addressable by id.

Tier: unit.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from logic_loom_api.prompts import (
    ActivePromptProfile,
    PromptProfile,
    PromptSlot,
    PromptStore,
    PromptVariant,
)
from logic_loom_api.prompts.store import content_hash, slugify


class TestPromptSlotEnum:
    """Eight slots are addressable by id."""

    @pytest.mark.unit
    def test_slot_enumeration_eight_ids(self):
        ids = PromptSlot.all_ids()
        # No duplicates.
        assert len(set(ids)) == len(ids)
        # The eight canonical AKMS-bundle slot ids. The Assistant mode ADDS six
        # ``assistant.*`` slots, so this test asserts the AKMS-bundle's
        # original 8 are a subset of ``all_ids()`` rather than an exact
        # equality — additive extension is the supported migration path.
        akms_bundle_slots = {
            "zsum.paper_card",
            "zsum.collection_digest",
            "zsum.collection_digest_structured",
            "zsum.collection_redigest",
            "zsum.restructure",
            "akms.node_gen_skill",
            "akms.node_gen_command",
            "akms.digest_refiner_agent",
        }
        assert akms_bundle_slots.issubset(set(ids)), (
            "AKMS-bundle's 8 original slots must remain part of the enum"
        )

    @pytest.mark.unit
    def test_slot_addressable_by_string_id(self):
        # Round-trip through the string value (the on-disk form).
        for sid in PromptSlot.all_ids():
            slot = PromptSlot(sid)
            assert slot.value == sid


class TestVariantRoundTrip:
    """Variant JSON round-trip."""

    @pytest.mark.unit
    def test_variant_round_trip(self):
        v = PromptVariant(
            id="strict-equations",
            family="akms",
            slot="akms.node_gen_skill",
            name="Strict equations",
            description="Requires complete formulas and notation definitions.",
            source="user",
            based_on="bundled:akms.node_gen_skill.default",
            filename=".claude/skills/node-gen/SKILL.md",
            content_type="markdown",
            content="---\nname: node-gen\n---\nhello",
        )
        as_json = v.model_dump_json()
        v2 = PromptVariant.model_validate_json(as_json)
        assert v2 == v
        # schema_version pinned to 1.
        assert v2.schema_version == 1

    @pytest.mark.unit
    def test_variant_rejects_family_slot_mismatch(self):
        with pytest.raises(ValidationError):
            PromptVariant(
                id="x",
                family="zsum",  # wrong: slot is akms.*
                slot="akms.node_gen_skill",
                name="x",
                filename=".claude/skills/node-gen/SKILL.md",
                content="x",
            )

    @pytest.mark.unit
    def test_variant_rejects_unknown_slot(self):
        with pytest.raises(ValidationError):
            PromptVariant(
                id="x",
                family="zsum",
                slot="zsum.does_not_exist",
                name="x",
                filename="prompts/foo.txt",
                content="x",
            )


class TestProfileRoundTrip:
    """Profile JSON round-trip."""

    @pytest.mark.unit
    def test_profile_round_trip(self):
        p = PromptProfile(
            id="akms-strict-methods",
            name="AKMS Strict Methods",
            description="Pinned strict-equations variants for AKMS work.",
            active={
                "akms.node_gen_skill": "user:akms.node_gen_skill.strict-equations",
                "zsum.collection_digest": "user:zsum.collection_digest.akms-oriented",
            },
        )
        p2 = PromptProfile.model_validate_json(p.model_dump_json())
        assert p2 == p
        assert p2.schema_version == 1

    @pytest.mark.unit
    def test_active_profile_round_trip_empty_default(self):
        ap = ActivePromptProfile()
        assert ap.profile_id is None
        assert ap.active == {}
        ap2 = ActivePromptProfile.model_validate_json(ap.model_dump_json())
        assert ap2 == ap


class TestStoreCRUD:
    """Variant + profile CRUD against a real on-disk store under tmp_path."""

    @pytest.mark.unit
    def test_store_variant_save_load_list_delete(self, tmp_path: Path):
        store = PromptStore(tmp_path / "prompt-library")
        store.ensure_dirs()

        v = PromptVariant(
            id="default",
            family="zsum",
            slot="zsum.paper_card",
            name="Default",
            filename="prompts/paper_card.txt",
            content_type="text",
            content="Summarize the paper.",
        )
        path = store.save_variant(v)
        assert path.exists()
        assert path.name == "default.logicloom.json"
        # Layout: variants/zsum/paper_card/default.logicloom.json
        assert path.parent.name == "paper_card"
        assert path.parent.parent.name == "zsum"

        # content_hash populated on save.
        loaded = store.load_variant("zsum.paper_card", "default")
        assert loaded.content_hash == content_hash(v.content)

        listed = store.list_variants(slot_id="zsum.paper_card")
        assert [x.id for x in listed] == ["default"]

        all_listed = store.list_variants()
        assert any(x.id == "default" and x.slot == "zsum.paper_card" for x in all_listed)

        assert store.delete_variant("zsum.paper_card", "default") is True
        assert store.delete_variant("zsum.paper_card", "default") is False

    @pytest.mark.unit
    def test_store_profile_save_load_list_delete(self, tmp_path: Path):
        store = PromptStore(tmp_path / "prompt-library")
        store.ensure_dirs()
        p = PromptProfile(id="akms-default", name="AKMS Default", active={})
        store.save_profile(p)
        loaded = store.load_profile("akms-default")
        assert loaded == p

        assert [x.id for x in store.list_profiles()] == ["akms-default"]
        assert store.delete_profile("akms-default") is True
        assert store.delete_profile("akms-default") is False

    @pytest.mark.unit
    def test_active_profile_empty_when_absent(self, tmp_path: Path):
        store = PromptStore(tmp_path / "prompt-library")
        ap = store.load_active_profile()
        assert ap.profile_id is None
        assert ap.active == {}

    @pytest.mark.unit
    def test_active_profile_save_and_reload(self, tmp_path: Path):
        store = PromptStore(tmp_path / "prompt-library")
        store.ensure_dirs()
        ap = ActivePromptProfile(
            profile_id="akms-default",
            active={"zsum.paper_card": "bundled:zsum.paper_card.default"},
        )
        store.save_active_profile(ap)
        ap2 = store.load_active_profile()
        assert ap2 == ap

    @pytest.mark.unit
    def test_store_skips_malformed_variant(self, tmp_path: Path, caplog):
        store = PromptStore(tmp_path / "prompt-library")
        store.ensure_dirs()
        bad_dir = store.variant_dir("zsum.paper_card")
        bad_dir.mkdir(parents=True, exist_ok=True)
        (bad_dir / "broken.logicloom.json").write_text("{not valid json")
        result = store.list_variants(slot_id="zsum.paper_card")
        assert result == []


class TestBundledDefaults:
    """Bundled defaults are discoverable via importlib.resources."""

    @pytest.mark.unit
    def test_bundled_defaults_have_two_variants(self, tmp_path: Path):
        store = PromptStore(tmp_path / "prompt-library")
        bundled = store.list_bundled_variants()
        # We seeded exactly two bundled defaults (zsum.paper_card + akms.node_gen_skill).
        ids = {(v.slot, v.id) for v in bundled}
        assert ("zsum.paper_card", "default") in ids
        assert ("akms.node_gen_skill", "default") in ids
        # All bundled variants must be flagged source="bundled".
        assert all(v.source == "bundled" for v in bundled)


class TestSlugify:
    @pytest.mark.unit
    def test_slugify_basic(self):
        assert slugify("Strict Equations") == "strict-equations"
        assert slugify("  AKMS  Default  ") == "akms-default"
        assert slugify("foo!!bar??") == "foo-bar"
        assert slugify("") == ""


class TestManifestIncludesPromptLibrary:
    """The packaging manifest must reference the prompt-library defaults so
    the wheel ships them.
    """

    @pytest.mark.unit
    def test_manifest_has_prompt_library_section(self):
        import tomllib
        from pathlib import Path as _Path

        repo_root = _Path(__file__).resolve().parents[4]
        manifest = repo_root / "packaging" / "akms_claude_assets.toml"
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
        assert "prompt_library_defaults" in data
        # At least one declared file referenced.
        sections = data["prompt_library_defaults"]
        assert sections, "prompt_library_defaults must have at least one section"
        total_files = sum(len(s.get("files", [])) for s in sections.values())
        assert total_files >= 2
