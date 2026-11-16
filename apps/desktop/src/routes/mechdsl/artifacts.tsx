import { useState } from "react";
import { useMutation } from "@tanstack/react-query";

import { getMechdslArtifact, type MechdslArtifact } from "../../api/mechdsl";

export default function MechdslArtifactsRoute() {
  const [artifactId, setArtifactId] = useState("");

  const mutation = useMutation<MechdslArtifact, Error>({
    mutationFn: () => getMechdslArtifact(artifactId),
  });

  return (
    <div className="space-y-4 p-6">
      <h1 className="text-xl font-semibold">Artifacts</h1>
      <form
        className="flex max-w-lg gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (artifactId.trim()) mutation.mutate();
        }}
      >
        <input
          className="flex-1 rounded border border-border bg-background px-2 py-1 text-sm"
          value={artifactId}
          onChange={(e) => setArtifactId(e.target.value)}
          placeholder="artifact id (from a Compile result)"
        />
        <button
          type="submit"
          disabled={!artifactId.trim() || mutation.isPending}
          className="rounded bg-accent px-3 py-1.5 text-sm font-semibold text-accent-foreground disabled:opacity-50"
        >
          {mutation.isPending ? "Loading…" : "Fetch"}
        </button>
      </form>

      {mutation.isError && (
        <p className="text-sm text-destructive">
          Not found: {mutation.error.message}
        </p>
      )}

      {mutation.data && (
        <pre className="overflow-auto rounded border border-border p-4 text-xs">
          {JSON.stringify(mutation.data, null, 2)}
        </pre>
      )}
    </div>
  );
}
