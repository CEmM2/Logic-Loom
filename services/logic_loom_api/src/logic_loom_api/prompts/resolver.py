"""Prompt resolution priority logic.

Implements the six-tier resolution order:

    bundled defaults
      override Global active prompt profile
        override Batch default prompt profile
          override Batch-specific prompt overrides
            override Run-selected prompt profile
              override Run-selected prompt overrides (always wins)

The function signature is stable so the batch model and the run-workflow
handler can call it without adaptation.

`batch` is duck-typed against :class:`PromptBatchLike` so this module does
NOT depend on :class:`EditableBatch` — the resolver sits below the batches
package in the dependency graph.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .models import (
    ActivePromptProfile,
    EffectivePromptSet,
    PromptProfile,
    PromptSlot,
    PromptVariant,
)


class PromptBatchLike(Protocol):
    """Duck-type protocol for the batch object passed to the resolver.

    :class:`EditableBatch` satisfies this exact shape. The contract is kept
    local to the prompts package so the resolver can be used and tested
    without importing the batches package.
    """

    prompt_profile_id: str | None
    prompt_overrides: dict[str, str]


@dataclass(frozen=True, slots=True)
class _ResolverBatch:
    """Concrete fallback used by tests and any caller that hasn't built a
    full :class:`EditableBatch` (e.g. resolver unit tests).
    """

    prompt_profile_id: str | None = None
    prompt_overrides: dict[str, str] = field(default_factory=dict)


def make_batch_view(
    *,
    prompt_profile_id: str | None = None,
    prompt_overrides: dict[str, str] | None = None,
) -> PromptBatchLike:
    """Construct a minimal :class:`PromptBatchLike` for the resolver.

    Convenience for test code and the API layer (prompts and batches routes)
    that wants to invoke the resolver without binding to the full
    :class:`EditableBatch` model surface.
    """
    return _ResolverBatch(
        prompt_profile_id=prompt_profile_id,
        prompt_overrides=dict(prompt_overrides) if prompt_overrides else {},
    )


# Loader callbacks. The default implementations look up profiles + variants
# through a :class:`PromptStore`; callers pass their own loaders in tests
# (parameterised priority tables) so the resolver stays decoupled from disk.
ProfileLoader = "callable taking profile_id -> PromptProfile"
VariantLoader = "callable taking variant_ref -> PromptVariant | None"


def parse_variant_ref(ref: str) -> tuple[str, str, str]:
    """Parse a ``"<source>:<slot>.<variant_id>"`` reference.

    Returns the tuple ``(source, slot, variant_id)``. Raises
    :class:`ValueError` on a malformed reference. Examples::

        parse_variant_ref("user:akms.node_gen_skill.strict-equations")
        # ("user", "akms.node_gen_skill", "strict-equations")
        parse_variant_ref("bundled:zsum.paper_card.default")
        # ("bundled", "zsum.paper_card", "default")
    """
    if ":" not in ref:
        raise ValueError(f"variant ref missing source prefix: {ref!r}")
    source, rest = ref.split(":", 1)
    if source not in ("user", "bundled"):
        raise ValueError(
            f"variant ref source must be 'user' or 'bundled': {ref!r}"
        )
    # Slot ids are always exactly two dot-segments (`<family>.<stem>`);
    # the variant id is everything after the second dot.
    parts = rest.split(".")
    if len(parts) < 3:
        raise ValueError(
            f"variant ref must be '<source>:<family>.<stem>.<variant_id>': {ref!r}"
        )
    slot = ".".join(parts[:2])
    variant_id = ".".join(parts[2:])
    if slot not in PromptSlot.all_ids():
        raise ValueError(f"variant ref references unknown slot {slot!r}: {ref!r}")
    return source, slot, variant_id


def resolve_prompts_for_run(
    *,
    batch: PromptBatchLike,
    global_profile: ActivePromptProfile,
    request_profile_id: str | None,
    request_overrides: dict[str, str] | None,
    profile_loader,
    variant_loader,
) -> EffectivePromptSet:
    """Resolve the effective slot→variant map for one run.

    Tier ordering (lowest precedence first; each subsequent tier overrides
    the previous tier per the same dict key):

    1. ``global_profile.active`` — the user's pinned default.
    2. ``load_profile(batch.prompt_profile_id).active`` — batch's profile.
    3. ``batch.prompt_overrides`` — batch's slot-level overrides.
    4. ``load_profile(request_profile_id).active`` — per-run profile.
    5. ``request_overrides`` — per-run slot-level overrides (always win).

    `profile_loader` is called only when a non-None profile_id is supplied.
    `variant_loader` resolves the chosen variant_ref to the
    :class:`PromptVariant` instance — its ``content`` field populates
    :class:`EffectivePromptSet.contents`. A missing variant (loader returns
    ``None``) is recorded in ``slot_to_variant`` but **not** in
    ``contents``; the route layer raises 400 if any chosen variant is
    missing.

    Returns:
        :class:`EffectivePromptSet` with both maps populated. The map is
        always a superset of the global profile's active set (assuming no
        explicit per-slot deletions, which the resolver does not support —
        an override of ``""`` is still a slot the user opted into).

    Raises:
        :class:`ValueError`: on a malformed variant reference (propagated
        from :func:`parse_variant_ref`).
    """
    # Tier 1 — global_profile.active (the floor).
    active: dict[str, str] = dict(global_profile.active)

    # Tier 2 — batch.prompt_profile_id (if set).
    if batch.prompt_profile_id:
        bp = profile_loader(batch.prompt_profile_id)
        active.update(bp.active)

    # Tier 3 — batch.prompt_overrides.
    if batch.prompt_overrides:
        active.update(batch.prompt_overrides)

    # Tier 4 — request_profile_id (if set).
    if request_profile_id:
        rp = profile_loader(request_profile_id)
        active.update(rp.active)

    # Tier 5 — request_overrides (always win).
    if request_overrides:
        active.update(request_overrides)

    # Resolve each variant_ref to its content via the variant_loader.
    contents: dict[str, str] = {}
    for slot, ref in active.items():
        if not ref:
            # Empty string means "intentionally unresolved" — skip contents
            # but keep the slot recorded so callers can surface a diagnostic.
            continue
        variant = variant_loader(ref)
        if variant is not None:
            contents[slot] = variant.content

    return EffectivePromptSet(slot_to_variant=dict(active), contents=contents)


def make_store_loaders(store):
    """Adapter: turn a :class:`PromptStore` into the two loader callables.

    Returns ``(profile_loader, variant_loader)``. The variant loader returns
    ``None`` instead of raising when a referenced variant is missing — the
    resolver records the slot but lets the route layer decide whether to
    400 or silently skip.
    """
    from .store import PromptStore  # noqa: F401 — type hint only

    def profile_loader(profile_id: str) -> PromptProfile:
        return store.load_profile(profile_id)

    # Bundled variants are addressed by `bundled:<slot>.<id>` — we look them
    # up via list_bundled_variants() once and cache for the loader closure.
    _bundled_cache: dict[tuple[str, str], PromptVariant] | None = None

    def _bundled_index() -> dict[tuple[str, str], PromptVariant]:
        nonlocal _bundled_cache
        if _bundled_cache is None:
            _bundled_cache = {(v.slot, v.id): v for v in store.list_bundled_variants()}
        return _bundled_cache

    def variant_loader(ref: str) -> PromptVariant | None:
        try:
            source, slot, variant_id = parse_variant_ref(ref)
        except ValueError:
            return None
        if source == "user":
            try:
                return store.load_variant(slot, variant_id)
            except FileNotFoundError:
                return None
        # source == "bundled"
        return _bundled_index().get((slot, variant_id))

    return profile_loader, variant_loader


__all__ = [
    "EffectivePromptSet",
    "PromptBatchLike",
    "make_batch_view",
    "make_store_loaders",
    "parse_variant_ref",
    "resolve_prompts_for_run",
]
