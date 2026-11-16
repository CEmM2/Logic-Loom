"""FastAPI routes for the Learn plugin.

All six endpoints delegate to :mod:`..adapters.learn_adapter`; zero compiler
logic lives here. The adapter itself is always importable whether or
not ``akms_learn`` is installed, so the router can be mounted unconditionally.

Degrade-gracefully behavior per endpoint type:
  GET  /status, /profiles, /exporters — always 200; return empty payload when
    the plugin is unavailable (never 503 on read-only discovery endpoints).
  POST /compile, /export — return structured 503 when
    ``akms_learn_available()`` returns False (mutating/compiling paths only).

Route summary (prefix ``/api/learn`` applied at mount in main.py):
  GET  /status
  GET  /profiles
  GET  /exporters
  POST /compile
  POST /export
  GET  /artifacts/{artifact_id}/{path:path}

Artifact persistence and path-traversal-proof file serving for /artifacts
live under <cache_path>/learning/{artifact_id}/. The
vault guard (learn_adapter.assert_outside_protected_roots) ensures generated
artifacts are never written into the AKMS global vault or source tree.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from ..adapters import learn_adapter
from ..adapters.graph_adapter import (
    GraphAdapterError,
    ego_slice,
    load_cached_graph,
    slice_to_payload,
)
from ..adapters.learn_adapter import LearnAdapterError
from ..config import Settings
from ..deps import get_settings
from ..errors import envelope, http_error

router = APIRouter()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper: structured 503 for unavailable-plugin paths
# ---------------------------------------------------------------------------


def _plugin_unavailable_503() -> JSONResponse:
    """Return a structured precondition-error envelope with 503 status."""
    return JSONResponse(
        envelope(
            "plugin_unavailable",
            "akms_learn plugin is not installed or not importable.",
            {"akms_learn_available": False},
        ),
        status_code=503,
    )


def _graph_unavailable_412(message: str) -> JSONResponse:
    """Return a structured 412 precondition envelope for graph-cache errors.

    Built directly as a JSONResponse (rather than raising http_error) because
    install_handlers never emits extra_detail.
    """
    return JSONResponse(
        envelope(
            "graph_unavailable",
            message,
            {"graph_available": False},
        ),
        status_code=412,
    )


def _validate_compile_request_shape(
    body: CompileRequest, settings: Settings | None = None
) -> JSONResponse | None:
    """Mode-aware request-shape gate for POST /compile.

    Returns a structured 4xx ``JSONResponse`` when the request is malformed for
    its declared generation mode, or ``None`` when the request shape passes and
    the handler may proceed to graph resolution + compilation.

    This is a **thin consumer-side gate** — it does NOT reimplement validation
    ``akms_learn`` already owns (capability availability rules, schema checks,
    per-mode field semantics, packet validation).  Its job is to reject a
    malformed request with a clean 4xx envelope BEFORE it reaches the compiler,
    where ``akms_learn`` would otherwise either silently produce a generic
    packet (unknown ``generation_option`` — the compiler does not gate the mode
    during compile) or surface a capability ``PreconditionError`` as a 500.

    Checks, in order (each names the offending mode/field):

    1. **Core required fields** — ``topic`` and ``goal`` must be non-empty after
       trimming.  Every generation mode requires both (topic or seed tags,
       learner audience, depth; goal is the lesson objective).  → 422
       ``compile_precondition_error``.
    2. **Known generation mode** — ``generation_option`` must be one of the true
       generation modes in the akms_learn catalog
       (:func:`~..adapters.learn_adapter.get_generation_mode_capabilities`).
       An unknown / mistyped value (or an exporter/adapter name in the mode
       slot) is rejected → 422 ``unknown_mode`` with the list of valid mode ids.
       The valid-mode set is driven entirely by the catalog, never hard-coded.
    3. **Mode availability** — the mode must report ``available``.  A mode that
       is ``unavailable`` (optional extra not installed, e.g. ``adaptive_path`` /
       ``llm_expanded`` without the ``llm`` provider) or ``planned`` is rejected
       → 412 ``capability_unavailable``, naming the mode and its status, rather
       than letting the request reach the compiler and either render a wrong
       artifact or raise a capability error as a 500.

    The generation modes declare no *additional strictly-required* per-mode request fields:
    mode-specific fields (``learner_profile`` for adaptive_path, ``granularity``
    for multi_granularity, ``policy`` for implementation_first) are all MAY /
    SHOULD (optional with defaults owned by ``akms_learn``), so this gate does
    NOT invent field requirements for them.
    """
    # 1) Core required fields (apply to every mode).
    missing_core = [
        name
        for name, value in (("topic", body.topic), ("goal", body.goal))
        if value is None or not str(value).strip()
    ]
    if missing_core:
        return JSONResponse(
            envelope(
                "compile_precondition_error",
                f"Missing required field(s) for generation_option "
                f"'{body.generation_option}': {', '.join(missing_core)}.",
                {"generation_option": body.generation_option, "fields": missing_core},
            ),
            status_code=422,
        )

    # 2) Known generation mode — driven by the akms_learn catalog, not a list here.
    #    ``settings`` is forwarded so the host-aware availability flip
    #    applies: an llm-gated mode reports ``available`` when the host has a
    #    provider configured, even if no raw API key is exported into the sidecar.
    status = learn_adapter.get_generation_mode_status(body.generation_option, settings)
    if status is None:
        valid_modes = [
            entry["capability"]
            for entry in learn_adapter.get_generation_mode_capabilities(settings)
        ]
        return JSONResponse(
            envelope(
                "unknown_mode",
                f"Unknown generation_option '{body.generation_option}'. "
                f"Must be one of the supported generation modes.",
                {"generation_option": body.generation_option, "valid_modes": valid_modes},
            ),
            status_code=422,
        )

    # 3) Mode availability — reject non-available modes cleanly (412), naming the mode.
    if status != "available":
        return JSONResponse(
            envelope(
                "capability_unavailable",
                f"Generation mode '{body.generation_option}' is not available "
                f"(status: {status}). It requires an optional dependency or "
                f"feature that is not installed/enabled in this build.",
                {"generation_option": body.generation_option, "status": status},
            ),
            status_code=412,
        )

    return None


# ---------------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------------


@router.get("/status")
def learn_status(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Return the plugin discovery payload.

    Always returns 200. On the unavailable path the payload contains
    ``akms_learn_available=false`` and ``capabilities=[]``; the frontend
    uses this to gate the Learn tab.

    On the available path the payload additionally carries ``domain_packs``
    — a descriptor-driven list of domain packs with their
    capabilities and source packs. ``settings`` is threaded through so the
    adapter can discover descriptor YAMLs from the configured search root; the
    domain-pack data is read as DATA via ``akms_learn`` only — no companion
    package is imported and no domain concept is hard-coded.
    """
    return learn_adapter.build_status(settings).to_payload()


