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

import type { RecordsGraph } from "../../api/records";
import {
  EDGE_STYLE,
  NODE_WIDTH,
  layoutGraph,
  nodeBackground,
  schemaColor,
} from "./graphLayout";

interface Props {
  graph: RecordsGraph;
  onSelectRecord?: (recordId: string) => void;
  emptyMessage?: string;
}

/**
 * The record graph: records (coloured by schema), the records they cite or
 * supersede, the AKMS nodes they were promoted to, and external source refs.
 * Built from envelope fields only — payloads are user-defined, so no payload
 * key is ever read as an edge.
 */
export function RecordsGraphCanvas({ graph, onSelectRecord, emptyMessage }: Props) {
  const { nodes, edges } = useMemo(() => buildFlow(graph), [graph]);

  if (nodes.length === 0) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-sm text-muted-foreground">
        {emptyMessage ?? "No records to draw."}
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
        nodesDraggable
        nodesConnectable={false}
        onNodeClick={(_, node) => {
          if (node.data?.kind === "record") onSelectRecord?.(node.id);
        }}
        proOptions={{ hideAttribution: true }}
      >
        <Controls position="bottom-right" />
        <Background gap={18} size={1} color="#cbd5e1" />
      </ReactFlow>
    </div>
  );
}

export function GraphLegend() {
  return (
    <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
      {Object.entries(EDGE_STYLE).map(([type, style]) => (
        <span key={type} className="inline-flex items-center gap-1">
          <span
            className="inline-block h-0 w-5 border-t-2"
            style={{ borderColor: style.stroke, borderStyle: style.dashed ? "dashed" : "solid" }}
          />
          {type}
        </span>
      ))}
    </div>
  );
}

function buildFlow(graph: RecordsGraph): { nodes: Node[]; edges: Edge[] } {
  const positions = layoutGraph(graph);
  const nodes: Node[] = graph.nodes.map((node) => {
    const isFocus = graph.focus === node.id;
    const accent = node.kind === "record" ? schemaColor(node.schema_ref) : "#cbd5e1";
    const subtitle =
      node.kind === "record"
        ? `${node.schema_ref} · ${node.lifecycle}${node.review_state ? ` · ${node.review_state}` : ""}`
        : node.kind === "akms_node"
          ? "AKMS node"
          : node.kind === "external"
            ? `source: ${node.ref_kind}`
            : "not in this store";
    return {
      id: node.id,
      position: positions.get(node.id) ?? { x: 0, y: 0 },
      data: {
        kind: node.kind,
        label: (
          <div className="text-left" title={node.id}>
            <div className="truncate text-[11px] font-semibold">{node.label}</div>
            <div className="truncate text-[10px] opacity-70">{subtitle}</div>
          </div>
        ),
      },
      style: {
        width: NODE_WIDTH,
        borderRadius: 6,
        fontSize: 11,
        padding: 6,
        color: "#0f172a",
        border: `${isFocus ? 3 : 1}px solid ${accent}`,
        borderLeft: `6px solid ${accent}`,
        background: nodeBackground(node),
        opacity: node.kind === "record" && node.lifecycle !== "active" ? 0.75 : 1,
        cursor: node.kind === "record" ? "pointer" : "default",
      },
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
    };
  });

  const edges: Edge[] = graph.edges.map((edge) => {
    const style = EDGE_STYLE[edge.type] ?? EDGE_STYLE.source_ref;
    return {
      id: edge.id,
      source: edge.source,
      target: edge.target,
      label: edge.label,
      labelStyle: { fontSize: 9 },
      style: { stroke: style.stroke, strokeDasharray: style.dashed ? "4 3" : undefined },
      markerEnd: { type: MarkerType.ArrowClosed, color: style.stroke },
    };
  });

  return { nodes, edges };
}
