/**
 * Unified host-LLM provider client — `/api/llm/*`.
 *
 * Generalises the Learn-only `/api/learn/providers` chooser to ALL consumers
 * (learn / zsum / nodegen). The provider availability source is the SAME as
 * `GET /api/learn/providers`; this endpoint is the superset that also
 * carries the per-consumer `effective` map (see routes/llm.py).
 *
 * Conventions match sibling clients (api/learn.ts, api/graph.ts): hand-authored
 * types, no default export, `api<T>` for JSON endpoints.
 */

import { api } from "./client";
import type { LearnProvider } from "./learn";

// Re-export the already-imported Learn provider row shape
// (`{name,label,route,available,detail}`) so the unified client doesn't
// duplicate it — the backend serves the identical row from `discover_providers`.
export type { LearnProvider };

/** Host-LLM consumers that carry a per-consumer effective choice. */
export type LlmConsumer = "learn" | "zsum" | "nodegen";

/**
 * The effective (resolved) provider label per consumer. `null` means
 * auto-resolve (no explicit choice anywhere in the precedence chain:
 * per-consumer override → global `llm_provider` → legacy `learn_llm_provider`).
 */
export type LlmEffective = Record<LlmConsumer, string | null>;

/**
 * `GET /api/llm/providers` — read-only discovery for the Settings chooser.
 *
 *   providers — availability rows (same source as `/api/learn/providers`).
 *   selected  — convenience mirror of `effective.learn` (the effective Learn choice).
 *   effective — per-consumer resolved choice map (`learn` / `zsum` / `nodegen`).
 */
export type LlmProviders = {
  providers: LearnProvider[];
  selected: string | null;
  effective: LlmEffective;
};

export async function getLlmProviders() {
  return api<LlmProviders>("/api/llm/providers");
}

/**
 * `POST /api/llm/providers/reload` — re-apply host-LLM wiring for ALL consumers
 * in-process (force-inject keys + re-register CLI providers), so a just-saved
 * key / provider choice / custom CLI goes live without restarting the app.
 *
 * `injected`/`registered` are the shared wiring summary (keys live + CLI
 * providers registered); `learn`/`zsum`/`nodegen` are the per-consumer rewire
 * slots (some may be `[]` by design — node-gen/zsum resolve per-dispatch).
 * Degrade-gracefully: never 503 — a rewire failure surfaces in `warnings`.
 */
export type LlmProvidersReload = LlmProviders & {
  reloaded: boolean;
  warnings: string[];
  injected: string[];
  registered: string[];
  learn: string[];
  zsum: string[];
  nodegen: string[];
};

export async function reloadLlmProviders() {
  return api<LlmProvidersReload>("/api/llm/providers/reload", {
    method: "POST",
  });
}
