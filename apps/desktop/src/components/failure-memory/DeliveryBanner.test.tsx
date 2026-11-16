// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { DeliveryBanner } from "./DeliveryBanner";
import type { DeliveryReport } from "../../api/failureMemory";

/**
 * The UI half of the program's most dangerous finding.
 *
 * A documentation-only task with no matching advisory tag returns
 * `records == []` with `status: "ok"` even though the lesson has a live compiled
 * route. Rendered the same way as a genuinely empty result, an operator would
 * conclude the lesson is inactive when it is merely unroutable. These tests fix
 * the two states apart at the DOM level, so a future restyle cannot quietly
 * merge them back together.
 */

const BASE: DeliveryReport = {
  verdict: "no_lessons_apply",
  headline: "",
  input_paths: [],
  request_paths: [],
  provider_derived_paths: [],
  symbols: [],
  empty_diff_fallback: false,
  documentation_paths: [],
  code_paths: [],
  documentation_only: false,
  routed_candidate_node_ids: [],
  delivered_node_ids: [],
  required_node_ids: [],
  delivered_nowhere_node_ids: [],
  downgraded_node_ids: [],
  tag_only_node_ids: [],
};

const NOWHERE: DeliveryReport = {
  ...BASE,
  verdict: "delivered_nowhere",
  headline:
    "1 lesson(s) with a live compiled route were delivered NOWHERE — not required, " +
    "not coactivated, not advisory. The route exists; delivery was suppressed. " +
    "This is not an empty result.",
  input_paths: ["docs/architecture.md"],
  request_paths: ["docs/architecture.md"],
  documentation_paths: ["docs/architecture.md"],
  documentation_only: true,
  routed_candidate_node_ids: ["cc-failure-l006"],
  delivered_nowhere_node_ids: ["cc-failure-l006"],
};

const NOTHING: DeliveryReport = {
  ...BASE,
  verdict: "no_lessons_apply",
  headline:
    "No compiled route matches any input path — there is genuinely nothing to deliver.",
  input_paths: ["src/unrelated.py"],
  request_paths: ["src/unrelated.py"],
  code_paths: ["src/unrelated.py"],
};

afterEach(cleanup);

