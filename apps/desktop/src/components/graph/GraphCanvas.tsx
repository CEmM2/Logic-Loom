import "@xyflow/react/dist/style.css";

import { useEffect, useMemo, useState } from "react";
import { Background, Controls, MiniMap, ReactFlow, type ReactFlowInstance } from "@xyflow/react";

import type { GraphEdge, GraphNode } from "../../api/graph";
import { buildFlowElements, computeFlowLayout } from "./layout";

const GRAPH_MIN_ZOOM = 0.02;
const GRAPH_MAX_ZOOM = 2.25;

interface Props {
  nodes: GraphNode[];
  edges: GraphEdge[];
  selectedNodeId: string | null;
  onSelectNode: (nodeId: string) => void;
}

export function GraphCanvas({ nodes, edges, selectedNodeId, onSelectNode }: Props) {
  const [reactFlow, setReactFlow] = useState<ReactFlowInstance | null>(null);
  const positions = useMemo(() => computeFlowLayout(nodes, edges), [nodes, edges]);
  const flow = useMemo(
    () => buildFlowElements(nodes, edges, selectedNodeId, positions),
    [nodes, edges, positions, selectedNodeId],
  );

  useEffect(() => {
    if (!reactFlow || nodes.length === 0) return;

    const raf = window.requestAnimationFrame(() => {
      reactFlow.fitView({
        padding: 0.18,
        duration: 250,
        minZoom: GRAPH_MIN_ZOOM,
        maxZoom: GRAPH_MAX_ZOOM,
      });
    });

    return () => window.cancelAnimationFrame(raf);
  }, [reactFlow, positions, nodes.length]);

  if (nodes.length === 0) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-sm text-muted-foreground">
        No graph nodes match the current view.
      </div>
    );
  }

  return (
    <div className="h-full w-full bg-slate-50/60">
      <ReactFlow
        nodes={flow.nodes}
        edges={flow.edges}
        fitView
        fitViewOptions={{ padding: 0.18, minZoom: GRAPH_MIN_ZOOM, maxZoom: GRAPH_MAX_ZOOM }}
        minZoom={GRAPH_MIN_ZOOM}
        maxZoom={GRAPH_MAX_ZOOM}
        nodesDraggable={false}
        nodesConnectable={false}
        onInit={setReactFlow}
        onNodeClick={(_, node) => onSelectNode(String(node.id))}
        proOptions={{ hideAttribution: true }}
      >
        <MiniMap pannable zoomable />
        <Controls position="bottom-right" />
        <Background gap={18} size={1} color="#cbd5e1" />
      </ReactFlow>
    </div>
  );
}
