"""Optional ``akms_learn`` plugin detection, status, and request delegation.

Logic-Loom is a **consumer** of ``akms_learn``, never a reimplementer. This
adapter does four things and nothing more:

1. **Detect** whether the optional ``akms_learn`` package is importable.
2. **Load** the plugin object and expose a discovery **status** payload
   in the documented status shape.
3. **Translate** a Logic-Loom compile request — either an explicit graph
   slice or a ``(node, max_depth, direction)`` selection plus the
   :class:`LearningRequest` fields — into ``akms_learn`` inputs.
4. **Delegate** compilation to :func:`akms_learn.compile_learning_source`
   and return the raw :class:`CompileResult` (or a thin wrapper).

It MUST NOT duplicate packet ordering, section extraction, validation, or
exporter logic — those are owned by ``akms_learn`` core.
Graph loading/slicing is likewise delegated to ``akms_learn``'s
``load_graph`` / ``GraphSlice`` so no traversal logic is re-derived here.

Scope boundaries:

* The route layer (``routes/learn.py``) owns the FastAPI route definitions —
  this module defines no routes.
* The route layer serialises the ``/status`` JSON; :func:`build_status`
  returns a dataclass/dict carrying the status fields.
* The route layer owns the pydantic compile request/response;
  :func:`compile` accepts plain Python inputs and returns the raw result.
* Artifact persistence under ``<cache_path>/learning/...`` happens in the
  route layer; :func:`compile` leaves an ``output_dir`` delegation seam but
  writes nothing itself.

Detection contract: this module imports cleanly even when
``akms_learn`` is absent. Availability is probed at import time via
:func:`importlib.util.find_spec` and recorded; the missing-package status
path works without raising.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import logging
import os
import shutil  # noqa: F401 — load-bearing: resolver tests monkeypatch `learn_adapter.shutil.which`; the resolvers now live in host_llm.routing but read the same shutil module singleton, so this re-export handle must stay.
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from ..config import Settings

if TYPE_CHECKING:  # pragma: no cover - typing only
    from akms_learn import CompileResult, GraphSlice, LearningRequest

logger = logging.getLogger(__name__)

Direction = Literal["in", "out", "both"]

#: Distribution name on PyPI / in ``pyproject.toml`` (``importlib.metadata``
#: keys on the *distribution* name, which uses a hyphen, not the import name).
_DISTRIBUTION_NAME = "akms-learn"
#: Import (module) name used for ``find_spec`` / ``import_module``.
_MODULE_NAME = "akms_learn"


def akms_learn_available() -> bool:
    """Return ``True`` when the optional ``akms_learn`` package is importable.

    Uses :func:`importlib.util.find_spec`, which performs no import side
    effects — so calling this is cheap and safe even on the unavailable
    path. Tests monkeypatch this function (or ``find_spec``) to simulate the
    package being absent.
    """
    try:
        return importlib.util.find_spec(_MODULE_NAME) is not None
    except ModuleNotFoundError:
        # find_spec can raise if a *parent* package is missing; treat as absent.
        return False


def _unavailable_reason() -> str:
    return f"Package {_MODULE_NAME} is not installed."


@dataclass(slots=True)
class LearnStatus:
    """Discovery/status payload for the Learn tab.

    The route layer serialises this; the field set here is the
    authoritative source of the status contract. When ``akms_learn`` is
    unavailable, every optional field stays ``None`` and ``capabilities`` is
    empty — matching the documented unavailable shape.
    """

    akms_learn_available: bool
    reason: str | None = None
    version: str | None = None
    plugin_api: str | None = None
    learning_packet_schema: str | None = None
    supported_akms_schema_min: str | None = None
    supported_akms_schema_max: str | None = None
    capabilities: list[str] = field(default_factory=list)
    #: The filtered, user-selectable generation modes (the same catalog the
    #: POST /compile gate accepts — see :func:`get_generation_mode_capabilities`).
    #: ``capabilities`` is the RAW plugin capability list and mixes generation
    #: modes with exporters (``*_export``), execution-backend adapters, and
    #: infra/metadata strings (``learning_source_packet`` & friends). A UI that
    #: partitioned ``capabilities`` by the ``_export`` suffix alone would offer
    #: those infra strings as selectable modes and POST them to /compile, where
    #: the mode gate rejects them as ``unknown_mode`` (422). Surfacing the
    #: already-filtered mode ids here lets the mode selector stay consistent with
    #: what the gate accepts, without the frontend re-deriving the denylist.
    generation_modes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: Domain-pack status payload. Populated only on the available
    #: path from descriptor DATA (never from a companion-package import). Stays
    #: empty when no descriptors are discovered — an empty registry is a valid
    #: state, not an error.
    domain_packs: list[dict[str, Any]] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        """Return a JSON-safe dict in the documented status shape.

        **Unavailable path** — emits EXACTLY 3 keys (nothing else):
        ``{"akms_learn_available": false, "reason": "...", "capabilities": []}``.
        Null metadata fields (version, plugin_api, …) — and ``domain_packs`` —
        are intentionally absent on this path to match the documented example
        literally (the contract test asserts an exact 3-key set here).

        **Available path** — emits the full field set: all non-None metadata
        fields are included; ``reason`` is omitted (it is null when available);
        ``warnings`` is included only when non-empty (it is optional);
        ``domain_packs`` is ALWAYS included (an additive key), defaulting
        to ``[]`` when no descriptors were discovered.
        """
        if not self.akms_learn_available:
            return {
                "akms_learn_available": False,
                "reason": self.reason,
                "capabilities": self.capabilities,
            }

        # Available path: include all required fields; omit None/empty optional ones.
        payload: dict[str, Any] = {
            "akms_learn_available": True,
            "version": self.version,
            "plugin_api": self.plugin_api,
            "learning_packet_schema": self.learning_packet_schema,
            "supported_akms_schema_min": self.supported_akms_schema_min,
            "supported_akms_schema_max": self.supported_akms_schema_max,
            "capabilities": self.capabilities,
            # Filtered, gate-consistent generation modes (additive). The mode
            # selector derives from this; ``capabilities`` stays for backward
            # compatibility and exporter partitioning.
            "generation_modes": self.generation_modes,
            # Domain-pack status drives the domain-pack UI from the
            # backend. Always present on the available path (possibly empty).
            "domain_packs": self.domain_packs,
        }
        # reason is null/absent on the available path.
        # warnings is optional — include only when non-empty.
        if self.warnings:
            payload["warnings"] = self.warnings
        return payload


CapabilityStatus = Literal["available", "unavailable", "planned"]

# ---------------------------------------------------------------------------
# Infra/metadata capability names that live in BASELINE_CAPABILITIES but are
# NOT user-selectable generation modes. These are exposed by the plugin for
# feature-detection purposes (e.g. "does this build support domain packs?")
# but must not appear as learning profiles in /profiles.
# ---------------------------------------------------------------------------

#: Baseline capability strings that are infrastructure/metadata, not generation modes.
_INFRA_CAPABILITIES: frozenset[str] = frozenset(
    {
        "learning_source_packet",  # the LSP structure itself — infra, not a mode
        "domain_pack_registry",  # domain-pack feature-detection — infra
        "static_domain_pack_descriptors",  # domain-pack feature-detection — infra
        "source_pack_descriptors",  # domain-pack feature-detection — infra
        "code_mirror_provenance",  # code-mirror feature-detection — infra
    }
)


def get_generation_mode_capabilities(
    settings: Settings | None = None,
) -> list[dict[str, str]]:
    """Return only the true generation-mode capabilities with their status.

    Filters ``get_capabilities_with_status()`` to the 12 user-selectable
    generation modes defined in akms_learn's ``generation_options_catalog``, by
    excluding:

    * ``*_export`` capabilities (those are exporters, surfaced by /exporters).
    * :data:`~akms_learn.capabilities_catalog.ADAPTER_CAPABILITIES` — the four
      execution-backend adapters are not user-selectable modes.
    * :data:`_INFRA_CAPABILITIES` — infra/metadata capability strings that
      the plugin exposes for feature-detection but are not generation options.

    The adapter imports ``ADAPTER_CAPABILITIES`` from the upstream catalog to
    avoid maintaining a parallel denylist.  On the plugin-absent path returns
    an empty list (degrade-gracefully).

    Host-aware LLM availability: ``akms_learn``'s capability gate
    inspects only process env (``ANTHROPIC_API_KEY`` … / ``NLM_NOTEBOOK_ID`` +
    the ``nlm`` CLI), so the ``llm`` -gated modes (``llm_expanded`` /
    ``adaptive_path``) report ``unavailable`` until a key is exported into the
    sidecar. When ``settings`` is supplied and a host provider resolves
    (:func:`resolve_host_llm_provider`), those modes are flipped to
    ``available`` so ``/status``, ``/profiles``, and the compile gate agree on
    selectability. A resolved provider means the akms_learn runtime gate will
    also pass, so the flip never lets a request through that would then raise
    mid-compile. ``planned`` modes are never flipped — only ``unavailable``.
    """
    all_caps = get_capabilities_with_status()
    if not all_caps:
        return []

    try:
        from akms_learn.capabilities_catalog import ADAPTER_CAPABILITIES as _ADAPTER_CAPS

        adapter_set: frozenset[str] = frozenset(_ADAPTER_CAPS)
    except ImportError:  # pragma: no cover — find_spec said available
        adapter_set = frozenset()

    modes = [
        entry
        for entry in all_caps
        if not entry["capability"].endswith("_export")
        and entry["capability"] not in adapter_set
        and entry["capability"] not in _INFRA_CAPABILITIES
    ]

    if settings is not None and host_llm_modes_available(settings):
        gated = _llm_gated_modes()
        modes = [
            {**entry, "status": "available"}
            if entry["capability"] in gated and entry["status"] == "unavailable"
            else entry
            for entry in modes
        ]
    return modes


def get_generation_mode_status(
    generation_option: str, settings: Settings | None = None
) -> str | None:
    """Return the availability status of a generation mode, or ``None`` if unknown.

    Looks the ``generation_option`` up (case-insensitively, trimmed) against the
    true generation-mode catalog from :func:`get_generation_mode_capabilities`.

    Returns one of ``"available"`` / ``"unavailable"`` / ``"planned"`` when the
    mode is a known generation option, or ``None`` when it is not a recognised
    generation mode (unknown / mistyped / an exporter or adapter name passed in
    the mode slot). The route layer uses this to gate compile requests:
    an unknown mode is a structured 422 and a non-available mode is a structured
    412 — never a 500 from a malformed request reaching the compiler.

    This is the single mode-status lookup site for the route layer; the catalog
    (and therefore the valid-mode set) is owned by ``akms_learn`` and surfaced
    via the capabilities catalog — never hard-coded here.  On the plugin-absent
    path the catalog is empty, so every lookup returns ``None``.

    ``settings`` is forwarded to :func:`get_generation_mode_capabilities` so the
    host-aware availability flip applies here too — the compile gate
    passes ``settings`` so an ``llm`` -gated mode reports ``available`` when the
    host has a provider configured.
    """
    key = (generation_option or "").strip().lower()
    for entry in get_generation_mode_capabilities(settings):
        if entry["capability"].strip().lower() == key:
            return entry["status"]
    return None


def get_capabilities_with_status() -> list[dict[str, str]]:
    """Return a status-aware capability list from the akms_learn plugin.

    Each entry is ``{"capability": str, "status": str}`` where status is one
    of ``available`` / ``unavailable`` / ``planned``.

    On the unavailable-plugin path returns an empty list — the same
    degrade-gracefully contract as the rest of the adapter.  This is the
    single call site for ``plugin.capabilities_with_status()``; the route
    layer must not call akms_learn directly.
    """
    if not akms_learn_available():
        return []

    try:
        from akms_learn.plugin import get_plugin
    except ImportError:  # pragma: no cover - find_spec said it's present
        return []

    try:
        return list(get_plugin().capabilities_with_status())
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("capabilities_with_status() raised: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Host LLM provider resolution
# ---------------------------------------------------------------------------
#
# akms_learn exposes an LLM provider seam: ``LearningRequest``
# carries ``llm_enable`` / ``llm_provider`` / ``llm_policy`` / ``sources`` and the
# compiler's single mapping adapter (``_build_llm_expansion_request``) turns those
# into the ``LLMExpansionRequest`` that drives Stage 7b. akms_learn's own
# capability gate, however, only inspects process env — the completion API keys
# (``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY`` / ``GOOGLE_API_KEY``) and the
# grounded notebook vars (``AKMS_LEARN_NLM_NOTEBOOK_ID`` / ``NLM_NOTEBOOK_ID``)
# plus the ``nlm`` CLI on PATH. On the Logic-Loom desktop host a provider is
# configured through Settings ▸ LLM (``zsum.config.yaml`` ``llm.*``) and the nlm
# workflow. This module bridges host config → that seam:
#
#   * resolve the host's configured provider through the EXISTING config helpers
#     (zsum's own ``zsum.config.load_config`` via
#     :meth:`Settings.effective_zsum_config_path`; the completion env keys; the
#     nlm CLI/env) — this module never re-parses ``zsum.config.yaml`` itself;
#   * report ``llm_expanded`` / ``adaptive_path`` available when (and only when)
#     such a provider resolves, keeping the gate honest;
#   * thread ``llm_enable`` + ``llm_provider`` + ``sources`` onto the
#     ``LearningRequest`` (akms_learn's single mapping does the rest — no second
#     mapping site here), so a mode reported available actually compiles
#     end-to-end instead of running the silent ``no_provider_stub``.
#
# Resolution only reports a provider when akms_learn's runtime gate would also
# pass (a completion key is already in env, or a notebook id + ``nlm`` CLI are
# present), so availability and compilability never disagree. Sensitive provider
# settings (API keys) stay host-side: only the registry name + the grounding
# ``sources`` bundle (notebook id) travel on the request.

# Provider resolution lives in ``host_llm/routing.py``. The cluster below
# was moved there verbatim — registry-name constants, the env-var tuples, the
# ``HostLLMProvider`` dataclass, the ``_resolve_*`` helpers, and
# ``resolve_host_llm_provider`` — and is re-exported here so every existing
# import site (and external callers such as the provider-bridge tests,
# which read ``learn_adapter._COMPLETION_ENV_VARS`` / ``_resolve_nlm_provider`` /
# ``resolve_host_llm_provider``) keeps resolving unchanged. ``resolve_choice`` /
# ``route_for`` are the new cross-consumer surface and live only in routing.py.
# Do not add resolution logic here; modify ``host_llm/routing.py`` instead.
from ..host_llm.routing import (  # noqa: E402
    HostLLMProvider,
    _COMPLETION_ENV_VARS,
    _COMPLETION_PROVIDER_NAME,
    _NLM_NOTEBOOK_ENV_VARS,
    _NLM_PROVIDER_NAME,
    _completion_env_key_present,
    _resolve_completion_provider,
    _resolve_explicit_provider,
    _resolve_nlm_provider,
    _zsum_completion_env_key,
    resolve_choice,
    resolve_host_llm_provider,
)


def host_llm_modes_available(settings: Settings | None) -> bool:
    """Return ``True`` when a host LLM provider is configured (the host-LLM gate)."""
    return resolve_choice(settings, "learn") is not None


def _llm_gated_modes() -> frozenset[str]:
    """Return the generation modes whose availability gates on the ``llm`` extra.

    Driven by akms_learn's own capability→extra map when importable, with a
    static fallback so the consumer never hard-fails if an upstream internal
    name moves.
    """
    try:
        from akms_learn.capability_gates import _CAPABILITY_EXTRA_MAP

        gated = frozenset(mode for mode, extra in _CAPABILITY_EXTRA_MAP.items() if extra == "llm")
        if gated:
            return gated
    except Exception:  # pragma: no cover - defensive
        pass
    return frozenset({"llm_expanded", "adaptive_path"})


#: Generation modes for which Logic-Loom threads a resolved provider onto the
#: request (sets ``llm_enable`` + ``llm_provider`` + ``sources``). ``llm_expanded``
#: is the dedicated LLM-expansion mode; ``adaptive_path`` is gated on the ``llm``
#: extra for *selectability* but runs a deterministic ordering strategy and is
#: NOT driven into LLM expansion here (its scope is "activate, not expand"). Kept separate from :func:`_llm_gated_modes` for that reason.
_LLM_EXPANSION_MODES: frozenset[str] = frozenset({"llm_expanded"})


# ---------------------------------------------------------------------------
# Domain-pack / source-pack status
# ---------------------------------------------------------------------------
#
# Consumer discipline:
#   * This module imports ONLY ``akms_learn`` (incl. ``akms_learn.domain_packs``)
#     — the AKMS Learn *infrastructure*. It NEVER imports a companion package
#     (the concrete domain/companion packages a descriptor points at).
#   * Domain-pack and source-pack descriptors are YAML *data*. We read them via
#     akms_learn's own loaders (``load_descriptor_from_yaml`` /
#     ``load_source_pack_from_yaml``) and serialize the descriptor fields. No
#     domain concept (domain id, pack id, role name) is hard-coded — every value
#     below comes from the descriptor data.
#   * Discovery is "scan a directory for files named ``domain_pack.yaml``", so
#     it works for ANY domain pack and an empty registry yields ``[]``.

#: Conventional filename of a domain-pack descriptor. This is the
#: AKMS Learn *schema* filename, not a domain concept — it is identical for
#: every domain pack regardless of subject matter.
_DOMAIN_PACK_DESCRIPTOR_FILENAME = "domain_pack.yaml"

#: Bound on how many descriptor files a single scan will load. Defends against
#: a misconfigured search root pointing at a huge tree.
_MAX_DOMAIN_PACK_DESCRIPTORS = 256

# ---------------------------------------------------------------------------
# Per-root discovery cache
# ---------------------------------------------------------------------------
# ``GET /api/learn/status`` is polled by the frontend. Descriptor discovery
# (rglob over potentially large source trees) should not run on every request.
# Keying by the resolved search-root path string means:
#   - A test that sets LOGIC_LOOM_LEARN_DOMAIN_PACKS_PATH to a tmp dir gets
#     its own cache entry — no cross-contamination across tests that use
#     distinct tmp paths.
#   - The "no search root" path (None) returns [] immediately without a cache
#     entry (callers bail before reaching the cache).
#   - A different env-pinned root (e.g. CI run with a different fixtures dir)
#     gets a fresh entry, not a stale hit from a previous root.
#
# The value stored is the serialised list; a None sentinel is never stored, so
# a cache miss always triggers a fresh discovery.
_domain_packs_cache: dict[str, list[dict]] = {}


def _enum_value(value: Any) -> Any:
    """Return ``value.value`` for str-enums, else ``value`` unchanged.

    ``CapabilityStatus`` / ``CompanionRole``-style enums serialise to their
    plain string value so the JSON payload carries ``"planned"`` rather than
    ``"CapabilityStatus.planned"``. Non-enum values pass through untouched.
    """
    inner = getattr(value, "value", None)
    if isinstance(inner, str):
        return inner
    return value


def _serialize_source_pack(sp: Any) -> dict[str, Any]:
    """Map a ``SourcePackDescriptor`` to the source-pack status shape.

    ``{source_pack_id, role, status}`` — drawn from descriptor fields
    ``id`` / ``companion_role`` / ``capability_status`` (the latter an enum,
    serialised to its string value). No field is invented or hard-coded.
    """
    return {
        "source_pack_id": sp.id,
        "role": _enum_value(sp.companion_role),
        "status": _enum_value(sp.capability_status),
    }


def _load_source_packs_for(descriptor: Any, descriptor_path: Path) -> list[dict[str, Any]]:
    """Load + serialise the source packs referenced by a domain descriptor.

    A ``DomainPackDescriptor.source_packs`` entry is a path *relative to the
    descriptor file*. Each is resolved against the descriptor's
    directory and parsed via akms_learn's ``load_source_pack_from_yaml`` (DATA,
    not an import of the companion). A single bad / missing source-pack YAML is
    logged and skipped — it never aborts the whole pack.
    """
    from akms_learn.domain_packs import load_source_pack_from_yaml

    base_dir = descriptor_path.parent
    out: list[dict[str, Any]] = []
    for rel in getattr(descriptor, "source_packs", []) or []:
        sp_path = (base_dir / rel) if not Path(rel).is_absolute() else Path(rel)
        try:
            sp = load_source_pack_from_yaml(sp_path)
        except Exception as exc:  # malformed / missing — log-and-skip
            logger.warning(
                "skipping source pack %s (referenced by %s): %s",
                sp_path,
                descriptor_path,
                exc,
            )
            continue
        try:
            out.append(_serialize_source_pack(sp))
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("could not serialise source pack %s: %s", sp_path, exc)
    return out


def _serialize_domain_pack(descriptor: Any, descriptor_path: Path) -> dict[str, Any]:
    """Map a ``DomainPackDescriptor`` to the domain-pack status shape.

    ``{domain_id, pack_id, status, capabilities, source_packs[]}`` — every value
    comes from descriptor DATA: ``domain_id`` / ``id`` (pack_id) / ``status`` /
    ``capabilities`` and the resolved+serialised source packs. ``capabilities``
    is the descriptor's own capability map (passed through as-is, defaulting to
    ``{}`` when the descriptor omits it).
    """
    return {
        "domain_id": getattr(descriptor, "domain_id", None),
        "pack_id": descriptor.id,
        "status": _enum_value(descriptor.status),
        "capabilities": dict(getattr(descriptor, "capabilities", None) or {}),
        "source_packs": _load_source_packs_for(descriptor, descriptor_path),
    }


def _discover_descriptor_paths(search_root: Path) -> list[Path]:
    """Return descriptor YAML paths under ``search_root`` (sorted, bounded).

    Scans recursively for files named :data:`_DOMAIN_PACK_DESCRIPTOR_FILENAME`.
    Deterministic (sorted) and capped at :data:`_MAX_DOMAIN_PACK_DESCRIPTORS`.
    Returns ``[]`` if the root does not exist or the scan raises.

    **Test-directory exclusion**: any candidate whose path contains a segment
    that belongs to a test/fixture tree is silently skipped.  The excluded
    segment names are (case-insensitive): ``tests``, ``test``, ``fixtures``,
    ``__pycache__``.  This prevents test fixtures bundled alongside a package
    (e.g. ``<pkg>/tests/fixtures/domain_packs/*/domain_pack.yaml``) from
    appearing as live domain packs in the production ``/status`` payload.  The
    check is deliberately general — it does not hard-code any package name or
    domain concept.
    """
    _EXCLUDED_SEGMENTS: frozenset[str] = frozenset({"tests", "test", "fixtures", "__pycache__"})

    if not search_root.exists() or not search_root.is_dir():
        return []
    try:
        found = sorted(search_root.rglob(_DOMAIN_PACK_DESCRIPTOR_FILENAME))
    except OSError as exc:  # pragma: no cover - defensive (permission, etc.)
        logger.warning("domain-pack descriptor scan failed under %s: %s", search_root, exc)
        return []

    def _is_test_path(p: Path) -> bool:
        """Return True if an excluded test segment appears WITHIN the scan tree.

        Only segments *relative to* ``search_root`` are inspected — ancestor
        directories above the root are ignored. This prevents a legitimately
        configured ``akms_repo`` that happens to sit under a ``test``/``fixtures``
        ancestor (e.g. ``/home/u/tests/AKMS``) from having every descriptor
        falsely excluded; only ``<root>/.../tests/...`` fixture trees are dropped.
        """
        try:
            parts = p.relative_to(search_root).parts
        except ValueError:  # pragma: no cover - p always under search_root here
            parts = p.parts
        return any(part.lower() in _EXCLUDED_SEGMENTS for part in parts)

    filtered = [p for p in found if not _is_test_path(p)]
    return filtered[:_MAX_DOMAIN_PACK_DESCRIPTORS]


def get_domain_packs(settings: Settings | None) -> list[dict[str, Any]]:
    """Return the ``domain_packs`` status list (descriptor-driven).

    Discovers domain-pack descriptor YAMLs under
    :meth:`Settings.learn_domain_packs_search_root`, parses each as DATA via
    akms_learn's ``load_descriptor_from_yaml``, and serialises it to the
    domain-pack status shape. Returns ``[]`` — never raises — when:

    * ``settings`` is ``None`` (no discovery source threaded through), or
    * ``akms_learn`` (the infrastructure) is not importable, or
    * no search root / no descriptor files are found, or
    * the descriptor loaders cannot be imported.

    A single malformed descriptor is logged and skipped; the rest still load.
    Descriptors are de-duplicated by ``pack_id`` (first wins) so two copies of
    the same pack in the tree do not produce duplicate entries.

    Results are memoised in :data:`_domain_packs_cache` keyed by the resolved
    search-root path string. This avoids repeating the rglob walk on every
    ``GET /api/learn/status`` poll call. Each distinct root gets its own cache
    entry — tests that point the env var at different tmp dirs see independent
    entries with no cross-contamination. Call :func:`clear_domain_packs_cache`
    to invalidate (e.g. after a descriptor file changes on disk).

    Consumer discipline: this imports only ``akms_learn.domain_packs`` and reads
    descriptor YAML as data — it NEVER imports a companion package and contains
    NO hard-coded domain/companion literal.
    """
    if settings is None:
        return []
    if not akms_learn_available():
        return []

    search_root = settings.learn_domain_packs_search_root()
    if search_root is None:
        return []

    # Normalise to a canonical string key so cache hits are robust against
    # equivalent Path representations (trailing slashes, symlinks via str).
    cache_key = str(search_root)
    if cache_key in _domain_packs_cache:
        return list(_domain_packs_cache[cache_key])  # return a copy; caller must not mutate

    try:
        from akms_learn.domain_packs import load_descriptor_from_yaml
    except ImportError as exc:  # pragma: no cover - find_spec said available
        logger.warning("akms_learn.domain_packs not importable: %s", exc)
        return []

    packs: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for path in _discover_descriptor_paths(Path(search_root)):
        try:
            descriptor = load_descriptor_from_yaml(path)
        except Exception as exc:  # malformed descriptor — log-and-skip
            logger.warning("skipping domain-pack descriptor %s: %s", path, exc)
            continue
        try:
            pack_id = descriptor.id
        except Exception:  # pragma: no cover - defensive
            continue
        if pack_id in seen_ids:
            continue
        try:
            packs.append(_serialize_domain_pack(descriptor, path))
            seen_ids.add(pack_id)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("could not serialise domain-pack descriptor %s: %s", path, exc)

    _domain_packs_cache[cache_key] = packs
    return list(packs)  # return a copy; caller must not mutate


def clear_domain_packs_cache() -> None:
    """Invalidate the discovery cache for all search roots.

    Call this after descriptor files change on disk (e.g. in tests that
    write new fixtures under an already-cached root, or in a CLI tool that
    refreshes domain-pack state without restarting the sidecar).
    """
    _domain_packs_cache.clear()


def build_status(settings: Settings | None = None) -> LearnStatus:
    """Build the plugin discovery status payload.

    On the unavailable path returns ``{akms_learn_available: False, reason,
    capabilities: []}`` (all version/schema fields ``None``). On the available
    path loads the plugin via the package's own ``get_plugin()`` and surfaces
    its static metadata + ``capabilities()`` verbatim — no capability set is
    re-derived here.

    ``settings`` is optional so existing call sites (and the unit tests that
    call ``build_status()`` with no args) keep working. When provided, the
    available-path status carries the ``domain_packs`` list, sourced
    from discovered descriptor DATA via :func:`get_domain_packs`. When ``None``
    (or on the unavailable path) ``domain_packs`` stays ``[]``.
    """
    if not akms_learn_available():
        return LearnStatus(
            akms_learn_available=False,
            reason=_unavailable_reason(),
            capabilities=[],
        )

    warnings: list[str] = []

    try:
        from akms_learn.plugin import get_plugin
    except ImportError as exc:  # pragma: no cover - find_spec said it's present
        # find_spec reported the module but the import still failed (broken
        # install / partial vendoring). Degrade to the unavailable shape with
        # the concrete error as the reason, rather than letting it propagate.
        logger.warning("akms_learn present per find_spec but import failed: %s", exc)
        return LearnStatus(
            akms_learn_available=False,
            reason=f"Package {_MODULE_NAME} could not be imported: {exc}",
            capabilities=[],
        )

    plugin = get_plugin()

    try:
        version: str | None = importlib.metadata.version(_DISTRIBUTION_NAME)
    except importlib.metadata.PackageNotFoundError:
        version = None
        warnings.append(f"Could not resolve installed version for {_DISTRIBUTION_NAME!r}.")

    # Discover + serialise domain-pack status from descriptor DATA.
    # Never raises (degrade-gracefully → []); only populated when settings is
    # supplied so the discovery source is known.
    domain_packs = get_domain_packs(settings)

    # Filtered generation modes — the SAME catalog the POST /compile gate accepts
    # (get_generation_mode_capabilities excludes exporters, adapters, and infra
    # capabilities). Surfacing these keeps the mode selector consistent with the
    # gate so the default selection never POSTs an infra capability and 422s.
    generation_modes = [entry["capability"] for entry in get_generation_mode_capabilities(settings)]

    return LearnStatus(
        akms_learn_available=True,
        reason=None,
        version=version,
        plugin_api=plugin.plugin_api,
        learning_packet_schema=plugin.learning_packet_schema,
        supported_akms_schema_min=plugin.supported_akms_schema_min,
        supported_akms_schema_max=plugin.supported_akms_schema_max,
        capabilities=list(plugin.capabilities()),
        generation_modes=generation_modes,
        warnings=warnings,
        domain_packs=domain_packs,
    )


class LearnAdapterError(RuntimeError):
    """Raised when a Learn compile request cannot be served by the adapter.

    Distinct from the structured errors ``akms_learn`` itself raises
    (``LearningCapabilityError``, ``PacketValidationError``, ``ValueError``):
    those bubble up unchanged so the route layer can map them to its
    structured precondition envelopes. This is reserved for the
    adapter's own preconditions, e.g. the package being unavailable.
    """


def build_learning_request(
    *,
    topic: str,
    goal: str,
    generation_option: str,
    audience: str | None = None,
    depth: str | None = None,
    seed_tags: list[str] | None = None,
    max_nodes: int | None = None,
    max_depth: int | None = None,
    include_pitfalls: bool | None = None,
    include_code_links: bool | None = None,
    exporters: list[str] | None = None,
    granularity: str | None = None,
    policy: str | None = None,
    learner_profile: dict[str, Any] | None = None,
    llm_enable: bool | None = None,
    llm_provider: str | None = None,
    llm_policy: str | None = None,
    sources: dict[str, Any] | None = None,
) -> LearningRequest:
    """Construct an ``akms_learn`` :class:`LearningRequest` from UI inputs.

    Only the package's own 11 normalized fields are forwarded; the
    ``LearningRequest`` model is configured with ``extra="ignore"`` so any
    stray UI-only keys a caller passes through are dropped by the model
    rather than here. We pass only non-``None`` values so the package's
    field defaults (``audience="engineer"``, ``depth="implementation"``,
    etc.) apply — this adapter does not re-encode those defaults.

    LLM expansion fields: ``llm_enable`` / ``llm_provider`` /
    ``llm_policy`` / ``sources`` are the public ``LearningRequest`` fields
    that the compiler's single mapping adapter (``_build_llm_expansion_request``)
    turns into Stage 7b's ``LLMExpansionRequest``. :func:`compile` populates them
    from :func:`resolve_host_llm_provider` when the mode is an LLM-expansion mode
    and a host provider is configured; otherwise they stay ``None`` and the
    request defers to akms_learn's defaults (``llm_enable=False`` → the silent
    ``no_provider_stub`` path). This is the ONLY place the consumer sets these —
    the LearningRequest → LLMExpansionRequest mapping itself stays single-sited in
    akms_learn.
    """
    if not akms_learn_available():
        raise LearnAdapterError(_unavailable_reason())

    from akms_learn import LearningRequest

    fields: dict[str, Any] = {
        "topic": topic,
        "goal": goal,
        "generation_option": generation_option,
    }
    if audience is not None:
        fields["audience"] = audience
    if depth is not None:
        fields["depth"] = depth
    if seed_tags is not None:
        fields["seed_tags"] = seed_tags
    if max_nodes is not None:
        fields["max_nodes"] = max_nodes
    if max_depth is not None:
        fields["max_depth"] = max_depth
    if include_pitfalls is not None:
        fields["include_pitfalls"] = include_pitfalls
    if include_code_links is not None:
        fields["include_code_links"] = include_code_links
    if exporters is not None:
        fields["exporters"] = exporters
    # Mode-specific options consumed by multi_granularity / llm_expanded /
    # adaptive_path. ``learner_profile`` is a dict; LearningRequest coerces it to
    # the akms_learn ``LearnerProfile`` model (extra keys ignored by the model).
    if granularity is not None:
        fields["granularity"] = granularity
    if policy is not None:
        fields["policy"] = policy
    if learner_profile is not None:
        fields["learner_profile"] = learner_profile
    # LLM-expansion fields (host-resolved provider seam). Forwarded
    # only when set; the compiler's single mapping adapter consumes them.
    if llm_enable is not None:
        fields["llm_enable"] = llm_enable
    if llm_provider is not None:
        fields["llm_provider"] = llm_provider
    if llm_policy is not None:
        fields["llm_policy"] = llm_policy
    if sources is not None:
        fields["sources"] = sources

    return LearningRequest(**fields)


def _resolve_graph_slice(
    *,
    graph_slice: GraphSlice | dict[str, Any] | None,
    graph_path: str | Path | None,
) -> GraphSlice:
    """Resolve a request's graph selection into an ``akms_learn`` GraphSlice.

    Delegates to ``akms_learn.load_graph`` / ``GraphSlice`` — this adapter
    performs no graph traversal of its own (no node/edge walking, no ego
    expansion). A ``GraphSlice`` instance is passed through unchanged; a dict
    payload or a ``graph_path`` is forwarded to ``load_graph``.

    This function expects the slice selection to be **already concrete**: it
    resolves an explicit ``GraphSlice`` / dict / ``graph_path`` into a loaded
    ``GraphSlice`` and nothing more. It does NOT consume a ``(node,
    max_depth, direction)`` selection and does NOT expand a slice out from a
    root node — ``akms_learn``'s compiler does not do node-rooted expansion
    either (``compile_learning_source`` only seed-tag-filters an
    already-resolved slice, and ``LearningRequest`` carries no node-rooted
    expansion field). Turning ``(node, max_depth, direction)`` into a concrete
    ``GraphSlice`` is Logic-Loom's own responsibility: the route layer does it
    via :func:`graph_adapter.ego_slice` against the cached compiled graph before
    calling into this adapter.
    """
    from akms_learn import GraphSlice, load_graph

    if isinstance(graph_slice, GraphSlice):
        return graph_slice
    return load_graph(graph_path=graph_path, graph_slice=graph_slice)


@dataclass(slots=True)
class LearnCompileOutcome:
    """Thin structured wrapper around an ``akms_learn`` :class:`CompileResult`.

    Carries the raw result plus the resolved cache directory seam for
    artifact persistence. The route layer shapes the compile response from
    ``result``; artifact persistence uses ``cache_dir``. ``cache_dir`` is the
    *intended* destination only — :func:`compile` does NOT write to it.
    """

    result: CompileResult
    cache_dir: Path | None = None


def learning_cache_dir(settings: Settings) -> Path:
    """Return the Logic-Loom-owned learning cache root.

    ``<cache_path>/learning``. Artifacts land under
    ``<cache_path>/learning/{artifact_id}/``. The
    global-vault guard (:func:`assert_outside_protected_roots`) protects the
    AKMS global vault + source tree from receiving any artifact write.
    """
    return settings.cache_path.expanduser() / "learning"


# ---------------------------------------------------------------------------
# Global-vault / AKMS-source-tree write guard
# ---------------------------------------------------------------------------


class VaultWriteGuardError(RuntimeError):
    """Raised when an artifact-write target resolves inside a protected root.

    Protected roots are the AKMS **global vault**
    (:meth:`Settings.effective_akms_global_vault`) and the AKMS **source tree**
    (:attr:`Settings.akms_repo`). Logic-Loom MUST NOT write
    generated learning artifacts into either. This is the safety-critical
    invariant — a false negative (allowing a write through) is the dangerous
    failure mode, so the check is intentionally conservative: it operates on
    fully-resolved absolute paths so ``..`` and symlink tricks cannot slip a
    write past it.
    """


def _protected_roots(settings: Settings) -> list[Path]:
    """Return the resolved absolute protected roots a write must stay out of.

    The roots are resolved with ``strict=False`` so they normalise ``..`` /
    symlinks even when the directory does not yet exist on disk (e.g. a
    not-yet-created default vault). Failure to resolve a single root must NOT
    silently drop it from the protected set — we fall back to an
    ``os.path.normpath`` of the absolute path so ``..`` segments are still
    collapsed lexically (without touching the filesystem) and the root stays
    enforced. The same normalisation is applied to the target in
    :func:`assert_outside_protected_roots` so the containment comparison stays
    apples-to-apples.
    """
    roots: list[Path] = []
    # ``akms_repo`` is optional; when configured it stays protected even if
    # the directory is currently missing.
    for raw in (settings.effective_akms_global_vault(), settings.akms_repo):
        if raw is None:
            continue
        candidate = raw.expanduser()
        try:
            roots.append(candidate.resolve(strict=False))
        except OSError:  # pragma: no cover - defensive; still enforce the root
            roots.append(Path(os.path.normpath(candidate.absolute())))
    return roots


def assert_outside_protected_roots(target: Path, settings: Settings) -> Path:
    """Raise :class:`VaultWriteGuardError` if *target* is in a protected root.

    A write target is rejected when its fully-resolved absolute path is equal
    to, or nested inside, the AKMS global vault or the AKMS source tree.
    Both the target and each protected root are passed through
    ``Path.resolve(strict=False)`` first, so containment is checked on
    canonical absolute paths — defeating ``..`` traversal and symlink escapes
    (a raw string ``startswith`` on unresolved paths would not).

    Returns the resolved target on success so callers can reuse it.
    """
    try:
        resolved_target = target.expanduser().resolve(strict=False)
    except OSError:  # pragma: no cover - defensive
        # Match the _protected_roots fallback: normalise so ``..`` segments are
        # collapsed lexically (os.path.normpath does not touch the filesystem),
        # keeping the containment comparison apples-to-apples and preventing a
        # ``..``-containing target from slipping past the guard in this branch.
        resolved_target = Path(os.path.normpath(target.expanduser().absolute()))

    for root in _protected_roots(settings):
        if resolved_target == root or resolved_target.is_relative_to(root):
            raise VaultWriteGuardError(
                f"Refusing to write artifact into protected AKMS path: "
                f"{resolved_target} is inside {root}. Generated learning "
                "artifacts must live under the Logic-Loom cache "
                "(<cache_path>/learning/), never the AKMS global vault or "
                "source tree."
            )
    return resolved_target


def prepare_artifact_dir(settings: Settings, artifact_id: str) -> Path:
    """Resolve, guard, and create ``<cache_path>/learning/{artifact_id}/``.

    Runs :func:`assert_outside_protected_roots` on the resolved artifact
    directory BEFORE creating it, so a misconfigured ``cache_path`` that
    happens to land inside the AKMS vault/source tree is rejected rather than
    written to. ``artifact_id`` is constrained to a bare path segment (no
    separators / parent refs) so it cannot redirect the write elsewhere.
    """
    if "/" in artifact_id or "\\" in artifact_id or artifact_id in (".", "..") or not artifact_id:
        raise VaultWriteGuardError(f"Invalid artifact_id for cache directory: {artifact_id!r}.")
    artifact_dir = learning_cache_dir(settings) / artifact_id
    assert_outside_protected_roots(artifact_dir, settings)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    return artifact_dir


def compile(
    *,
    topic: str,
    goal: str,
    generation_option: str,
    settings: Settings | None = None,
    audience: str | None = None,
    depth: str | None = None,
    seed_tags: list[str] | None = None,
    max_nodes: int | None = None,
    max_depth: int | None = None,
    include_pitfalls: bool | None = None,
    include_code_links: bool | None = None,
    exporters: list[str] | None = None,
    graph_slice: GraphSlice | dict[str, Any] | None = None,
    graph_path: str | Path | None = None,
    node: str | None = None,
    direction: Direction | None = None,
    output_dir: str | Path | None = None,
    granularity: str | None = None,
    policy: str | None = None,
    learner_profile: dict[str, Any] | None = None,
) -> LearnCompileOutcome:
    """Translate a Logic-Loom compile request and delegate to the compiler.

    Accepts an explicit ``graph_slice`` (a ``GraphSlice`` instance or a dict
    payload) plus the ``LearningRequest`` fields, builds a
    :class:`LearningRequest`, resolves the slice via ``akms_learn``'s loader,
    and calls :func:`akms_learn.compile_learning_source`. Returns the raw
    :class:`CompileResult` wrapped in :class:`LearnCompileOutcome`.

    This function reimplements **none** of the compiler's stages — it only
    marshals inputs and forwards them.

    On node-rooted selection: ``akms_learn`` does NOT perform node-rooted
    slicing. ``compile_learning_source`` consumes neither ``node`` nor
    ``direction`` (its signature takes ``graph_path`` / ``graph_slice`` /
    ``output_dir`` / pack paths only), and ``LearningRequest`` carries no
    node-rooted expansion field — ``max_depth`` rides on the request but only
    feeds the compiler's seed-tag/node-budget filtering of an *already
    resolved* slice, not an ego expansion. Turning ``(node, max_depth,
    direction)`` into a concrete ``GraphSlice`` is Logic-Loom's own job: the
    route layer calls :func:`graph_adapter.ego_slice` against the cached
    compiled graph before delegating. Here, ``node`` / ``direction`` are
    **accepted but advisory only** — logged as selection metadata and
    otherwise unused; this function does NOT slice the graph. Errors
    raised by ``akms_learn`` (``LearningCapabilityError``,
    ``PacketValidationError``, ``ValueError``) propagate unchanged for the
    route layer to envelope.

    Artifact persistence is **not** performed here: ``output_dir`` is a
    pass-through delegation seam, defaulting to ``None`` so the compiler
    writes nothing unless a caller explicitly opts in.
    """
    if not akms_learn_available():
        raise LearnAdapterError(_unavailable_reason())

    from akms_learn import compile_learning_source

    # For an LLM-expansion mode, thread a host-configured provider
    # onto the request so Stage 7b invokes a real provider instead of the silent
    # no_provider_stub. resolve_host_llm_provider only returns a provider whose
    # akms_learn runtime gate will pass, so this never builds a request that then
    # raises mid-compile. CompileRequest.policy carries the llm source-lock policy
    # for these modes (forwarded as llm_policy). With no host provider the fields
    # stay None and the request defers to the stub path.
    llm_enable: bool | None = None
    llm_provider: str | None = None
    llm_policy: str | None = None
    llm_sources: dict[str, Any] | None = None
    if generation_option in _LLM_EXPANSION_MODES:
        host_provider = resolve_choice(settings, "learn")
        if host_provider is not None:
            llm_enable = True
            llm_provider = host_provider.provider
            llm_sources = host_provider.sources
            llm_policy = policy
            logger.info(
                "learn compile: LLM expansion via host provider %r (%s)",
                host_provider.provider,
                host_provider.origin,
            )

    request = build_learning_request(
        topic=topic,
        goal=goal,
        generation_option=generation_option,
        audience=audience,
        depth=depth,
        seed_tags=seed_tags,
        max_nodes=max_nodes,
        max_depth=max_depth,
        include_pitfalls=include_pitfalls,
        include_code_links=include_code_links,
        exporters=exporters,
        granularity=granularity,
        policy=policy,
        learner_profile=learner_profile,
        llm_enable=llm_enable,
        llm_provider=llm_provider,
        llm_policy=llm_policy,
        sources=llm_sources,
    )

    resolved_slice = _resolve_graph_slice(graph_slice=graph_slice, graph_path=graph_path)

    if node is not None:
        logger.debug(
            "learn compile node-depth selection: node=%s max_depth=%s direction=%s",
            node,
            max_depth,
            direction,
        )

    result = compile_learning_source(
        request,
        graph_slice=resolved_slice,
        output_dir=output_dir,
    )

    cache_dir = learning_cache_dir(settings) if settings is not None else None
    return LearnCompileOutcome(result=result, cache_dir=cache_dir)
