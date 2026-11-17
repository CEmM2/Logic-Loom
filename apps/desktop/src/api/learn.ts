/**
 * Learn API client — `/api/learn/*`.
 *
 * Mirrors the backend contract in routes/learn.py +
 * adapters/learn_adapter.py. The frontend trusts backend status only: tab
 * gating and capability-driven UI are driven entirely by `GET /api/learn/status`,
 * never by inspecting the Python environment.
 *
 * Conventions match sibling clients (api/graph.ts): hand-authored types, no
 * default export, `api<T>` for JSON endpoints and a raw URL builder for the
 * artifact file endpoint (which returns bytes, not JSON).
 */

import { api, apiUrl } from "./client";

import type { GraphDirection, GraphEdge, GraphNode } from "./graph";

/**
 * Mirrors backend GraphSlicePayload (routes/learn.py): nodes/edges/metadata.
 * NOT ./graph's GraphSlice, whose third field is `meta` (would be silently dropped
 * — pydantic ignores `meta` and defaults `metadata={}`, so a real slice would lose
 * its provenance). Backend reads `body.graph_slice.metadata`.
 *
 * `components/graph/sendToLearn.ts` maps a real graph view's `GraphSlice.meta` →
 * `LearnGraphSlice.metadata`.
 */
export type LearnGraphSlice = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  metadata?: Record<string, unknown>;
};

/**
 * `GET /api/learn/status` — always HTTP 200, never 500.
 *
 * `akms_learn_available` is the discriminant. Null/absent fields are OMITTED by
 * the backend (not emitted as `null`), so the available-only metadata fields are
 * optional. `capabilities` is always present (`[]` when unavailable).
 *
 * Baseline capability tokens: `deterministic_outline`, `node_anthology`,
 * `pitfall_driven`, `markdown_export`, `bundle_export`.
 */
export type LearnStatus = {
  akms_learn_available: boolean;
  capabilities: string[];
  /**
   * Filtered, user-selectable generation modes (available-only path; omitted
   * by older backends). This is the SAME catalog the `POST /compile` gate
   * accepts — exporters (`*_export`), execution-backend adapters, and infra
   * capabilities (`learning_source_packet`, …) are already excluded. The mode
   * selector derives from this; partitioning `capabilities` by the `_export`
   * suffix alone would offer those infra strings as modes and get them rejected
   * with `422 unknown_mode`.
   */
  generation_modes?: string[];
  /** Present only when unavailable (omitted when available). */
  reason?: string | null;
  /** Available-only plugin metadata (omitted when unavailable). */
  version?: string | null;
  plugin_api?: string | null;
  learning_packet_schema?: string | null;
  supported_akms_schema_min?: string | null;
  supported_akms_schema_max?: string | null;
  warnings?: string[];
  /**
   * Domain-pack status. ADDITIVE + available-path ONLY:
   * the backend `to_payload()` includes this key only on the available branch
   * (always present there, possibly `[]`) and OMITS it entirely on the
   * unavailable path, which stays exactly `{akms_learn_available, reason,
   * capabilities}`. So treat it as optional — absent on older payloads and on
   * the unavailable path; an empty array means "no descriptors discovered"
   * (a valid state, not an error).
   *
   * Every value here is descriptor DATA the backend read via akms_learn's own
   * loaders. The UI renders these strings generically — it hard-codes NO
   * domain/pack/role/capability name. The `compmech` pack is just the first
   * REFERENCE descriptor, never the UI ontology.
   */
  domain_packs?: LearnDomainPack[];
};

/**
 * A domain pack as serialized by the backend (`_serialize_domain_pack`,
 * learn_adapter.py). Field-for-field:
 *
 *   domain_id    — descriptor `domain_id` (may be null if the descriptor omits it).
 *   pack_id      — descriptor `id`.
 *   status       — pack-level status (`_enum_value(descriptor.status)`, e.g.
 *                  `"reference"`). A free-form string — NOT the capability-status
 *                  vocabulary — so render whatever it carries.
 *   capabilities — the descriptor's own capability map: capability-name → status
 *                  string (e.g. `{static_markdown: "available", ...}`). The keys
 *                  are payload data the UI displays, NOT a hard-coded enum.
 *   source_packs — resolved + serialized companion/source packs.
 *
 * Tolerant/additive: every field optional so an older/partial payload never
 * crashes the renderer.
 */
export type LearnDomainPack = {
  domain_id?: string | null;
  pack_id?: string | null;
  status?: string | null;
  /** capability-name → status string (descriptor's own map; rendered generically). */
  capabilities?: Record<string, string>;
  source_packs?: LearnSourcePack[];
};

