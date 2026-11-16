// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";

import { JobRow } from "./JobPanel";
import type { Job } from "../../api/failureMemory";

/**
 * The hazard in DOM form: a background failure must not be able to
 * leave a partially accepted UI state.
 *
 * A queued or running job is not an outcome, and a failed job is not a
 * near-success. These tests pin that the three states are visually and
 * structurally distinct, so a restyle cannot quietly let "queued" read as done.
 */

afterEach(cleanup);

const BASE: Job = {
  id: "0123456789abcdef",
  project_id: "pack",
  action: "record",
  status: "queued",
  detail: "src/engine/queue.py",
  created_at: "2026-08-16T00:00:00+00:00",
  started_at: null,
  finished_at: null,
  summary: "",
  result: null,
  error: null,
};

describe("JobRow", () => {
  it("never presents a queued or running job as finished", () => {
    for (const status of ["queued", "running"] as const) {
      const { container, unmount } = render(<JobRow job={{ ...BASE, status }} />);
      const row = container.querySelector("[data-job-status]")!;
      expect(row.getAttribute("data-job-status")).toBe(status);
      const text = row.textContent ?? "";
      expect(text).toContain("not finished");
      expect(text).toContain("Nothing is confirmed until it does");
      expect(text).not.toContain("Succeeded");
      unmount();
    }
  });

  it("shows a succeeded job with the package's own summary", () => {
    const { container } = render(
      <JobRow
        job={{
          ...BASE,
          status: "succeeded",
          finished_at: "2026-08-16T00:00:01+00:00",
          summary: "Recorded L007; 8 generated file(s) were rewritten.",
          result: { record: { id: "L007" } },
        }}
      />,
    );
    const text = container.textContent ?? "";
    expect(text).toContain("Succeeded");
    expect(text).toContain("Recorded L007");
    expect(text).not.toContain("not finished");
  });

  it("shows a failed job with the package error code, not a paraphrase", () => {
    const { container } = render(
      <JobRow
        job={{
          ...BASE,
          status: "failed",
          error: { code: "record_request", message: "Add request has missing fields" },
        }}
      />,
    );
    const text = container.textContent ?? "";
    expect(text).toContain("Failed");
    expect(text).toContain("record_request");
    expect(text).not.toContain("Succeeded");
  });

  it("explains a repo2md failure as a toolchain shortfall, never as staleness", () => {
    const { container } = render(
      <JobRow
        job={{
          ...BASE,
          action: "refresh",
          status: "failed",
          error: { code: "repo2md_missing", message: "repo2md executable is unavailable" },
        }}
      />,
    );
    const text = container.textContent ?? "";
    expect(text).toContain("TOOLCHAIN shortfall, not stale knowledge");
    expect(text).toContain("Nothing about your recorded lessons has changed");
  });

  it("explains lock_parent_missing as never-written rather than broken", () => {
    const { container } = render(
      <JobRow
        job={{
          ...BASE,
          status: "failed",
          error: {
            code: "lock_parent_missing",
            message: "Project lock parent directory does not exist",
          },
        }}
      />,
    );
    expect(container.textContent).toContain("has never been written to");
  });
});
