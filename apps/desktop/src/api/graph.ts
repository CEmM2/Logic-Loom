import { api, apiFetch } from "./client";

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

/**
 * Split an SSE byte stream into `{event, data}` messages. Exported for tests.
 * Handles CRLF, multi-line `data:` fields and comments (`:` lines).
 */
export function parseSseChunk(buffer: string): { messages: { event: string; data: string }[]; rest: string } {
  const messages: { event: string; data: string }[] = [];
  const normalized = buffer.replace(/\r\n/g, "\n");
  const blocks = normalized.split("\n\n");
  const rest = blocks.pop() ?? "";
  for (const block of blocks) {
    let event = "message";
    const data: string[] = [];
    for (const line of block.split("\n")) {
      if (!line || line.startsWith(":")) continue;
      const idx = line.indexOf(":");
      const field = idx === -1 ? line : line.slice(0, idx);
      const value = idx === -1 ? "" : line.slice(idx + 1).replace(/^ /, "");
      if (field === "event") event = value;
      else if (field === "data") data.push(value);
    }
    if (data.length > 0) messages.push({ event, data: data.join("\n") });
  }
  return { messages, rest };
}

/**
 * Regenerate the graph with streamed progress.
 *
 * `POST /api/graph/refresh/stream` answers with Server-Sent Events. It used to
 * be a GET (EventSource only does GET), which let a plain link rebuild the
 * cache; the request is now a POST read with `fetch` and parsed here.
 */
export async function streamGraphRefresh(
  options: GraphRefreshOptions,
  onProgress: (progress: GraphRefreshProgress) => void,
  onDone: (status: GraphStatus) => void,
  onError: (message: string) => void,
  onReviewRequired?: (report: GraphIssueLog) => void,
): Promise<() => void> {
  const controller = new AbortController();
  let settled = false;

  const handle = (event: string, data: string) => {
    if (settled) return;
    try {
      if (event === "progress") {
        onProgress(JSON.parse(data) as GraphRefreshProgress);
        return;
      }
      if (event === "done") {
        settled = true;
        onDone(JSON.parse(data) as GraphStatus);
        return;
      }
      if (event === "review-required") {
        settled = true;
        const report = JSON.parse(data) as GraphIssueLog;
        if (onReviewRequired) onReviewRequired(report);
        else onError(`Graph source review required for ${report.issue_count} file(s).`);
        return;
      }
      if (event === "failed") {
        settled = true;
        let message = "Graph generation failed.";
        try {
          message = (JSON.parse(data) as { message?: string }).message ?? message;
        } catch {
          if (data) message = data;
        }
        onError(message);
      }
    } catch (error) {
      settled = true;
      onError(error instanceof Error ? error.message : String(error));
    }
  };

  const run = async () => {
    let res: Response;
    try {
      res = await apiFetch("/api/graph/refresh/stream", {
        method: "POST",
        headers: { "content-type": "application/json", accept: "text/event-stream" },
        body: JSON.stringify({ skip_invalid_files: Boolean(options.skipInvalidFiles) }),
        signal: controller.signal,
      });
    } catch (error) {
      if (!controller.signal.aborted && !settled) {
        settled = true;
        onError(error instanceof Error ? error.message : String(error));
      }
      return;
    }
    if (!res.ok || !res.body) {
      settled = true;
      onError(`Graph generation failed: HTTP ${res.status}`);
      return;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parsed = parseSseChunk(buffer);
        buffer = parsed.rest;
        for (const m of parsed.messages) handle(m.event, m.data);
        if (settled) break;
      }
    } catch (error) {
      if (controller.signal.aborted) return;
    }
    if (!settled && !controller.signal.aborted) {
      settled = true;
      onError("Graph generation stream disconnected.");
    }
  };
  void run();

  return () => {
    settled = true;
    controller.abort();
  };
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