/**
 * A source/companion pack as serialized by the backend
 * (`_serialize_source_pack`, learn_adapter.py): `{source_pack_id, role, status}`
 * drawn from descriptor fields `id` / `companion_role` / `capability_status`.
 * All optional/tolerant — payload-driven, no hard-coded role/status enum.
 */
export type LearnSourcePack = {
  source_pack_id?: string | null;
  role?: string | null;
  status?: string | null;
};

/**
 * Capability availability status, shared by profiles and exporters.
 *
 * `available`   — selectable/submittable.
 * `unavailable` — present in the catalog but gated on a missing optional extra.
 * `planned`     — declared but not yet implemented.
 *
 * The frontend renders `unavailable`/`planned` entries DISABLED so a user can
 * never submit one and get a `412 capability_unavailable`.
 */
export type LearnExporterStatus = "available" | "unavailable" | "planned";

/**
 * `GET /api/learn/profiles` item.
 *
 * Backend shape: `{id, label, status}` (routes/learn.py `learn_profiles`). The
 * type stays tolerant/additive — `label`/`status` are optional so older payloads
 * and any future additive fields don't break consumers; the registry builder
 * defaults `status` to `available` and humanizes `id` when `label` is absent.
 */
export type LearnProfile = {
  id: string;
  label?: string | null;
  status?: LearnExporterStatus;
  description?: string | null;
  capabilities?: string[];
};

/**
 * `GET /api/learn/exporters` item.
 *
 * Backend shape: `{id, label, status}` (routes/learn.py `learn_exporters`).
 * `id` is the FULL `*_export` capability token (e.g. `markdown_export`); the
 * compile request's `exporters[]` expects the BARE id (`markdown`), so the
 * registry builder strips the suffix. Tolerant/additive as above.
 */
export type LearnExporter = {
  id: string;
  label?: string | null;
  status?: LearnExporterStatus;
  description?: string | null;
};

/**
 * Per-artifact metadata object. Shape depends on artifact family:
 *
 *   notebook (.ipynb) → cell_count: number, executed: boolean, generated_content: boolean
 *   html (.html)      → static: boolean, size_bytes?: number, generated_content: boolean
 *   assessment family → has_public: boolean, has_answer_key: boolean, generated_content: boolean
 *   markdown (.md)    → generated_content: boolean
 *   packet / bundle   → {} (empty — no extra metadata)
 *
 * The `generated_content` flag is true when `generation_option == "llm_expanded"`.
 * Additive/tolerant: older responses omit `metadata`; new responses include it.
 *
 * Presence flags only — rubric/answer-key content is NEVER read or rendered.
 */
export type LearnArtifactMetadata = {
  /** notebook (.ipynb) */
  cell_count?: number;
  executed?: boolean;
  /** html (.html) */
  static?: boolean;
  size_bytes?: number;
  /** assessment family (assessment.md / assessment.json / rubric.md) */
  has_public?: boolean;
  has_answer_key?: boolean;
  /** all families (when generation_option == "llm_expanded") */
  generated_content?: boolean;
  /** forward-compat: unknown future keys */
  [key: string]: unknown;
};

/**
 * An exported artifact entry — matches the `exports[]` items in the compile
 * response and the artifact-file endpoint's addressable units.
 *
 * `metadata` is additive: absent in older responses, present in newer
 * ones with per-family keys. Consumers must treat it as optional.
 */
export type LearnArtifact = {
  path: string;
  type: string;
  /** Additive metadata; absent on older responses → treat as {}. */
  metadata?: LearnArtifactMetadata;
};

/**
 * `POST /api/learn/compile` request body.
 *
 * Exactly ONE source-selection form must be supplied:
 *   - an explicit `graph_slice`, OR
 *   - node-depth selection (`node` + `max_depth` + `direction`), which the
 *     backend resolves server-side via `graph_adapter.ego_slice` (node-depth
 *     slicing is Logic-Loom's job, not the plugin's — the client just passes
 *     these fields through).
 *
 * Supplying neither form → backend responds 422.
 */
export type LearnCompileRequest = {
  generation_option: string;
  topic: string;
  goal: string;
  audience?: string;
  depth?: string;
  seed_tags?: string[];
  exporters?: string[];
  /**
   * Mode-specific options (additive; mirror the backend CompileRequest fields,
   * which forward to akms_learn.LearningRequest). All optional — an omitted
   * field defers to akms_learn's own default. The Learn-tab mode panels populate
   * ONLY the field(s) the SELECTED mode consumes (see LearnTab.handleCompile):
   *
   *   granularity        → multi_granularity / pedagogical_template
   *   policy             → llm_expanded source-lock
   *                        (source_locked / explanatory_only / no_new_claims)
   *   learner_profile    → adaptive_path learner form
   *   include_code_links → implementation_first (code-first ⇒ true)
   *   include_pitfalls   → general knob
   *   max_nodes          → general knob
   */
  granularity?: "overview" | "standard" | "deep_dive";
  policy?: string;
  learner_profile?: {
    knows: string[];
    weak: string[];
    goals: string[];
    conservative_mode: boolean;
  };
  include_pitfalls?: boolean;
  include_code_links?: boolean;
  max_nodes?: number;
  /** Source form A: explicit graph slice (mirrors backend GraphSlicePayload). */
  graph_slice?: LearnGraphSlice;
  /** Source form B: node-depth selection (resolved server-side). */
  node?: string;
  max_depth?: number;
  direction?: GraphDirection;
};

