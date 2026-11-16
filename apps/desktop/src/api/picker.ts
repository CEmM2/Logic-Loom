/**
 * Typed wrappers for the AKMS batch picker, mounted at ``/api/picker``.
 *
 * Path note: AKMS routes are themselves under ``/api/...``, so calls land at
 * ``/api/picker/api/...`` (the doubled prefix is intentional).
 *
 * The AKMS server returns plain arrays for list endpoints (not wrapped objects).
 */

import { api } from "./client";

// ── Types ────────────────────────────────────────────────────────────────────

export type Paper = {
  citekey: string;
  title?: string | null;
  year?: number | null;
  authors: string[];
  collections: string[];
  item_type?: string | null;
  has_pdf?: boolean;
  assigned_to: string[];
  abstract?: string | null;
  doi?: string | null;
};

export type FilterSpec = {
  q?: string;
  collection?: string;
  year_from?: number;
  year_to?: number;
  item_type?: string;
  only_with_pdf?: boolean;
  suggest_for?: string;
  limit?: number;
};

/** Batch card returned by AKMS list_batches. */
export type Batch = {
  id: string;
  title: string;
  round?: number;
  round_title?: string;
  node_count?: number;
  pdf_slug?: string;
  /** Number of papers currently assigned to this batch. */
  n_assigned: number;
  has_notebook?: boolean;
  notebook_id?: string;
  n_uploaded?: number;
  // Legacy aliases kept for component compatibility:
  paper_count: number;   // alias for n_assigned
  citekeys: string[];
};

export type SavedQuery = {
  name: string;
  spec: FilterSpec;
  count?: number;
};

export type Collection = {
  name: string;
  paper_count: number;
  parent_name?: string;
};

const ROOT = "/api/picker";

// ── Reads ────────────────────────────────────────────────────────────────────

export async function listBatches(): Promise<Batch[]> {
  const data = await api<Batch[]>(`${ROOT}/api/batches`);
  return (data ?? []).map((b) => ({
    ...b,
    paper_count: b.n_assigned ?? 0,
    citekeys: [],
  }));
}

export async function getBatch(id: string): Promise<{
  batch: Batch;
  papers: Paper[];
  unknown_citekeys: string[];
}> {
  const data = await api<{
    batch: Batch;
    assignment: {
      papers: string[];
      nlm_notebook_id?: string;
      uploaded_papers?: string[];
    };
    papers: Paper[];
    unknown_citekeys: string[];
  }>(`${ROOT}/api/batches/${encodeURIComponent(id)}`);
  const a = data.assignment;
  return {
    batch: {
      ...data.batch,
      paper_count: a?.papers?.length ?? 0,
      citekeys: a?.papers ?? [],
      notebook_id: a?.nlm_notebook_id || data.batch.notebook_id || undefined,
      has_notebook: !!(a?.nlm_notebook_id || data.batch.notebook_id),
      n_uploaded: a?.uploaded_papers?.length ?? data.batch.n_uploaded ?? 0,
    },
    papers: data.papers,
    unknown_citekeys: data.unknown_citekeys,
  };
}

export async function listPapers(spec: FilterSpec): Promise<{
  results: Paper[];
  total: number;
  limit: number;
}> {
  const params = new URLSearchParams();
  Object.entries(spec).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
  });
  return api(`${ROOT}/api/papers?${params}`);
}

export async function listCollections(): Promise<Collection[]> {
  const data = await api<Collection[]>(`${ROOT}/api/collections`);
  return data ?? [];
}

export async function listSavedQueries(): Promise<SavedQuery[]> {
  const data = await api<SavedQuery[]>(`${ROOT}/api/saved_queries`);
  return data ?? [];
}

// ── Writes ───────────────────────────────────────────────────────────────────

export async function addPapers(batchId: string, citekeys: string[]) {
  return api(`${ROOT}/api/batches/${encodeURIComponent(batchId)}/papers/add`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ citekeys }),
  });
}

export async function removePapers(batchId: string, citekeys: string[]) {
  return api(`${ROOT}/api/batches/${encodeURIComponent(batchId)}/papers/remove`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ citekeys }),
  });
}

export async function movePapers(
  fromBatchId: string,
  toBatchId: string,
  citekeys: string[],
) {
  return api(`${ROOT}/api/batches/${encodeURIComponent(fromBatchId)}/papers/move`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ to_batch_id: toBatchId, citekeys }),
  });
}

export async function saveQuery(name: string, spec: FilterSpec) {
  return api(`${ROOT}/api/saved_queries/${encodeURIComponent(name)}`, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ spec }),
  });
}

export async function deleteSavedQuery(name: string) {
  return api(`${ROOT}/api/saved_queries/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
}

export async function createNotebook(
  batchId: string,
  options: { upload?: boolean; wait?: boolean } = {},
) {
  return api(`${ROOT}/api/batches/${encodeURIComponent(batchId)}/create_notebook`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(options),
  });
}
