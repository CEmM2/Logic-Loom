import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { getFirstRunStatus } from "../api/app";

/**
 * A config.toml snippet with a Copy button and the path of the file to paste it
 * into. Used by the empty states of modes that are configured only through
 * config.toml (Failure Memory, Records), so the screen offers a concrete next
 * step instead of a bare example.
 */
export function ConfigSnippet({ snippet }: { snippet: string }) {
  const [copied, setCopied] = useState<"idle" | "copied" | "failed">("idle");
  const pathQuery = useQuery({
    queryKey: ["app", "config-path"],
    queryFn: async () => (await getFirstRunStatus()).config_path,
    staleTime: Infinity,
  });

  async function copy() {
    try {
      await navigator.clipboard.writeText(snippet);
      setCopied("copied");
    } catch {
      setCopied("failed");
    }
  }

  return (
    <div className="mt-3 text-left">
      <pre className="overflow-x-auto rounded bg-muted p-3 text-xs">{snippet}</pre>
      <div className="mt-2 flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => void copy()}
          className="rounded border border-border px-2.5 py-1 text-xs font-medium hover:bg-muted"
        >
          {copied === "copied" ? "Copied" : "Copy snippet"}
        </button>
        {copied === "failed" && (
          <span className="text-xs text-destructive">Copy failed. Select the text above instead.</span>
        )}
      </div>
      <p className="mt-2 text-xs">
        Paste it into{" "}
        {pathQuery.data ? (
          <code className="break-all rounded bg-muted px-1.5 py-0.5">{pathQuery.data}</code>
        ) : (
          <code className="rounded bg-muted px-1.5 py-0.5">config.toml</code>
        )}
        , then restart Logic-Loom.
      </p>
    </div>
  );
}
