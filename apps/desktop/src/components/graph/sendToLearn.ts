/**
 * Pure state-builder helpers for the Graph-to-Learn "Send to Learn" handoff.
 *
 * These functions build `LearnPrefillState` objects consumed by `LearnTab` on
 * mount via `useLocation().state`. They contain no side-effects and are
 * unit-testable without mounting the graph route.
 *
 * Two handoff forms:
 *   - `buildNodePrefill`  → form B: node + depth + direction (resolved
 *     server-side via `graph_adapter.ego_slice`). No client-side slicing needed.
 *   - `buildSlicePrefill` → form A: explicit `GraphSlice`, takes precedence.
 *     CRITICAL: maps `GraphSlice.meta` → `LearnGraphSlice.metadata` to match the
 *     backend `GraphSlicePayload`. Sending a raw `meta` key would be silently
 *     dropped by pydantic.
 */

import type { GraphDirection, GraphSlice } from "../../api/graph";
import type { LearnPrefillState } from "../../routes/learn/prefill";

/**
 * Build a node-depth prefill payload (form B).
 *
 * The backend resolves the ego slice server-side; the client only passes the
 * node id, depth, and traversal direction.
 */
export function buildNodePrefill(
  node: string,
  maxDepth: number,
  direction: GraphDirection,
): LearnPrefillState {
  return {
    learnPrefill: true,
    node,
    maxDepth,
    direction,
  };
}

/**
 * Build an explicit-slice prefill payload (form A).
 *
 * Maps `GraphSlice.meta` (GraphMeta) → `LearnGraphSlice.metadata`
 * (Record<string, unknown>) so the backend `GraphSlicePayload` receives the
 * correct key. `nodes`/`edges` pass through unchanged (same types).
 *
 * The returned payload contains NO `meta` key — only `graphSlice.metadata`.
 */
export function buildSlicePrefill(slice: GraphSlice): LearnPrefillState {
  return {
    learnPrefill: true,
    graphSlice: {
      nodes: slice.nodes,
      edges: slice.edges,
      metadata: slice.meta ? { ...slice.meta } : {},
    },
  };
}
