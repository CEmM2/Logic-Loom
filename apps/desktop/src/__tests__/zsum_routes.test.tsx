// @vitest-environment jsdom
import { describe, it, expect, vi, beforeAll } from "vitest";
import { render } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// ── jsdom shims ───────────────────────────────────────────────────────────────
// react-resizable-panels uses ResizeObserver which is not in jsdom.
// Providing a no-op stub lets the panel Group mount without crashing.
beforeAll(() => {
  if (typeof window !== "undefined" && !("ResizeObserver" in window)) {
    // @ts-expect-error -- jsdom shim
    window.ResizeObserver = class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  }
});

// ── API mocks — prevent real network calls in unit tests ──────────────────────
// The pipeline and vault components call queryFns that invoke getApiBase();
// without the Tauri runtime or VITE_LOGIC_LOOM_API_BASE, that throws.
// We use importOriginal so vitest exports all real type definitions / constants
// but replaces every async function with a never-resolving promise, leaving
// components in their loading state (which renders unconditionally).

vi.mock("../api/app", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/app")>();
  return {
    ...actual,
    getConfig: () => new Promise(() => {}),
    pathExists: () => new Promise(() => {}),
    postConfig: () => new Promise(() => {}),
    testZsumEnvironment: () => new Promise(() => {}),
  };
});

vi.mock("../api/zsum", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/zsum")>();
  return {
    ...actual,
    listRuns: () => new Promise(() => {}),
    getZsumIndex: () => new Promise(() => {}),
    submitRun: () => new Promise(() => {}),
    cancelRun: () => new Promise(() => {}),
    streamRun: () => new Promise(() => {}),
    getBbtStatus: () => new Promise(() => {}),
    getConfigDoc: () => new Promise(() => {}),
    postConfigDoc: () => new Promise(() => {}),
    getEffectiveConfig: () => new Promise(() => {}),
    getLocalModels: () => new Promise(() => {}),
    installLocalModel: () => new Promise(() => {}),
    getZsumStatus: () => new Promise(() => {}),
    getRun: () => new Promise(() => {}),
  };
});

vi.mock("../api/vault", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/vault")>();
  return {
    ...actual,
    getTree: () => new Promise(() => {}),
    getFile: () => new Promise(() => {}),
    resolveWikilink: () => new Promise(() => {}),
    listVaultCollections: () => new Promise(() => {}),
  };
});

vi.mock("../api/search", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/search")>();
  return {
    ...actual,
    searchVault: () => new Promise(() => {}),
  };
});

// ── Helpers ───────────────────────────────────────────────────────────────────

function makeQC() {
  return new QueryClient({
    defaultOptions: { queries: { retry: 0 } },
  });
}

function renderAtRoute(
  path: string,
  element: React.ReactElement,
  qc: QueryClient,
) {
  const router = createMemoryRouter(
    [{ path, element }],
    { initialEntries: [path] },
  );
  return render(
    <QueryClientProvider client={qc}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("Existing Zsum screens under /zsum/*", () => {
  it("existing vault flows render unchanged under /zsum/vault", async () => {
    // All existing vault flows work identically from /zsum/vault.
    // Smoke test: the relocated routes/zsum/vault component is importable
    // and renders its loading/structural shell without crashing.
    const { default: VaultRoute } = await import("../routes/zsum/vault");

    const qc = makeQC();
    const { container } = renderAtRoute("/zsum/vault", <VaultRoute />, qc);

    // The component renders without throwing.
    expect(container.firstChild).not.toBeNull();

    // VaultSearch input is unconditionally rendered in the left panel.
    const searchInput = container.querySelector('input[placeholder="Search vault…"]');
    expect(searchInput).not.toBeNull();
  });

  it("existing pipeline flows render unchanged under /zsum/pipeline", async () => {
    // All existing pipeline flows work identically from /zsum/pipeline.
    // Smoke test: the relocated routes/zsum/pipeline component is importable
    // and renders its structural shell (run-history sidebar + tab bar) without crashing.
    const { default: PipelineRoute } = await import("../routes/zsum/pipeline");

    const qc = makeQC();
    const { container } = renderAtRoute("/zsum/pipeline", <PipelineRoute />, qc);

    // The component renders without throwing.
    expect(container.firstChild).not.toBeNull();

    // "Run history" header in the sidebar is an unconditional structural element.
    expect(container.textContent).toContain("Run history");

    // The Library / Config tab bar is always rendered.
    expect(container.textContent).toContain("Library");
    expect(container.textContent).toContain("Config");
  });
});
