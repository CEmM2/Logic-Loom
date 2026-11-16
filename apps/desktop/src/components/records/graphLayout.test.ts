import { describe, expect, it } from "vitest";

import type { RecordsGraph } from "../../api/records";
import { layoutGraph, schemaColor } from "./graphLayout";

describe("records graph layout", () => {
  it("places a cited record to the right of the record citing it", () => {
    const graph: RecordsGraph = {
      nodes: [
        { id: "a", kind: "record", label: "A", schema_ref: "decision/v1", lifecycle: "active" },
        { id: "b", kind: "record", label: "B", schema_ref: "decision/v1", lifecycle: "active" },
      ],
      edges: [{ id: "cites:b->a", source: "b", target: "a", type: "cites", label: "cites" }],
      truncated: false,
      total_nodes: 2,
      focus: null,
    };
    const pos = layoutGraph(graph);
    expect(pos.get("a")!.x).toBeGreaterThan(pos.get("b")!.x);
  });

  it("gives every version of a schema the same colour", () => {
    expect(schemaColor("decision/v1")).toBe(schemaColor("decision/v2"));
    expect(schemaColor("decision/v1")).not.toBe(schemaColor("idea/v1"));
  });
});
