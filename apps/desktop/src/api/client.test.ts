import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const commands = vi.hoisted(() => ({
  responses: {} as Record<string, unknown>,
}));

vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(async (cmd: string) => commands.responses[cmd] ?? null),
}));

vi.mock("@tauri-apps/api/event", () => ({
  // Never fires: these tests drive readiness through the polled commands.
  listen: vi.fn(async () => () => {}),
}));

import { SidecarStartError, getApiBase } from "./client";

describe("getApiBase under Tauri", () => {
  beforeEach(() => {
    vi.stubGlobal("window", { __TAURI_INTERNALS__: {} });
    commands.responses = {};
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reports the shell's start error at once and retries on the next call", async () => {
    commands.responses = { get_sidecar_error: "sidecar binary not found at C:\\x" };
    const failed = getApiBase();
    await expect(failed).rejects.toBeInstanceOf(SidecarStartError);
    await expect(failed).rejects.toThrow("sidecar binary not found");

    // The failure is not cached: once the shell has a port, the next call uses it.
    commands.responses = { get_api_port: 43210 };
    await expect(getApiBase()).resolves.toBe("http://127.0.0.1:43210");
  });
});
