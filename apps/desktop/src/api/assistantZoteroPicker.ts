/**
 * Assistant Zotero picker API client.
 *
 * Wraps the three endpoints under ``/api/assistant/zotero/``:
 *
 * - GET  /search?q&collection&limit — hybrid Library + Already-processed search.
 * - GET  /preview/{citekey}         — drawer payload.
 * - POST /reindex                   — force BBT rebuild.
 *
 * Backend file: services/logic_loom_api/src/logic_loom_api/routes/assistant_zotero_picker.py.
 */
import { api } from "./client";

export type PickerSection = "library" | "card";

export type PickerHit = {
  citekey: string;
  title: string;
  authors: string[];
  year: number | null;
  venue: string;
  abstract: string;
  doi: string;
  has_pdf: boolean;
  section: PickerSection;
  card_path: string | null;
};

export type PickerSearchEnvelope = {
  items: PickerHit[];
  library_count: number;
  card_count: number;
  library_reason: string | null;
  bbt_path: string | null;
};

export type PickerPreview = {
  citekey: string;
  title: string;
  authors: string[];
  year: number | null;
  venue: string;
  abstract: string;
  doi: string;
  url: string;
  has_pdf: boolean;
  card_path: string | null;
  card_body: string | null;
  library_reason: string | null;
};

export type SearchPickerArgs = {
  q?: string;
  collection?: string | null;
  limit?: number;
};

export async function searchPicker(
  args: SearchPickerArgs = {},
): Promise<PickerSearchEnvelope> {
  const params = new URLSearchParams();
  if (args.q) params.set("q", args.q);
  if (args.collection) params.set("collection", args.collection);
  if (args.limit !== undefined) params.set("limit", String(args.limit));
  const qs = params.toString();
  const url = `/api/assistant/zotero/search${qs ? `?${qs}` : ""}`;
  return api<PickerSearchEnvelope>(url);
}

export async function getPickerPreview(citekey: string): Promise<PickerPreview> {
  return api<PickerPreview>(
    `/api/assistant/zotero/preview/${encodeURIComponent(citekey)}`,
  );
}

export async function reindexPickerLibrary(): Promise<{
  rebuilt: boolean;
  count?: number;
  reason?: string | null;
  bbt_path?: string | null;
}> {
  return api<{
    rebuilt: boolean;
    count?: number;
    reason?: string | null;
    bbt_path?: string | null;
  }>("/api/assistant/zotero/reindex", { method: "POST" });
}
