import { useState } from "react";
import { useMutation } from "@tanstack/react-query";

import { compileMechdsl, type MechdslCompileResult } from "../../api/mechdsl";

export default function MechdslCompileRoute() {
  const [nodeId, setNodeId] = useState("");
  const [runVerify, setRunVerify] = useState(false);

  const mutation = useMutation<MechdslCompileResult, Error>({
    mutationFn: () => compileMechdsl({ node_id: nodeId, run_verify: runVerify }),
  });

  return (
    <div className="space-y-4 p-6">
      <h1 className="text-xl font-semibold">Compile</h1>
      <form
        className="max-w-lg space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (nodeId.trim()) mutation.mutate();
        }}
      >
        <label className="block text-sm">
          <span className="mb-1 block text-muted-foreground">AKMS node id</span>
          <input
            className="w-full rounded border border-border bg-background px-2 py-1 text-sm"
            value={nodeId}
            onChange={(e) => setNodeId(e.target.value)}
            placeholder="compmech.node.radial_return_j2"
          />
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={runVerify}
            onChange={(e) => setRunVerify(e.target.checked)}
          />
          <span>Run verify (pays the Taichi cost)</span>
        </label>
        <button
          type="submit"
          disabled={!nodeId.trim() || mutation.isPending}
          className="rounded bg-accent px-3 py-1.5 text-sm font-semibold text-accent-foreground disabled:opacity-50"
        >
          {mutation.isPending ? "Compiling…" : "Compile"}
        </button>
      </form>

      {mutation.isError && (
        <p className="text-sm text-destructive">
          Compile failed: {mutation.error.message}
        </p>
      )}

      {mutation.data && (
        <section className="space-y-2 rounded border border-border p-4">
          <div className="text-sm">
            Artifact{" "}
            <code className="rounded bg-muted px-1.5 py-0.5 text-xs">
              {mutation.data.artifact_id}
            </code>{" "}
            — status {mutation.data.status ?? "unknown"}
          </div>
          {mutation.data.warnings.length > 0 && (
            <ul className="list-disc pl-5 text-xs text-muted-foreground">
              {mutation.data.warnings.map((w, i) => (
                <li key={`${i}-${w}`}>{w}</li>
              ))}
            </ul>
          )}
          {mutation.data.vv_plan && (
            <pre className="overflow-auto text-xs">
              {JSON.stringify(mutation.data.vv_plan, null, 2)}
            </pre>
          )}
        </section>
      )}
    </div>
  );
}
