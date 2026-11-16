import dagre from "@dagrejs/dagre";

import type { RecordsGraph, RecordsGraphEdgeType, RecordsGraphNode } from "../../api/records";

export const NODE_WIDTH = 240;
export const NODE_HEIGHT = 56;

export const EDGE_STYLE: Record<RecordsGraphEdgeType, { stroke: string; dashed: boolean }> = {
  superseded_by: { stroke: "#dc2626", dashed: false },
  cites: { stroke: "#2563eb", dashed: false },
  references: { stroke: "#7c3aed", dashed: false },
  promoted_to: { stroke: "#059669", dashed: false },
  source_ref: { stroke: "#94a3b8", dashed: true },
};

/** A stable colour per schema name, so one schema reads as one group. */
export function schemaColor(schemaRef: string | undefined): string {
  const name = (schemaRef ?? "").split("/")[0];
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) | 0;
  return `hsl(${Math.abs(hash) % 360} 65% 45%)`;
}

export function nodeBackground(node: RecordsGraphNode): string {
  switch (node.kind) {
    case "akms_node":
      return "#ecfdf5";
    case "external":
      return "#f8fafc";
    case "missing_record":
      return "#fef2f2";
    default:
      return node.lifecycle === "active" ? "#ffffff" : "#f1f5f9";
  }
}

/** Left-to-right layered layout; records point at what they cite or became. */
export function layoutGraph(graph: RecordsGraph): Map<string, { x: number; y: number }> {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "LR", nodesep: 24, ranksep: 90 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const node of graph.nodes) g.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  for (const edge of graph.edges) g.setEdge(edge.source, edge.target);
  dagre.layout(g);
  const out = new Map<string, { x: number; y: number }>();
  for (const node of graph.nodes) {
    const pos = g.node(node.id);
    out.set(node.id, { x: (pos?.x ?? 0) - NODE_WIDTH / 2, y: (pos?.y ?? 0) - NODE_HEIGHT / 2 });
  }
  return out;
}
