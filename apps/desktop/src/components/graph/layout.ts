import {
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from "d3-force";
import { MarkerType, type Edge, type Node, type XYPosition } from "@xyflow/react";

import type { GraphEdge, GraphNode } from "../../api/graph";

const NODE_WIDTH = 220;
const NODE_HEIGHT = 88;
const LAYOUT_PADDING = 72;
const COMPONENT_HORIZONTAL_GAP = 620;
const COMPONENT_VERTICAL_GAP = 480;
const DOMAIN_RING_BASE = 120;
const NODE_RING_BASE = 58;
const NODE_COLLISION_RADIUS = 116;

type LayoutPosition = XYPosition;

type FlowNodeData = {
  label: string;
  meta: GraphNode;
};

type LayoutNode = SimulationNodeDatum & {
  id: string;
  domainKey: string;
  componentIndex: number;
  anchorX: number;
  anchorY: number;
};

type LayoutLink = SimulationLinkDatum<LayoutNode> & {
  source: string | LayoutNode;
  target: string | LayoutNode;
  weight: number;
};

export function computeFlowLayout(nodes: GraphNode[], edges: GraphEdge[]): Map<string, LayoutPosition> {
  if (nodes.length === 0) {
    return new Map();
  }

  const components = findConnectedComponents(nodes, edges);
  const componentCenters = buildComponentCenters(components.length);
  const domainAnchors = buildDomainAnchors(nodes, components, componentCenters);
  const nodeLookup = new Map(nodes.map((node) => [node.id, node]));

  const simulationNodes: LayoutNode[] = [];
  for (let componentIndex = 0; componentIndex < components.length; componentIndex += 1) {
    const componentNodeIds = components[componentIndex];
    const groupedByDomain = new Map<string, GraphNode[]>();
    for (const nodeId of componentNodeIds) {
      const node = nodeLookup.get(nodeId);
      if (!node) continue;
      const domainKey = normalizeDomain(node.domain);
      const bucket = groupedByDomain.get(domainKey) ?? [];
      bucket.push(node);
      groupedByDomain.set(domainKey, bucket);
    }

    for (const [domainKey, bucket] of Array.from(groupedByDomain.entries()).sort(([a], [b]) => a.localeCompare(b))) {
      const anchor = domainAnchors.get(`${componentIndex}:${domainKey}`) ?? componentCenters[componentIndex] ?? { x: 0, y: 0 };
      const radius = bucket.length > 1 ? Math.min(140, NODE_RING_BASE + bucket.length * 10) : 0;
      bucket
        .slice()
        .sort((a, b) => a.id.localeCompare(b.id))
        .forEach((node, index) => {
          const angle = bucket.length <= 1 ? 0 : (2 * Math.PI * index) / bucket.length - Math.PI / 2;
          simulationNodes.push({
            id: node.id,
            domainKey,
            componentIndex,
            anchorX: anchor.x,
            anchorY: anchor.y,
            x: anchor.x + Math.cos(angle) * radius,
            y: anchor.y + Math.sin(angle) * radius,
          });
        });
    }
  }

  const validNodeIds = new Set(simulationNodes.map((node) => node.id));
  const simulationLinks: LayoutLink[] = edges
    .filter((edge) => validNodeIds.has(edge.source) && validNodeIds.has(edge.target))
    .map((edge) => ({
      source: edge.source,
      target: edge.target,
      weight: edge.weight ?? 1,
    }));

  const linkDistance = nodes.length > 80 ? 150 : nodes.length > 30 ? 165 : 180;
  const chargeStrength = nodes.length > 80 ? -180 : nodes.length > 30 ? -220 : -260;
  const iterations = Math.max(180, Math.min(320, nodes.length * 4));

  const simulation = forceSimulation(simulationNodes)
    .force(
      "link",
      forceLink<LayoutNode, LayoutLink>(simulationLinks)
        .id((node) => node.id)
        .distance((link) => {
          const source = link.source as LayoutNode;
          const target = link.target as LayoutNode;
          return source.domainKey === target.domainKey ? linkDistance - 18 : linkDistance;
        })
        .strength((link) => {
          const source = link.source as LayoutNode;
          const target = link.target as LayoutNode;
          const sameDomain = source.domainKey === target.domainKey;
          return sameDomain ? 0.2 : 0.12;
        }),
    )
    .force("charge", forceManyBody<LayoutNode>().strength(chargeStrength))
    .force("collision", forceCollide<LayoutNode>(NODE_COLLISION_RADIUS).strength(1))
    .force("anchor-x", forceX<LayoutNode>((node) => node.anchorX).strength(0.08))
    .force("anchor-y", forceY<LayoutNode>((node) => node.anchorY).strength(0.08))
    .stop();

  for (let tick = 0; tick < iterations; tick += 1) {
    simulation.tick();
  }
  simulation.stop();

  const laidOutNodes = simulationNodes.map((node) => ({
    id: node.id,
    x: (node.x ?? node.anchorX) - NODE_WIDTH / 2,
    y: (node.y ?? node.anchorY) - NODE_HEIGHT / 2,
  }));
  const minX = Math.min(...laidOutNodes.map((node) => node.x));
  const minY = Math.min(...laidOutNodes.map((node) => node.y));

  return new Map(
    laidOutNodes.map((node) => [
      node.id,
      {
        x: node.x - minX + LAYOUT_PADDING,
        y: node.y - minY + LAYOUT_PADDING,
      },
    ]),
  );
}

export function buildFlowElements(
  nodes: GraphNode[],
  edges: GraphEdge[],
  selectedNodeId: string | null,
  positions: Map<string, LayoutPosition> = computeFlowLayout(nodes, edges),
): {
  nodes: Node<FlowNodeData>[];
  edges: Edge[];
} {
  return {
    nodes: nodes.map((node) => {
      const pos = positions.get(node.id) ?? { x: 0, y: 0 };
      const selected = node.id === selectedNodeId;
      return {
        id: node.id,
        position: pos,
        data: {
          label: node.title || node.id,
          meta: node,
        },
        draggable: false,
        selectable: true,
        style: {
          width: NODE_WIDTH,
          borderRadius: 14,
          border: `2px solid ${selected ? "#2563eb" : statusBorder(node.status)}`,
          background: statusBackground(node.status),
          color: "#111827",
          boxShadow: selected ? "0 0 0 3px rgba(37,99,235,0.15)" : "0 1px 3px rgba(15,23,42,0.08)",
          padding: 10,
        },
      } satisfies Node<FlowNodeData>;
    }),
    edges: edges.map((edge) => ({
      id: `${edge.source}:${edge.target}:${edge.type ?? "edge"}`,
      source: edge.source,
      target: edge.target,
      label: edge.type ?? undefined,
      animated: edge.type === "pitfall",
      markerEnd: { type: MarkerType.ArrowClosed },
      style: {
        stroke: edge.type === "pitfall" ? "#dc2626" : "#64748b",
        strokeWidth: edge.type === "requires" ? 2 : 1.5,
      },
      labelStyle: { fill: "#475569", fontSize: 11 },
    } satisfies Edge)),
  };
}

function statusBorder(status: string | null): string {
  switch (status) {
    case "established":
      return "#059669";
    case "tentative":
      return "#d97706";
    case "deprecated":
      return "#dc2626";
    default:
      return "#94a3b8";
  }
}

function statusBackground(status: string | null): string {
  switch (status) {
    case "established":
      return "#ecfdf5";
    case "tentative":
      return "#fffbeb";
    case "deprecated":
      return "#fef2f2";
    default:
      return "#f8fafc";
  }
}

function normalizeDomain(domain: string | null): string {
  return domain?.trim() || "(unknown domain)";
}

function findConnectedComponents(nodes: GraphNode[], edges: GraphEdge[]): string[][] {
  const adjacency = new Map(nodes.map((node) => [node.id, new Set<string>()]));

  for (const edge of edges) {
    adjacency.get(edge.source)?.add(edge.target);
    adjacency.get(edge.target)?.add(edge.source);
  }

  const remaining = new Set(nodes.map((node) => node.id));
  const components: string[][] = [];

  while (remaining.size > 0) {
    const start = Array.from(remaining).sort()[0];
    remaining.delete(start);

    const queue = [start];
    const component: string[] = [];

    while (queue.length > 0) {
      const current = queue.shift();
      if (!current) continue;
      component.push(current);

      for (const neighbor of Array.from(adjacency.get(current) ?? []).sort()) {
        if (!remaining.has(neighbor)) continue;
        remaining.delete(neighbor);
        queue.push(neighbor);
      }
    }

    components.push(component.sort((a, b) => a.localeCompare(b)));
  }

  return components;
}

function buildComponentCenters(componentCount: number): LayoutPosition[] {
  const columns = Math.max(1, Math.ceil(Math.sqrt(componentCount)));
  return Array.from({ length: componentCount }, (_, index) => ({
    x: (index % columns) * COMPONENT_HORIZONTAL_GAP,
    y: Math.floor(index / columns) * COMPONENT_VERTICAL_GAP,
  }));
}

function buildDomainAnchors(
  nodes: GraphNode[],
  components: string[][],
  componentCenters: LayoutPosition[],
): Map<string, LayoutPosition> {
  const nodeLookup = new Map(nodes.map((node) => [node.id, node]));
  const anchors = new Map<string, LayoutPosition>();

  components.forEach((component, componentIndex) => {
    const center = componentCenters[componentIndex] ?? { x: 0, y: 0 };
    const domains = Array.from(
      new Set(
        component.map((nodeId) => normalizeDomain(nodeLookup.get(nodeId)?.domain ?? null)),
      ),
    ).sort((a, b) => a.localeCompare(b));

    const domainRadius = domains.length <= 1 ? 0 : Math.min(230, DOMAIN_RING_BASE + domains.length * 18);
    domains.forEach((domainKey, domainIndex) => {
      const angle = domains.length <= 1 ? 0 : (2 * Math.PI * domainIndex) / domains.length - Math.PI / 2;
      anchors.set(`${componentIndex}:${domainKey}`, {
        x: center.x + Math.cos(angle) * domainRadius,
        y: center.y + Math.sin(angle) * domainRadius,
      });
    });
  });

  return anchors;
}
