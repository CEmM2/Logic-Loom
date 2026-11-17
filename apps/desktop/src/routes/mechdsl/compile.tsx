import { useState } from "react";
import { useMutation } from "@tanstack/react-query";

import { compileMechdsl, type MechdslCompileResult } from "../../api/mechdsl";

export const EXAMPLE_NODE_ID = "compmech.node.radial_return_j2";

export default function MechdslCompileRoute() {
  const [nodeId, setNodeId] = useState("");
  const [runVerify, setRunVerify] = useState(false);
  const [emptyHint, setEmptyHint] = useState(false);

  const mutation = useMutation<MechdslCompileResult, Error>({
    mutationFn: () => compileMechdsl({ node_id: nodeId.trim(), run_verify: runVerify }),
  });

  return (
    <div className="space-y-4 p-6">
      <h1 className="text-xl font-semibold">Compile</h1>
      <form
        className="max-w-lg space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          // The example id is placeholder text, not a value: an empty field used
          // to make Compile silently do nothing. Say so instead.
          if (!nodeId.trim()) {
            setEmptyHint(true);
            return;
          }
          setEmptyHint(false);
          mutation.mutate();
        }}
      >
        <label className="block text-sm">
          <span className="mb-1 block text-muted-foreground">AKMS node id</span>
          <input
            className="w-full rounded border border-border bg-background px-2 py-1 text-sm"
            value={nodeId}
            onChange={(e) => {
              setNodeId(e.target.value);
              if (e.target.value.trim()) setEmptyHint(false);
            }}
            placeholder={`e.g. ${EXAMPLE_NODE_ID}`}
            aria-describedby="mechdsl-node-help"
            data-testid="mechdsl-node-id"
          />
          <span id="mechdsl-node-help" className="mt-1 block text-xs text-muted-foreground">
            Type the id of an AKMS node from the graph (the grey text is only an
            example).
          </span>
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
          disabled={mutation.isPending}
          className="rounded bg-accent px-3 py-1.5 text-sm font-semibold text-accent-foreground disabled:opacity-50"
        >
          {mutation.isPending ? "Compiling…" : "Compile"}
        </button>
        {emptyHint && (
          <p role="alert" className="text-sm text-destructive">
            Enter an AKMS node id first.
          </p>
        )}
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
