// @vitest-environment jsdom
import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("../../api/app", async () => {
  return {
    getConfig: vi.fn(),
    postConfig: vi.fn(),
    testAssistantProvider: vi.fn(),
  };
});

import * as appApi from "../../api/app";
import AssistantSettingsRoute from "../../routes/assistant/settings";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function makeQC() {
  return new QueryClient({
    defaultOptions: { queries: { retry: 0, gcTime: 0 } },
  });
}

function renderRoute() {
  return render(
    <QueryClientProvider client={makeQC()}>
      <AssistantSettingsRoute />
    </QueryClientProvider>,
  );
}

const BASE_CONFIG = {
  logic_loom_root: "/root",
  akms_repo: "/repo/akms",
  akms_workspace: "/root/Logic-Loom/AKMS-Workspace",
  akms_global_vault: null,
  akms_global_vault_effective: "/repo/akms/Packages/Nodes_Vault",
  zsum_repo: "/repo/zsum",
  vault_path: null,
  cache_path: "/cache",
  graph_cache_path: "/cache/graph/graph.json",
  search_backend: "ripgrep" as const,
  vault_qmd_collection: null,
  zsum_config_path: null,
  zsum_config_path_effective: "/repo/zsum/zsum.config.yaml",
  zsum_python_cmd: "uv run zsum",
  zsum_cost_per_mtoken_in: 3,
  zsum_cost_per_mtoken_out: 15,
  zsum_tokens_per_card_in: 4000,
  zsum_tokens_per_card_out: 500,
  nodes_folders: [],
  nodes_editor: null,
  openalex_user_email: null,
  openalex_api_key: null,
  semantic_scholar_api_key: null,
  assistant_workspace: null,
  assistant_workspace_colocate: false,
  assistant_workspace_effective: "/root/Assistant-Workspace",
  anthropic_api_key: null,
  openai_api_key: null,
  google_api_key: null,
  llm_provider: null,
  learn_llm_provider: null,
  zsum_llm_provider: null,
  nodegen_llm_provider: null,
  learn_llm_custom_clis: {},
};

describe("Assistant Settings page (frontend)", () => {
  it("renders all required inputs (OpenAlex email/key, S2 key, workspace)", async () => {
    vi.mocked(appApi.getConfig).mockResolvedValue(BASE_CONFIG);

    renderRoute();

    // Wait for the page to load past the "Loading settings…" splash.
    await waitFor(() => {
      expect(screen.getByTestId("input-openalex-email")).toBeTruthy();
    });

    expect(screen.getByTestId("input-openalex-email")).toBeTruthy();
    expect(screen.getByTestId("input-openalex-key")).toBeTruthy();
    expect(screen.getByTestId("input-s2-key")).toBeTruthy();
    expect(screen.getByTestId("input-assistant-workspace")).toBeTruthy();
    expect(screen.getByTestId("input-colocate")).toBeTruthy();

    // The "Test connection" buttons are present for each provider.
    expect(screen.getByTestId("test-connection-openalex")).toBeTruthy();
    expect(screen.getByTestId("test-connection-s2")).toBeTruthy();
  });

  it("'Test connection' surfaces the {ok, latency_ms, quota_remaining} payload", async () => {
    vi.mocked(appApi.getConfig).mockResolvedValue(BASE_CONFIG);
    vi.mocked(appApi.testAssistantProvider).mockResolvedValue({
      ok: true,
      latency_ms: 123,
      quota_remaining: 9000,
      detail: null,
    });

    renderRoute();

    const testButton = await waitFor(() =>
      screen.getByTestId("test-connection-openalex"),
    );
    fireEvent.click(testButton);

    await waitFor(() => {
      expect(screen.getByTestId("test-connection-result")).toBeTruthy();
    });

    expect(screen.getByTestId("test-connection-ok").textContent).toBe("yes");
    expect(screen.getByTestId("test-connection-latency").textContent).toBe("123");
    expect(screen.getByTestId("test-connection-quota").textContent).toBe("9000");
    expect(appApi.testAssistantProvider).toHaveBeenCalledWith("openalex");
  });
});
