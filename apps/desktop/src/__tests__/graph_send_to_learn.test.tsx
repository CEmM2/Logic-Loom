// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";

/**
 * Graph-to-Learn "Send to Learn" handoff.
 *
 * Contract: the action transfers a valid node/depth or GraphSlice into the Learn tab
 *     input as a `LearnPrefillState`.
 *
 * Two test groups:
 *  1. Pure unit tests on the state-builder functions (`buildNodePrefill`,
 *     `buildSlicePrefill`). No mounting required — these run fast.
 *  2. Wiring tests: render `NodeInspector` with spy callbacks and assert the
 *     buttons fire the correct prop.
 */

import {
  buildNodePrefill,
  buildSlicePrefill,
} from "../components/graph/sendToLearn";
import { isLearnPrefillState } from "../routes/learn/prefill";
import type { GraphEdge, GraphMeta, GraphNode } from "../api/graph";
import { NodeInspector } from "../components/graph/NodeInspector";

// ── Minimal fixture data ────────────────────────────────────────────────────

const minimalNode: GraphNode = {
  id: "n1",
  title: "Test Node",
  domain: null,
  subdomain: null,
  status: null,
  tags: [],
  confidence: null,
  source: null,
  node_origin: null,
  file_path: null,
};

const minimalEdge: GraphEdge = {
  source: "n1",
  target: "n2",
  type: "relates_to",
  weight: null,
  note: null,
  edge_origin: null,
};

const minimalMeta: GraphMeta = {
  generated_at: "2026-01-01T00:00:00Z",
  node_count: 1,
  edge_count: 1,
  repo_id: "test-repo",
  global_vault: null,
  cache_path: "/cache/graph",
  scope: "ego",
  selected_node: "n1",
  depth: 2,
  direction: "both",
  tag: null,
  domain: null,
};

// ── Cleanup ─────────────────────────────────────────────────────────────────

afterEach(() => {
  cleanup();
});

// ── Tests ────────────────────────────────────────────────────────────────────

describe("Graph-to-Learn 'Send to Learn' handoff", () => {
  // Action transfers a valid node/depth or GraphSlice into the Learn tab input.
  it("handoff passes node/depth", () => {
    const state = buildNodePrefill("n1", 3, "out");

    // Must be tagged correctly.
    expect(state.learnPrefill).toBe(true);

    // Node-depth fields must be present.
    expect(state.node).toBe("n1");
    expect(state.maxDepth).toBe(3);
    expect(state.direction).toBe("out");

    // Explicit-slice field must NOT be present.
    expect(state.graphSlice).toBeUndefined();

    // Must pass the consumer's type guard.
    expect(isLearnPrefillState(state)).toBe(true);

    // NodeInspector wiring: "Send to Learn" button fires onSendToLearn.
    const spy = vi.fn();
    render(
      <NodeInspector
        node={minimalNode}
        edges={[]}
        onFocusEgo={() => {}}
        onOpenInNodes={() => {}}
        onOpenInEditor={() => {}}
        onSendToLearn={spy}
      />,
    );

    const btn = screen.getByTestId("node-inspector-send-to-learn");
    expect(btn).toBeTruthy();
    fireEvent.click(btn);
    expect(spy).toHaveBeenCalledTimes(1);
  });

  // Action transfers a valid node/depth or GraphSlice into the Learn tab input.
  it("handoff passes GraphSlice", () => {
    const slice = {
      nodes: [minimalNode],
      edges: [minimalEdge],
      meta: minimalMeta,
    };

    const state = buildSlicePrefill(slice);

    // Must be tagged correctly.
    expect(state.learnPrefill).toBe(true);

    // Node-depth fields must NOT be present.
    expect(state.node).toBeUndefined();
    expect(state.maxDepth).toBeUndefined();
    expect(state.direction).toBeUndefined();

    // graphSlice must be present with correct shape.
    expect(state.graphSlice).toBeDefined();
    expect(state.graphSlice!.nodes).toEqual([minimalNode]);
    expect(state.graphSlice!.edges).toEqual([minimalEdge]);

    // CRITICAL: meta content must land under `metadata`, NOT `meta`.
    expect((state.graphSlice as unknown as Record<string, unknown>)["meta"]).toBeUndefined();
    expect(state.graphSlice!.metadata).toBeDefined();
    // All meta fields are preserved in metadata.
    expect(state.graphSlice!.metadata!["node_count"]).toBe(1);
    expect(state.graphSlice!.metadata!["scope"]).toBe("ego");
    expect(state.graphSlice!.metadata!["selected_node"]).toBe("n1");

    // Must pass the consumer's type guard.
    expect(isLearnPrefillState(state)).toBe(true);

    // NodeInspector wiring: "Send slice to Learn" button fires onSendSliceToLearn.
    const spy = vi.fn();
    render(
      <NodeInspector
        node={minimalNode}
        edges={[minimalEdge]}
        onFocusEgo={() => {}}
        onOpenInNodes={() => {}}
        onOpenInEditor={() => {}}
        onSendSliceToLearn={spy}
      />,
    );

    const btn = screen.getByTestId("node-inspector-send-slice-to-learn");
    expect(btn).toBeTruthy();
    fireEvent.click(btn);
    expect(spy).toHaveBeenCalledTimes(1);
  });
});