# ---------------------------------------------------------------------------
# GET /providers (legacy Learn LLM provider chooser)
# ---------------------------------------------------------------------------


@router.get("/providers")
def learn_providers(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Discovery for the Settings provider chooser — kept as a read-only alias.

    Lists each Learn LLM provider with its route (``api`` | ``cli``), availability,
    and a short detail, plus the current explicit choice. Frozen to the legacy
    payload contract — exactly ``{providers, selected}`` with ``selected`` the RAW
    ``settings.learn_llm_provider`` (kept byte-identical for back-compat). The unified
    generalisation — the per-consumer ``effective`` map — lives on the new
    ``GET /api/llm/providers`` endpoint, NOT here. Read-only — never 503.
    """
    from ..learn_llm_providers import discover_providers

    return {
        "providers": discover_providers(settings),
        "selected": settings.learn_llm_provider,
    }


@router.post("/providers/reload")
def learn_providers_reload() -> dict[str, Any]:
    """Re-apply LLM provider wiring (keys + registration) without an app restart.

    Frozen legacy alias of ``POST /api/llm/providers/reload``: shares the same reload
    side-effect (``routes.llm._reload_core``) but returns the RAW
    ``learn_llm_provider`` for ``selected`` — byte-consistent with
    ``GET /api/learn/providers`` (back-compat), where the ``/api/llm`` surface
    instead reports the effective choice. Degrade-gracefully: never 503 —
    rewire failures become a ``warnings`` entry. Response still contains
    ``reloaded``, ``providers``, ``injected``, ``registered`` (legacy contract).
    """
    from .llm import _reload_core, _reload_response

    settings, rewired, warnings = _reload_core()
    return _reload_response(settings, rewired, warnings, selected=settings.learn_llm_provider)


# ---------------------------------------------------------------------------
# GET /profiles
# ---------------------------------------------------------------------------


@router.get("/profiles")
def learn_profiles(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Return the true generation-mode profiles with availability status.

    Each profile object carries ``id``, ``label``, and ``status`` (available /
    unavailable / planned) so the frontend can render disabled states without
    second-guessing which optional extras are installed.  The list is derived
    from :func:`~..adapters.learn_adapter.get_generation_mode_capabilities`,
    which narrows the full catalog to the 12 user-selectable generation modes
    — excluding exporters (``*_export``), execution-backend
    adapters (``ADAPTER_CAPABILITIES``), and infra/metadata capabilities that
    the plugin surfaces for feature-detection but are not generation options.

    ``settings`` is threaded so the host-aware availability flip
    applies here: this is the endpoint the Learn tab renders selectability from,
    so an llm-gated mode (``llm_expanded`` / ``adaptive_path``) must report
    ``available`` when the host has a provider configured — consistent with
    ``GET /status`` and the compile gate, which both thread ``settings`` too.

    When the plugin is unavailable returns an empty list so callers can render a
    disabled state without a 503 (degrade-gracefully invariant, GET path).
    """
    caps = learn_adapter.get_generation_mode_capabilities(settings)
    if not caps:
        # plugin absent or failed — degrade gracefully
        available = learn_adapter.akms_learn_available()
        return {"profiles": [], "akms_learn_available": available}

    profiles = [
        {
            "id": entry["capability"],
            "label": entry["capability"].replace("_", " ").title(),
            "status": entry["status"],
        }
        for entry in caps
    ]
    return {"profiles": profiles, "akms_learn_available": True}


# ---------------------------------------------------------------------------
# GET /exporters
# ---------------------------------------------------------------------------


@router.get("/exporters")
def learn_exporters() -> dict[str, Any]:
    """Return ALL exporters with optional-dependency availability status.

    Each exporter object carries ``id``, ``label``, and ``status`` (available /
    unavailable / planned).  Exporters are all ``*_export`` capabilities surfaced
    by ``akms_learn.plugin.capabilities_with_status()``.  Optional-dep-gated
    exporters (notebook / html / quiz) report ``available`` only when the relevant
    extra is installed; otherwise ``unavailable`` or ``planned``.

    Availability is derived from ``capabilities_with_status()`` — never by
    importing optional dependencies here (consumer discipline).

    When the plugin is unavailable returns an empty list (degrade-gracefully).
    """
    caps = learn_adapter.get_capabilities_with_status()
    if not caps:
        available = learn_adapter.akms_learn_available()
        return {"exporters": [], "akms_learn_available": available}

    exporters = [
        {
            "id": entry["capability"],
            "label": entry["capability"].replace("_export", "").replace("_", " ").strip().title(),
            "status": entry["status"],
        }
        for entry in caps
        if entry["capability"].endswith("_export")
    ]
    return {"exporters": exporters, "akms_learn_available": True}


# ---------------------------------------------------------------------------
# POST /compile  —  formal contract
# ---------------------------------------------------------------------------


class GraphSlicePayload(BaseModel):
    """Explicit graph-slice payload forwarded verbatim to akms_learn.

    Mirrors the ``GraphSlice`` Pydantic model in ``akms_learn.graph_import``
    (nodes, edges, metadata).  Using a separate name avoids an import of the
    akms_learn model at module load time (akms_learn is optional).
    """

    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CompileRequest(BaseModel):
    """Formal compile request.

    Validation rule: EITHER ``graph_slice`` OR ``node`` must be present.
    A request with neither is rejected with 422 (precondition error).
    ``max_depth`` and ``direction`` are only meaningful when ``node`` is set;
    they are silently ignored on the ``graph_slice`` path.

    ``topic`` / ``goal`` are required, but typed ``str | None`` (default
    ``None``) on purpose: presence + non-blank is enforced in
    :func:`_validate_compile_request_shape` so that BOTH an absent field and a
    blank one surface under the SAME structured ``compile_precondition_error``
    envelope. Were they plain ``str``, an absent field would be caught
    earlier by pydantic as a raw ``validation_error`` — two different 422 codes
    for the same "missing topic/goal" failure.
    """

    # --- Core LearningRequest fields ---
    generation_option: str
    topic: str | None = None
    goal: str | None = None
    audience: str | None = None
    depth: str | None = None
    seed_tags: list[str] | None = None
    exporters: list[str] | None = None

    # --- Mode-specific options (forwarded to akms_learn.LearningRequest) ---
    # These let the Learn-tab mode panels actually CONFIGURE generation instead of
    # collecting input that goes nowhere:
    #   granularity     → multi_granularity (overview / standard / deep_dive)
    #   policy          → llm_expanded source-lock policy
    #                     (source_locked / explanatory_only / no_new_claims)
    #   learner_profile → adaptive_path learner form (knows/weak/goals/conservative_mode)
    #   include_pitfalls / include_code_links / max_nodes → general LearningRequest knobs
    # All optional; omitted fields defer to akms_learn's own defaults.
    granularity: Literal["overview", "standard", "deep_dive"] | None = None
    policy: str | None = None
    learner_profile: dict[str, Any] | None = None
    include_pitfalls: bool | None = None
    include_code_links: bool | None = None
    max_nodes: int | None = None

    # --- Graph selection (mutually exclusive; at least one required) ---
    # Validation is enforced in the route handler (not via model_validator) to
    # produce a structured precondition JSONResponse instead of a raw 422
    # envelope whose ctx dict may contain non-JSON-serializable objects.
    graph_slice: GraphSlicePayload | None = None
    node: str | None = None
    max_depth: int | None = None
    direction: Literal["in", "out", "both"] | None = None


class CompileResponse(BaseModel):
    """Formal compile response.

    Fields
    ------
    artifact_id:
        Deterministic hex digest derived from the packet's provenance hashes.
        Formula: sha256(f"{request_hash}:{graph_hash}")[:32] — identical inputs
        always produce the same artifact_id, enabling cache lookups without
        re-compiling.
    markdown:
        In-memory rendered lesson.md via the akms_learn markdown exporter
        (``_build_context`` + Jinja2 template).  No file is written to disk.
    warnings:
        Serialised ``LearningWarning`` list from ``CompileResult.warnings``
        plus any ``unavailable_capabilities`` surfaced as synthetic warnings.
    provenance_summary:
        Subset of provenance fields from the packet (request_hash, graph_hash,
        topic, generation_option, node_count).
    cache_dir:
        Resolved artifact directory ``<cache_path>/learning/{artifact_id}/``.
        Created (and vault-guarded) before compilation.
    packet_path:
        Filesystem path of the canonical packet JSON written by akms_learn
        (``<artifact_dir>/<request_hash>.json``), or null if nothing was
        written.
    exports:
        Artifacts akms_learn wrote under the artifact dir, as ``{path, type}``
        dicts. The canonical packet is tagged ``packet``; other artifacts are
        tagged by file suffix.
    """

    artifact_id: str
    markdown: str
    warnings: list[dict[str, Any]]
    provenance_summary: dict[str, Any]
    cache_dir: str | None
    packet_path: str | None = None
    exports: list[dict[str, Any]] = Field(default_factory=list)


def _derive_artifact_id_from_inputs(body: CompileRequest, resolved_graph_slice: Any) -> str:
    """Derive the artifact_id BEFORE compiling, from the same canonical hashes.

    The compiler computes ``request_hash`` from :func:`akms_learn.normalize_request`
    and ``graph_hash`` from :func:`akms_learn.compute_graph_hash` (compiler.py
    Stages 2-3). We replay those exact public functions on the request body +
    resolved slice so the id we choose for ``output_dir`` is byte-identical to
    the one :func:`_derive_artifact_id` later derives from the assembled
    packet. This lets us set ``output_dir`` before delegating without a second
    compile pass. An equality assert post-compile guards the contract.
    """
    from akms_learn import compute_graph_hash, normalize_request, request_hash

    normalized = normalize_request(
        {
            "topic": body.topic,
            "goal": body.goal,
            "generation_option": body.generation_option,
            "audience": body.audience,
            "depth": body.depth,
            "seed_tags": body.seed_tags,
            "exporters": body.exporters,
        }
    )
    req_hash = request_hash(normalized)
    graph_hash = compute_graph_hash(resolved_graph_slice)
    return _derive_artifact_id(req_hash, graph_hash)


def _artifact_metadata(path_obj: Any, export_type: str, result: Any) -> dict[str, Any]:
    """Derive per-family metadata for an export artifact.

    Family is determined from the filename/suffix:
      - notebook  (.ipynb)        → cell_count, executed, generated_content
      - assessment (assessment.md / rubric.md name convention)
                                  → has_public, has_answer_key, generated_content
      - html       (.html)        → static, size_bytes, generated_content
      - bundle     (.zip)         → {} (domain-pack metadata is not surfaced here)
      - packet     (export_type == "packet") → {}
      - markdown   (lesson.md / .md) → generated_content

    ``generated_content`` is set True when the compile was run with
    ``generation_option == "llm_expanded"`` (the only mode that generates
    novel LLM content).  A future warning-model pass may refine this.
    """
    from pathlib import Path as _Path

    p: _Path = path_obj if isinstance(path_obj, _Path) else _Path(str(path_obj))
    suffix = p.suffix.lower()
    name = p.name.lower()

    # Determine whether this compile produced generated content.
    # Real path: LearningSourcePacket.request.generation_option
    # (packet.generation_option does NOT exist; the field lives on the nested
    #  LearningRequestInfo sub-model at packet.request.generation_option).
    packet = getattr(result, "packet", None)
    req = getattr(packet, "request", None)
    gen_opt = getattr(req, "generation_option", None)
    generated = gen_opt == "llm_expanded"

    # ---- packet ----
    if export_type == "packet":
        return {}

    # ---- notebook ----
    if suffix == ".ipynb":
        meta: dict[str, Any] = {"cell_count": 0, "executed": False, "generated_content": generated}
        try:
            import json as _json

            nb_text = p.read_text(encoding="utf-8")
            nb = _json.loads(nb_text)
            cells = nb.get("cells", [])
            meta["cell_count"] = len(cells)
            # Considered "executed" when at least one code cell has execution_count set.
            meta["executed"] = any(
                c.get("cell_type") == "code" and c.get("execution_count") is not None for c in cells
            )
        except Exception:
            pass  # metadata best-effort; leave defaults on read/parse error
        return meta

    # ---- html ----
    if suffix == ".html":
        meta = {"static": True, "generated_content": generated}
        try:
            meta["size_bytes"] = p.stat().st_size
        except Exception:
            pass
        return meta

    # ---- bundle ----
    if suffix == ".zip":
        return {}

    # ---- assessment family ----
    # The akms_learn assessment exporter writes exactly three files:
    # assessment.md + assessment.json (public) and rubric.md (hidden answer
    # key). Match those names EXACTLY rather than a loose ``"assessment" in
    # name`` substring, so an unrelated file that merely contains the word
    # "assessment" (e.g. a future self_assessment_notes.md) is not mis-tagged
    # with assessment-family metadata. Presence is derived from sibling files;
    # rubric content is NEVER read.
    if name in ("assessment.md", "assessment.json", "rubric.md"):
        artifact_dir = p.parent
        has_public = (artifact_dir / "assessment.md").is_file()
        has_answer_key = (artifact_dir / "rubric.md").is_file()
        return {
            "has_public": has_public,
            "has_answer_key": has_answer_key,
            "generated_content": generated,
        }

    # ---- markdown (lesson.md / generic .md) ----
    if suffix == ".md":
        return {"generated_content": generated}

    # ---- fallback ----
    return {}


def _serialize_exports(result: Any) -> list[dict[str, Any]]:
    """Serialise ``CompileResult.export_paths`` to ``{path, type, metadata}`` dicts
    (with per-type metadata).

    ``type`` is inferred from the on-disk filename: the canonical packet JSON
    (``<request_hash>.json``) is tagged ``packet``; every other artifact is
    tagged by its suffix (e.g. ``md``, ``zip``) so the frontend can label it.

    ``metadata`` is a per-family dict (see ``_artifact_metadata``).  The
    ``path`` and ``type`` keys are unchanged for backward compatibility.
    """
    out: list[dict[str, Any]] = []
    packet_path = result.packet_path
    for p in result.export_paths:
        path_obj = p
        if packet_path is not None and path_obj == packet_path:
            export_type = "packet"
        else:
            export_type = path_obj.suffix.lstrip(".") or "file"
        meta = _artifact_metadata(path_obj, export_type, result)
        out.append({"path": str(path_obj), "type": export_type, "metadata": meta})
    return out


def _derive_artifact_id(request_hash: str, graph_hash: str) -> str:
    """Derive a deterministic artifact_id from the packet's provenance hashes.

    Formula: hex( SHA-256( "<request_hash>:<graph_hash>" ) )[:32]

    The 32-char prefix (128 bits) is collision-resistant for practical use
    while keeping the id short enough for directory names and URL segments.
    Both hashes come from akms_learn's canonical serialisation, so the same
    inputs always produce the same id — enabling future cache lookups without
    recompiling.
    """
    payload = f"{request_hash}:{graph_hash}".encode()
    return hashlib.sha256(payload).hexdigest()[:32]


def _render_markdown_preview(packet: Any) -> str:
    """Render the markdown preview in-memory via akms_learn's markdown exporter.

    Uses the same Jinja2 context builder as the on-disk exporter but writes
    to a string instead of a file — no disk I/O.  Dispatches to the
    ``_TEMPLATE_EXPANDED`` context/template when the packet's mode key is in
    ``PLAN2_MODE_KEYS``.
    """
    from akms_learn.exporters.markdown import (
        _TEMPLATE_EXPANDED,
        _TEMPLATE_NAME,
        _TEMPLATES_DIR,
        PLAN2_MODE_KEYS,
        _build_context,
        _build_plan2_context,
        _mode_key,
    )
    from jinja2 import Environment, FileSystemLoader

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        autoescape=False,
    )
    mode = _mode_key(packet)
    if mode in PLAN2_MODE_KEYS:
        template = env.get_template(_TEMPLATE_EXPANDED)
        context = _build_plan2_context(packet)
    else:
        template = env.get_template(_TEMPLATE_NAME)
        context = _build_context(packet)
    return template.render(**context)


