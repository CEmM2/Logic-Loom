import { describe, expect, it } from "vitest";

import type { LearnExporter, LearnProfile } from "../../api/learn";
import {
  buildExporterRegistry,
  buildModeRegistry,
  defaultSelectableModeId,
  humanizeToken,
} from "./capabilities";

// ── Realistic fixtures mirroring the live /profiles + /exporters payloads ──────
// (routes/learn.py learn_profiles / learn_exporters: each {id, label, status}).
// `id` for exporters is the FULL `*_export` capability token; the bare id
// (suffix stripped) is what the compile request's exporters[] expects.

const PROFILES: LearnProfile[] = [
  { id: "deterministic_outline", label: "Deterministic Outline", status: "available" },
  { id: "node_anthology", label: "Node Anthology", status: "available" },
  { id: "llm_expanded", label: "Llm Expanded", status: "unavailable" },
  { id: "adaptive_path", label: "Adaptive Path", status: "planned" },
];

const EXPORTERS: LearnExporter[] = [
  { id: "markdown_export", label: "Markdown", status: "available" },
  { id: "bundle_export", label: "Bundle", status: "available" },
  { id: "notebook_export", label: "Notebook", status: "unavailable" },
];

describe("buildModeRegistry (from /profiles)", () => {
  it("reflects backend profiles in order, carrying id/label/status", () => {
    const modes = buildModeRegistry(PROFILES);
    expect(modes.map((m) => m.id)).toEqual([
      "deterministic_outline",
      "node_anthology",
      "llm_expanded",
      "adaptive_path",
    ]);
    expect(modes[0]).toEqual({
      id: "deterministic_outline",
      label: "Deterministic Outline",
      status: "available",
      selectable: true,
    });
  });

  it("marks unavailable/planned modes non-selectable (rendered disabled, not omitted)", () => {
    const modes = buildModeRegistry(PROFILES);
    const byId = Object.fromEntries(modes.map((m) => [m.id, m]));
    // Unavailable/planned are KEPT (visible) but not selectable.
    expect(byId["llm_expanded"].selectable).toBe(false);
    expect(byId["adaptive_path"].selectable).toBe(false);
    expect(byId["deterministic_outline"].selectable).toBe(true);
    expect(byId["node_anthology"].selectable).toBe(true);
  });

  it("humanizes the label when the backend omits it", () => {
    const modes = buildModeRegistry([{ id: "multi_granularity", status: "available" }]);
    expect(modes[0].label).toBe("Multi Granularity");
  });

  it("defaults absent status to available (tolerant of older payloads)", () => {
    const modes = buildModeRegistry([{ id: "deterministic_outline", label: "X" }]);
    expect(modes[0].status).toBe("available");
    expect(modes[0].selectable).toBe(true);
  });

  it("falls back to status.generation_modes when /profiles is empty/absent", () => {
    const modes = buildModeRegistry(undefined, {
      capabilities: ["learning_source_packet", "deterministic_outline", "markdown_export"],
      generation_modes: ["deterministic_outline", "node_anthology"],
    });
    // The backend-filtered list is used (NOT the lossy capabilities partition).
    expect(modes.map((m) => m.id)).toEqual(["deterministic_outline", "node_anthology"]);
    // Fallback entries are treated as available (legacy backend had no status).
    expect(modes.every((m) => m.selectable)).toBe(true);
  });

  it("falls back to the _export partition only when generation_modes is also absent", () => {
    const modes = buildModeRegistry(undefined, {
      capabilities: ["deterministic_outline", "node_anthology", "markdown_export"],
    });
    expect(modes.map((m) => m.id)).toEqual(["deterministic_outline", "node_anthology"]);
  });

  it("returns an empty list when neither /profiles nor status is supplied", () => {
    expect(buildModeRegistry(undefined)).toEqual([]);
    expect(buildModeRegistry([])).toEqual([]);
  });
});

describe("buildExporterRegistry (from /exporters)", () => {
  it("strips the _export suffix to the bare compile id, keeping label/status", () => {
    const exporters = buildExporterRegistry(EXPORTERS);
    expect(exporters.map((e) => e.id)).toEqual(["markdown", "bundle", "notebook"]);
    expect(exporters[0]).toEqual({
      id: "markdown",
      label: "Markdown",
      status: "available",
      selectable: true,
    });
  });

  it("marks unavailable exporters non-selectable (rendered disabled)", () => {
    const exporters = buildExporterRegistry(EXPORTERS);
    const byId = Object.fromEntries(exporters.map((e) => [e.id, e]));
    expect(byId["notebook"].selectable).toBe(false);
    expect(byId["markdown"].selectable).toBe(true);
    expect(byId["bundle"].selectable).toBe(true);
  });

  it("humanizes the bare id when the backend omits a label", () => {
    const exporters = buildExporterRegistry([{ id: "html_export", status: "available" }]);
    expect(exporters[0].label).toBe("Html");
  });

  it("falls back to the capabilities _export partition when /exporters is empty/absent", () => {
    const exporters = buildExporterRegistry(undefined, {
      capabilities: ["deterministic_outline", "markdown_export", "bundle_export"],
    });
    expect(exporters.map((e) => e.id)).toEqual(["markdown", "bundle"]);
    expect(exporters.every((e) => e.selectable)).toBe(true);
  });

  it("returns an empty list when neither /exporters nor status is supplied", () => {
    expect(buildExporterRegistry(undefined)).toEqual([]);
    expect(buildExporterRegistry([])).toEqual([]);
  });
});

describe("defaultSelectableModeId", () => {
  it("picks the first AVAILABLE mode, skipping unavailable/planned leaders", () => {
    const modes = buildModeRegistry([
      { id: "llm_expanded", status: "unavailable" },
      { id: "adaptive_path", status: "planned" },
      { id: "deterministic_outline", status: "available" },
    ]);
    expect(defaultSelectableModeId(modes)).toBe("deterministic_outline");
  });

  it("returns '' when no mode is selectable (all unavailable/planned)", () => {
    const modes = buildModeRegistry([
      { id: "llm_expanded", status: "unavailable" },
      { id: "adaptive_path", status: "planned" },
    ]);
    expect(defaultSelectableModeId(modes)).toBe("");
  });
});

describe("humanizeToken", () => {
  it("title-cases an underscore-delimited token", () => {
    expect(humanizeToken("deterministic_outline")).toBe("Deterministic Outline");
  });
});
