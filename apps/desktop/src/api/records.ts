import { api } from "./client";

// akms-records (EXPERIMENTAL upstream) — read-only browse of the stores listed
// in [[records_stores]]. Mirrors backend routes/records.py.

export type RecordsStoreSummary = {
  id: string;
  scope: string;
  root: string | null;
  exists: boolean;
  record_count: number | null;
  schema_count: number | null;
  replay_errors: number;
  error: { code: string; message: string } | null;
};

export type RecordsStatus = {
  available: boolean;
  package_installed: boolean;
  version: string | null;
  reason: string | null;
  experimental: boolean;
  stores: RecordsStoreSummary[];
};

export type RecordRow = {
  record_id: string;
  revision: number;
  schema_ref: string;
  summary: string;
  tags: string[];
  recorded_at: string;
  updated_at: string | null;
  origin: { kind: string; actor_id: string };
  sensitivity: string;
  review_state: string;
  lifecycle: string;
  superseded_by: string | null;
  promoted_to: { node_id: string; revision: number; path: string | null } | null;
  source_ref_count: number;
};

export type RecordFacets = {
  schema_ref: Record<string, number>;
  tags: Record<string, number>;
  lifecycle: Record<string, number>;
  review_state: Record<string, number>;
};

export type RecordsList = {
  records: RecordRow[];
  total: number;
  replay_errors: { record_id: string | null; code: string; message: string }[];
  facets: RecordFacets;
};

export type RecordFilters = {
  schema?: string;
  tag?: string;
  lifecycle?: string;
  review_state?: string;
  q?: string;
  limit?: number;
  offset?: number;
};

export type SourceRef = { kind: string; ref: string; note?: string };

export type RecordEnvelope = {
  record_id: string;
  revision: number;
  schema_ref: string;
  schema_sha256: string;
  scope: { kind: string; id: string };
  recorded_at: string;
  origin: { kind: string; actor_id: string };
  source_refs: SourceRef[];
  summary: string;
  tags: string[];
  sensitivity: string;
  review_state: string;
  lifecycle: string;
  payload: Record<string, unknown>;
};

export type RegisteredSchema = {
  schema_ref: string;
  schema_sha256: string;
  registered_at: string;
  registered_by: string;
  title: string | null;
  schema: Record<string, unknown>;
};

export type RecordsGraphNodeKind = "record" | "missing_record" | "akms_node" | "external";
export type RecordsGraphEdgeType =
  | "superseded_by"
  | "cites"
  | "references"
  | "promoted_to"
  | "source_ref";

export type RecordsGraphNode = {
  id: string;
  kind: RecordsGraphNodeKind;
  label: string;
  schema_ref?: string;
  lifecycle?: string;
  review_state?: string;
  tags?: string[];
  recorded_at?: string;
  ref_kind?: string;
};

export type RecordsGraphEdge = {
  id: string;
  source: string;
  target: string;
  type: RecordsGraphEdgeType;
  label: string;
  revision?: number | string | null;
};

export type RecordsGraph = {
  nodes: RecordsGraphNode[];
  edges: RecordsGraphEdge[];
  truncated: boolean;
  total_nodes: number;
  focus: string | null;
};

export type RecordDetail = {
  record: RecordEnvelope;
  state: {
    updated_at: string;
    head_sha256: string;
    superseded_by: string | null;
    reason: string | null;
    events: number;
    promoted_to?: { node_id: string; revision: number; path: string | null };
  };
  history: Record<string, unknown>[];
  schema: RegisteredSchema | null;
  schema_error: { code: string; message: string } | null;
  neighbours: RecordsGraph;
};

export type SchemaEntry = Partial<RegisteredSchema> & {
  schema_ref: string;
  record_count: number;
  error: { code: string; message: string } | null;
};

export type GraphOptions = {
  focus?: string;
  depth?: number;
  include_external?: boolean;
  schema?: string;
  limit?: number;
};

function query(params: Record<string, string | number | boolean | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === "") continue;
    search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

const store = (id: string) => `/api/records/stores/${encodeURIComponent(id)}`;

export async function getRecordsStatus() {
  return api<RecordsStatus>("/api/records/status");
}

export async function listRecords(storeId: string, filters: RecordFilters) {
  return api<RecordsList>(`${store(storeId)}/records${query(filters)}`);
}

export async function getRecord(storeId: string, recordId: string) {
  return api<RecordDetail>(`${store(storeId)}/records/${encodeURIComponent(recordId)}`);
}

export async function listRecordSchemas(storeId: string) {
  return api<{ schemas: SchemaEntry[] }>(`${store(storeId)}/schemas`);
}

export async function getRecordsGraph(storeId: string, options: GraphOptions) {
  return api<RecordsGraph>(`${store(storeId)}/graph${query(options)}`);
}