# ---------------------------------------------------------------------------
# Structured warning categories
# ---------------------------------------------------------------------------

# Explicit category constants — consumers MUST use these strings.
_WARN_CAT_GENERATED_CONTENT = "generated_content"
_WARN_CAT_SKIPPED_PREREQUISITE = "skipped_prerequisite"
_WARN_CAT_MISSING_OPTIONAL_DEP = "missing_optional_dep"
_WARN_CAT_SOURCE_QUALITY = "source_quality"
_WARN_CAT_GENERIC = "generic"

# Mapping from akms_learn warning codes → Logic-Loom category.
# Every known code must appear here; unknown codes fall through to _WARN_CAT_GENERIC.
_WARNING_CODE_TO_CATEGORY: dict[str, str] = {
    # --- source quality / structural issues ---
    "missing_section": _WARN_CAT_SOURCE_QUALITY,
    "section_fallback_body": _WARN_CAT_SOURCE_QUALITY,
    "dangling_reference": _WARN_CAT_SOURCE_QUALITY,
    "code_mirror_missing_source_path": _WARN_CAT_SOURCE_QUALITY,
    "empty_nodes": _WARN_CAT_SOURCE_QUALITY,
    "empty_edges": _WARN_CAT_SOURCE_QUALITY,
    "cycle_broken": _WARN_CAT_SOURCE_QUALITY,
    # --- source quality (continued): graph-data and input-mismatch issues ---
    # invalid_reading_priority: "Node has non-numeric reading_priority; coerced to +infinity"
    #   — malformed graph data, not a missing prerequisite.
    "invalid_reading_priority": _WARN_CAT_SOURCE_QUALITY,
    # adaptive_learner_claim_unmatched: "Learner claim does not match any graph tag or node id."
    #   — learner-profile vs. graph mismatch (input quality), not a missing prerequisite.
    "adaptive_learner_claim_unmatched": _WARN_CAT_SOURCE_QUALITY,
    # missing_teaching_section: "Node missing teaching section"
    #   — structural twin of missing_section (node lacks a section); belongs in source_quality.
    "missing_teaching_section": _WARN_CAT_SOURCE_QUALITY,
    # --- generated content: LLM-generated sections that were rejected / need review ---
    # llm_citation_outside_packet: "GeneratedSection cites node id(s) not present in packet.nodes;
    #   section rejected and NOT attached to LSP." — a rejected LLM-generated section.
    "llm_citation_outside_packet": _WARN_CAT_GENERATED_CONTENT,
    # --- skipped prerequisite: a feature/section was partially unavailable,
    #     the compile continued but with reduced fidelity ---
    "notebook_section_missing": _WARN_CAT_SKIPPED_PREREQUISITE,
    "pedagogical_section_missing": _WARN_CAT_SKIPPED_PREREQUISITE,
    "thin_pitfall_content": _WARN_CAT_SKIPPED_PREREQUISITE,
    "assessment_weak_support": _WARN_CAT_SKIPPED_PREREQUISITE,
    "notebook_unsafe_code_degraded": _WARN_CAT_SKIPPED_PREREQUISITE,
    # --- missing optional dependency: an exporter/adapter/companion is absent ---
    "exporter_unavailable": _WARN_CAT_MISSING_OPTIONAL_DEP,
    "exporter_failed": _WARN_CAT_MISSING_OPTIONAL_DEP,
    "llm_provider_unavailable": _WARN_CAT_MISSING_OPTIONAL_DEP,
    "planned_companion_unavailable": _WARN_CAT_MISSING_OPTIONAL_DEP,
    # Logic-Loom synthetic (unavailable_capabilities list on CompileResult)
    "unavailable_capability": _WARN_CAT_MISSING_OPTIONAL_DEP,
}


