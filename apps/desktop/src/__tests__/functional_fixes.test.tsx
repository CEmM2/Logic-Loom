// @vitest-environment jsdom
/**
 * QA batch 5 UI fixes: frontmatter values, math in the vault preview, and
 * MechDSL Compile feedback on an empty node id.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

const compileMechdsl = vi.fn();
vi.mock("../api/mechdsl", () => ({ compileMechdsl: (...a: unknown[]) => compileMechdsl(...a) }));

import { formatValue } from "../components/FrontmatterCard";
import { MarkdownPreview } from "../components/MarkdownPreview";
import MechdslCompileRoute from "../routes/mechdsl/compile";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("frontmatter values", () => {
  it("never prints [object Object]", () => {
    const out = formatValue([{ name: "Smith", role: "author" }, { name: "Lee" }]);
    expect(out).not.toContain("[object Object]");
    expect(out).toContain("name: Smith");
    expect(out).toContain("role: author");
  });

  it("shows nested maps one key per line", () => {
    expect(formatValue({ zotero: { key: "ABC", year: 2024 } })).toBe(
      "zotero:\n  key: ABC\n  year: 2024",
    );
  });

  it("keeps scalars and scalar lists on one line", () => {
    expect(formatValue("x")).toBe("x");
    expect(formatValue(["a", "b"])).toBe("a, b");
    expect(formatValue(null)).toBe("—");
  });
});

describe("vault preview math", () => {
  it("renders $...$ with KaTeX instead of raw source", () => {
    const { container } = render(
      <MemoryRouter>
        <MarkdownPreview content={"Energy $E = mc^2$ here."} />
      </MemoryRouter>,
    );
    expect(container.querySelector(".katex")).not.toBeNull();
  });
});

describe("MechDSL Compile", () => {
  function renderCompile() {
    return render(
      <QueryClientProvider client={new QueryClient()}>
        <MechdslCompileRoute />
      </QueryClientProvider>,
    );
  }

  it("explains an empty node id instead of doing nothing", () => {
    renderCompile();
    fireEvent.click(screen.getByRole("button", { name: "Compile" }));
    expect(screen.getByRole("alert").textContent).toMatch(/Enter an AKMS node id/);
    expect(compileMechdsl).not.toHaveBeenCalled();
  });

  it("sends the trimmed id", async () => {
    compileMechdsl.mockResolvedValue({ artifact_id: "a1", status: "ok", warnings: [], vv_plan: null });
    renderCompile();
    fireEvent.change(screen.getByTestId("mechdsl-node-id"), { target: { value: "  my.node  " } });
    fireEvent.click(screen.getByRole("button", { name: "Compile" }));
    await waitFor(() =>
      expect(compileMechdsl).toHaveBeenCalledWith({ node_id: "my.node", run_verify: false }),
    );
  });
});
