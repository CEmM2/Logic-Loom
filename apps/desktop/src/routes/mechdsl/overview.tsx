import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";

import {
  useInstallTaichi,
  useMechdslRuntime,
  useMechdslStatus,
} from "../../hooks/mechdsl";

export default function MechdslOverviewRoute() {
  const { data } = useMechdslStatus();
  const { data: runtime } = useMechdslRuntime();
  const install = useInstallTaichi();
  const qc = useQueryClient();

  // Once taichi lands, refresh status so verify_available flips on.
  const installState = runtime?.install_status;
  useEffect(() => {
    if (installState === "installed") {
      void qc.invalidateQueries({ queryKey: ["mechdsl", "status"] });
    }
  }, [installState, qc]);

  const taichiReady = runtime?.taichi_available ?? data?.taichi_available ?? false;
  const platformSupported = runtime?.platform_supported ?? true;
  const installing = installState === "installing";
  const failed = installState === "failed";

  return (
    <div className="space-y-4 p-6">
      <h1 className="text-xl font-semibold">MechDSL — Executable Bridge</h1>
      <p className="max-w-2xl text-sm text-muted-foreground">
        Compile AKMS knowledge nodes (equations + <code>algpseudocode</code>)
        into Taichi solvers via the MechDSL companion adapter. Emit and
        transpile are Taichi-free; only “Run verify” pays the Taichi cost.
      </p>

      <section className="space-y-3 rounded border border-border p-4">
        <h2 className="text-sm font-semibold">Backends</h2>
        <div className="flex items-center justify-between text-sm">
          <span>Emit / transpile</span>
          <span className="text-emerald-600" data-testid="mechdsl-emit-status">
            ✓ Ready (no Taichi needed)
          </span>
        </div>
        <div className="flex items-center justify-between gap-4 text-sm">
          <span>
            Verify <span className="text-muted-foreground">(Taichi)</span>
          </span>
          {taichiReady ? (
            <span className="text-emerald-600" data-testid="mechdsl-verify-status">
              ✓ Ready (Taichi installed)
            </span>
          ) : !platformSupported ? (
            <span className="text-muted-foreground" data-testid="mechdsl-verify-status">
              ✗ Not supported on this computer
            </span>
          ) : (
            <span className="flex items-center gap-3">
              <span className="text-muted-foreground" data-testid="mechdsl-verify-status">
                {failed ? "Install failed" : installing || install.isPending ? "" : "Not installed"}
              </span>
              <button
                type="button"
                disabled={installing || install.isPending}
                onClick={() => install.mutate()}
                className="rounded bg-accent px-3 py-1.5 text-sm font-semibold text-accent-foreground disabled:opacity-50"
              >
                {installing || install.isPending
                  ? "Installing…"
                  : failed
                    ? "Retry install"
                    : "Install taichi 1.7.4"}
              </button>
            </span>
          )}
        </div>

        {!taichiReady && !platformSupported && runtime?.platform_reason && (
          <p className="text-xs text-muted-foreground">{runtime.platform_reason}</p>
        )}

        {!taichiReady && platformSupported && !installing && (
          <p className="text-xs text-muted-foreground">
            ~170 MB, one-time. Installs the pinned Taichi wheel into{" "}
            <code className="rounded bg-muted px-1 py-0.5">
              {runtime?.target_dir ?? "the app cache"}
            </code>{" "}
            and unblocks “Run verify”.
          </p>
        )}

        {failed && runtime?.error && (
          <p className="text-xs text-red-600">{runtime.error}</p>
        )}

        {(installing || failed) && runtime?.log && runtime.log.length > 0 && (
          <pre className="max-h-40 overflow-auto rounded bg-muted p-2 text-[11px] leading-snug">
            {runtime.log.slice(-12).join("\n")}
          </pre>
        )}
      </section>

      <section className="rounded border border-border p-4">
        <h2 className="mb-2 text-sm font-semibold">Capabilities</h2>
        {data?.capabilities ? (
          <pre className="overflow-auto text-xs">
            {JSON.stringify(data.capabilities, null, 2)}
          </pre>
        ) : (
          <p className="text-sm text-muted-foreground">
            No capabilities reported.
          </p>
        )}
      </section>
    </div>
  );
}
