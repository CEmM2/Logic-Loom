// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor, within, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

/**
 * AKMS Settings UI: global "LLM provider" section + per-consumer overrides.
 *
 * Promotes the original Learn-only provider section to a GLOBAL section (bound to
 * `llm_provider`) with optional per-consumer override rows (`zsum_llm_provider`,
 * `nodegen_llm_provider`, Learn) that default to "use global" (field is `None`).
 * Save -> auto-reload via the unified `POST /api/llm/providers/reload`, reusing
 * the Learn reload flow + the unavailable-provider warning banner.
 *
 * Strategy: mock the API client modules so the section's react-query hooks
 * resolve deterministically with no backend (mirrors the repo's vi.mock +
 * QueryClientProvider + fireEvent pattern, e.g. __tests__/prompt_library_page.test.tsx).
 * ClaudeAssetsPane is stubbed out — its own queries are irrelevant here.
 */

vi.mock("../../api/app", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/app")>();
  return { ...actual, getConfig: vi.fn(), postConfig: vi.fn() };
});

vi.mock("../../api/llm", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/llm")>();
  return { ...actual, getLlmProviders: vi.fn(), reloadLlmProviders: vi.fn() };
});

vi.mock("../../components/akms/ClaudeAssetsPane", () => ({
  ClaudeAssetsPane: () => null,
}));

import * as appApi from "../../api/app";
import * as llmApi from "../../api/llm";
import AkmsSettingsRoute from "./settings";

const getConfig = vi.mocked(appApi.getConfig);
const postConfig = vi.mocked(appApi.postConfig);
const getLlmProviders = vi.mocked(llmApi.getLlmProviders);
const reloadLlmProviders = vi.mocked(llmApi.reloadLlmProviders);

// Provider rows: claude-cli available, akms (API) unavailable (no key).
const PROVIDERS = [
  { name: "akms", label: "API (LiteLLM)", route: "api" as const, available: false, detail: "no API key set" },
  { name: "claude-cli", label: "claude", route: "cli" as const, available: true, detail: "claude on PATH" },
  { name: "codex-cli", label: "codex", route: "cli" as const, available: true, detail: "codex on PATH" },
];

function baseConfig(overrides: Record<string, unknown> = {}) {
  return {
    akms_workspace: "/tmp/ws",
    anthropic_api_key: null,
    openai_api_key: null,
    google_api_key: null,
    llm_provider: null,
    learn_llm_provider: null,
    zsum_llm_provider: null,
    nodegen_llm_provider: null,
    learn_llm_custom_clis: { copilot: "", antigravity: "" },
    ...overrides,
  } as unknown as appApi.AppConfig;
}

function providersPayload(
  effective: Record<"learn" | "zsum" | "nodegen", string | null> = {
    learn: null,
    zsum: null,
    nodegen: null,
  },
) {
  return { providers: PROVIDERS, selected: effective.learn, effective } as llmApi.LlmProviders;
}

function reloadResult() {
  return {
    reloaded: true,
    providers: PROVIDERS,
    selected: null,
    warnings: [],
    injected: [],
    registered: [],
    learn: [],
    zsum: [],
    nodegen: [],
    effective: { learn: null, zsum: null, nodegen: null },
  } as llmApi.LlmProvidersReload;
}

function renderRoute() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: 0 } } });
  return render(
    <QueryClientProvider client={qc}>
      <AkmsSettingsRoute />
    </QueryClientProvider>,
  );
}

/**
 * Scope queries to the LLM-provider <section> — the route also renders the
 * AKMS-workspace section (its own "Save" button) so global role queries would
 * be ambiguous.
 */
async function llmSection() {
  const header = await screen.findByText("LLM provider");
  const section = header.closest("section");
  if (!section) throw new Error("LLM provider section not found");
  return within(section as HTMLElement);
}

afterEach(cleanup);

