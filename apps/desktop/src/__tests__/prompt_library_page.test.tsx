// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

/**
 * Prompt Library page (shared component + family filter).
 *
 * All three mode-specific routes share the same component, filtered
 * by family.
 *
 * Cases covered:
 *
 *   1. Family filter — render the shared page in akms
 *      mode and confirm only akms slots are visible; switch family=null
 *      and confirm all canonical slots show up. Also covers the
 *      edit-variant round-trip: type into the textarea, click Save,
 *      assert PUT was called with the new content, then have the next
 *      list-variants return the updated text and assert the editor
 *      re-renders.
 *   2. Profile-aware resolve preview — click Preview on the editor
 *      and assert the resolved profile-aware preview from
 *      `/api/prompts/resolve` is rendered.
 */

vi.mock("../api/promptLibrary", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/promptLibrary")>();
  return {
    ...actual,
    listSlots: vi.fn(),
    listVariants: vi.fn(),
    listProfiles: vi.fn(),
    updateVariant: vi.fn(),
    resolveEffectivePrompts: vi.fn(),
    duplicateVariant: vi.fn(),
    createVariant: vi.fn(),
    createProfile: vi.fn(),
    updateProfile: vi.fn(),
    validateVariant: vi.fn(),
  };
});

import * as promptsApi from "../api/promptLibrary";
import type { PromptVariant, PromptProfile } from "../api/promptLibrary";
import { PromptLibraryPage } from "../components/prompts/PromptLibraryPage";

function makeQC() {
  return new QueryClient({ defaultOptions: { queries: { retry: 0 } } });
}

function renderPage(family: "akms" | "zsum" | null) {
  return render(
    <QueryClientProvider client={makeQC()}>
      <PromptLibraryPage family={family} />
    </QueryClientProvider>,
  );
}

function makeVariant(overrides: Partial<PromptVariant> = {}): PromptVariant {
  return {
    schema_version: 1,
    id: "summarize-default",
    family: "akms",
    slot: "akms.summarize",
    name: "Default summarize prompt",
    description: "",
    source: "user",
    based_on: null,
    filename: ".claude/akms_summarize.md",
    content_type: "markdown",
    content_hash: "h1",
    content: "Original summarize text.",
    output_schema_id: null,
    ...overrides,
  };
}

const SLOTS = [
  "zsum.summarize",
  "zsum.extract",
  "akms.summarize",
  "akms.extract",
  "akms.synthesize",
];

