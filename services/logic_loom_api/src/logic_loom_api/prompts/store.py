"""On-disk JSON store for prompt variants and profiles.

Layout (under ``<akms_workspace>/prompt-library/``)::

    variants/<family>/<slot_stem>/<id>.logicloom.json
    profiles/<id>.json
    active-profile.json

`family` is ``"zsum"`` or ``"akms"`` (the first dot-segment of a slot id).
`slot_stem` is the post-dot segment (e.g. ``node_gen_skill``).

The store is **idempotent** — writing the same variant twice with the same
content is a no-op-equivalent (content_hash unchanged). It is **not**
thread-safe; callers wrap writes at the route layer if concurrency matters.

Bundled defaults are read via :func:`importlib.resources` from
``logic_loom_api.resources.prompt_library.defaults``. They are surfaced as
synthetic :class:`PromptVariant` instances with ``source="bundled"`` so the
resolver can address them uniformly.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Iterable

from pydantic import ValidationError

from .models import (
    ActivePromptProfile,
    PromptProfile,
    PromptSchema,
    PromptSlot,
    PromptVariant,
    slot_family,
)

logger = logging.getLogger(__name__)

# Package path for bundled defaults. The package may not exist in dev-from-
# source until the asset sync runs; reads tolerate that.
BUNDLED_RESOURCE_PACKAGE = "logic_loom_api.resources.prompt_library.defaults"

ACTIVE_PROFILE_FILENAME = "active-profile.json"
VARIANT_SUFFIX = ".logicloom.json"
PROFILE_SUFFIX = ".json"
SCHEMA_SUFFIX = ".schema.json"

# Slug regex for sanitising names → ids. Lowercase, ASCII, hyphen-separated.
_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """Return a stable lowercase-hyphenated slug for ``value``.

    Strips non-alphanumerics, collapses to single hyphens, trims edges. Empty
    inputs return ``""`` so the caller can re-prompt the user; the store
    treats empty-id writes as an error.
    """
    s = _SLUG_NON_ALNUM.sub("-", value.strip().lower()).strip("-")
    return s


def content_hash(content: str) -> str:
    """Return ``"sha256:<hexdigest>"`` for ``content``."""
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def _slot_stem(slot_id: str) -> str:
    """Return the post-``.`` portion of a slot id (e.g. ``paper_card``)."""
    if slot_id not in PromptSlot.all_ids():
        raise ValueError(f"unknown prompt slot id: {slot_id!r}")
    return slot_id.split(".", 1)[1]


class PromptStore:
    """Filesystem-backed store for variants/profiles/active-profile.

    The store is constructed with an absolute ``root`` path (typically
    ``<settings.akms_workspace>/prompt-library``). The route layer wires
    this up at request time from :class:`Settings`.

    Bundled defaults are read separately via :meth:`list_bundled_variants` —
    they live in the wheel under :data:`BUNDLED_RESOURCE_PACKAGE` and never
    touch disk.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.variants_root = self.root / "variants"
        self.profiles_root = self.root / "profiles"
        self.schemas_root = self.root / "schemas"
        self.active_profile_path = self.root / ACTIVE_PROFILE_FILENAME

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------

    def variant_dir(self, slot_id: str) -> Path:
        """Return the directory holding variants for ``slot_id``."""
        return self.variants_root / slot_family(slot_id) / _slot_stem(slot_id)

    def variant_path(self, slot_id: str, variant_id: str) -> Path:
        """Return the on-disk path for a (slot, variant) pair."""
        return self.variant_dir(slot_id) / f"{variant_id}{VARIANT_SUFFIX}"

    def profile_path(self, profile_id: str) -> Path:
        """Return the on-disk path for a profile id."""
        return self.profiles_root / f"{profile_id}{PROFILE_SUFFIX}"

    def ensure_dirs(self) -> None:
        """Create the workspace subtree if absent. Idempotent."""
        self.variants_root.mkdir(parents=True, exist_ok=True)
        self.profiles_root.mkdir(parents=True, exist_ok=True)
        self.schemas_root.mkdir(parents=True, exist_ok=True)

    def schema_path(self, schema_id: str) -> Path:
        """Return the on-disk path for a schema id."""
        return self.schemas_root / f"{schema_id}{SCHEMA_SUFFIX}"

    # ------------------------------------------------------------------
    # Variants
    # ------------------------------------------------------------------

    def save_variant(self, variant: PromptVariant) -> Path:
        """Persist a variant to disk.

        Computes ``content_hash`` from the variant's current ``content``
        before serialising — the hash on the model is always authoritative
        of what landed on disk. Idempotent: re-writing identical content is
        a no-op-equivalent.
        """
        if not variant.id:
            raise ValueError("variant.id is required")
        variant.content_hash = content_hash(variant.content)
        target = self.variant_path(variant.slot, variant.id)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(variant.model_dump(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return target

    def load_variant(self, slot_id: str, variant_id: str) -> PromptVariant:
        """Load a single variant by (slot, id)."""
        path = self.variant_path(slot_id, variant_id)
        if not path.exists():
            raise FileNotFoundError(
                f"variant not found: slot={slot_id!r} id={variant_id!r}"
            )
        return PromptVariant.model_validate_json(path.read_text(encoding="utf-8"))

    def list_variants(self, slot_id: str | None = None) -> list[PromptVariant]:
        """List all user variants on disk, optionally filtered by slot.

        Skips files that fail Pydantic validation, logging a warning. The
        route layer surfaces malformed files as a separate diagnostic.
        """
        out: list[PromptVariant] = []
        slots: Iterable[str] = (slot_id,) if slot_id else PromptSlot.all_ids()
        for sid in slots:
            d = self.variant_dir(sid)
            if not d.is_dir():
                continue
            for path in sorted(d.iterdir()):
                if not path.name.endswith(VARIANT_SUFFIX):
                    continue
                try:
                    out.append(
                        PromptVariant.model_validate_json(
                            path.read_text(encoding="utf-8")
                        )
                    )
                except (OSError, ValidationError, json.JSONDecodeError) as e:
                    logger.warning("skipping malformed variant %s: %s", path, e)
        return out

    def delete_variant(self, slot_id: str, variant_id: str) -> bool:
        """Delete a single variant from disk. Returns ``True`` if removed."""
        path = self.variant_path(slot_id, variant_id)
        if path.exists():
            path.unlink()
            return True
        return False

    # ------------------------------------------------------------------
    # Profiles
    # ------------------------------------------------------------------

    def save_profile(self, profile: PromptProfile) -> Path:
        """Persist a profile to disk. Idempotent."""
        if not profile.id:
            raise ValueError("profile.id is required")
        target = self.profile_path(profile.id)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(profile.model_dump(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return target

    def load_profile(self, profile_id: str) -> PromptProfile:
        """Load a single profile by id."""
        path = self.profile_path(profile_id)
        if not path.exists():
            raise FileNotFoundError(f"profile not found: {profile_id!r}")
        return PromptProfile.model_validate_json(path.read_text(encoding="utf-8"))

    def list_profiles(self) -> list[PromptProfile]:
        """List every profile on disk, sorted by id."""
        if not self.profiles_root.is_dir():
            return []
        out: list[PromptProfile] = []
        for path in sorted(self.profiles_root.iterdir()):
            if not path.name.endswith(PROFILE_SUFFIX):
                continue
            try:
                out.append(
                    PromptProfile.model_validate_json(path.read_text(encoding="utf-8"))
                )
            except (OSError, ValidationError, json.JSONDecodeError) as e:
                logger.warning("skipping malformed profile %s: %s", path, e)
        return out

    def delete_profile(self, profile_id: str) -> bool:
        """Delete a profile from disk. Returns ``True`` if removed."""
        path = self.profile_path(profile_id)
        if path.exists():
            path.unlink()
            return True
        return False

    # ------------------------------------------------------------------
    # Schemas (decoupled JSON schemas)
    # ------------------------------------------------------------------

    def save_schema(self, schema: PromptSchema) -> Path:
        """Persist a schema to disk. Idempotent."""
        if not schema.id:
            raise ValueError("schema.id is required")
        target = self.schema_path(schema.id)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(schema.model_dump(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return target

    def load_schema(self, schema_id: str) -> PromptSchema:
        """Load a schema by id. Falls back to bundled defaults if absent."""
        path = self.schema_path(schema_id)
        if path.exists():
            return PromptSchema.model_validate_json(path.read_text(encoding="utf-8"))
        for bundled in self.list_bundled_schemas():
            if bundled.id == schema_id:
                return bundled
        raise FileNotFoundError(f"schema not found: {schema_id!r}")

    def list_schemas(self) -> list[PromptSchema]:
        """List user schemas on disk, sorted by id."""
        if not self.schemas_root.is_dir():
            return []
        out: list[PromptSchema] = []
        for path in sorted(self.schemas_root.iterdir()):
            if not path.name.endswith(SCHEMA_SUFFIX):
                continue
            try:
                out.append(
                    PromptSchema.model_validate_json(path.read_text(encoding="utf-8"))
                )
            except (OSError, ValidationError, json.JSONDecodeError) as e:
                logger.warning("skipping malformed schema %s: %s", path, e)
        return out

    def delete_schema(self, schema_id: str) -> bool:
        """Delete a user schema. Returns ``True`` if removed."""
        path = self.schema_path(schema_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def list_bundled_schemas(self) -> list[PromptSchema]:
        """Return bundled schemas as :class:`PromptSchema` objects.

        Bundled schemas live under
        ``resources/prompt_library/defaults/schemas/<id>.schema.json``. The
        file shape is a normal JSON Schema document (not wrapped in the
        :class:`PromptSchema` envelope) so users can hand-author them; the
        loader wraps the bare schema in a :class:`PromptSchema` with
        ``source="bundled"`` and a derived ``name``/``id``.
        """
        root = self._bundled_root()
        if root is None:
            return []
        try:
            schemas_dir = root.joinpath("schemas")
            if not schemas_dir.is_dir():
                return []
            children = list(schemas_dir.iterdir())
        except (FileNotFoundError, NotADirectoryError):
            return []
        out: list[PromptSchema] = []
        for child in sorted(children, key=lambda c: c.name):
            if not child.name.endswith(SCHEMA_SUFFIX):
                continue
            schema_id = child.name[: -len(SCHEMA_SUFFIX)]
            try:
                payload = json.loads(child.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                logger.warning(
                    "skipping malformed bundled schema %s: %s", child.name, e
                )
                continue
            family = payload.get("x-logicloom-family")
            if family not in (None, "zsum", "akms", "assistant"):
                logger.warning(
                    "bundled schema %s: invalid x-logicloom-family %r — treating as unscoped",
                    child.name,
                    family,
                )
                family = None
            try:
                schema = PromptSchema(
                    id=schema_id,
                    name=payload.get("title") or schema_id,
                    description=payload.get("description") or "",
                    source="bundled",
                    family=family,
                    content=payload,
                )
            except ValidationError as e:
                logger.warning(
                    "skipping bundled schema %s (validation): %s", child.name, e
                )
                continue
            out.append(schema)
        return out

    # ------------------------------------------------------------------
    # Active profile
    # ------------------------------------------------------------------

    def load_active_profile(self) -> ActivePromptProfile:
        """Read the active-profile.json file; return an empty model if absent.

        An empty :class:`ActivePromptProfile` (no ``profile_id``, empty
        ``active``) is the correct default for a fresh workspace: the
        resolver falls through to bundled defaults.
        """
        if not self.active_profile_path.exists():
            return ActivePromptProfile()
        try:
            return ActivePromptProfile.model_validate_json(
                self.active_profile_path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError, json.JSONDecodeError) as e:
            logger.warning(
                "active-profile.json malformed (%s); returning empty active set", e
            )
            return ActivePromptProfile()

    def save_active_profile(self, active: ActivePromptProfile) -> Path:
        """Persist the active-profile.json file. Idempotent."""
        self.root.mkdir(parents=True, exist_ok=True)
        self.active_profile_path.write_text(
            json.dumps(active.model_dump(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return self.active_profile_path

    # ------------------------------------------------------------------
    # Bundled defaults (read-only)
    # ------------------------------------------------------------------

    def _bundled_root(self) -> Traversable | None:
        """Return the ``importlib.resources`` Traversable, or None if absent.

        The defaults package is only present after the asset sync runs (or
        in a built wheel). Dev-from-source without the sync should not crash
        the store.
        """
        try:
            return resources.files(BUNDLED_RESOURCE_PACKAGE)
        except (ModuleNotFoundError, FileNotFoundError):
            return None

    def list_bundled_variants(self) -> list[PromptVariant]:
        """Return bundled variants as :class:`PromptVariant` objects.

        Bundled defaults live under
        ``resources/prompt_library/defaults/<family>/<slot_stem>/`` as either
        ``<id>.logicloom.json`` (full schema, used by zsum/akms) or
        ``<id>.md`` (raw markdown content, used by assistant prompts that
        were authored as plain markdown). For ``.md`` files we synthesize a
        :class:`PromptVariant` whose ``content`` is the file body, ``id``
        is the filename stem, ``filename`` is a stable runtime target
        derived from the slot, and ``content_type`` is ``"markdown"``.

        Empty list when the bundled package isn't present (degrades
        gracefully the same way the Claude assets routes do).
        """
        root = self._bundled_root()
        if root is None:
            return []
        out: list[PromptVariant] = []
        for slot_id in PromptSlot.all_ids():
            family = slot_family(slot_id)
            stem = _slot_stem(slot_id)
            slot_dir = root.joinpath(family).joinpath(stem)
            try:
                if not slot_dir.is_dir():
                    continue
            except (FileNotFoundError, NotADirectoryError):
                continue
            try:
                children = list(slot_dir.iterdir())
            except (FileNotFoundError, NotADirectoryError):
                continue
            for child in sorted(children, key=lambda c: c.name):
                variant = _load_bundled_child(child, slot_id, family, stem)
                if variant is not None:
                    out.append(variant)
        return out


MARKDOWN_SUFFIX = ".md"


_AKMS_FILENAME_MAP: dict[str, str] = {
    "akms.node_gen_skill": ".claude/skills/node-gen/SKILL.md",
    "akms.node_gen_command": ".claude/commands/node-gen-invoker.md",
    "akms.digest_refiner_agent": ".claude/agents/digest-refiner.md",
}

_ZSUM_FILENAME_MAP: dict[str, str] = {
    "zsum.paper_card": "prompts/paper_card.txt",
    "zsum.collection_digest": "prompts/collection_digest.txt",
    "zsum.collection_digest_structured": "prompts/collection_digest_structured.txt",
    "zsum.collection_redigest": "prompts/collection_redigest.txt",
    "zsum.restructure": "prompts/restructure.txt",
}


def _default_runtime_filename(slot_id: str, family: SlotFamily) -> str:
    """Return a stable runtime overlay path for a bundled slot.

    The :func:`_is_safe_filename` check in :mod:`routes.prompts` enforces
    that filenames live under ``.claude/``, ``scripts/`` or ``prompts/``.
    Concrete mappings live in :data:`_AKMS_FILENAME_MAP` and
    :data:`_ZSUM_FILENAME_MAP` so the runtime overlays land in the right
    place (skills vs commands vs agents for AKMS; ``prompts/<name>.txt``
    for zsum). Assistant slots have no Claude/zsum runtime target so they
    get ``prompts/assistant_<slot>.md`` placeholders.
    """
    if slot_id in _AKMS_FILENAME_MAP:
        return _AKMS_FILENAME_MAP[slot_id]
    if slot_id in _ZSUM_FILENAME_MAP:
        return _ZSUM_FILENAME_MAP[slot_id]
    stem = slot_id.split(".", 1)[1]
    return f"prompts/{family}_{stem}.md"


def _load_bundled_child(
    child: Traversable,
    slot_id: str,
    family: SlotFamily,
    stem: str,
) -> "PromptVariant | None":
    """Parse one bundled-defaults file into a ``PromptVariant``.

    Supports ``.logicloom.json`` (full schema) and ``.md`` (raw markdown,
    wrapped into a synthetic variant). Returns ``None`` for unrelated
    files or on parse failure (logged).
    """
    name = child.name
    try:
        text = child.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("skipping unreadable bundled variant %s: %s", name, e)
        return None

    if name.endswith(VARIANT_SUFFIX):
        try:
            variant = PromptVariant.model_validate_json(text)
        except (ValidationError, json.JSONDecodeError) as e:
            logger.warning("skipping malformed bundled variant %s: %s", name, e)
            return None
        variant.source = "bundled"
        return variant

    if name.endswith(MARKDOWN_SUFFIX):
        variant_id = name[: -len(MARKDOWN_SUFFIX)] or "default"
        try:
            return PromptVariant(
                id=variant_id,
                family=family,
                slot=slot_id,
                name=f"Default {stem.replace('_', ' ')}",
                description=f"Bundled default for {slot_id}.",
                source="bundled",
                filename=_default_runtime_filename(slot_id, family),
                content_type="markdown",
                content=text,
            )
        except ValidationError as e:
            logger.warning("could not synthesize bundled variant from %s: %s", name, e)
            return None

    return None


__all__ = [
    "ACTIVE_PROFILE_FILENAME",
    "BUNDLED_RESOURCE_PACKAGE",
    "MARKDOWN_SUFFIX",
    "PROFILE_SUFFIX",
    "PromptStore",
    "SCHEMA_SUFFIX",
    "VARIANT_SUFFIX",
    "content_hash",
    "slugify",
]
