// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

/**
 * Settings → AKMS → Claude Assets pane.
 *
 * Covers:
 *   1. Status pill reflects installed / uninstalled.
 *   2. Reset triggers a confirmation modal that mentions the backup folder.
 */

// ── Mocks ────────────────────────────────────────────────────────────────────

vi.mock("../api/claudeAssets", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/claudeAssets")>();
  return {
    ...actual,
    getClaudeAssetsStatus: vi.fn(),
    installClaudeAssets: () => new Promise(() => {}),
    materializeRuntime: () => new Promise(() => {}),
    resetClaudeAssetFile: () => new Promise(() => {}),
  };
});

import { getClaudeAssetsStatus } from "../api/claudeAssets";
import { ClaudeAssetsPane } from "../components/akms/ClaudeAssetsPane";

function makeQC() {
  return new QueryClient({ defaultOptions: { queries: { retry: 0 } } });
}

function renderPane() {
  return render(
    <QueryClientProvider client={makeQC()}>
      <ClaudeAssetsPane />
    </QueryClientProvider>,
  );
}

describe("Claude Assets pane", () => {
  beforeEach(() => {
    (getClaudeAssetsStatus as unknown as ReturnType<typeof vi.fn>).mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it("pill reflects installed state", async () => {
    (getClaudeAssetsStatus as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      installed: true,
      path: "/tmp/akms_workspace/claude-assets",
      manifest: { entries: [{ path: "CLAUDE.md" }] },
    });

    renderPane();
    await waitFor(() => {
      expect(screen.getByTestId("claude-assets-status-pill").textContent).toMatch(/Installed/);
    });
  });

  it("pill reflects uninstalled state", async () => {
    (getClaudeAssetsStatus as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      installed: false,
      path: "/tmp/akms_workspace/claude-assets",
      manifest: null,
    });

    renderPane();
    await waitFor(() => {
      expect(screen.getByTestId("claude-assets-status-pill").textContent).toMatch(/Uninstalled/);
    });
  });

  it("clicking Reset all surfaces backup folder name", async () => {
    (getClaudeAssetsStatus as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      installed: true,
      path: "/tmp/akms_workspace/claude-assets",
      manifest: null,
    });

    renderPane();
    // Wait for the status pill to flip to Installed — confirms the
    // useQuery resolved so the Reset all button is enabled.
    await waitFor(() => {
      expect(screen.getByTestId("claude-assets-status-pill").textContent).toMatch(/Installed/);
    });

    const resetBtn = screen.getByRole("button", { name: /Reset all/i });
    expect((resetBtn as HTMLButtonElement).disabled).toBe(false);

    fireEvent.click(resetBtn);

    // The confirmation modal opens.
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toBeTruthy();

    // It explicitly names the backup folder so the user knows
    // their edits are recoverable.
    expect(dialog.textContent).toMatch(/claude-assets\.backup/);
    expect(screen.getByTestId("reset-backup-path").textContent).toMatch(/\.backup/);
  });
});