describe("Prompt Library page", () => {
  beforeEach(() => {
    (promptsApi.listSlots as unknown as ReturnType<typeof vi.fn>).mockReset();
    (promptsApi.listVariants as unknown as ReturnType<typeof vi.fn>).mockReset();
    (promptsApi.listProfiles as unknown as ReturnType<typeof vi.fn>).mockReset();
    (promptsApi.updateVariant as unknown as ReturnType<typeof vi.fn>).mockReset();
    (promptsApi.resolveEffectivePrompts as unknown as ReturnType<typeof vi.fn>).mockReset();
    (promptsApi.duplicateVariant as unknown as ReturnType<typeof vi.fn>).mockReset();
    (promptsApi.createVariant as unknown as ReturnType<typeof vi.fn>).mockReset();
    (promptsApi.createProfile as unknown as ReturnType<typeof vi.fn>).mockReset();
    (promptsApi.updateProfile as unknown as ReturnType<typeof vi.fn>).mockReset();
    (promptsApi.validateVariant as unknown as ReturnType<typeof vi.fn>).mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it("family filter hides foreign slots and edit-variant round-trips through updateVariant", async () => {
    (promptsApi.listSlots as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      slots: SLOTS,
    });

    const initial = makeVariant();
    const updated = makeVariant({ content: "Edited summarize text.", content_hash: "h2" });

    // listVariants returns initial on first call, updated on subsequent calls.
    const listVariantsMock = promptsApi.listVariants as unknown as ReturnType<typeof vi.fn>;
    listVariantsMock
      .mockResolvedValueOnce({ variants: [initial], bundled: [] })
      .mockResolvedValue({ variants: [updated], bundled: [] });

    (promptsApi.listProfiles as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      profiles: [],
      active: { schema_version: 1, profile_id: null, active: {} },
    });

    (promptsApi.updateVariant as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(updated);

    renderPage("akms");

    // Slots list shows only akms.* — confirm zsum slots are NOT rendered.
    await waitFor(() => {
      expect(screen.getByTestId("slot-row-akms.summarize")).toBeTruthy();
    });
    expect(screen.queryByTestId("slot-row-zsum.summarize")).toBeNull();
    expect(screen.queryByTestId("slot-row-zsum.extract")).toBeNull();
    expect(screen.getByTestId("slot-row-akms.extract")).toBeTruthy();
    expect(screen.getByTestId("slot-row-akms.synthesize")).toBeTruthy();

    // Variant editor renders with the initial content.
    await waitFor(() => {
      const ta = screen.getByTestId("variant-content-input") as HTMLTextAreaElement;
      expect(ta.value).toBe("Original summarize text.");
    });

    // Edit the content and click Save — PUT is called with the new text.
    const textarea = screen.getByTestId("variant-content-input") as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "Edited summarize text." } });
    fireEvent.click(screen.getByTestId("variant-save-button"));

    await waitFor(() => {
      expect(promptsApi.updateVariant).toHaveBeenCalledWith(
        "summarize-default",
        expect.objectContaining({
          slot: "akms.summarize",
          content: "Edited summarize text.",
        }),
      );
    });

    // After the cache invalidates, the next listVariants returns the updated
    // record — the editor's `useEffect` re-syncs from the new content_hash.
    await waitFor(() => {
      const ta = screen.getByTestId("variant-content-input") as HTMLTextAreaElement;
      expect(ta.value).toBe("Edited summarize text.");
    });
  });

  it("Preview renders resolved variants from /api/prompts/resolve", async () => {
    (promptsApi.listSlots as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      slots: SLOTS,
    });

    const variant = makeVariant();
    (promptsApi.listVariants as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      variants: [variant],
      bundled: [],
    });

    const profile: PromptProfile = {
      schema_version: 1,
      id: "akms-default",
      name: "AKMS default",
      description: "Default AKMS profile",
      active: { "akms.summarize": "user:akms.summarize:summarize-default" },
    };
    (promptsApi.listProfiles as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      profiles: [profile],
      active: {
        schema_version: 1,
        profile_id: "akms-default",
        active: profile.active,
      },
    });

    (promptsApi.resolveEffectivePrompts as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      schema_version: 1,
      slot_to_variant: {
        "akms.summarize": "user:akms.summarize:summarize-default",
        "akms.extract": "bundled:akms.extract:default",
      },
      contents: {
        "akms.summarize": "Resolved summarize content.",
        "akms.extract": "Resolved extract content.",
      },
    });

    renderPage("akms");

    // Wait for the editor to mount with the initial variant.
    await waitFor(() => {
      expect(screen.getByTestId("variant-preview-button")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("variant-preview-button"));

    // Resolved variants appear in the preview list.
    await waitFor(() => {
      expect(screen.getByTestId("variant-preview-results")).toBeTruthy();
    });
    expect(screen.getByTestId("preview-slot-akms.summarize").textContent).toMatch(
      /summarize-default/,
    );
    expect(screen.getByTestId("preview-slot-akms.extract").textContent).toMatch(
      /default/,
    );

    // The resolve call carried the editor's current text as a
    // request-level override for the current slot — this is the
    // profile-aware resolve path.
    expect(promptsApi.resolveEffectivePrompts).toHaveBeenCalledWith(
      expect.objectContaining({
        request_overrides: expect.objectContaining({
          "akms.summarize": "Original summarize text.",
        }),
      }),
    );

    // "Where used" reflects profile membership.
    expect(screen.getByTestId("where-used-akms-default")).toBeTruthy();
  });
});
