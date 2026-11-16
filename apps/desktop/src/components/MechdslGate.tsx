import type { ReactNode } from "react";

import { useMechdslStatus } from "../hooks/mechdsl";

interface Props {
  children: ReactNode;
}

const INSTALL_COMMAND = "uv pip install -e './services/logic_loom_api[mechdsl]'";

/**
 * Gates the MechDSL mode subtree on `GET /api/mechdsl/status`. Mirrors
 * AkmsGate: while the optional `compmech_reference_pack` companion adapter
 * (+ the `mechdsl` core) is not installed, the tab shows an install hint
 * instead of the children — the sidecar still runs.
 */
export function MechdslGate({ children }: Props) {
  const { data, isLoading } = useMechdslStatus();

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        Checking MechDSL availability…
      </div>
    );
  }

  if (!data?.available) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
        <div className="text-lg font-semibold">MechDSL not available</div>
        <p className="max-w-md text-sm text-muted-foreground">
          This tab requires the MechDSL executable-bridge plugin (
          <code className="rounded bg-muted px-1.5 py-0.5 text-xs">
            compmech_reference_pack
          </code>{" "}
          + the{" "}
          <code className="rounded bg-muted px-1.5 py-0.5 text-xs">mechdsl</code>{" "}
          core).
          {data?.reason && <span className="mt-1 block">{data.reason}</span>}
          <span className="mt-1 block">
            Source/dev setup:{" "}
            <code className="rounded bg-muted px-1.5 py-0.5 text-xs">
              {INSTALL_COMMAND}
            </code>
          </span>
          <span className="mt-1 block">
            Packaged app note: desktop builds use an isolated sidecar
            environment, so rebuild the app after bundling the MechDSL packages.
          </span>
        </p>
      </div>
    );
  }

  return <>{children}</>;
}
