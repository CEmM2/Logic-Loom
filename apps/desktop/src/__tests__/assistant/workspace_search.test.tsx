// @vitest-environment jsdom
/**
 * Frontend tests for the Assistant workspace search UI.
 *
 * Covers:
 *   1. Search returns results across all seven kinds.
 *
 * Additional coverage:
 *   - Keyboard shortcut (Cmd-K / Ctrl-K) opens the modal (via AppShell
 *     wiring; exercised by toggling the `open` prop on WorkspaceSearch
 *     directly + an AppShell integration test).
 *   - Filter state toggles per kind, scoping the results.
 *   - Empty state renders 'rebuild index' link when /index/status
 *     reports zero records.
 *   - Click on a result navigates via `onNavigate(target)` with the
 *     right per-kind route.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { AppShell } from "../../components/AppShell";
import {
  WorkspaceSearch,
  resolveNavigationTarget,
  type SearchResult,
} from "../../components/assistant/WorkspaceSearch";
import type { IndexStatus } from "../../api/assistantIndex";

// Mock the assistantIndex API client — the modal calls statusFn /
// rebuildFn / searchFn via prop injection, so the defaults read from
// this module. Tests pass explicit overrides for determinism. The
// default mock implementations return resolved promises so the modal's
// effects don't reject when no statusFn override is passed (e.g. the
// AppShell shortcut test mounts <WorkspaceSearch /> via the shell's
// default props).
vi.mock("../../api/assistantIndex", async () => {
  const actual = await vi.importActual<typeof import("../../api/assistantIndex")>(
    "../../api/assistantIndex",
  );
  const emptyStatus = {
    counts: {
      zotero: 0,
      abstract: 0,
      zsum_card: 0,
      zsum_digest: 0,
      assistant_output: 0,
      akms_node: 0,
      run_metadata: 0,
    },
    total: 0,
    last_rebuild_at: null,
  };
  return {
    ...actual,
    getIndexStatus: vi.fn().mockResolvedValue(emptyStatus),
    rebuildIndex: vi.fn().mockResolvedValue(emptyStatus),
  };
});

// Mock the AppShell's auxiliary components so we can render the
// shell in isolation. The shortcut wiring is the only behaviour
// under test for the AppShell case.
vi.mock("../../components/SectionSideNav", () => ({
  SectionSideNav: () => null,
}));
vi.mock("../../components/TopModeNav", () => ({
  TopModeNav: () => null,
}));
vi.mock("../../components/GlobalRunStatus", () => ({
  GlobalRunStatus: () => null,
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function makeStatus(total: number, overrides: Partial<IndexStatus> = {}): IndexStatus {
  const counts = {
    zotero: 0,
    abstract: 0,
    zsum_card: 0,
    zsum_digest: 0,
    assistant_output: 0,
    akms_node: 0,
    run_metadata: 0,
  };
  return {
    counts: { ...counts, ...(overrides.counts ?? {}) },
    total,
    last_rebuild_at: overrides.last_rebuild_at ?? null,
    ...overrides,
  };
}

function makeResult(
  kind: SearchResult["kind"],
  title: string,
  extras: Partial<SearchResult> = {},
): SearchResult {
  return {
    id: extras.id ?? `${kind}:${title}`,
    kind,
    title,
    snippet: extras.snippet,
    batch_id: extras.batch_id,
    workflow_id: extras.workflow_id,
    run_id: extras.run_id,
  };
}

describe("Workspace search UI", () => {
  it("does not render when closed", () => {
    const { container } = render(
      <WorkspaceSearch open={false} onClose={() => undefined} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders the modal with the seven kind filters when open", async () => {
    const statusFn = vi.fn().mockResolvedValue(makeStatus(0));
    render(
      <WorkspaceSearch open onClose={() => undefined} statusFn={statusFn} />,
    );

    // Seven kind filter buttons render in the modal's filter row.
    expect(await screen.findByRole("button", { name: "Zotero" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Abstracts" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Cards" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Digests" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Outputs" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Nodes" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Runs" })).toBeTruthy();
  });

  it("toggles filter state per kind (aria-pressed)", () => {
    const statusFn = vi.fn().mockResolvedValue(makeStatus(5));
    render(
      <WorkspaceSearch open onClose={() => undefined} statusFn={statusFn} />,
    );

    const zoteroBtn = screen.getByRole("button", { name: "Zotero" });
    const nodesBtn = screen.getByRole("button", { name: "Nodes" });
    expect(zoteroBtn.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(zoteroBtn);
    expect(zoteroBtn.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(nodesBtn);
    expect(nodesBtn.getAttribute("aria-pressed")).toBe("true");
    // Toggling off works too.
    fireEvent.click(zoteroBtn);
    expect(zoteroBtn.getAttribute("aria-pressed")).toBe("false");
  });

  it("renders 'rebuild index' link in empty state when status reports zero records", async () => {
    const statusFn = vi.fn().mockResolvedValue(makeStatus(0));
    const rebuildFn = vi.fn().mockResolvedValue(makeStatus(5));
    render(
      <WorkspaceSearch
        open
        onClose={() => undefined}
        statusFn={statusFn}
        rebuildFn={rebuildFn}
      />,
    );
    // Empty state with the rebuild affordance.
    const rebuildBtn = await screen.findByRole("button", {
      name: /rebuild index/i,
    });
    expect(rebuildBtn).toBeTruthy();
    fireEvent.click(rebuildBtn);
    await waitFor(() => expect(rebuildFn).toHaveBeenCalledTimes(1));
  });

  it("does not show the empty-state when status reports records exist", async () => {
    const statusFn = vi.fn().mockResolvedValue(makeStatus(3));
    render(
      <WorkspaceSearch open onClose={() => undefined} statusFn={statusFn} />,
    );
    // Wait for the status fetch to settle.
    await waitFor(() => expect(statusFn).toHaveBeenCalledTimes(1));
    // The rebuild button is not in the DOM when total > 0.
    expect(screen.queryByRole("button", { name: /rebuild index/i })).toBeNull();
  });

  it("returns results across all seven kinds", async () => {
    const statusFn = vi.fn().mockResolvedValue(makeStatus(7));
    const allKinds: Array<SearchResult["kind"]> = [
      "zotero",
      "abstract",
      "zsum_card",
      "zsum_digest",
      "assistant_output",
      "akms_node",
      "run_metadata",
    ];
    const searchFn = vi
      .fn()
      .mockResolvedValue(
        allKinds.map((k) => makeResult(k, `Hit-${k}`)),
      );

    render(
      <WorkspaceSearch
        open
        onClose={() => undefined}
        statusFn={statusFn}
        searchFn={searchFn}
      />,
    );

    const input = await screen.findByLabelText("Workspace search query");
    fireEvent.change(input, { target: { value: "alpha" } });

    // Wait past the debounce window.
    await waitFor(
      () => expect(searchFn).toHaveBeenCalled(),
      { timeout: 500 },
    );

    // Every kind's hit renders in the result list.
    for (const k of allKinds) {
      expect(await screen.findByText(`Hit-${k}`)).toBeTruthy();
    }
  });

  it("clicking a result calls onNavigate with the right per-kind path", async () => {
    const statusFn = vi.fn().mockResolvedValue(makeStatus(1));
    const searchFn = vi
      .fn()
      .mockResolvedValue([
        makeResult("akms_node", "Plasticity"),
      ]);
    const onNavigate = vi.fn();
    const onClose = vi.fn();

    render(
      <WorkspaceSearch
        open
        onClose={onClose}
        statusFn={statusFn}
        searchFn={searchFn}
        onNavigate={onNavigate}
      />,
    );

    const input = await screen.findByLabelText("Workspace search query");
    fireEvent.change(input, { target: { value: "plasticity" } });
    await waitFor(() => expect(searchFn).toHaveBeenCalled(), { timeout: 500 });

    const hit = await screen.findByText("Plasticity");
    fireEvent.click(hit);

    expect(onNavigate).toHaveBeenCalledTimes(1);
    const arg = onNavigate.mock.calls[0][0];
    expect(arg.path).toBe("/nodes");
    expect(arg.recordId).toBe("akms_node:Plasticity");
    // Clicking a result closes the modal.
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("filters results by selected kind", async () => {
    const statusFn = vi.fn().mockResolvedValue(makeStatus(2));
    const searchFn = vi.fn().mockResolvedValue([
      makeResult("zotero", "Z-Hit"),
      makeResult("akms_node", "N-Hit"),
    ]);
    render(
      <WorkspaceSearch
        open
        onClose={() => undefined}
        statusFn={statusFn}
        searchFn={searchFn}
      />,
    );

    const input = await screen.findByLabelText("Workspace search query");
    fireEvent.change(input, { target: { value: "alpha" } });
    await waitFor(() => expect(searchFn).toHaveBeenCalled(), { timeout: 500 });

    // Both hits visible before filtering.
    expect(await screen.findByText("Z-Hit")).toBeTruthy();
    expect(screen.getByText("N-Hit")).toBeTruthy();

    // Click "Zotero" filter — only Z-Hit should remain.
    fireEvent.click(screen.getByRole("button", { name: "Zotero" }));
    expect(screen.queryByText("N-Hit")).toBeNull();
    expect(screen.getByText("Z-Hit")).toBeTruthy();
  });
});

describe("resolveNavigationTarget", () => {
  it("maps zotero / abstract → /assistant/batches", () => {
    expect(resolveNavigationTarget(makeResult("zotero", "x")).path).toBe(
      "/assistant/batches",
    );
    expect(resolveNavigationTarget(makeResult("abstract", "x")).path).toBe(
      "/assistant/batches",
    );
  });

  it("maps zsum_card / zsum_digest → /assistant/discover", () => {
    expect(resolveNavigationTarget(makeResult("zsum_card", "x")).path).toBe(
      "/assistant/discover",
    );
    expect(resolveNavigationTarget(makeResult("zsum_digest", "x")).path).toBe(
      "/assistant/discover",
    );
  });

  it("maps assistant_output / run_metadata → /assistant/outputs", () => {
    expect(resolveNavigationTarget(makeResult("assistant_output", "x")).path).toBe(
      "/assistant/outputs",
    );
    expect(resolveNavigationTarget(makeResult("run_metadata", "x")).path).toBe(
      "/assistant/outputs",
    );
  });

  it("maps akms_node → /nodes", () => {
    expect(resolveNavigationTarget(makeResult("akms_node", "x")).path).toBe(
      "/nodes",
    );
  });
});

describe("AppShell — Cmd-K shortcut", () => {
  it("opens the workspace search modal on Ctrl-K", async () => {
    render(
      <MemoryRouter>
        <AppShell currentMode={"assistant" as never} onModeChange={() => undefined} />
      </MemoryRouter>,
    );
    // The modal isn't in the DOM before the shortcut fires.
    expect(screen.queryByRole("dialog", { name: /workspace search/i })).toBeNull();
    act(() => {
      const event = new KeyboardEvent("keydown", {
        key: "k",
        ctrlKey: true,
        bubbles: true,
      });
      document.dispatchEvent(event);
    });
    // Modal becomes visible.
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: /workspace search/i })).not.toBeNull();
    });
  });

  it("toggles the modal on repeated Cmd-K", async () => {
    render(
      <MemoryRouter>
        <AppShell currentMode={"assistant" as never} onModeChange={() => undefined} />
      </MemoryRouter>,
    );
    act(() => {
      const event = new KeyboardEvent("keydown", {
        key: "k",
        metaKey: true,
        bubbles: true,
      });
      document.dispatchEvent(event);
    });
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: /workspace search/i })).not.toBeNull();
    });
    act(() => {
      const event = new KeyboardEvent("keydown", {
        key: "k",
        metaKey: true,
        bubbles: true,
      });
      document.dispatchEvent(event);
    });
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: /workspace search/i })).toBeNull();
    });
  });
});
