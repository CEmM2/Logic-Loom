import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// The client reads the dev overrides at module load, so each test stubs the
// env and re-imports the module fresh.
async function loadClient() {
  vi.resetModules();
  return import("../api/client");
}

const fetchMock = vi.fn(async () => new Response("{}", { status: 200 }));

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockClear();
  vi.stubEnv("VITE_LOGIC_LOOM_API_BASE", "http://127.0.0.1:17321");
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("local API token", () => {
  it("sends the token header on api() calls and keeps caller headers", async () => {
    vi.stubEnv("VITE_LOGIC_LOOM_API_TOKEN", "tok123");
    const { api, TOKEN_HEADER } = await loadClient();
    await api("/api/app/config", { headers: { "Content-Type": "application/json" } });
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:17321/api/app/config");
    const headers = new Headers(init.headers);
    expect(headers.get(TOKEN_HEADER)).toBe("tok123");
    expect(headers.get("Content-Type")).toBe("application/json");
  });

  it("puts the token in the query string for stream and link URLs", async () => {
    vi.stubEnv("VITE_LOGIC_LOOM_API_TOKEN", "tok123");
    const { apiUrl } = await loadClient();
    expect(await apiUrl("/api/nodes/promote/stream?source=a")).toBe(
      "http://127.0.0.1:17321/api/nodes/promote/stream?source=a&llt=tok123",
    );
  });

  it("sends no token when none is configured", async () => {
    vi.stubEnv("VITE_LOGIC_LOOM_API_TOKEN", "");
    const { api, apiUrl, TOKEN_HEADER } = await loadClient();
    await api("/api/app/health");
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(new Headers(init.headers).has(TOKEN_HEADER)).toBe(false);
    expect(await apiUrl("/api/runs/x/stream")).toBe("http://127.0.0.1:17321/api/runs/x/stream");
  });
});
