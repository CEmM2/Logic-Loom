"""Prompt resolution priority logic.

Covers: the priority order matches the six-tier table below. Tier: unit.

The six tiers (lowest precedence first):
  1. bundled defaults  (implicit — represented by global_profile.active when
     it points at `bundled:...` refs)
  2. Global active prompt profile         (`global_profile.active`)
  3. Batch default prompt profile         (`batch.prompt_profile_id`)
  4. Batch-specific prompt overrides      (`batch.prompt_overrides`)
  5. Run-selected prompt profile          (`request_profile_id`)
  6. Run-selected prompt overrides        (`request_overrides`) — always wins
"""

from __future__ import annotations

import pytest

from logic_loom_api.prompts import (
    ActivePromptProfile,
    PromptProfile,
    PromptSlot,
    PromptVariant,
    make_batch_view,
    parse_variant_ref,
    resolve_prompts_for_run,
)


def _profile(profile_id: str, active: dict[str, str]) -> PromptProfile:
    return PromptProfile(id=profile_id, name=profile_id, active=active)


def _variant(slot: str, variant_id: str, content: str) -> PromptVariant:
    family = slot.split(".", 1)[0]
    return PromptVariant(
        id=variant_id,
        family=family,
        slot=slot,
        name=variant_id,
        filename=(
            ".claude/skills/node-gen/SKILL.md"
            if family == "akms"
            else f"prompts/{slot.split('.', 1)[1]}.txt"
        ),
        content_type="markdown" if family == "akms" else "text",
        content=content,
    )


# A simple in-memory profile / variant store for unit tests.
class _Loaders:
    def __init__(self, profiles: list[PromptProfile], variants: list[PromptVariant]):
        self._profiles = {p.id: p for p in profiles}
        self._variants = {f"{v.slot}.{v.id}": v for v in variants}

    def load_profile(self, profile_id: str) -> PromptProfile:
        return self._profiles[profile_id]

    def load_variant(self, ref: str) -> PromptVariant | None:
        try:
            _, slot, variant_id = parse_variant_ref(ref)
        except ValueError:
            return None
        return self._variants.get(f"{slot}.{variant_id}")


class TestPriorityOrder:
    """Explicit table-driven coverage of all six tiers."""

    @pytest.mark.unit
    def test_priority_run_overrides_win(self):
        loaders = _Loaders(
            profiles=[
                _profile(
                    "global",
                    {"zsum.paper_card": "bundled:zsum.paper_card.default"},
                ),
                _profile(
                    "batch-profile",
                    {"zsum.paper_card": "user:zsum.paper_card.batch-pick"},
                ),
                _profile(
                    "request-profile",
                    {"zsum.paper_card": "user:zsum.paper_card.request-pick"},
                ),
            ],
            variants=[
                _variant("zsum.paper_card", "default", "BUNDLED"),
                _variant("zsum.paper_card", "batch-pick", "BATCH"),
                _variant("zsum.paper_card", "request-pick", "REQUEST"),
                _variant("zsum.paper_card", "winner", "RUN_OVERRIDE_WIN"),
            ],
        )
        gp = ActivePromptProfile(
            profile_id="global",
            active={"zsum.paper_card": "bundled:zsum.paper_card.default"},
        )
        batch = make_batch_view(
            prompt_profile_id="batch-profile",
            prompt_overrides={"zsum.paper_card": "user:zsum.paper_card.batch-pick"},
        )
        eff = resolve_prompts_for_run(
            batch=batch,
            global_profile=gp,
            request_profile_id="request-profile",
            request_overrides={"zsum.paper_card": "user:zsum.paper_card.winner"},
            profile_loader=loaders.load_profile,
            variant_loader=loaders.load_variant,
        )
        assert (
            eff.slot_to_variant["zsum.paper_card"] == "user:zsum.paper_card.winner"
        )
        assert eff.contents["zsum.paper_card"] == "RUN_OVERRIDE_WIN"

    @pytest.mark.unit
    def test_batch_default_used_when_no_request_overrides(self):
        loaders = _Loaders(
            profiles=[
                _profile(
                    "global",
                    {"zsum.paper_card": "bundled:zsum.paper_card.default"},
                ),
                _profile(
                    "batch-profile",
                    {"zsum.paper_card": "user:zsum.paper_card.batch-pick"},
                ),
            ],
            variants=[
                _variant("zsum.paper_card", "default", "BUNDLED"),
                _variant("zsum.paper_card", "batch-pick", "BATCH"),
            ],
        )
        gp = ActivePromptProfile(
            profile_id="global",
            active={"zsum.paper_card": "bundled:zsum.paper_card.default"},
        )
        batch = make_batch_view(
            prompt_profile_id="batch-profile",
            prompt_overrides=None,
        )
        eff = resolve_prompts_for_run(
            batch=batch,
            global_profile=gp,
            request_profile_id=None,
            request_overrides=None,
            profile_loader=loaders.load_profile,
            variant_loader=loaders.load_variant,
        )
        assert (
            eff.slot_to_variant["zsum.paper_card"]
            == "user:zsum.paper_card.batch-pick"
        )
        assert eff.contents["zsum.paper_card"] == "BATCH"

    @pytest.mark.unit
    def test_batch_overrides_beat_batch_profile(self):
        loaders = _Loaders(
            profiles=[
                _profile(
                    "batch-profile",
                    {"zsum.paper_card": "user:zsum.paper_card.batch-pick"},
                ),
            ],
            variants=[
                _variant("zsum.paper_card", "batch-pick", "BATCH"),
                _variant("zsum.paper_card", "batch-override-pick", "BATCH_OVERRIDE"),
            ],
        )
        gp = ActivePromptProfile()
        batch = make_batch_view(
            prompt_profile_id="batch-profile",
            prompt_overrides={
                "zsum.paper_card": "user:zsum.paper_card.batch-override-pick"
            },
        )
        eff = resolve_prompts_for_run(
            batch=batch,
            global_profile=gp,
            request_profile_id=None,
            request_overrides=None,
            profile_loader=loaders.load_profile,
            variant_loader=loaders.load_variant,
        )
        assert eff.contents["zsum.paper_card"] == "BATCH_OVERRIDE"

    @pytest.mark.unit
    def test_request_profile_beats_batch_overrides(self):
        loaders = _Loaders(
            profiles=[
                _profile(
                    "request-profile",
                    {"zsum.paper_card": "user:zsum.paper_card.request-pick"},
                ),
            ],
            variants=[
                _variant("zsum.paper_card", "batch-pick", "BATCH"),
                _variant("zsum.paper_card", "request-pick", "REQUEST"),
            ],
        )
        gp = ActivePromptProfile()
        batch = make_batch_view(
            prompt_profile_id=None,
            prompt_overrides={"zsum.paper_card": "user:zsum.paper_card.batch-pick"},
        )
        eff = resolve_prompts_for_run(
            batch=batch,
            global_profile=gp,
            request_profile_id="request-profile",
            request_overrides=None,
            profile_loader=loaders.load_profile,
            variant_loader=loaders.load_variant,
        )
        assert eff.contents["zsum.paper_card"] == "REQUEST"


