import type { ReactNode } from "react";

import { useRecordsStatus } from "../hooks/records";
import { ConfigSnippet } from "./ConfigSnippet";

interface Props {
  children: ReactNode;
}

const INSTALL_COMMAND = "uv sync --all-packages --extra records";

/**
 * Gates the Records mode subtree on `GET /api/records/status`.
 *
 * Mirrors MechdslGate / FailureMemoryGate: while the optional (and upstream
 * EXPERIMENTAL) `akms-records` package is missing, or no store is configured,
 * the tab explains what to do instead of rendering the children. The sidecar
 * keeps running either way.
 */
export function RecordsGate({ children }: Props) {
  const { data, isLoading, error } = useRecordsStatus();

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        Checking akms-records availability…
      </div>
    );
  }

  if (error || !data) {
    return (
      <Explain title="Records status unavailable">
        The sidecar did not answer <code>GET /api/records/status</code>. The rest
        of Logic-Loom is unaffected.
      </Explain>
    );
  }

  if (!data.available) {
    return (
      <Explain title="akms-records is not installed">
        {data.reason}
        <span className="mt-2 block">
          Source/dev setup:{" "}
          <code className="rounded bg-muted px-1.5 py-0.5 text-xs">{INSTALL_COMMAND}</code>
        </span>
        <span className="mt-1 block">
          Packaged builds use an isolated sidecar environment, so the package must
          be bundled at build time.
        </span>
      </Explain>
    );
  }

  if (data.stores.length === 0) {
    return (
      <Explain title="No record stores are configured">
        Logic-Loom never discovers record stores by scanning your filesystem — a
        store is visible only when you list it in <code>config.toml</code>:
        <ConfigSnippet
          snippet={`[[records_stores]]
id = "my-project"
scope = "project:my-project"
repo = "/path/to/my-project"   # records in <repo>/.knowledge/records

[[records_stores]]
id = "scout"
scope = "agent:scout"          # ~/.knowledge/records/agent/scout`}
        />
      </Explain>
    );
  }

  return <>{children}</>;
}

function Explain({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
      <div className="text-lg font-semibold">{title}</div>
      <div className="max-w-xl text-sm text-muted-foreground">{children}</div>
    </div>
  );
}
