import { describe, expect, it } from "vitest";

import { getIssueLogButtonLabel, getRefreshButtonLabel } from "./FilterSidebar";

describe("getRefreshButtonLabel", () => {
  it("uses Generate when no cache exists yet", () => {
    expect(getRefreshButtonLabel(false, false)).toBe("Generate");
  });

  it("uses Regenerate when a cache already exists", () => {
    expect(getRefreshButtonLabel(true, false)).toBe("Regenerate");
  });

  it("uses the pending generate label while creating the first cache", () => {
    expect(getRefreshButtonLabel(false, true)).toBe("Generating…");
  });

  it("uses the pending regenerate label while refreshing an existing cache", () => {
    expect(getRefreshButtonLabel(true, true)).toBe("Regenerating…");
  });
});

describe("getIssueLogButtonLabel", () => {
  it("shows the skipped-file count when issues exist", () => {
    expect(getIssueLogButtonLabel(3)).toBe("Review skipped files (3)");
  });

  it("falls back to a neutral label when no files were skipped", () => {
    expect(getIssueLogButtonLabel(0)).toBe("View last scan log");
  });
});