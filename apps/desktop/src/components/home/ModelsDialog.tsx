import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError } from "../../api/client";
import { getLocalModels, installLocalModel } from "../../api/zsum";
import { cn } from "../../lib/utils";

interface Props {
  open: boolean;
  onClose: () => void;
}

function formatApiError(error: unknown): string {
  if (error instanceof ApiError) {
    const detail = error.detail as
      | { error?: { message?: string; detail?: unknown } }
      | undefined;
    const message = detail?.error?.message;
    if (message) return message;
  }
  return error instanceof Error ? error.message : String(error);
}

export function ModelsDialog({ open, onClose }: Props) {
  const qc = useQueryClient();
  const [customModel, setCustomModel] = useState("");
  const [pendingModel, setPendingModel] = useState<string | null>(null);

  const modelsQuery = useQuery({
    queryKey: ["zsum", "local-models"],
    queryFn: getLocalModels,
    enabled: open,
  });

  const installMutation = useMutation({
    mutationFn: (model: string) => installLocalModel(model),
    onSuccess: async (result) => {
      if (result.status === "configured") {
        setCustomModel("");
        await Promise.all([
          qc.invalidateQueries({ queryKey: ["zsum", "local-models"] }),
          qc.invalidateQueries({ queryKey: ["zsum", "config-doc"] }),
          qc.invalidateQueries({ queryKey: ["zsum", "effective-config"] }),
        ]);
      }
    },
  });

  useEffect(() => {
    if (open) return;
    setCustomModel("");
    setPendingModel(null);
    installMutation.reset();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  const installResult = installMutation.data ?? null;
  const installError = installMutation.isError ? formatApiError(installMutation.error) : null;

  const allKnownModels = useMemo(() => {
    const tierModels = modelsQuery.data?.tiers.map((tier) => tier.model) ?? [];
    const customModels = modelsQuery.data?.custom_models ?? [];
    return new Set([...tierModels, ...customModels]);
  }, [modelsQuery.data]);

  const onInstall = async (model: string) => {
    const normalized = model.trim();
    if (!normalized) return;
    setPendingModel(normalized);
    try {
      await installMutation.mutateAsync(normalized);
    } finally {
      setPendingModel(null);
    }
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div
        className="flex max-h-[85vh] w-full max-w-4xl flex-col overflow-hidden rounded-xl border border-border bg-background shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
          <div>
            <h2 className="text-lg font-semibold">Local models</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Install an Ollama-backed local model using zsum’s built-in setup flow. Any custom
              model you add here must be available through an OpenAI-compatible local server.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded border border-border px-3 py-1 text-sm hover:bg-muted"
          >
            Close
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          {modelsQuery.isLoading ? (
            <div className="text-sm text-muted-foreground">Loading model catalog…</div>
          ) : modelsQuery.isError ? (
            <div className="rounded border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {formatApiError(modelsQuery.error)}
            </div>
          ) : (
            <div className="flex flex-col gap-5">
              <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
                <span className="rounded-full border border-border px-3 py-1">
                  Config: <span className="font-mono">{modelsQuery.data?.config_path}</span>
                </span>
                <span className="rounded-full border border-border px-3 py-1">
                  Ollama endpoint: <span className="font-mono">{modelsQuery.data?.ollama_endpoint}</span>
                </span>
                {modelsQuery.data?.last_installed_model && (
                  <span className="rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-emerald-700">
                    Last installed: <span className="font-mono">{modelsQuery.data.last_installed_model}</span>
                  </span>
                )}
                {modelsQuery.data?.current_config_model && (
                  <span className="rounded-full border border-blue-200 bg-blue-50 px-3 py-1 text-blue-700">
                    Configured now: <span className="font-mono">{modelsQuery.data.current_config_model}</span>
                  </span>
                )}
              </div>

              <section className="flex flex-col gap-3">
                <div>
                  <h3 className="text-sm font-semibold">Predefined models</h3>
                  <p className="text-xs text-muted-foreground">
                    These come directly from zotero-summarizer’s bundled model tiers.
                  </p>
                </div>
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                  {modelsQuery.data?.tiers.map((tier) => {
                    const isCurrent = modelsQuery.data.current_config_model === tier.model;
                    const isLastInstalled = modelsQuery.data.last_installed_model === tier.model;
                    const isPending = pendingModel === tier.model && installMutation.isPending;
                    return (
                      <div
                        key={tier.id}
                        className={cn(
                          "flex flex-col gap-3 rounded-lg border border-border bg-background/60 p-4",
                          isCurrent && "border-blue-300 bg-blue-50/50",
                          isLastInstalled && "border-emerald-300 bg-emerald-50/40",
                        )}
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div>
                            <div className="text-sm font-semibold">{tier.model}</div>
                            <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
                              {tier.id.replace(/_/g, " ")}
                            </div>
                          </div>
                          {tier.is_default && (
                            <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                              default
                            </span>
                          )}
                        </div>
                        <div className="space-y-1 text-xs text-muted-foreground">
                          <div>RAM: {tier.ram}</div>
                          <div>Params: {tier.params}</div>
                          <div>{tier.note}</div>
                        </div>
                        <div className="flex flex-wrap gap-2 text-[10px] font-semibold uppercase tracking-wide">
                          {isLastInstalled && (
                            <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-emerald-700">
                              last installed
                            </span>
                          )}
                          {isCurrent && (
                            <span className="rounded-full bg-blue-500/15 px-2 py-0.5 text-blue-700">
                              active in config
                            </span>
                          )}
                        </div>
                        <button
                          type="button"
                          onClick={() => onInstall(tier.model)}
                          disabled={installMutation.isPending}
                          className="rounded bg-foreground px-3 py-1.5 text-sm text-background hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {isPending ? "Installing…" : "Install"}
                        </button>
                      </div>
                    );
                  })}
                </div>
              </section>

              <section className="rounded-lg border border-border bg-background/60 p-4">
                <div>
                  <h3 className="text-sm font-semibold">Add a different model</h3>
                  <p className="text-xs text-muted-foreground">
                    Enter any compatible local model name. LogicLoom will pass it through to
                    <span className="font-mono"> zsum setup-model --model …</span>.
                  </p>
                </div>

                <div className="mt-3 flex flex-col gap-3 md:flex-row md:items-end">
                  <label className="flex-1">
                    <span className="mb-1 block text-xs text-muted-foreground">Custom model</span>
                    <input
                      type="text"
                      value={customModel}
                      onChange={(event) => setCustomModel(event.target.value)}
                      placeholder="e.g. llama3.2"
                      className="w-full rounded border border-border bg-background px-3 py-2 font-mono text-sm"
                    />
                  </label>
                  <button
                    type="button"
                    onClick={() => onInstall(customModel)}
                    disabled={!customModel.trim() || installMutation.isPending}
                    className="rounded bg-foreground px-4 py-2 text-sm text-background hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {pendingModel === customModel.trim() && installMutation.isPending
                      ? "Installing…"
                      : "Install custom model"}
                  </button>
                </div>

                {(modelsQuery.data?.custom_models.length ?? 0) > 0 && (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {modelsQuery.data?.custom_models.map((model) => (
                      <button
                        key={model}
                        type="button"
                        onClick={() => setCustomModel(model)}
                        className={cn(
                          "rounded-full border border-border px-3 py-1 text-xs hover:bg-muted",
                          allKnownModels.has(model) && "font-mono",
                        )}
                      >
                        {model}
                      </button>
                    ))}
                  </div>
                )}
              </section>

              {(installResult || installError) && (
                <section
                  className={cn(
                    "rounded-lg border px-4 py-3",
                    installResult?.status === "configured"
                      ? "border-emerald-200 bg-emerald-50"
                      : "border-red-200 bg-red-50",
                  )}
                >
                  <div
                    className={cn(
                      "text-sm font-semibold",
                      installResult?.status === "configured" ? "text-emerald-700" : "text-red-700",
                    )}
                  >
                    {installResult?.status === "configured"
                      ? `Installed ${installResult.installed_model}`
                      : "Model installation failed"}
                  </div>
                  {installResult?.status === "configured" &&
                    (installResult.backfilled_paths.length > 0 ? (
                      <div className="mt-2 text-xs text-emerald-700">
                        Wired into <span className="font-mono">zsum.config.yaml</span>:{" "}
                        {installResult.backfilled_paths.map((p) => (
                          <span
                            key={p}
                            className="ml-1 inline-block rounded bg-emerald-500/15 px-1.5 py-0.5 font-mono text-[10px]"
                          >
                            {p}
                          </span>
                        ))}
                      </div>
                    ) : (
                      <div className="mt-2 text-xs text-emerald-700">
                        Your existing LLM config already has explicit values — left untouched.
                        Edit the LLM tab to switch over manually.
                      </div>
                    ))}
                  <pre className="mt-2 max-h-64 overflow-y-auto whitespace-pre-wrap rounded border border-black/5 bg-background/80 p-3 font-mono text-[11px] text-foreground">
                    {installResult?.output ?? installError}
                  </pre>
                </section>
              )}
            </div>
          )}
        </div>

        <div className="flex items-center justify-between gap-3 border-t border-border px-5 py-3 text-sm">
          <div className="text-xs text-muted-foreground">
            Want to review the full zsum LLM configuration after installing? The LLM group is one
            click away.
          </div>
          <Link
            to="/settings?tab=llm"
            onClick={onClose}
            className="rounded border border-border px-3 py-1.5 hover:bg-muted"
          >
            Open LLM settings
          </Link>
        </div>
      </div>
    </div>
  );
}