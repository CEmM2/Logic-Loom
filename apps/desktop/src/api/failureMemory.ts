import { api } from "./client";

// Failure Memory workspace client.
// Mirrors api/mechdsl.ts conventions: typed wrappers over the shared `api<T>`
// fetch, one function per backend route, no logic.
//
// The whole surface is READ-ONLY. `resolveFailureMemory` and `checkFailureMemory`
// are POST because they carry a body / are diagnostics, not because anything is
// created: the backend runs the provider with `write_artifacts=false` and the
// compiler in `validate`/`check` mode only.

export type FailureMemoryMode = "disabled" | "read-only" | "operator";
export type SelectionClass = "required" | "coactivated" | "advisory";

export type OverlayEdgeType =
  | "failure_route"
  | "load_with"
  | "selected_required"
  | "selected_coactivated"
  | "selected_advisory";

/**
 * The frozen consumer-contract degradation envelope. Returned verbatim (as the
 * whole 503 body) by every per-project endpoint when `akms-failure-memory` is
 * not installed, because `provider-result.v1.json` pins `status` to the constant
 * `"ok"` and has no shape for a failed resolution.
 */
export type DegradationEnvelope = {
  schema_version: "failure-memory-consumer-degradation/v1";
  status: "unavailable";
  degraded: true;
  enforcement: "disabled";
  reason: { code: string; message: string };
  records: never[];
  required_node_ids: never[];
};

export type FailureMemoryProjectSummary = {
  id: string;
  config_path: string;
  root: string | null;
  available: boolean;
  repository_id: string | null;
  node_namespace: string | null;
  config_fingerprint: string | null;
  error_code: string | null;
  error: string | null;
  /**
   * Nothing has ever been written here AT ALL: no registry FILE, no route
   * index, no generated nodes, no graph. Not a fault. The registry must be
   * absent, not merely empty — a scaffolded project with an empty registry
   * reports `uncompiled` instead. Never derived from the disposable lock
   * directory, whose absence is ordinary in any fresh clone.
   */
  never_written: boolean;
  /** Operational only: lock-taking calls need it. Never evidence about content. */
  lock_parent_exists: boolean;
};

export type FailureMemoryProjects = {
  mode: FailureMemoryMode;
  enabled: boolean;
  operator: boolean;
  available: boolean;
  package_installed: boolean;
  package_version: string | null;
  reason: string | null;
  degradation: DegradationEnvelope | null;
  projects: FailureMemoryProjectSummary[];
};

export type ArtifactState = {
  key: string;
  path: string;
  exists: boolean;
  sha256: string | null;
  policy: string | null;
};

export type StalenessState = {
  state: "current" | "stale" | "uncompiled" | "unknown";
  registry_sha256: string | null;
  routes_source_hash: string | null;
  reason: string;
};

export type GraphState = {
  /** `unverifiable` is not `current`. See FailureMemoryStatus.currency. */
  currency: "current" | "stale" | "unverifiable" | "absent";
  currency_reason: string;
  /**
   * Nodes counted from the graph document's `nodes` array — never the
   * `graph.node_count` header, which is metadata about content, not content.
   */
  content_node_count: number;
  routed_node_count: number;
  /** Routed nodes absent from the graph. Resolution fails for exactly these. */
  missing_routed_node_ids: string[];
  published: boolean;
  generated_at: string | null;
  repo_id: string | null;
  global_vault: string | null;
  node_count: number | null;
  edge_count: number | null;
  identity_matches_config: boolean | null;
};

