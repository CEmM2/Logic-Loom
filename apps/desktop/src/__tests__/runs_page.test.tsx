// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  cleanup,
  within,
  act,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

/**
 * Runs page + comparison view.
 *
 * Covers:
 *   1. Selecting two runs and clicking Compare opens the comparison view.
 *   2. Prompt diff highlights changed slots (rendered side-by-side — we
 *      surface the prompt_hash on each pane so
 *      "prompts-only-different" reruns are visually obvious).
 *
 * Cases:
 *   A. List + select round trip.
 *   B. SSE log tail — EventSource stub: data events append to the
 *      log buffer; a status: "succeeded" event closes the handle.
 *   C. Rerun → GlobalRunStatus — clicking Rerun adds the
 *      new run id into the GlobalRunStatus store.
 *   D. Comparison view — checking two rows renders two
 *      side-by-side panels.
 */

vi.mock("../api/runs", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/runs")>();
  return {
    ...actual,
    listRuns: vi.fn(),
    getRun: vi.fn(),
    rerunRun: vi.fn(),
    openRunStream: vi.fn(),
  };
});

import * as runsApi from "../api/runs";
import type { RunRecord } from "../api/runs";
import { RunsPage } from "../components/runs/RunsPage";
import { useGlobalRunStatusStore } from "../components/GlobalRunStatus";

// ── EventSource stub ───────────────────────────────────────────────────────

interface ListenerMap {
  [name: string]: ((e: MessageEvent) => void) | undefined;
}

class MockEventSource {
  url: string;
  readyState = 0;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  close = vi.fn(() => {
    this.readyState = 2;
  });
  private listeners: ListenerMap = {};
  static instances: MockEventSource[] = [];

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  addEventListener(name: string, fn: (e: MessageEvent) => void) {
    this.listeners[name] = fn;
  }
  removeEventListener(name: string) {
    delete this.listeners[name];
  }
  emitMessage(data: string) {
    this.onmessage?.(new MessageEvent("message", { data }));
  }
  emitNamed(name: string, data: string) {
    const fn = this.listeners[name];
    fn?.(new MessageEvent(name, { data }));
  }
}

function makeQC() {
  return new QueryClient({ defaultOptions: { queries: { retry: 0 } } });
}

