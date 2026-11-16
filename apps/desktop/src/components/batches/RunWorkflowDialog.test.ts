// @vitest-environment jsdom
/**
 * Unit tests for the effective-prompts preview decoder (T4.3).
 *
 * Pre-T4.3 the decoder did not understand the canonical
 * ``EffectivePromptSet`` envelope (``{schema_version, slot_to_variant,
 * contents}``) and instead iterated those top-level keys as if they
 * were slot names, yielding the literal ``variant: ?`` placeholders
 * the user reported. The tests below pin the fix.
 */
import { describe, expect, it } from "vitest";

import { decodeEffectivePrompts } from "./RunWorkflowDialog";

describe("decodeEffectivePrompts (T4.3)", () => {
  it("decodes the canonical EffectivePromptSet envelope", () => {
    const entries = decodeEffectivePrompts({
      schema_version: 1,
      slot_to_variant: {
        "akms.node_gen_skill": "bundled:akms.node_gen_skill.default",
        "akms.node_gen_command": "user:akms.node_gen_command.my-variant",
      },
      contents: {
        "akms.node_gen_skill": "SKILL prompt body",
        "akms.node_gen_command": "COMMAND prompt body",
      },
    } as unknown as Parameters<typeof decodeEffectivePrompts>[0]);

    expect(entries).toEqual([
      {
        slot: "akms.node_gen_skill",
        variant_id: "default",
        resolved_text: "SKILL prompt body",
        source: "bundled",
        family: "akms",
      },
      {
        slot: "akms.node_gen_command",
        variant_id: "my-variant",
        resolved_text: "COMMAND prompt body",
        source: "user",
        family: "akms",
      },
    ]);
  });

  it("never produces literal '?' placeholders for the canonical shape", () => {
    // Regression target: the user saw "variant: ?" strings rendered as
    // if they were real values. After T4.3, no entry should carry the
    // literal "?" string in variant_id when the envelope is canonical.
    const entries = decodeEffectivePrompts({
      schema_version: 1,
      slot_to_variant: {
        "zsum.paper_card": "bundled:zsum.paper_card.default",
      },
      contents: { "zsum.paper_card": "x" },
    } as unknown as Parameters<typeof decodeEffectivePrompts>[0]);

    expect(entries.every((e) => e.variant_id !== "?")).toBe(true);
    expect(entries[0].slot).toBe("zsum.paper_card");
    expect(entries[0].family).toBe("zsum");
  });

  it("falls back to the legacy entries[] array when present", () => {
    const entries = decodeEffectivePrompts({
      entries: [
        {
          slot: "akms.node_gen_skill",
          variant_id: "legacy-id",
          resolved_text: "body",
          source: "user",
          family: "akms",
        },
      ],
    } as unknown as Parameters<typeof decodeEffectivePrompts>[0]);
    expect(entries).toHaveLength(1);
    expect(entries[0].variant_id).toBe("legacy-id");
  });

  it("falls back to a flat slot→entry map when neither canonical nor entries[]", () => {
    const entries = decodeEffectivePrompts({
      "akms.node_gen_skill": {
        variant_id: "v1",
        resolved_text: "body",
        source: "user",
        family: "akms",
      },
    } as unknown as Parameters<typeof decodeEffectivePrompts>[0]);
    expect(entries).toEqual([
      {
        slot: "akms.node_gen_skill",
        variant_id: "v1",
        resolved_text: "body",
        source: "user",
        family: "akms",
      },
    ]);
  });

  it("strips schema_version from the legacy flat fallback", () => {
    const entries = decodeEffectivePrompts({
      schema_version: 1,
      // Intentionally NOT a string-map — forces the flat-fallback branch.
      "akms.node_gen_skill": {
        variant_id: "v1",
        resolved_text: "body",
      },
    } as unknown as Parameters<typeof decodeEffectivePrompts>[0]);
    expect(entries.map((e) => e.slot)).toEqual(["akms.node_gen_skill"]);
  });

  it("emits empty resolved_text when contents map is missing a slot", () => {
    const entries = decodeEffectivePrompts({
      schema_version: 1,
      slot_to_variant: {
        "zsum.paper_card": "bundled:zsum.paper_card.default",
      },
      contents: {},
    } as unknown as Parameters<typeof decodeEffectivePrompts>[0]);
    expect(entries[0].resolved_text).toBe("");
  });
});