export type FailureMemoryStatus = {
  mode: FailureMemoryMode;
  project_id: string;
  repository_id: string;
  node_namespace: string;
  config_path: string;
  root: string;
  config_fingerprint: string;
  lesson_count: number;
  never_written: boolean;
  /**
   * The single honest answer to "is what this project would resolve today
   * current?", the worst of the route-index and graph findings.
   *
   * `current` requires POSITIVE verification of both. When currency cannot be
   * established the answer is `unverifiable`, never `current` — an operator who
   * cannot tell "stale" from "unverifiable" from "current" will make the wrong
   * call, and rendering uncertainty as a green tick is the failure that does not
   * announce itself.
   */
  currency: "current" | "stale" | "unverifiable" | "uncompiled" | "never_written";
  currency_reason: string;
  /**
   * The compiler's own `/check` verdict, obtained on the same code path as
   * POST /check and consulted in `currency`. `null` means the diagnostics
   * could not be obtained at all — which blocks `current`.
   */
  check_status: "clean" | "drift" | "invalid" | "never_written" | "unverifiable" | null;
  check_reason: string;
  /**
   * Lock-free comparison of the generated-nodes directory against the
   * registry's lessons. `incomplete` is definitive published-content
   * disagreement and reads as stale even where the recompile-based check
   * cannot run (e.g. a cold clone).
   */
  generated_state: "complete" | "incomplete" | "absent";
  generated_reason: string;
  generated_missing_node_ids: string[];
  generated_orphan_node_ids: string[];
  /**
   * Always true. Detecting that registry content moved reads the registry digest
   * and the route-index hash; repo2md is nowhere in that path. An unavailable
   * refresh must never be readable as an inability to notice.
   */
  currency_verifiable_without_toolchain: boolean;
  /** Operational only: lock-taking calls need it. Never evidence about content. */
  lock_parent_exists: boolean;
  routed_path_count: number;
  routed_symbol_count: number;
  artifacts: ArtifactState[];
  staleness: StalenessState;
  graph: GraphState;
  toolchain: Record<string, unknown>;
  generated_policy: Record<string, string>;
};

export type LessonSummary = {
  id: string;
  node_id: string;
  overlay_node_id: string;
  repository_id: string;
  location: {
    area: string;
    file: string;
    line_hint: string;
    module: string;
    package: string;
    symbol: string;
  };
  found_by: { type: string; trigger: string };
  references: { commit: string; issue: string; plan: string; pr: string };
  symptom: string;
  root_cause: string;
  fix: string;
  prevention: string;
  notes: string | null;
  related: string[];
  date_found: string;
  date_found_precision: string;
  date_fixed: string;
  date_fixed_precision: string;
  declared_paths: string[];
  routed_paths: string[];
  routed_symbols: string[];
  documentation_only: boolean;
  node_generated: boolean;
};

export type LessonFacets = {
  categories: string[];
  areas: string[];
  packages: string[];
  modules: string[];
  symbols: string[];
  paths: string[];
};

export type LessonsResponse = {
  project_id: string;
  repository_id: string;
  total: number;
  count: number;
  lessons: LessonSummary[];
  facets: LessonFacets;
};

export type RouteEntry = {
  kind: "path" | "symbol";
  key: string;
  node_id: string;
  lesson_id: string | null;
  overlay_edge_id: string;
  match_semantics: string | null;
  reasons: { source_field: string; matched_text: string }[];
  source_exists: boolean | null;
  documentation: boolean;
};

export type LessonDetail = {
  project_id: string;
  repository_id: string;
  lesson: LessonSummary;
  node_path: string | null;
  node_markdown: string | null;
  related_lessons: LessonSummary[];
  routes: RouteEntry[];
};

export type OverlayNode = {
  id: string;
  kind: "source_file" | "failure_lesson";
  project_id: string;
  repository_id: string;
  label: string;
  path: string | null;
  node_id: string | null;
  lesson_id: string | null;
  exists: boolean | null;
  documentation: boolean | null;
  selection_class: SelectionClass | null;
};

export type OverlayEdge = {
  id: string;
  source: string;
  target: string;
  type: OverlayEdgeType;
  project_id: string;
  repository_id: string;
  label: string | null;
};

export type Overlay = {
  project_id: string;
  repository_id: string;
  nodes: OverlayNode[];
  edges: OverlayEdge[];
};

export type RouteCoverage = {
  lesson_count: number;
  routed_lesson_count: number;
  unrouted_lesson_ids: string[];
  path_route_count: number;
  symbol_route_count: number;
  missing_source_paths: string[];
  documentation_path_count: number;
};

