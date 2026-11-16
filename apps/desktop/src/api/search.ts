import { api } from "./client";

export type SearchHit = {
  path: string;
  line: number | null;
  snippet: string;
  score: number | null;
};

export type SearchBackend = "ripgrep" | "qmd";

export async function searchVault(
  q: string,
  backend?: SearchBackend,
  limit = 200,
): Promise<SearchHit[]> {
  const params = new URLSearchParams({ q, limit: String(limit) });
  if (backend) params.set("backend", backend);
  return api<SearchHit[]>(`/api/vault/search?${params}`);
}
