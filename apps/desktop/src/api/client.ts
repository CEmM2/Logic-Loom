/**
 * API client base resolution.
 *
 * The Tauri shell spawns logic-loom-api on a random port and emits `logic-loom://ready`
 * with the chosen port. Three sources, in order:
 *   1. The Tauri command `get_api_port` — returns Some(port) once the shell
 *      has captured the handshake. Polled with a short backoff.
 *   2. The `logic-loom://ready` event — handles the case where readiness lands
 *      after our first poll.
 *   3. `import.meta.env.VITE_LOGIC_LOOM_API_BASE` — dev-only override (running
 *      `vite` outside of Tauri, e.g. for component tests).
 *
 * Every request also carries the per-launch API token the shell generated
 * (`get_api_token`; `VITE_LOGIC_LOOM_API_TOKEN` when running outside Tauri
 * against a sidecar started with `LOGIC_LOOM_API_TOKEN`). Use `api()` or
 * `apiFetch()` for requests and `apiUrl()` for EventSource streams and links,
 * which cannot set headers. Never build an API URL from `getApiBase()` alone.
 */

import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

const DEV_OVERRIDE = (import.meta.env.VITE_LOGIC_LOOM_API_BASE ?? "").trim() || null;
const DEV_TOKEN = (import.meta.env.VITE_LOGIC_LOOM_API_TOKEN ?? "").trim() || null;

/** Header and query-parameter names; contract with `logic_loom_api/auth.py`. */
export const TOKEN_HEADER = "X-Logic-Loom-Token";
export const TOKEN_QUERY_PARAM = "llt";
const STARTUP_TIMEOUT_MS = 30_000;
const POLL_INTERVAL_MS = 200;

let cachedBase: string | null = null;
let pendingResolve: Promise<string> | null = null;
let cachedToken: string | null = null;

export function isTauriRuntime(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

async function pollPort(maxAttempts = 60, intervalMs = 200): Promise<number> {
  const attempts = Math.max(1, maxAttempts);
  for (let i = 0; i < attempts; i++) {
    const port = await invoke<number | null>("get_api_port").catch(() => null);
    if (typeof port === "number") return port;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  throw new Error(
    `logic-loom-api did not become ready within ${Math.ceil((attempts * intervalMs) / 1000)}s`,
  );
}

async function waitForFirstResolved<T>(promises: Promise<T>[]): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const errors: unknown[] = [];
    let remaining = promises.length;

    for (const promise of promises) {
      promise.then(resolve).catch((error) => {
        errors.push(error);
        remaining -= 1;
        if (remaining === 0) {
          const messages = errors
            .map((err) => (err instanceof Error ? err.message : String(err)))
            .join("; ");
          reject(new Error(messages || "logic-loom-api readiness checks failed"));
        }
      });
    }
  });
}

async function awaitReadyEvent(timeoutMs = STARTUP_TIMEOUT_MS): Promise<number> {
  return new Promise<number>((resolve, reject) => {
    let unlisten: (() => void) | null = null;
    const timer = setTimeout(() => {
      unlisten?.();
      reject(new Error(`logic-loom://ready not emitted within ${Math.ceil(timeoutMs / 1000)}s`));
    }, timeoutMs);
    listen<{ port: number }>("logic-loom://ready", (e) => {
      clearTimeout(timer);
      unlisten?.();
      resolve(e.payload.port);
    })
      .then((un) => {
        unlisten = un;
      })
      .catch(reject);
  });
}

export async function getApiBase(): Promise<string> {
  if (cachedBase) return cachedBase;
  if (DEV_OVERRIDE) {
    cachedBase = DEV_OVERRIDE;
    return DEV_OVERRIDE;
  }
  if (!isTauriRuntime()) {
    throw new Error(
      "Not running under Tauri and VITE_LOGIC_LOOM_API_BASE is unset. " +
        "Set it (e.g. http://localhost:17321) when running the dev server outside the Tauri shell.",
    );
  }
  if (!pendingResolve) {
    pendingResolve = (async () => {
      const port = await waitForFirstResolved([
        pollPort(Math.ceil(STARTUP_TIMEOUT_MS / POLL_INTERVAL_MS), POLL_INTERVAL_MS),
        awaitReadyEvent(STARTUP_TIMEOUT_MS),
      ]);
      // The bundled sidecar binds its auto-port socket on the IPv4 loopback
      // address. Use the same literal host here so the packaged macOS webview
      // does not trip over `localhost` resolving to `::1` first.
      const base = `http://127.0.0.1:${port}`;
      cachedBase = base;
      return base;
    })();
  }
  return pendingResolve;
}

/**
 * The per-launch API token, or `null` when none applies (a sidecar started
 * without `LOGIC_LOOM_API_TOKEN` for standalone debugging). Resolves after
 * `getApiBase()`, because the shell stores the token when it spawns the
 * sidecar, before the port is known.
 */
export async function getApiToken(): Promise<string | null> {
  if (cachedToken) return cachedToken;
  if (DEV_OVERRIDE || !isTauriRuntime()) return DEV_TOKEN;
  await getApiBase();
  const token = await invoke<string | null>("get_api_token").catch(() => null);
  if (token) cachedToken = token;
  return token ?? null;
}

/** `init` with the token header added (existing headers are kept). */
export async function withAuth(init?: RequestInit): Promise<RequestInit> {
  const token = await getApiToken();
  if (!token) return init ?? {};
  const headers = new Headers(init?.headers);
  headers.set(TOKEN_HEADER, token);
  return { ...init, headers };
}

/**
 * Absolute URL for `path` with the token as a query parameter. For
 * EventSource streams and `href`/`src` links, which cannot send headers.
 */
export async function apiUrl(path: string): Promise<string> {
  const base = await getApiBase();
  const url = new URL(`${base}${path}`);
  const token = await getApiToken();
  if (token) url.searchParams.set(TOKEN_QUERY_PARAM, token);
  return url.toString();
}

/** Raw `fetch` against the API with the token attached (non-JSON bodies). */
export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const base = await getApiBase();
  return fetch(`${base}${path}`, await withAuth(init));
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await apiFetch(path, init);
  if (!res.ok) {
    let detail: unknown = null;
    try {
      detail = await res.json();
    } catch {
      /* leave null */
    }
    throw new ApiError(res.status, `HTTP ${res.status} on ${path}`, detail);
  }
  return res.json() as Promise<T>;
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly detail: unknown = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