export type RoutesResponse = {
  project_id: string;
  repository_id: string;
  schema_version: string | null;
  source_hash: string | null;
  compiled: boolean;
  staleness: StalenessState;
  routes: RouteEntry[];
  coverage: RouteCoverage;
  overlay: Overlay;
};

export type RecordView = {
  node_id: string;
  lesson_id: string | null;
  overlay_node_id: string;
  selection_class: SelectionClass;
  content_ref: string | null;
  source_ref: string | null;
  reasons: string[];
};

/**
 * `delivered_nowhere` is the verdict this whole workspace exists to make
 * visible: a lesson with a LIVE compiled route can arrive in no lane at all
 * while the call still reports success. It must never render like
 * `no_lessons_apply`, which is a genuinely empty result.
 */
export type DeliveryVerdict =
  | "no_lessons_apply"
  | "delivered"
  | "downgraded_to_advisory"
  | "delivered_nowhere";

export type DeliveryReport = {
  verdict: DeliveryVerdict;
  headline: string;
  /** Every path the resolution considered: request paths plus provider-derived. */
  input_paths: string[];
  request_paths: string[];
  /**
   * Paths the provider computed itself. A base/head post-diff call supplies
   * none of its paths in the request, so attribution that ignores these can
   * report `no_lessons_apply` above a selected required lesson.
   */
  provider_derived_paths: string[];
  symbols: string[];
  empty_diff_fallback: boolean;
  documentation_paths: string[];
  code_paths: string[];
  documentation_only: boolean;
  routed_candidate_node_ids: string[];
  delivered_node_ids: string[];
  required_node_ids: string[];
  delivered_nowhere_node_ids: string[];
  downgraded_node_ids: string[];
  tag_only_node_ids: string[];
};

export type ResolveRequest = {
  task_id?: string;
  title?: string;
  objective?: string;
  declared_paths: string[];
  changed_paths?: string[];
  base?: string | null;
  head?: string | null;
  akms_tags?: string[];
  mode?: "pre-task" | "post-diff";
  role?: string;
  baseline?: string;
  invocation_id?: string;
  /** Task symbol seeds. Sent to the provider only when non-empty. */
  symbols?: string[];
};

export type ResolveResponse = {
  project_id: string;
  repository_id: string;
  write_artifacts: false;
  request: Record<string, unknown>;
  result: Record<string, unknown>;
  required: RecordView[];
  coactivated: RecordView[];
  advisory: RecordView[];
  delivery: DeliveryReport;
  overlay: Overlay;
};

export type CheckResponse = {
  project_id: string;
  repository_id: string;
  /** `unverifiable` = the check could not run, so it establishes nothing. Never read it as reassurance. */
  status: "clean" | "drift" | "invalid" | "never_written" | "unverifiable";
  drift: boolean;
  validation: Record<string, unknown>;
  check: Record<string, unknown> | null;
  global_vault: string | null;
  global_vault_exists: boolean;
  warnings: Record<string, string>[];
};

const base = "/api/failure-memory";

export async function getFailureMemoryProjects() {
  return api<FailureMemoryProjects>(`${base}/projects`);
}

export async function getFailureMemoryStatus(project: string) {
  return api<FailureMemoryStatus>(`${base}/${encodeURIComponent(project)}/status`);
}

export type LessonFilters = {
  path?: string;
  symbol?: string;
  category?: string;
  root_cause?: string;
  related?: string;
  q?: string;
};

export async function getFailureMemoryLessons(project: string, filters: LessonFilters = {}) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value && value.trim()) params.set(key, value.trim());
  }
  const query = params.toString();
  return api<LessonsResponse>(
    `${base}/${encodeURIComponent(project)}/lessons${query ? `?${query}` : ""}`,
  );
}

export async function getFailureMemoryLesson(project: string, lessonId: string) {
  return api<LessonDetail>(
    `${base}/${encodeURIComponent(project)}/lessons/${encodeURIComponent(lessonId)}`,
  );
}

export async function getFailureMemoryRoutes(project: string) {
  return api<RoutesResponse>(`${base}/${encodeURIComponent(project)}/routes`);
}

