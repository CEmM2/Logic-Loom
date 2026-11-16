// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

/**
 * Workflows page (shared component + bundled/user distinction).
 *
 * Duplicating a built-in workflow produces an editable user workflow
 * visible under `<akms_workspace>/workflow-library/user-workflows/`.
 *
 * Cases covered:
 *   1. List + select round trip — mock list + render, click a
 *      bundled workflow row and assert the detail pane renders for that
 *      workflow.
 *   2. Bundled read-only, user editable — list returns one bundled
 *      and one user workflow; selecting bundled disables the edit form
 *      and shows the read-only banner; selecting user enables the
 *      inputs.
 *   3. Duplicate → edit → archive — click Duplicate on
 *      a bundled workflow; mock returns a user-source copy; assert the
 *      new row appears in the User group and the detail switches to the
 *      duplicated workflow. Then edit + save + archive the user
 *      duplicate and assert the matching API calls.
 */

vi.mock("../api/workflows", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/workflows")>();
  return {
    ...actual,
    listWorkflows: vi.fn(),
    getWorkflow: vi.fn(),
    createWorkflow: vi.fn(),
    updateWorkflow: vi.fn(),
    duplicateWorkflow: vi.fn(),
    archiveWorkflow: vi.fn(),
  };
});

import * as workflowsApi from "../api/workflows";
import type { WorkflowDefinition } from "../api/workflows";
import { WorkflowsPage } from "../components/workflows/WorkflowsPage";

function makeQC() {
  return new QueryClient({ defaultOptions: { queries: { retry: 0 } } });
}

