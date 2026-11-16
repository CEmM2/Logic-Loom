import "@xyflow/react/dist/style.css";

import { useMemo } from "react";
import {
  Background,
  Controls,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";

import type { Overlay, OverlayEdgeType } from "../../api/failureMemory";

/**
 * Renders the failure-memory overlay.
 *
 * Deliberately its own canvas rather than nodes injected into the AKMS graph
 * view: failure memory is an OVERLAY on the source graph, not a second writer
 * into it. Node ids arrive from the backend already prefixed with the project's
 * `repository_id` (`RepoAlpha::file:src/config.py`), so displaying two projects
 * side by side can never collapse two repositories' identically named files
 * into one node — the ids simply do not collide.
 *
 * Layout is a simple three-column arrangement (preview → source files → lesson
 * nodes) rather than the force simulation used by the AKMS graph: the overlay is
 * inherently layered, and a layered graph drawn by a force layout mostly
 * communicates that a force layout was used.
 */

const EDGE_STYLE: Record<OverlayEdgeType, { stroke: string; dashed: boolean }> = {
  failure_route: { stroke: "#64748b", dashed: false },
  load_with: { stroke: "#8b5cf6", dashed: true },
  selected_required: { stroke: "#dc2626", dashed: false },
  selected_coactivated: { stroke: "#d97706", dashed: false },
  selected_advisory: { stroke: "#2563eb", dashed: true },
};

const COLUMN_X = { preview: 0, source_file: 360, failure_lesson: 760 };
const ROW_HEIGHT = 78;

interface Props {
  overlay: Overlay;
  emptyMessage?: string;
}

export function OverlayCanvas({ overlay, emptyMessage }: Props) {
  const { nodes, edges } = useMemo(() => buildFlow(overlay), [overlay]);

  if (nodes.length === 0) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-sm text-muted-foreground">
        {emptyMessage ?? "No overlay nodes for this project yet."}
      </div>
    );
  }

  return (
    <div className="h-full w-full">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        nodesDraggable={false}
        nodesConnectable={false}
        proOptions={{ hideAttribution: true }}
      >
        <Controls position="bottom-right" />
        <Background gap={18} size={1} color="#cbd5e1" />
      </ReactFlow>
    </div>
  );
}

function buildFlow(overlay: Overlay): { nodes: Node[]; edges: Edge[] } {
  const columns: Record<string, number> = { preview: 0, source_file: 0, failure_lesson: 0 };

  const nodes: Node[] = overlay.nodes.map((node) => {
    const column = node.id.includes("::preview:") ? "preview" : node.kind;
    const row = columns[column]++;
    const selected = node.selection_class;
    return {
      id: node.id,
      position: { x: COLUMN_X[column as keyof typeof COLUMN_X], y: row * ROW_HEIGHT },
      data: {
        label: (
          <div className="text-left">
            <div className="truncate text-[11px] font-semibold">{node.label}</div>
            <div className="truncate text-[10px] opacity-70">
              {node.repository_id}
              {selected ? ` · ${selected}` : ""}
              {node.documentation ? " · docs" : ""}
              {node.exists === false ? " · missing in checkout" : ""}
            </div>
          </div>
        ),
      },
      style: {
        width: 300,
        borderRadius: 6,
        fontSize: 11,
        padding: 6,
        border: `1px solid ${selected ? EDGE_STYLE[`selected_${selected}` as OverlayEdgeType].stroke : "#cbd5e1"}`,
        background: node.kind === "failure_lesson" ? "#fff7ed" : "#f8fafc",
      },
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
    };
  });

  const edges: Edge[] = overlay.edges.map((edge) => {
    const style = EDGE_STYLE[edge.type];
    return {
      id: edge.id,
      source: edge.source,
      target: edge.target,
      label: edge.type,
      labelStyle: { fontSize: 9 },
      style: {
        stroke: style.stroke,
        strokeDasharray: style.dashed ? "4 3" : undefined,
      },
      markerEnd: { type: MarkerType.ArrowClosed, color: style.stroke },
    };
  });

  return { nodes, edges };
}
