import type { ReactNode } from "react";

import { useFailureMemoryProjects } from "../hooks/failureMemory";
import { ConfigSnippet } from "./ConfigSnippet";

interface Props {
  children: ReactNode;
}

const INSTALL_COMMAND = "uv sync --all-packages --extra failure-memory";

/**
 * Gates the Failure Memory mode subtree on `GET /api/failure-memory/projects`.
 *
 * Mirrors AkmsGate / MechdslGate: while the optional `akms-failure-memory`
 * package is not installed — or the workspace is switched off, or no project is
 * configured — the tab shows an explanation instead of the children, and the
 * sidecar keeps running. The three unavailable reasons are distinguished, since
 * "not installed" and "installed but nothing configured" need opposite fixes.
 */
export function FailureMemoryGate({ children }: Props) {
  const { data, isLoading, error } = useFailureMemoryProjects();

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        Checking failure-memory availability…
      </div>
    );
  }

  if (error || !data) {
    return (
      <Explain title="Failure memory status unavailable">
        The sidecar did not answer <code>GET /api/failure-memory/projects</code>.
        The rest of Logic-Loom is unaffected.
      </Explain>
    );
  }

  if (!data.enabled) {
    return (
      <Explain title="Failure memory is disabled">
        <code>failure_memory_mode</code> is <code>"disabled"</code> in your
        Logic-Loom configuration. Set it to <code>"read-only"</code> (the default)
        to inspect projects.
      </Explain>
    );
  }

  if (!data.package_installed) {
    return (
      <Explain title="akms-failure-memory is not installed">
        {data.degradation?.reason.message}
        <span className="mt-2 block">
          Source/dev setup:{" "}
          <code className="rounded bg-muted px-1.5 py-0.5 text-xs">
            {INSTALL_COMMAND}
          </code>
        </span>
        <span className="mt-1 block">
          Packaged builds use an isolated sidecar environment, so the package must
          be bundled at build time.
        </span>
      </Explain>
    );
  }

  if (data.projects.length === 0) {
    return (
      <Explain title="No failure-memory projects are configured">
        Logic-Loom never discovers <code>.failure-memory</code> directories by
        scanning your filesystem — a project is visible only when you list it.
        Add an entry like this one for each project:
        <ConfigSnippet
          snippet={`failure_memory_mode = "read-only"

[[failure_memory_projects]]
id = "logic-loom"
config = "/path/to/Logic-Loom/.failure-memory/config.toml"`}
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
