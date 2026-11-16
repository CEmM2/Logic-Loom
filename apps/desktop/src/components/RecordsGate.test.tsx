// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { RecordsGate } from "./RecordsGate";
import { subnav } from "./SectionSideNav";
import { topLevelModes } from "./TopModeNav";

const mockUseRecordsStatus = vi.fn();
vi.mock("../hooks/records", () => ({
  useRecordsStatus: () => mockUseRecordsStatus(),
}));

afterEach(cleanup);

const base = { package_installed: true, version: "0.4.0", reason: null, experimental: true };

function renderGate() {
  return render(
    <RecordsGate>
      <div data-testid="gated">RECORDS</div>
    </RecordsGate>,
  );
}

describe("RecordsGate", () => {
  it("shows the install hint when akms-records is missing", () => {
    mockUseRecordsStatus.mockReturnValue({
      data: { ...base, available: false, package_installed: false, reason: "akms-records is not installed", stores: [] },
      isLoading: false,
    });
    renderGate();
    expect(screen.queryByTestId("gated")).toBeNull();
    expect(screen.getByText(/--extra records/)).toBeTruthy();
  });

  it("explains [[records_stores]] when nothing is configured", () => {
    mockUseRecordsStatus.mockReturnValue({ data: { ...base, available: true, stores: [] }, isLoading: false });
    renderGate();
    expect(screen.queryByTestId("gated")).toBeNull();
    expect(screen.getByText(/No record stores are configured/)).toBeTruthy();
  });

  it("renders the subtree once a store is configured", () => {
    mockUseRecordsStatus.mockReturnValue({
      data: {
        ...base,
        available: true,
        stores: [{ id: "demo", scope: "project:demo", root: "/r", exists: true, record_count: 1, schema_count: 1, replay_errors: 0, error: null }],
      },
      isLoading: false,
    });
    renderGate();
    expect(screen.getByTestId("gated")).toBeTruthy();
  });

  it("is registered as a top-level mode with its sub-tabs", () => {
    expect(topLevelModes.map((m) => m.id)).toContain("records");
    expect(subnav.records).toEqual(["Overview", "Browse", "Schemas", "Graph"]);
  });
});