function renderPage() {
  return render(
    <MemoryRouter>
      <QueryClientProvider client={makeQC()}>
        <RunsPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

function makeRun(overrides: Partial<RunRecord> = {}): RunRecord {
  return {
    id: "run-001",
    stage: "workflow",
    status: "running",
    started_at: "2026-05-14T10:00:00Z",
    ended_at: null,
    exit_code: null,
    pid: 1234,
    log_path: "/tmp/runs/run-001.log",
    args: [],
    cwd: null,
    last_lines: ["seed-line-1", "seed-line-2"],
    kind: "workflow",
    batch_id: "batch-a",
    workflow_id: "akms.node_extraction",
    run_label: "Run 001",
    prompt_hash: "abcd1234",
    ...overrides,
  };
}

describe("Runs page + comparison view", () => {
  beforeEach(() => {
    (runsApi.listRuns as unknown as ReturnType<typeof vi.fn>).mockReset();
    (runsApi.getRun as unknown as ReturnType<typeof vi.fn>).mockReset();
    (runsApi.rerunRun as unknown as ReturnType<typeof vi.fn>).mockReset();
    (runsApi.openRunStream as unknown as ReturnType<typeof vi.fn>).mockReset();
    MockEventSource.instances = [];
    (globalThis as unknown as { EventSource: typeof MockEventSource }).EventSource =
      MockEventSource;
    useGlobalRunStatusStore.getState().clearAll();
  });

  afterEach(() => {
    cleanup();
    useGlobalRunStatusStore.getState().clearAll();
  });

  it("list runs, click a row, detail renders", async () => {
    const r1 = makeRun({ id: "run-001", run_label: "Run 001" });
    const r2 = makeRun({
      id: "run-002",
      run_label: "Run 002",
      workflow_id: "akms.summarize",
      status: "succeeded",
      prompt_hash: "ff00aa11",
      last_lines: ["done"],
    });
    (runsApi.listRuns as unknown as ReturnType<typeof vi.fn>).mockResolvedValue([
      r1,
      r2,
    ]);
    // openRunStream may be called for the default selection (r1 is
    // running) — return a fresh MockEventSource.
    (runsApi.openRunStream as unknown as ReturnType<typeof vi.fn>).mockImplementation(
      async () => new MockEventSource("test://stream"),
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("run-row-run-001")).toBeTruthy();
    });
    expect(screen.getByTestId("run-row-run-002")).toBeTruthy();

    // Default selection is the first row — detail shows its label.
    await waitFor(() => {
      const detail = screen.getByTestId("run-detail");
      expect(detail.textContent).toMatch(/Run 001/);
    });

    // Click into row 2 — detail swaps.
    fireEvent.click(screen.getByTestId("run-open-run-002"));
    await waitFor(() => {
      const detail = screen.getByTestId("run-detail");
      expect(detail.textContent).toMatch(/Run 002/);
    });
    // Status pill reflects the second run's terminal status.
    expect(
      within(screen.getByTestId("run-detail")).getByTestId(
        "run-status-succeeded",
      ),
    ).toBeTruthy();
  });

  it("EventSource appends data events and closes on terminal status", async () => {
    const r1 = makeRun({ id: "run-001", status: "running" });
    (runsApi.listRuns as unknown as ReturnType<typeof vi.fn>).mockResolvedValue([
      r1,
    ]);
    const sources: MockEventSource[] = [];
    (runsApi.openRunStream as unknown as ReturnType<typeof vi.fn>).mockImplementation(
      async () => {
        const es = new MockEventSource("test://stream/run-001");
        sources.push(es);
        return es;
      },
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("run-detail")).toBeTruthy();
    });
    await waitFor(() => {
      expect(sources.length).toBe(1);
    });
    const es = sources[0];

    // Emit two log lines via the default `data:` channel — they should
    // appear in the log tail.
    await act(async () => {
      es.emitMessage(JSON.stringify({ line: "hello world" }));
      es.emitMessage(JSON.stringify({ line: "second line" }));
    });

    await waitFor(() => {
      const tail = screen.getByTestId("run-log-tail");
      expect(tail.textContent).toMatch(/hello world/);
      expect(tail.textContent).toMatch(/second line/);
    });

    // Emit a terminal-status payload — page must close the handle.
    await act(async () => {
      es.emitMessage(
        JSON.stringify({ status: "succeeded", line: "terminal" }),
      );
    });

    await waitFor(() => {
      expect(es.close).toHaveBeenCalled();
    });
  });

  it("clicking Rerun adds the new run id to the store", async () => {
    const r1 = makeRun({ id: "run-001", status: "succeeded" });
    (runsApi.listRuns as unknown as ReturnType<typeof vi.fn>).mockResolvedValue([
      r1,
    ]);
    (runsApi.rerunRun as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      run_id: "run-001-rerun",
      status: "pending",
      kind: "workflow",
    });
    // r1 is terminal so no stream opens — no mock needed for openRunStream.

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("run-detail")).toBeTruthy();
    });

    expect(useGlobalRunStatusStore.getState().activeRuns).toHaveLength(0);

    fireEvent.click(screen.getByTestId("run-rerun-button"));

    await waitFor(() => {
      expect(runsApi.rerunRun).toHaveBeenCalledWith("run-001");
    });
    await waitFor(() => {
      const ids = useGlobalRunStatusStore
        .getState()
        .activeRuns.map((r) => r.id);
      expect(ids).toContain("run-001-rerun");
    });
  });

  it("checking two rows opens side-by-side panes", async () => {
    const r1 = makeRun({
      id: "run-001",
      prompt_hash: "aaaa1111",
      workflow_id: "akms.node_extraction",
    });
    const r2 = makeRun({
      id: "run-002",
      run_label: "Run 002",
      prompt_hash: "bbbb2222",
      workflow_id: "akms.summarize",
      status: "succeeded",
      last_lines: ["finished"],
    });
    (runsApi.listRuns as unknown as ReturnType<typeof vi.fn>).mockResolvedValue([
      r1,
      r2,
    ]);
    (runsApi.openRunStream as unknown as ReturnType<typeof vi.fn>).mockImplementation(
      async () => new MockEventSource("test://stream"),
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("run-row-run-001")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("run-select-run-001"));
    fireEvent.click(screen.getByTestId("run-select-run-002"));

    await waitFor(() => {
      expect(screen.getByTestId("run-comparison-view")).toBeTruthy();
    });
    const pane1 = screen.getByTestId("run-comparison-pane-run-001");
    const pane2 = screen.getByTestId("run-comparison-pane-run-002");
    expect(pane1).toBeTruthy();
    expect(pane2).toBeTruthy();

    // Distinct prompt-hashes render in each pane so prompt-only-different
    // reruns are obvious side by side.
    expect(
      screen.getByTestId("run-comparison-prompt-hash-run-001").textContent,
    ).toMatch(/aaaa1111/);
    expect(
      screen.getByTestId("run-comparison-prompt-hash-run-002").textContent,
    ).toMatch(/bbbb2222/);

    // The detail pane is hidden while comparing.
    expect(screen.queryByTestId("run-detail")).toBeNull();
  });
});
