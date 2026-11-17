import { api, apiUrl } from "./client";

export type GraphDirection = "in" | "out" | "both";

export type GraphNode = {
  id: string;
  title: string | null;
  domain: string | null;
  subdomain: string | null;
  status: string | null;
  tags: string[];
  confidence: number | null;
  source: string | null;
  node_origin: string | null;
  file_path: string | null;
  // Provenance fields — undefined on older payloads → treat as Tier B.
  is_established?: boolean;
  tier?: "A" | null;
  verified_by?: string | null;
  verified_at?: string | null;
  version?: string | null;
  source_ref?: string | null;
};

export type GraphEdge = {
  source: string;
  target: string;
  type: string | null;
  weight: number | null;
  note: string | null;
  edge_origin: string | null;
};

export type GraphMeta = {
  generated_at: string | null;
  node_count: number;
  edge_count: number;
  repo_id: string | null;
  global_vault: string | null;
  cache_path: string;
  scope: string;
  selected_node: string | null;
  depth: number | null;
  direction: string | null;
  tag: string | null;
  domain: string | null;
};

export type GraphSlice = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  meta: GraphMeta;
};

export type GraphStatus = {
  cache_exists: boolean;
  cache_path: string;
  repo_graph_path: string | null;
  generated_at: string | null;
  node_count: number;
  edge_count: number;
  repo_id: string | null;
  akms_repo: string | null;
  akms_global_vault: string;
  graph_package_installed: boolean;
  repo_exists: boolean;
  global_vault_exists: boolean;
  graph_available: boolean;
};

export type GraphRefreshProgress = {
  stage: string;
  message: string;
  current: number;
  total: number;
  percent: number;
};

export type GraphSourceIssue = {
  path: string;
  relative_path: string;
  source_kind: string;
  reason: string;
  error_type: string;
  ignored: boolean;
};

export type GraphIssueLog = {
  generated_at: string | null;
  log_path: string;
  issue_count: number;
  ignored_issue_count: number;
  total_issue_count: number;
  total_markdown_files: number;
  valid_markdown_files: number;
  issues: GraphSourceIssue[];
};

export type GraphRefreshOptions = {
  skipInvalidFiles?: boolean;
};

export class GraphReviewRequiredError extends Error {
  report: GraphIssueLog;

  constructor(report: GraphIssueLog) {
    super(`Graph source review required for ${report.issue_count} file(s).`);
    this.name = "GraphReviewRequiredError";
    this.report = report;
  }
}

export type GraphNodeFilters = Partial<{
  query: string;
  domain: string;
  status: string;
  tag: string;
  origin: string;
}>;

export type GraphEdgeFilters = Partial<{
  source: string;
  target: string;
  type: string;
}>;

function withParams(path: string, params: Record<string, string | number | null | undefined>) {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value == null || value === "") continue;
    qs.set(key, String(value));
  }
  const suffix = qs.toString();
  return suffix ? `${path}?${suffix}` : path;
}

export async function getGraphStatus() {
  return api<GraphStatus>("/api/graph/status");
}

export async function getGraphIssueLog() {
  return api<GraphIssueLog>("/api/graph/issues");
}

export async function scanGraphIssueLog() {
  return api<GraphIssueLog>("/api/graph/issues/scan", { method: "POST" });
}

export async function setGraphIssueIgnored(path: string, ignored = true) {
  return api<GraphIssueLog>("/api/graph/issues/ignore", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ path, ignored }),
  });
}

export async function refreshGraph(options: GraphRefreshOptions = {}) {
  return api<GraphStatus>("/api/graph/refresh", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ skip_invalid_files: Boolean(options.skipInvalidFiles) }),
  });
}

export async function streamGraphRefresh(
  options: GraphRefreshOptions,
  onProgress: (progress: GraphRefreshProgress) => void,
  onDone: (status: GraphStatus) => void,
  onError: (message: string) => void,
  onReviewRequired?: (report: GraphIssueLog) => void,
): Promise<() => void> {
  const url = new URL(await apiUrl("/api/graph/refresh/stream"));
  if (options.skipInvalidFiles) {
    url.searchParams.set("skip_invalid_files", "true");
  }
  const es = new EventSource(url.toString());
  let settled = false;

  const cleanup = () => {
    es.onmessage = null;
    es.onerror = null;
    es.close();
  };

  es.addEventListener("progress", (event) => {
    if (settled) return;
    try {
      onProgress(JSON.parse((event as MessageEvent).data) as GraphRefreshProgress);
    } catch (error) {
      settled = true;
      onError(error instanceof Error ? error.message : String(error));
      cleanup();
    }
  });

  es.addEventListener("done", (event) => {
    if (settled) return;
    settled = true;
    try {
      onDone(JSON.parse((event as MessageEvent).data) as GraphStatus);
    } catch (error) {
      onError(error instanceof Error ? error.message : String(error));
    } finally {
      cleanup();
    }
  });

  es.addEventListener("review-required", (event) => {
    if (settled) return;
    settled = true;
    try {
      const report = JSON.parse((event as MessageEvent).data) as GraphIssueLog;
      if (onReviewRequired) {
        onReviewRequired(report);
      } else {
        onError(`Graph source review required for ${report.issue_count} file(s).`);
      }
    } catch (error) {
      onError(error instanceof Error ? error.message : String(error));
    } finally {
      cleanup();
    }
  });

  es.addEventListener("failed", (event) => {
    if (settled) return;
    settled = true;
    const raw = (event as MessageEvent).data;
    try {
      const parsed = JSON.parse(raw) as { message?: string };
      onError(parsed.message ?? "Graph generation failed.");
    } catch {
      onError(typeof raw === "string" && raw ? raw : "Graph generation failed.");
    } finally {
      cleanup();
    }
  });

  es.onerror = () => {
    if (settled) return;
    settled = true;
    onError("Graph generation stream disconnected.");
    cleanup();
  };

  return cleanup;
}

export async function getGraphNodes(filters: GraphNodeFilters = {}) {
  return api<GraphNode[]>(withParams("/api/graph/nodes", filters));
}

export async function getGraphEdges(filters: GraphEdgeFilters = {}) {
  return api<GraphEdge[]>(withParams("/api/graph/edges", filters));
}

export async function getEgoGraph(params: {
  node: string;
  depth?: number;
  direction?: GraphDirection;
}) {
  return api<GraphSlice>(withParams("/api/graph/ego", {
    node: params.node,
    depth: params.depth ?? 2,
    direction: params.direction ?? "both",
  }));
}

export async function getClusterGraph(params: { tag?: string; domain?: string }) {
  return api<GraphSlice>(withParams("/api/graph/cluster", params));
}