/**
 * Warning category (backend _serialize_warnings).
 *
 * Values (from routes/learn.py _WARN_CAT_* constants):
 *   generated_content   — LLM-generated section rejected/needing review
 *   skipped_prerequisite — feature skipped due to reduced fidelity
 *   missing_optional_dep — exporter/adapter/provider absent
 *   source_quality      — structural or data quality issue in the graph
 *   generic             — fallback for unknown codes
 *
 * Additive/tolerant: absent on older responses.
 */
export type LearnWarningCategory =
  | "generated_content"
  | "skipped_prerequisite"
  | "missing_optional_dep"
  | "source_quality"
  | "generic";

/** A structured compile warning (backend _serialize_warnings). */
export type LearnWarning = {
  severity: string;
  code: string;
  message: string;
  source_ref: string | null;
  /** Additive category; absent on older responses → treat as "generic". */
  category?: LearnWarningCategory;
};

/** Compact provenance summary (backend _build_provenance_summary). Tolerant of additive fields. */
export type LearnProvenanceSummary = {
  request_hash?: string;
  graph_hash?: string;
  topic?: string | null;
  generation_option?: string | null;
  node_count?: number;
  edge_count?: number;
  [key: string]: unknown;
};

/** `POST /api/learn/compile` response body. */
export type LearnCompileResponse = {
  artifact_id: string;
  markdown: string;
  warnings: LearnWarning[];
  provenance_summary: LearnProvenanceSummary;
  cache_dir: string | null;
  packet_path: string | null;
  exports: LearnArtifact[];
};

/** `GET /api/learn/status` */
export async function getLearnStatus() {
  return api<LearnStatus>("/api/learn/status");
}

/** `GET /api/learn/profiles` */
export async function getLearnProfiles() {
  return api<{ profiles: LearnProfile[] }>("/api/learn/profiles");
}

/** `GET /api/learn/exporters` */
export async function getLearnExporters() {
  return api<{ exporters: LearnExporter[] }>("/api/learn/exporters");
}

/** `POST /api/learn/compile` */
export async function compileLearningSource(req: LearnCompileRequest) {
  return api<LearnCompileResponse>("/api/learn/compile", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
}

// NOTE: there is no separate export call. The Learn tab exports via
// `compileLearningSource`, which persists the artifact and returns `exports`.

/**
 * Build the URL for `GET /api/learn/artifacts/{artifact_id}/{path}`.
 *
 * Returns raw file bytes (a `FileResponse`), not JSON — so this is a URL builder
 * (for use as an `href`/`src`), NOT an `api<T>` call.
 */
export async function learnArtifactUrl(artifactId: string, path: string): Promise<string> {
  const segments = path
    .split("/")
    .filter((seg) => seg.length > 0)
    .map((seg) => encodeURIComponent(seg))
    .join("/");
  return apiUrl(`/api/learn/artifacts/${encodeURIComponent(artifactId)}/${segments}`);
}

/**
 * `GET /api/learn/providers` — discovery for the Settings LLM-provider
 * chooser. `route` is `"api"` (LiteLLM completion) or `"cli"` (agent CLI / nlm /
 * custom). `available` reflects key-presence (api) or binary-on-PATH (cli).
 */
export type LearnProvider = {
  name: string;
  label: string;
  route: "api" | "cli";
  available: boolean;
  detail: string;
};

export type LearnProviders = {
  providers: LearnProvider[];
  selected: string | null;
};

export async function getLearnProviders() {
  return api<LearnProviders>("/api/learn/providers");
}

/**
 * `POST /api/learn/providers/reload` — re-apply LLM provider wiring
 * (force-inject keys + re-register agent CLIs) in-process, so a just-saved key
 * or custom CLI goes live without restarting the app.
 */
export type LearnProvidersReload = LearnProviders & {
  reloaded: boolean;
  injected: string[];
  registered: string[];
};

export async function reloadLearnProviders() {
  return api<LearnProvidersReload>("/api/learn/providers/reload", {
    method: "POST",
  });
}