describe("DeliveryBanner", () => {
  it("distinguishes 'delivered nowhere' from 'no lessons apply'", () => {
    const nowhere = render(<DeliveryBanner delivery={NOWHERE} />);
    const nowhereRegion = nowhere.container.querySelector("[data-verdict]")!;
    expect(nowhereRegion.getAttribute("data-verdict")).toBe("delivered_nowhere");
    const nowhereText = nowhereRegion.textContent ?? "";
    nowhere.unmount();

    const nothing = render(<DeliveryBanner delivery={NOTHING} />);
    const nothingRegion = nothing.container.querySelector("[data-verdict]")!;
    expect(nothingRegion.getAttribute("data-verdict")).toBe("no_lessons_apply");
    const nothingText = nothingRegion.textContent ?? "";

    // Both have an empty required lane; they must not read the same.
    expect(nowhereText).not.toBe(nothingText);
    expect(nowhereRegion.className).not.toBe(nothingRegion.className);
  });

  it("names the suppressed lesson nodes when delivery went nowhere", () => {
    render(<DeliveryBanner delivery={NOWHERE} />);
    expect(screen.getByText("Routed, and delivered nowhere")).toBeTruthy();
    expect(screen.getByText("cc-failure-l006")).toBeTruthy();
    // Twice on purpose: once in the backend-computed headline, once in the
    // banner's own copy, so the point survives either being reworded.
    expect(screen.getAllByText(/This is not an empty result/).length).toBeGreaterThan(0);
  });

  it("explains the documentation-only routing rule, including the .md-anywhere part", () => {
    const { container } = render(<DeliveryBanner delivery={NOWHERE} />);
    const text = container.textContent ?? "";
    expect(text).toContain("advisory tag");
    expect(text).toContain("restores it to required");
    expect(text).toContain("not only files under");
  });

  it("does not claim suppression when nothing was routed", () => {
    const { container } = render(<DeliveryBanner delivery={NOTHING} />);
    const text = container.textContent ?? "";
    expect(text).toContain("No lessons apply");
    expect(text).not.toContain("delivered nowhere");
    expect(text).toContain("genuinely empty");
  });

  it("reports the routed-candidate count so an empty lane is never ambiguous", () => {
    const nowhere = render(<DeliveryBanner delivery={NOWHERE} />);
    expect(
      nowhere.container.querySelector('[data-stat="Routed candidates"]')?.textContent,
    ).toBe("1");
    nowhere.unmount();

    const nothing = render(<DeliveryBanner delivery={NOTHING} />);
    expect(
      nothing.container.querySelector('[data-stat="Routed candidates"]')?.textContent,
    ).toBe("0");
  });

  it("surfaces paths the provider derived rather than the caller supplying", () => {
    // A base/head post-diff call supplies none of its paths in the request.
    // Showing only the request paths would make the evidence look unrelated to
    // the lesson that was actually selected.
    const { container } = render(
      <DeliveryBanner
        delivery={{
          ...BASE,
          verdict: "delivered",
          headline: "1 lesson(s) were delivered, 1 of them through a compiled route.",
          input_paths: ["src/nothing/routes/here.py", "src/engine/interface.py"],
          request_paths: ["src/nothing/routes/here.py"],
          provider_derived_paths: ["src/engine/interface.py"],
          routed_candidate_node_ids: ["cc-failure-l001"],
          delivered_node_ids: ["cc-failure-l001"],
          required_node_ids: ["cc-failure-l001"],
        }}
      />,
    );
    const text = container.textContent ?? "";
    expect(text).toContain("Computed by the provider from the diff");
    expect(text).toContain("src/engine/interface.py");
    expect(container.querySelector('[data-stat="Routed candidates"]')?.textContent).toBe("1");
  });

  it("explains an empty-diff fallback instead of silently answering pre-task", () => {
    const { container } = render(
      <DeliveryBanner
        delivery={{
          ...BASE,
          verdict: "delivered",
          headline: "1 lesson(s) were delivered, 1 of them through a compiled route.",
          empty_diff_fallback: true,
          delivered_node_ids: ["cc-failure-l001"],
          routed_candidate_node_ids: ["cc-failure-l001"],
        }}
      />,
    );
    expect(container.textContent).toContain("really a pre-task answer");
  });

  it("never shows the empty-state copy while a lesson was delivered", () => {
    // The DOM half of the backend invariant: `no_lessons_apply` is reachable
    // only with an empty record set, so the "genuinely empty" copy can never
    // appear above a delivered lesson.
    const { container } = render(
      <DeliveryBanner
        delivery={{
          ...BASE,
          verdict: "delivered",
          headline: "1 lesson(s) were delivered.",
          delivered_node_ids: ["cc-failure-l006"],
        }}
      />,
    );
    const text = container.textContent ?? "";
    expect(text).not.toContain("genuinely empty");
    expect(text).not.toContain("No lessons apply");
  });

  it("marks a downgraded lesson as advisory rather than delivered", () => {
    const { container } = render(
      <DeliveryBanner
        delivery={{
          ...BASE,
          verdict: "downgraded_to_advisory",
          headline: "1 routed lesson(s) arrived only as advisory, not required.",
          documentation_only: true,
          routed_candidate_node_ids: ["cc-failure-l006"],
          delivered_node_ids: ["cc-failure-l006"],
          downgraded_node_ids: ["cc-failure-l006"],
        }}
      />,
    );
    expect(container.querySelector("[data-verdict]")?.getAttribute("data-verdict")).toBe(
      "downgraded_to_advisory",
    );
    expect(container.textContent).toContain("Downgraded to advisory");
    expect(container.querySelector('[data-stat="Required"]')?.textContent).toBe("0");
  });
});