export async function resolveFailureMemory(project: string, request: ResolveRequest) {
  return api<ResolveResponse>(`${base}/${encodeURIComponent(project)}/resolve`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function checkFailureMemory(project: string) {
  return api<CheckResponse>(`${base}/${encodeURIComponent(project)}/check`, {
    method: "POST",
  });
}


// ── operator mode ───────────────────────────────────────────────────────────
//
// Every operator endpoint returns 202 with a Job whose status is `queued` —
// deliberately NOT success. The outcome arrives only when the package returns
// and the job flips to `succeeded` carrying its result. A form that cleared
// itself on the 202 would be claiming an outcome that has not happened.

export type JobStatus = "queued" | "running" | "succeeded" | "failed";
export type JobAction = "record" | "compile" | "refresh";

export type Job = {
  id: string;
  project_id: string;
  action: JobAction;
  status: JobStatus;
  detail: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  summary: string;
  result: Record<string, unknown> | null;
  error: { code: string; message: string } | null;
};

export type RecordRequest = {
  date_found: string;
  date_found_precision?: "exact" | "approximate";
  date_fixed: string;
  date_fixed_precision?: "exact" | "approximate";
  location: {
    area?: string;
    file: string;
    line_hint?: string;
    module?: string;
    package?: string;
    symbol?: string;
  };
  found_by?: { type?: string; trigger?: string };
  references?: { commit?: string; issue?: string; plan?: string; pr?: string };
  symptom?: string;
  root_cause?: string;
  fix?: string;
  prevention?: string;
  related?: string[];
  notes?: string | null;
};

/** `clean` and `status` are intentionally not offered — see the backend model. */
export type RefreshAction = "preflight" | "lessons" | "mirror" | "graph" | "all";

export type ToolchainRequirement = {
  name: string;
  satisfied: boolean;
  detail: string;
};

export type WriteDiagnostics = {
  project_id: string;
  repository_id: string;
  mode: FailureMemoryMode;
  operator: boolean;
  package_installed: boolean;
  can_record: boolean;
  can_compile: boolean;
  can_refresh: boolean;
  never_written: boolean;
  global_vault: string;
  global_vault_exists: boolean;
  lock_path: string;
  lock_parent_exists: boolean;
  generated_at_source: string;
  derived_generated_at: string | null;
  /**
   * `unavailable` means FIX THE TOOLCHAIN. It is never `stale`, which would
   * mean the recorded knowledge moved — conflating the two makes a permanently
   * failing gate indistinguishable from a real change.
   */
  refresh_classification: "available" | "unavailable";
  refresh_reason: string;
  /** [toolchain].mirror_provider: "builtin" needs no repo2md checkout (AKMS 0.4+). */
  mirror_provider?: "builtin" | "repo2md";
  repo2md_required_commit: string;
  repo2md_required_version: string;
  repo2md_command: string[];
  /** Concrete steps that enable refresh. repo2md is a documented deployment
   *  prerequisite, so in a normal deployment these are the expected content. */
  refresh_remediation: string[];
  detection_still_works: boolean;
  toolchain: ToolchainRequirement[];
  blockers: string[];
};

export async function getWriteDiagnostics(project: string) {
  return api<WriteDiagnostics>(
    `${base}/${encodeURIComponent(project)}/write-diagnostics`,
  );
}

export async function listFailureMemoryJobs(project?: string) {
  const query = project ? `?project=${encodeURIComponent(project)}` : "";
  return api<Job[]>(`${base}/jobs${query}`);
}

export async function getFailureMemoryJob(jobId: string) {
  return api<Job>(`${base}/jobs/${encodeURIComponent(jobId)}`);
}

export async function recordLesson(project: string, request: RecordRequest) {
  return api<Job>(`${base}/${encodeURIComponent(project)}/record`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function compileProject(project: string) {
  return api<Job>(`${base}/${encodeURIComponent(project)}/compile`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({}),
  });
}

export async function refreshProject(
  project: string,
  request: { action: RefreshAction; generated_at?: string | null },
) {
  return api<Job>(`${base}/${encodeURIComponent(project)}/refresh`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(request),
  });
}