class TestSupersetInvariant:
    """A property-style assertion: the result is always a superset of
    ``global_profile.active`` for whatever slots the global declares."""

    @pytest.mark.unit
    def test_result_is_superset_of_global_profile_active(self):
        loaders = _Loaders(
            profiles=[],
            variants=[
                _variant("zsum.paper_card", "default", "P"),
                _variant("zsum.collection_digest", "default", "D"),
            ],
        )
        gp = ActivePromptProfile(
            active={
                "zsum.paper_card": "bundled:zsum.paper_card.default",
                "zsum.collection_digest": "bundled:zsum.collection_digest.default",
            },
        )
        batch = make_batch_view()
        eff = resolve_prompts_for_run(
            batch=batch,
            global_profile=gp,
            request_profile_id=None,
            request_overrides=None,
            profile_loader=loaders.load_profile,
            variant_loader=loaders.load_variant,
        )
        # Every key from gp.active appears in the result.
        for slot in gp.active:
            assert slot in eff.slot_to_variant
            assert eff.slot_to_variant[slot] == gp.active[slot]


class TestParseVariantRef:
    @pytest.mark.unit
    def test_valid_user_ref(self):
        s, slot, vid = parse_variant_ref(
            "user:akms.node_gen_skill.strict-equations"
        )
        assert s == "user"
        assert slot == "akms.node_gen_skill"
        assert vid == "strict-equations"

    @pytest.mark.unit
    def test_valid_bundled_ref(self):
        s, slot, vid = parse_variant_ref("bundled:zsum.paper_card.default")
        assert s == "bundled"
        assert slot == "zsum.paper_card"
        assert vid == "default"

    @pytest.mark.unit
    def test_variant_id_with_dots(self):
        # Variant ids may contain dots (e.g. semver-like slugs).
        s, slot, vid = parse_variant_ref("user:zsum.paper_card.v1.2.3")
        assert vid == "v1.2.3"
        assert slot == "zsum.paper_card"

    @pytest.mark.unit
    def test_rejects_missing_source(self):
        with pytest.raises(ValueError, match="missing source prefix"):
            parse_variant_ref("zsum.paper_card.default")

    @pytest.mark.unit
    def test_rejects_unknown_source(self):
        with pytest.raises(ValueError, match="source must be"):
            parse_variant_ref("evil:zsum.paper_card.default")

    @pytest.mark.unit
    def test_rejects_unknown_slot(self):
        with pytest.raises(ValueError, match="unknown slot"):
            parse_variant_ref("user:zsum.does_not_exist.default")


class TestVariantLoaderFailureSurfacedNonFatally:
    """Missing variants leave slot_to_variant entries but no contents row."""

    @pytest.mark.unit
    def test_missing_variant_skipped_in_contents(self):
        loaders = _Loaders(profiles=[], variants=[])
        gp = ActivePromptProfile(
            active={"zsum.paper_card": "bundled:zsum.paper_card.missing"},
        )
        batch = make_batch_view()
        eff = resolve_prompts_for_run(
            batch=batch,
            global_profile=gp,
            request_profile_id=None,
            request_overrides=None,
            profile_loader=loaders.load_profile,
            variant_loader=loaders.load_variant,
        )
        assert "zsum.paper_card" in eff.slot_to_variant
        assert "zsum.paper_card" not in eff.contents