def _classify_warning_code(code: str) -> str:
    """Return the category string for a given warning code.

    Unknown codes fall back to ``_WARN_CAT_GENERIC`` so no warning is ever
    silently dropped; categories are assigned per the static table above.
    """
    return _WARNING_CODE_TO_CATEGORY.get(code, _WARN_CAT_GENERIC)


def _serialize_warnings(
    result: Any,
    requested_exporters: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Serialise CompileResult.warnings + unavailable_capabilities to structured dicts.

    Every emitted warning carries an additional ``category``
    key (one of the ``_WARN_CAT_*`` constants above) so consumers can group /
    filter by category rather than inspecting raw ``code`` strings.  The four
    legacy keys (``severity``, ``code``, ``message``, ``source_ref``) are
    preserved unchanged — the category is purely ADDITIVE (backward-compatible
    with the Pass-A contract-shape test).

    Additionally, when ``requested_exporters`` is supplied, any exporter
    whose capability status is NOT ``available`` in the catalog is surfaced as
    a structured ``missing_optional_dep`` warning naming the exporter and the
    fact that its optional extra is not installed.

    Finally, when the compile was run with ``generation_option == "llm_expanded"``
    a ``generated_content`` warning is synthesized so consumers know the
    lesson contains LLM-generated content that should be reviewed.
    """
    out: list[dict[str, Any]] = []

    # 1. Propagate akms_learn LearningWarning objects (adding category).
    for w in result.warnings:
        out.append(
            {
                "severity": w.severity,
                "code": w.code,
                "message": w.message,
                "source_ref": w.source_ref,
                "category": _classify_warning_code(w.code),
            }
        )

    # 2. Synthetic warnings for unavailable_capabilities on CompileResult.
    for cap in result.unavailable_capabilities:
        out.append(
            {
                "severity": "warning",
                "code": "unavailable_capability",
                "message": f"Requested capability '{cap}' is not available.",
                "source_ref": cap,
                "category": _WARN_CAT_MISSING_OPTIONAL_DEP,
            }
        )

    # 3. Missing-dep warnings for exporters the caller requested but whose
    #    capability is not ``available`` in the akms_learn catalog.
    #    This catches the case where a client sends an exporter name that is
    #    present in the catalog but gated on an optional extra (e.g. an LLM
    #    provider), before akms_learn itself would emit exporter_unavailable.
    if requested_exporters:
        caps = learn_adapter.get_capabilities_with_status()
        cap_status: dict[str, str] = {entry["capability"]: entry["status"] for entry in caps}
        # akms_learn exporters are named as bare names (e.g. "notebook") or with
        # the "_export" suffix (e.g. "notebook_export"). The caller passes bare
        # names in the exporters list; we check both forms.
        for exporter_name in requested_exporters:
            cap_key = (
                exporter_name if exporter_name.endswith("_export") else f"{exporter_name}_export"
            )
            status = cap_status.get(cap_key) or cap_status.get(exporter_name)
            if status is not None and status != "available":
                out.append(
                    {
                        "severity": "warning",
                        "code": "missing_optional_dep",
                        "message": (
                            f"Exporter '{exporter_name}' is not available "
                            f"(status: {status}). Install the required optional "
                            f"extra to enable it."
                        ),
                        "source_ref": exporter_name,
                        "category": _WARN_CAT_MISSING_OPTIONAL_DEP,
                    }
                )

    # 4. Synthesize a generated-content warning when the compile used
    #    llm_expanded mode — notifies consumers that the lesson body contains
    #    LLM-generated text and should be reviewed before use.
    packet = getattr(result, "packet", None)
    req = getattr(packet, "request", None)
    gen_opt = getattr(req, "generation_option", None)
    if gen_opt == "llm_expanded":
        out.append(
            {
                "severity": "info",
                "code": "generated_content",
                "message": (
                    "This lesson was produced with the 'llm_expanded' generation "
                    "option and contains LLM-generated content. Review the output "
                    "before using it in a learning context."
                ),
                "source_ref": None,
                "category": _WARN_CAT_GENERATED_CONTENT,
            }
        )

    return out


def _build_provenance_summary(packet: Any) -> dict[str, Any]:
    """Extract a compact provenance summary from the assembled packet."""
    return {
        "request_hash": packet.request.request_hash,
        "graph_hash": packet.source.graph_hash,
        "topic": packet.request.topic if packet.request else None,
        "generation_option": packet.request.generation_option if packet.request else None,
        "node_count": len(packet.body.nodes) if packet.body else 0,
        "edge_count": len(packet.body.edges) if packet.body else 0,
    }


@router.post("/compile", response_model=CompileResponse)
def learn_compile(
    body: CompileRequest,
    settings: Settings = Depends(get_settings),
) -> Any:
    """Compile a learning source packet and return the formal response.

    Node/depth path:
        If ``node`` is supplied, resolves an ego-slice via
        :func:`~..adapters.graph_adapter.ego_slice` against the compiled
        graph cache, translates it to an ``akms_learn.GraphSlice``, then
        delegates to the adapter.  A missing graph cache yields a structured
        412 precondition envelope — never a 500.

    GraphSlice path:
        If ``graph_slice`` is supplied directly, it is forwarded straight to
        the adapter with no traversal logic in this layer.

    Artifact persistence:
        The artifact directory ``<cache_path>/learning/{artifact_id}/`` is
        resolved, vault-guarded, and created BEFORE compilation, then passed
        as ``output_dir`` so akms_learn writes the canonical packet + exports
        there. A destination resolving inside the AKMS global vault or source
        tree is rejected (``cache_policy_violation``). ``packet_path`` and
        ``exports`` in the response reflect what was actually written.
    """
    # graph_slice OR node must be present.  Done here (not in
    # model_validator) so we can return a structured JSONResponse rather than a
    # raw Pydantic 422 whose `ctx` dict may contain non-serialisable objects.
    if body.graph_slice is None and body.node is None:
        return JSONResponse(
            envelope(
                "compile_precondition_error",
                "Either 'graph_slice' or 'node' must be provided.",
                {"fields": ["graph_slice", "node"]},
            ),
            status_code=422,
        )

    if not learn_adapter.akms_learn_available():
        return _plugin_unavailable_503()

    # Mode-aware request-shape gate. Runs after the
    # plugin-availability check (the catalog is empty without the plugin) and
    # BEFORE graph resolution / compilation, so a malformed-for-its-mode request
    # is rejected with a clean structured 4xx instead of silently producing a
    # generic packet (unknown mode) or surfacing a capability error as a 500
    # (unavailable mode). akms_learn owns the capability/schema/packet rules;
    # this gate only checks request shape against the mode catalog.
    mode_error = _validate_compile_request_shape(body, settings)
    if mode_error is not None:
        return mode_error

    # ------------------------------------------------------------------
    # Resolve graph_slice — node/depth path or passthrough
    # ------------------------------------------------------------------
    resolved_graph_slice: Any  # akms_learn.GraphSlice

    if body.graph_slice is not None:
        # Explicit GraphSlice supplied — translate to akms_learn.GraphSlice.
        from akms_learn import GraphSlice as AkmsGraphSlice

        resolved_graph_slice = AkmsGraphSlice(
            nodes=tuple(body.graph_slice.nodes),
            edges=tuple(body.graph_slice.edges),
            metadata=body.graph_slice.metadata,
        )
    else:
        # node/depth path — resolve via Logic-Loom's own compiled graph cache.
        assert body.node is not None  # guaranteed by handler-level selector validation above
        try:
            cached = load_cached_graph(settings)
        except GraphAdapterError as exc:
            return _graph_unavailable_412(str(exc))

        depth = body.max_depth if body.max_depth is not None else 2
        direction = body.direction if body.direction is not None else "out"

        try:
            node_ids = ego_slice(cached.graph, body.node, depth=depth, direction=direction)
        except KeyError:
            return JSONResponse(
                envelope(
                    "node_not_found",
                    f"Node '{body.node}' does not exist in the compiled graph.",
                    {"node": body.node},
                ),
                status_code=422,
            )

        nodes_payload, edges_payload = slice_to_payload(cached.graph, node_ids)

        # Translate Logic-Loom's slice_to_payload format to akms_learn's GraphSlice.
        # slice_to_payload returns dicts with Logic-Loom's node/edge schema
        # (id, title, domain, …). akms_learn.GraphSlice accepts arbitrary dicts
        # in its nodes/edges tuples (it stores them as raw dicts, no schema
        # enforcement). The translator below maps field names so the packet body
        # carries node_id (akms_learn convention) alongside the LL 'id' field.
        def _translate_node(n: dict[str, Any]) -> dict[str, Any]:
            translated = dict(n)
            # akms_learn uses node_id as the primary key; LL graph uses id
            if "id" in translated and "node_id" not in translated:
                translated["node_id"] = translated["id"]
            return translated

        def _translate_edge(e: dict[str, Any]) -> dict[str, Any]:
            translated = dict(e)
            # akms_learn edges use from/to; LL graph uses source/target
            if "source" in translated and "from" not in translated:
                translated["from"] = translated["source"]
            if "target" in translated and "to" not in translated:
                translated["to"] = translated["target"]
            # edge_id is expected by akms_learn; generate one if absent
            if "edge_id" not in translated:
                src = translated.get("source") or translated.get("from") or ""
                tgt = translated.get("target") or translated.get("to") or ""
                etype = translated.get("type") or "edge"
                translated["edge_id"] = f"{src}__{etype}__{tgt}"
            return translated

        translated_nodes = tuple(_translate_node(n) for n in nodes_payload)
        translated_edges = tuple(_translate_edge(e) for e in edges_payload)

        from akms_learn import GraphSlice as AkmsGraphSlice

        resolved_graph_slice = AkmsGraphSlice(
            nodes=translated_nodes,
            edges=translated_edges,
            metadata={"source": "logic_loom_ego_slice", "root_node": body.node},
        )

    # ------------------------------------------------------------------
    # Resolve + guard + create the artifact cache dir BEFORE compiling.
    # artifact_id is deterministic from the request +
    # resolved slice, so we can compute the destination up-front and pass it
    # as output_dir so akms_learn writes the packet + exports there. The
    # guard rejects any destination that resolves inside the AKMS global vault
    # or source tree — generated artifacts NEVER leave the Logic-Loom cache.
    # ------------------------------------------------------------------
    try:
        artifact_id = _derive_artifact_id_from_inputs(body, resolved_graph_slice)
        artifact_dir = learn_adapter.prepare_artifact_dir(settings, artifact_id)
    except learn_adapter.VaultWriteGuardError as exc:
        logger.error("learn compile rejected by vault guard: %s", exc)
        raise http_error(500, "cache_policy_violation", str(exc))

    # ------------------------------------------------------------------
    # Delegate to learn adapter (output_dir → akms_learn persists artifacts)
    # ------------------------------------------------------------------
    try:
        outcome = learn_adapter.compile(
            topic=body.topic,
            goal=body.goal,
            generation_option=body.generation_option,
            settings=settings,
            audience=body.audience,
            depth=body.depth,
            seed_tags=body.seed_tags,
            exporters=body.exporters,
            graph_slice=resolved_graph_slice,
            output_dir=artifact_dir,
            # Mode-specific options (the UI panels now drive real generation).
            granularity=body.granularity,
            policy=body.policy,
            learner_profile=body.learner_profile,
            include_pitfalls=body.include_pitfalls,
            include_code_links=body.include_code_links,
            max_nodes=body.max_nodes,
        )
    except LearnAdapterError as exc:
        raise http_error(503, "plugin_unavailable", str(exc))
    except ValueError as exc:
        return JSONResponse(
            envelope("compile_precondition_error", str(exc), {}),
            status_code=422,
        )
    except Exception as exc:
        logger.exception("learn compile failed")
        raise http_error(500, "compile_error", str(exc))

    result = outcome.result
    packet = result.packet

    # ------------------------------------------------------------------
    # Build response fields
    # ------------------------------------------------------------------
    # The packet's canonical hashes MUST reproduce the pre-compile artifact_id;
    # if they ever diverged, artifacts would be written under a different id
    # than the response advertises. Assert the invariant rather than silently
    # serving an inconsistent path.
    packet_artifact_id = _derive_artifact_id(
        packet.request.request_hash,
        packet.source.graph_hash,
    )
    assert packet_artifact_id == artifact_id, (
        "artifact_id drift: pre-compile id "
        f"{artifact_id!r} != packet-derived id {packet_artifact_id!r}"
    )

    markdown = _render_markdown_preview(packet)

    warnings = _serialize_warnings(result, requested_exporters=body.exporters)

    provenance = _build_provenance_summary(packet)

    # cache_dir advertises the (now-created) artifact directory; packet_path
    # and exports are populated from what akms_learn actually wrote.
    cache_dir = str(artifact_dir)
    packet_path = str(result.packet_path) if result.packet_path is not None else None
    exports = _serialize_exports(result)

    return CompileResponse(
        artifact_id=artifact_id,
        markdown=markdown,
        warnings=warnings,
        provenance_summary=provenance,
        cache_dir=cache_dir,
        packet_path=packet_path,
        exports=exports,
    )


# ---------------------------------------------------------------------------
# POST /export  (minimal stub)
# ---------------------------------------------------------------------------


class ExportRequest(BaseModel):
    """Minimal export request stub.

    A formal request contract has not been defined yet.
    """

    artifact_id: str
    exporters: list[str] = Field(default_factory=list)


@router.post("/export")
def learn_export(
    body: ExportRequest,
    settings: Settings = Depends(get_settings),
) -> Any:
    """Trigger an export of an existing compiled artifact.

    Minimal stub — returns 503 when plugin unavailable, 501 when plugin
    present. Path-traversal-safe artifact serving is already implemented (see
    ``GET /artifacts/{artifact_id}/{path}``); only the export *dispatch* is
    still deferred.

    The request/response contract is not yet formalised.
    """
    if not learn_adapter.akms_learn_available():
        return _plugin_unavailable_503()

    # Artifact persistence + serving are implemented; only the export
    # dispatch on this endpoint is still deferred.
    raise http_error(
        501,
        "not_implemented",
        "Export dispatch is not yet implemented.",
    )


# ---------------------------------------------------------------------------
# GET /artifacts/{artifact_id}/{path:path}
# ---------------------------------------------------------------------------


@router.get("/artifacts/{artifact_id}/{path:path}")
def learn_artifact_file(
    artifact_id: str,
    path: str,
    settings: Settings = Depends(get_settings),
) -> Any:
    """Serve a file from a compiled artifact directory.

    Path-traversal-safe: the artifact id is constrained to a bare path segment
    (no separators), and the requested ``path`` is joined onto the artifact
    dir and fully resolved. The file is served ONLY when its resolved path is
    equal to, or nested inside, the resolved artifact dir — so ``..`` segments,
    absolute paths, and symlink escapes are rejected (403) rather than allowed
    to read a file outside the cache. A missing artifact dir or file is 404.
    """
    # Reject artifact ids that are not bare path segments — they could redirect
    # the lookup outside the learning cache root.
    if "/" in artifact_id or "\\" in artifact_id or artifact_id in (".", ".."):
        raise http_error(400, "invalid_artifact_id", "artifact_id must be a single path segment.")

    learning_root = learn_adapter.learning_cache_dir(settings).resolve(strict=False)
    artifact_dir = (learning_root / artifact_id).resolve(strict=False)

    # Defence in depth: the resolved artifact dir must still sit inside the
    # learning cache root (guards against a crafted id slipping past the
    # segment check above).
    if not (artifact_dir == learning_root or artifact_dir.is_relative_to(learning_root)):
        raise http_error(403, "forbidden", "Resolved artifact path escapes the learning cache.")

    if not artifact_dir.is_dir():
        raise http_error(404, "not_found", f"Artifact '{artifact_id}' not found.")

    requested = (artifact_dir / path).resolve(strict=False)
    if not (requested == artifact_dir or requested.is_relative_to(artifact_dir)):
        raise http_error(403, "forbidden", "Requested path escapes the artifact directory.")

    if not requested.is_file():
        raise http_error(404, "not_found", f"File '{path}' not found in artifact '{artifact_id}'.")

    return FileResponse(str(requested))
