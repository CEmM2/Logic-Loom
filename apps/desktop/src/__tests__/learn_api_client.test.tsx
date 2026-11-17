import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mock the shared client so we can spy on `api` (path + init) and stub
// `apiUrl` (used only by the artifact URL builder, which adds the token). This is a pure
// node-env module test — no jsdom needed.
const apiMock = vi.fn(async (..._args: unknown[]) => ({}) as unknown);
const apiUrlMock = vi.fn(async (path: string) => `http://127.0.0.1:17321${path}?llt=t`);

vi.mock("../api/client", () => ({
  api: (...args: unknown[]) => apiMock(...args),
  apiUrl: (path: string) => apiUrlMock(path),
}));

import {
  compileLearningSource,
  getLearnExporters,
  getLearnProfiles,
  getLearnStatus,
  learnArtifactUrl,
} from "../api/learn";
import type {
  LearnArtifact,
  LearnCompileRequest,
  LearnCompileResponse,
  LearnExporter,
  LearnProfile,
  LearnStatus,
} from "../api/learn";

beforeEach(() => {
  apiMock.mockReset();
  apiMock.mockResolvedValue({} as unknown);
  apiUrlMock.mockClear();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("learn.ts API client + types", () => {
  // Types mirror the backend contract.
  it("type-level compile check: all six types compile incl. the either/or selector", () => {
    // LearnStatus — UNAVAILABLE variant (reason present, capabilities []).
    const unavailable: LearnStatus = {
      akms_learn_available: false,
      reason: "Package akms_learn is not installed.",
      capabilities: [],
    };
    // LearnStatus — AVAILABLE variant (metadata present, reason omitted).
    const available: LearnStatus = {
      akms_learn_available: true,
      version: "0.1.0",
      plugin_api: "akms-learn-plugin/v1",
      learning_packet_schema: "learn/v1",
      supported_akms_schema_min: "v2",
      supported_akms_schema_max: "v2",
      capabilities: [
        "deterministic_outline",
        "node_anthology",
        "pitfall_driven",
        "markdown_export",
        "bundle_export",
      ],
      warnings: ["partial graph"],
    };
    // Discriminant narrows; reason is optional on the available path.
    expect(unavailable.akms_learn_available).toBe(false);
    expect(available.akms_learn_available).toBe(true);
    expect(available.reason).toBeUndefined();

    // LearnProfile / LearnExporter — provisional, minimal.
    const profile: LearnProfile = { id: "anthology" };
    const exporter: LearnExporter = { id: "markdown", label: "Markdown" };
    expect(profile.id).toBe("anthology");
    expect(exporter.id).toBe("markdown");

    // LearnArtifact — exported entry shape.
    const artifact: LearnArtifact = { path: "learn.md", type: "markdown" };
    expect(artifact.type).toBe("markdown");

    // LearnCompileRequest — form B (node-depth selection).
    // topic/goal are REQUIRED (backend CompileRequest); exporters is OPTIONAL
    // here (backend `exporters: list[str] | None = None`) — omitted to prove it.
    const nodeForm: LearnCompileRequest = {
      generation_option: "node_anthology",
      topic: "Topic",
      goal: "Goal",
      node: "node-123",
      max_depth: 2,
      direction: "both",
    };
    // LearnCompileRequest — form A (explicit graph slice).
    // graph_slice mirrors backend GraphSlicePayload (nodes/edges/metadata), NOT
    // ./graph's GraphSlice (whose third field is `meta` and would be dropped).
    const sliceForm: LearnCompileRequest = {
      generation_option: "deterministic_outline",
      topic: "Topic",
      goal: "Goal",
      audience: "beginner",
      depth: "overview",
      seed_tags: ["a", "b"],
      exporters: ["markdown", "bundle"],
      graph_slice: {
        nodes: [],
        edges: [],
        metadata: { source: "logic_loom_ego_slice" },
      },
    };
    expect(nodeForm.node).toBe("node-123");
    expect(nodeForm.exporters).toBeUndefined();
    expect(sliceForm.graph_slice?.nodes).toEqual([]);
    expect(sliceForm.graph_slice?.metadata).toEqual({ source: "logic_loom_ego_slice" });

    // LearnCompileResponse — full response shape, reusing LearnArtifact for exports.
    // warnings/provenance_summary mirror the backend dict contract
    // (_serialize_warnings / _build_provenance_summary); paths are nullable.
    const response: LearnCompileResponse = {
      artifact_id: "abc123",
      markdown: "# Title",
      warnings: [
        {
          severity: "warning",
          code: "partial_graph",
          message: "Graph slice was truncated.",
          source_ref: null,
        },
      ],
      provenance_summary: {
        request_hash: "rh",
        graph_hash: "gh",
        topic: "Topic",
        generation_option: "node_anthology",
        node_count: 1,
        edge_count: 0,
      },
      cache_dir: "/cache/learning/abc123",
      packet_path: "/cache/learning/abc123/packet.json",
      exports: [{ path: "learn.md", type: "markdown" }],
    };
    expect(response.exports[0]).toEqual(artifact);
    expect(response.warnings[0].message).toBe("Graph slice was truncated.");
    expect(response.provenance_summary.node_count).toBe(1);

    expect(true).toBe(true);
  });

  // Client functions cover all six endpoints.
  it("client calls hit correct routes + methods", async () => {
    await getLearnStatus();
    expect(apiMock).toHaveBeenLastCalledWith("/api/learn/status");

    await getLearnProfiles();
    expect(apiMock).toHaveBeenLastCalledWith("/api/learn/profiles");

    await getLearnExporters();
    expect(apiMock).toHaveBeenLastCalledWith("/api/learn/exporters");

    const req: LearnCompileRequest = {
      generation_option: "node_anthology",
      topic: "Topic",
      goal: "Goal",
      exporters: ["markdown"],
      node: "node-123",
      max_depth: 2,
      direction: "both",
    };

    await compileLearningSource(req);
    expect(apiMock).toHaveBeenLastCalledWith(
      "/api/learn/compile",
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(req),
      }),
    );

    // Artifact endpoint is a URL builder (raw bytes), NOT an api<T> call.
    const url = await learnArtifactUrl("abc123", "sub/learn.md");
    expect(apiUrlMock).toHaveBeenCalledWith("/api/learn/artifacts/abc123/sub/learn.md");
    expect(url).toBe("http://127.0.0.1:17321/api/learn/artifacts/abc123/sub/learn.md?llt=t");
    // The URL builder must not route through `api`.
    // (No /export call: that deferred-stub client was removed — see api/learn.ts.)
    expect(apiMock).toHaveBeenCalledTimes(4);
  });
});
