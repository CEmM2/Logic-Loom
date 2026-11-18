// @vitest-environment jsdom
/**
 * Home page (v0.3.3 retest): a card for every mode, and Ctrl-K opens the
 * workspace search from Home too (the shortcut used to live only in AppShell).
 */
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import HomeRoute from "../routes/home";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    getApiBase: vi.fn().mockResolvedValue("http://127.0.0.1:1"),
    api: vi.fn().mockResolvedValue({ status: "ok", version: "0.0.0" }),
  };
});

vi.mock("../api/assistantIndex", async () => {
  const actual = await vi.importActual<typeof import("../api/assistantIndex")>(
    "../api/assistantIndex",
  );
  const empty = { counts: {}, total: 0, last_rebuild_at: null };
  return {
    ...actual,
    getIndexStatus: vi.fn().mockResolvedValue(empty),
    rebuildIndex: vi.fn().mockResolvedValue(empty),
  };
});

vi.mock("../components/home/ModelsDialog", () => ({ ModelsDialog: () => null }));

afterEach(() => cleanup());

function renderHome() {
  return render(
    <MemoryRouter>
      <HomeRoute />
    </MemoryRouter>,
  );
}

describe("Home", () => {
  it("shows a card for every mode, marking the optional ones", () => {
    renderHome();
    for (const [label, href] of [
      ["Zsum", "/zsum"],
      ["AKMS", "/akms"],
      ["Assistant", "/assistant"],
      ["MechDSL", "/mechdsl"],
      ["Failure Memory", "/failure-memory"],
      ["Records", "/records"],
    ]) {
      const link = screen.getByText(label).closest("a");
      expect(link?.getAttribute("href")).toBe(href);
    }
    expect(screen.getAllByText("optional")).toHaveLength(3);
  });

  it("opens the workspace search on Ctrl-K", async () => {
    renderHome();
    expect(screen.queryByRole("dialog", { name: /workspace search/i })).toBeNull();
    act(() => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "k", ctrlKey: true, bubbles: true }));
    });
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: /workspace search/i })).not.toBeNull();
    });
  });
});
