import type { ReactNode } from "react";

import { useAkmsStatus } from "../hooks/app";

interface Props {
  children: ReactNode;
  feature?: "picker" | "graph";
}

const INSTALL_COMMAND = "uv pip install -e './services/logic_loom_api[akms]'";

export function AkmsGate({ children, feature = "picker" }: Props) {
  const { data, isLoading } = useAkmsStatus();
  const available = feature === "graph" ? data?.graph_available : data?.available;

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        Checking AKMS availability…
      </div>
    );
  }

  if (!available) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
        <div className="text-lg font-semibold">
          {feature === "graph" ? "AKMS graph not available" : "AKMS not available"}
        </div>
        <p className="max-w-md text-sm text-muted-foreground">
          {feature === "graph"
            ? "This tab requires the AKMS core graph package and a graph source vault."
            : "This tab requires the AKMS package."}
          {feature === "graph" && !data?.graph_package_installed && (
            <span className="block mt-1">
              Source/dev setup: <code className="rounded bg-muted px-1.5 py-0.5 text-xs">{INSTALL_COMMAND}</code>
              <span className="block mt-1">
                Packaged app note: desktop builds use an isolated sidecar environment, so rebuild the app after bundling the sibling AKMS packages.
              </span>
            </span>
          )}
          {feature !== "graph" && !data?.package_installed && (
            <span className="block mt-1">
              Source/dev setup: <code className="rounded bg-muted px-1.5 py-0.5 text-xs">{INSTALL_COMMAND}</code>
              <span className="block mt-1">
                Packaged app note: desktop builds use an isolated sidecar environment, so rebuild the app after bundling the sibling AKMS packages.
              </span>
            </span>
          )}
          {feature === "graph" && data?.graph_package_installed && !data?.akms_global_vault_exists && (
            <span className="block mt-1">
              Graph source vault not found at <code className="rounded bg-muted px-1.5 py-0.5 text-xs">{data.akms_global_vault}</code>.
              Set <code className="rounded bg-muted px-1.5 py-0.5 text-xs">akms_global_vault</code> in Settings.
            </span>
          )}
        </p>
      </div>
    );
  }

  return <>{children}</>;
}