describe("AKMS Settings: global LLM provider + per-consumer overrides", () => {
  beforeEach(() => {
    getConfig.mockReset().mockResolvedValue(baseConfig());
    postConfig
      .mockReset()
      .mockResolvedValue({ status: "ok", path: "x", applied: {} });
    getLlmProviders.mockReset().mockResolvedValue(providersPayload());
    reloadLlmProviders.mockReset().mockResolvedValue(reloadResult());
  });

  it("renders the global LLM-provider chooser + per-consumer override rows", async () => {
    renderRoute();

    // Section header + the global chooser row.
    expect(await screen.findByText("LLM provider")).toBeTruthy();
    expect(screen.getByText("Global provider")).toBeTruthy();

    // One override row per consumer (Learn / zsum / node-gen), labelled.
    expect(screen.getByLabelText("Learn override")).toBeTruthy();
    expect(screen.getByLabelText("zsum override")).toBeTruthy();
    expect(screen.getByLabelText("node-gen override")).toBeTruthy();

    // 1 global select + 3 override selects = 4 provider selects.
    expect(screen.getAllByRole("combobox").length).toBe(4);
  });

  it("defaults each per-consumer override to 'use global' when unset", async () => {
    renderRoute();
    await screen.findByLabelText("Learn override");

    // Each override select has its "Use global" sentinel option selected.
    const useGlobal = screen.getAllByRole("option", {
      name: "Use global",
    }) as HTMLOptionElement[];
    expect(useGlobal.length).toBe(3); // learn + zsum + nodegen
    expect(useGlobal.every((o) => o.selected)).toBe(true);

    // The global chooser defaults to "Auto" (no explicit global choice).
    const autoOpt = screen.getByRole("option", {
      name: /^Auto/,
    }) as HTMLOptionElement;
    expect(autoOpt.selected).toBe(true);
  });

  it("reflects each consumer's effective choice from the effective map", async () => {
    getLlmProviders.mockResolvedValue(
      providersPayload({ learn: "claude-cli", zsum: "codex-cli", nodegen: null }),
    );
    renderRoute();
    await screen.findByLabelText("Learn override");

    // Effective labels surface the resolved per-consumer choice.
    const effectives = screen
      .getAllByText(/^effective:/)
      .map((el) => el.textContent);
    expect(effectives.some((t) => t?.includes("claude-cli"))).toBe(true);
    expect(effectives.some((t) => t?.includes("codex-cli"))).toBe(true);
    // nodegen had no choice → "auto".
    expect(effectives.some((t) => t?.includes("auto"))).toBe(true);
  });

  it("Save persists the choices and triggers the unified providers reload", async () => {
    renderRoute();
    const llm = await llmSection();

    // Drive the global chooser (first combobox) to an available provider.
    const selects = llm.getAllByRole("combobox") as HTMLSelectElement[];
    fireEvent.change(selects[0], { target: { value: "claude-cli" } });

    const saveBtn = llm.getByRole("button", { name: "Save" }) as HTMLButtonElement;
    await waitFor(() => expect(saveBtn.disabled).toBe(false));
    fireEvent.click(saveBtn);

    // Save -> postConfig(global choice) -> auto-reload via the unified endpoint.
    await waitFor(() => expect(postConfig).toHaveBeenCalledTimes(1));
    expect(postConfig).toHaveBeenCalledWith(
      expect.objectContaining({ llm_provider: "claude-cli" }),
    );
    await waitFor(() => expect(reloadLlmProviders).toHaveBeenCalledTimes(1));
  });

  it("shows the unavailable-provider warning banner (reuses the Learn provider UX)", async () => {
    renderRoute();
    const selects = (await screen.findAllByRole(
      "combobox",
    )) as HTMLSelectElement[];

    // Choose the unavailable API provider as the global → banner appears.
    fireEvent.change(selects[0], { target: { value: "akms" } });

    const banner = await screen.findByText(/isn’t ready/);
    expect(banner.textContent).toContain("akms");
    expect(banner.textContent).toContain("no API key set");
  });
});