function renderPage(executor: "claude" | "zsum" | null = "claude") {
  return render(
    <MemoryRouter>
      <QueryClientProvider client={makeQC()}>
        <WorkflowsPage executor={executor} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

function makeWorkflow(overrides: Partial<WorkflowDefinition> = {}): WorkflowDefinition {
  return {
    schema_version: 1,
    id: "akms.node_extraction",
    name: "AKMS Node Extraction",
    category: "extraction",
    description: "Built-in node extraction workflow.",
    executor: "claude",
    source_mode: "none",
    input_slots: ["source.markdown"],
    output_format: "yaml",
    default_output_subdir: "nodes/",
    prompt_slots: { "akms.extract": "bundled:akms.extract:default" },
    required_external_tools: [],
    validation: { required_sections: [] },
    status: "active",
    source: "bundled",
    ...overrides,
  };
}

describe("Workflows page", () => {
  beforeEach(() => {
    (workflowsApi.listWorkflows as unknown as ReturnType<typeof vi.fn>).mockReset();
    (workflowsApi.getWorkflow as unknown as ReturnType<typeof vi.fn>).mockReset();
    (workflowsApi.createWorkflow as unknown as ReturnType<typeof vi.fn>).mockReset();
    (workflowsApi.updateWorkflow as unknown as ReturnType<typeof vi.fn>).mockReset();
    (workflowsApi.duplicateWorkflow as unknown as ReturnType<typeof vi.fn>).mockReset();
    (workflowsApi.archiveWorkflow as unknown as ReturnType<typeof vi.fn>).mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it("list workflows, click a row, detail renders", async () => {
    const bundled = makeWorkflow();
    const other = makeWorkflow({
      id: "akms.summarize",
      name: "AKMS Summarize",
      prompt_slots: { "akms.summarize": "bundled:akms.summarize:default" },
    });
    (workflowsApi.listWorkflows as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      workflows: [bundled, other],
    });

    renderPage("claude");

    // Wait for list to render both rows.
    await waitFor(() => {
      expect(screen.getByTestId("workflow-row-akms.node_extraction")).toBeTruthy();
    });
    expect(screen.getByTestId("workflow-row-akms.summarize")).toBeTruthy();

    // Default selection lands on the first workflow — detail shows its id.
    await waitFor(() => {
      const detail = screen.getByTestId("workflow-detail");
      expect(detail.textContent).toMatch(/AKMS Node Extraction/);
    });

    // Click the second row and confirm the detail pane swaps.
    fireEvent.click(screen.getByTestId("workflow-row-akms.summarize"));
    await waitFor(() => {
      const detail = screen.getByTestId("workflow-detail");
      expect(detail.textContent).toMatch(/AKMS Summarize/);
    });
  });

  it("bundled workflows are read-only, user workflows are editable", async () => {
    const bundled = makeWorkflow();
    const userWf = makeWorkflow({
      id: "akms.node_extraction-2",
      name: "Custom Node Extraction",
      source: "user",
      description: "User-edited copy.",
    });
    (workflowsApi.listWorkflows as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      workflows: [bundled, userWf],
    });

    renderPage("claude");

    // Bundled is the default selection (first by id sort in the list).
    await waitFor(() => {
      const banner = screen.queryByTestId("workflow-readonly-banner");
      expect(banner).toBeTruthy();
    });

    // The name input is disabled for bundled.
    const nameInputBundled = screen.getByTestId(
      "workflow-name-input",
    ) as HTMLInputElement;
    expect(nameInputBundled.disabled).toBe(true);
    // Save button disabled for bundled too.
    expect(
      (screen.getByTestId("workflow-save-button") as HTMLButtonElement).disabled,
    ).toBe(true);
    // No archive button for bundled workflows.
    expect(screen.queryByTestId("workflow-archive-button")).toBeNull();
    // Source label reads "bundled".
    expect(screen.getByTestId("workflow-source-label").textContent).toBe(
      "bundled",
    );

    // Click into the user workflow and confirm the editor unlocks.
    fireEvent.click(
      screen.getByTestId("workflow-row-akms.node_extraction-2"),
    );

    await waitFor(() => {
      expect(screen.queryByTestId("workflow-readonly-banner")).toBeNull();
    });
    const nameInputUser = screen.getByTestId(
      "workflow-name-input",
    ) as HTMLInputElement;
    expect(nameInputUser.disabled).toBe(false);
    expect(screen.getByTestId("workflow-source-label").textContent).toBe("user");
    // Archive surfaces for user workflows.
    expect(screen.getByTestId("workflow-archive-button")).toBeTruthy();
  });

  it("duplicate → edit → archive round trip", async () => {
    const bundled = makeWorkflow();
    const duplicated = makeWorkflow({
      id: "akms.node_extraction-2",
      source: "user",
      status: "active",
    });
    const edited = makeWorkflow({
      id: "akms.node_extraction-2",
      source: "user",
      name: "AKMS Node Extraction (edited)",
    });
    const archived = makeWorkflow({
      id: "akms.node_extraction-2",
      source: "user",
      status: "archived",
    });

    const listMock = workflowsApi.listWorkflows as unknown as ReturnType<typeof vi.fn>;
    // 1st list call: only the bundled workflow exists.
    listMock.mockResolvedValueOnce({ workflows: [bundled] });
    // After duplicate: list returns both.
    listMock.mockResolvedValueOnce({ workflows: [bundled, duplicated] });
    // After save: list returns the edited user workflow.
    listMock.mockResolvedValueOnce({ workflows: [bundled, edited] });
    // After archive: list returns the archived user workflow (still
    // visible if include_archived is toggled on; otherwise just the
    // bundled). Because the archive handler clears the selection and we
    // do not toggle archived-visibility in this test, return bundled
    // only for any subsequent calls.
    listMock.mockResolvedValue({ workflows: [bundled] });

    (workflowsApi.duplicateWorkflow as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(
      duplicated,
    );
    (workflowsApi.updateWorkflow as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(
      edited,
    );
    (workflowsApi.archiveWorkflow as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(
      archived,
    );

    renderPage("claude");

    // Wait for the bundled row to appear, then click Duplicate.
    await waitFor(() => {
      expect(screen.getByTestId("workflow-row-akms.node_extraction")).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId("workflow-duplicate-button"));

    await waitFor(() => {
      expect(workflowsApi.duplicateWorkflow).toHaveBeenCalledWith(
        "akms.node_extraction",
      );
    });

    // The duplicated row appears under User and becomes the selection.
    await waitFor(() => {
      expect(
        screen.getByTestId("workflow-row-akms.node_extraction-2"),
      ).toBeTruthy();
    });
    await waitFor(() => {
      expect(screen.getByTestId("workflow-source-label").textContent).toBe(
        "user",
      );
    });

    // Edit the name input then click Save.
    const nameInput = screen.getByTestId(
      "workflow-name-input",
    ) as HTMLInputElement;
    expect(nameInput.disabled).toBe(false);
    fireEvent.change(nameInput, {
      target: { value: "AKMS Node Extraction (edited)" },
    });
    fireEvent.click(screen.getByTestId("workflow-save-button"));

    await waitFor(() => {
      expect(workflowsApi.updateWorkflow).toHaveBeenCalledWith(
        "akms.node_extraction-2",
        expect.objectContaining({
          id: "akms.node_extraction-2",
          name: "AKMS Node Extraction (edited)",
          executor: "claude",
        }),
      );
    });

    // After the list re-fetches the edited workflow, click Archive.
    await waitFor(() => {
      expect(screen.getByTestId("workflow-archive-button")).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId("workflow-archive-button"));

    await waitFor(() => {
      expect(workflowsApi.archiveWorkflow).toHaveBeenCalledWith(
        "akms.node_extraction-2",
      );
    });
  });
});
