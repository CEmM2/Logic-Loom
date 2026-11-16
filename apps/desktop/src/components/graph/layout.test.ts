import { describe, expect, it } from "vitest";

import type { GraphEdge, GraphNode } from "../../api/graph";
import { buildFlowElements, computeFlowLayout } from "./layout";

describe("buildFlowElements", () => {
  it("maps graph data into positioned React Flow elements", () => {
    const nodes: GraphNode[] = [
      {
        id: "plasticity-von-mises",
        title: "Plasticity — von Mises",
        domain: "computational-mechanics",
        subdomain: null,
        status: "established",
        tags: ["plasticity"],
        confidence: 0.93,
        source: "human",
        node_origin: "global",
        file_path: "/tmp/plasticity-von-mises.md",
      },
      {
        id: "return-mapping",
        title: "Return mapping",
        domain: "computational-mechanics",
        subdomain: null,
        status: "tentative",
        tags: ["solver"],
        confidence: 0.81,
        source: "human",
        node_origin: "global",
        file_path: "/tmp/return-mapping.md",
      },
    ];
    const edges: GraphEdge[] = [
      {
        source: "plasticity-von-mises",
        target: "return-mapping",
        type: "requires",
        weight: 1,
        note: "",
        edge_origin: "global",
      },
    ];

    const flow = buildFlowElements(nodes, edges, "plasticity-von-mises");

    expect(flow.nodes).toHaveLength(2);
    expect(flow.edges).toHaveLength(1);
    expect(flow.nodes.map((node) => node.id)).toEqual([
      "plasticity-von-mises",
      "return-mapping",
    ]);
    expect(flow.edges[0].source).toBe("plasticity-von-mises");
    expect(flow.edges[0].target).toBe("return-mapping");
    expect(Number.isFinite(flow.nodes[0].position.x)).toBe(true);
    expect(Number.isFinite(flow.nodes[0].position.y)).toBe(true);
    expect(String(flow.nodes[0].style?.border)).toContain("#2563eb");
    expect(String(flow.nodes[1].style?.border)).toContain("#d97706");
  });

  it("produces deterministic positions and keeps disconnected components apart", () => {
    const nodes: GraphNode[] = [
      {
        id: "a",
        title: "A",
        domain: "mechanics",
        subdomain: null,
        status: "established",
        tags: [],
        confidence: null,
        source: "human",
        node_origin: "global",
        file_path: "/tmp/a.md",
      },
      {
        id: "b",
        title: "B",
        domain: "mechanics",
        subdomain: null,
        status: "tentative",
        tags: [],
        confidence: null,
        source: "human",
        node_origin: "global",
        file_path: "/tmp/b.md",
      },
      {
        id: "c",
        title: "C",
        domain: "materials",
        subdomain: null,
        status: "tentative",
        tags: [],
        confidence: null,
        source: "human",
        node_origin: "global",
        file_path: "/tmp/c.md",
      },
    ];
    const edges: GraphEdge[] = [
      {
        source: "a",
        target: "b",
        type: "requires",
        weight: 1,
        note: null,
        edge_origin: "global",
      },
    ];

    const first = computeFlowLayout(nodes, edges);
    const second = computeFlowLayout(nodes, edges);

    const firstA = first.get("a");
    const firstB = first.get("b");
    const firstC = first.get("c");
    const secondA = second.get("a");
    const secondB = second.get("b");
    const secondC = second.get("c");

    expect(firstA).toBeDefined();
    expect(firstB).toBeDefined();
    expect(firstC).toBeDefined();
    expect(secondA).toEqual(firstA);
    expect(secondB).toEqual(firstB);
    expect(secondC).toEqual(firstC);

    const connectedDistance = Math.hypot((firstA?.x ?? 0) - (firstB?.x ?? 0), (firstA?.y ?? 0) - (firstB?.y ?? 0));
    const disconnectedDistance = Math.hypot((firstA?.x ?? 0) - (firstC?.x ?? 0), (firstA?.y ?? 0) - (firstC?.y ?? 0));
    expect(disconnectedDistance).toBeGreaterThan(connectedDistance);

    const flow = buildFlowElements(nodes, edges, null, first);
    expect(flow.nodes.every((node) => Number.isFinite(node.position.x) && Number.isFinite(node.position.y))).toBe(true);
  });
});
