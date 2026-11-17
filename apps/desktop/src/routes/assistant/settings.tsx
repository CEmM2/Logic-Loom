import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getConfig,
  postConfig,
  testAssistantProvider,
  type AppConfig,
  type AssistantTestConnectionResult,
  type AssistantTestProvider,
  type ConfigUpdate,
} from "../../api/app";

/**
 * Assistant Settings sub-tab.
 *
 * Surfaces the four Assistant-specific fields from `Settings`:
 *   - `openalex_user_email` (polite-pool contact, required for OpenAlex)
 *   - `openalex_api_key` (optional premium key)
 *   - `semantic_scholar_api_key` (optional)
 *   - `assistant_workspace` (optional explicit override of the resolver)
 *   - `assistant_workspace_colocate` (default-resolver layout flag)
 *
 * Each provider has a "Test connection" button that hits
 * `/api/app/assistant/test-connection/<provider>` and renders the
 * canonical `{ok, latency_ms, quota_remaining}` payload below the input.
 */

type ProviderTestState = {
  status: "idle" | "loading" | "done" | "error";
  result: AssistantTestConnectionResult | null;
  errorMessage: string | null;
};

const INITIAL_TEST_STATE: ProviderTestState = {
  status: "idle",
  result: null,
  errorMessage: null,
};

interface DraftState {
  openalex_user_email: string;
  openalex_api_key: string;
  semantic_scholar_api_key: string;
  assistant_workspace: string;
  assistant_workspace_colocate: boolean;
}

function configToDraft(cfg: AppConfig): DraftState {
  return {
    openalex_user_email: cfg.openalex_user_email ?? "",
    openalex_api_key: cfg.openalex_api_key ?? "",
    semantic_scholar_api_key: cfg.semantic_scholar_api_key ?? "",
    assistant_workspace: cfg.assistant_workspace ?? "",
    assistant_workspace_colocate: cfg.assistant_workspace_colocate,
  };
}

function draftToPatch(draft: DraftState, original: AppConfig): ConfigUpdate {
  const patch: ConfigUpdate = {};
  if (draft.openalex_user_email !== (original.openalex_user_email ?? "")) {
    patch.openalex_user_email = draft.openalex_user_email;
  }
  if (draft.openalex_api_key !== (original.openalex_api_key ?? "")) {
    patch.openalex_api_key = draft.openalex_api_key;
  }
  if (
    draft.semantic_scholar_api_key !== (original.semantic_scholar_api_key ?? "")
  ) {
    patch.semantic_scholar_api_key = draft.semantic_scholar_api_key;
  }
  if (draft.assistant_workspace !== (original.assistant_workspace ?? "")) {
    patch.assistant_workspace = draft.assistant_workspace;
  }
  if (
    draft.assistant_workspace_colocate !== original.assistant_workspace_colocate
  ) {
    patch.assistant_workspace_colocate = draft.assistant_workspace_colocate;
  }
  return patch;
}

function TestConnectionRow({ state }: { state: ProviderTestState }) {
  if (state.status === "idle") return null;
  if (state.status === "loading") {
    return (
      <p className="text-xs text-muted-foreground" data-testid="test-connection-loading">
        Testing connection…
      </p>
    );
  }
  if (state.status === "error" || state.result === null) {
    return (
      <p className="text-xs text-destructive" data-testid="test-connection-error">
        {state.errorMessage ?? "Test connection failed."}
      </p>
    );
  }
  const r = state.result;
  return (
    <dl
      className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 rounded-md border bg-muted/40 px-3 py-2 text-xs"
      data-testid="test-connection-result"
    >
      <dt className="font-medium">ok</dt>
      <dd data-testid="test-connection-ok">{r.ok ? "yes" : "no"}</dd>
      <dt className="font-medium">latency_ms</dt>
      <dd data-testid="test-connection-latency">{r.latency_ms}</dd>
      <dt className="font-medium">quota_remaining</dt>
      <dd data-testid="test-connection-quota">
        {r.quota_remaining === null ? "—" : r.quota_remaining}
      </dd>
      {r.detail && (
        <>
          <dt className="font-medium">detail</dt>
          <dd>{r.detail}</dd>
        </>
      )}
    </dl>
  );
}

export default function AssistantSettingsRoute() {
  const qc = useQueryClient();
  const configQuery = useQuery({ queryKey: ["app", "config"], queryFn: getConfig });

  const [draft, setDraft] = useState<DraftState | null>(null);
  const [openalexTest, setOpenalexTest] = useState<ProviderTestState>(
    INITIAL_TEST_STATE,
  );
  const [s2Test, setS2Test] = useState<ProviderTestState>(INITIAL_TEST_STATE);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);

  useEffect(() => {
    if (configQuery.data && draft === null) {
      setDraft(configToDraft(configQuery.data));
    }
  }, [configQuery.data, draft]);

  const dirty = useMemo(() => {
    if (!draft || !configQuery.data) return false;
    return Object.keys(draftToPatch(draft, configQuery.data)).length > 0;
  }, [draft, configQuery.data]);

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (!draft || !configQuery.data) return;
      const patch = draftToPatch(draft, configQuery.data);
      await postConfig(patch);
    },
    onSuccess: () => {
      setSaveMessage("Saved.");
      qc.invalidateQueries({ queryKey: ["app", "config"] });
    },
    onError: (err) => {
      setSaveMessage(`Save failed: ${(err as Error).message}`);
    },
  });

  async function runProviderTest(provider: AssistantTestProvider) {
    const setter = provider === "openalex" ? setOpenalexTest : setS2Test;
    setter({ status: "loading", result: null, errorMessage: null });
    try {
      const result = await testAssistantProvider(provider);
      setter({ status: "done", result, errorMessage: null });
    } catch (err) {
      setter({
        status: "error",
        result: null,
        errorMessage: (err as Error).message ?? "Unknown error.",
      });
    }
  }

  if (configQuery.isPending) {
    return (
      <main className="flex h-full items-center justify-center p-8 text-sm text-muted-foreground">
        Loading settings…
      </main>
    );
  }
  if (configQuery.isError || !configQuery.data || !draft) {
    return (
      <main className="flex h-full items-center justify-center p-8 text-sm text-destructive">
        Failed to load settings: {(configQuery.error as Error | undefined)?.message ?? "unknown error"}
      </main>
    );
  }

  const cfg = configQuery.data;

  return (
    <main className="flex h-full flex-col gap-6 overflow-y-auto p-8">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold">Assistant Settings</h1>
        <p className="text-sm text-muted-foreground max-w-prose">
          Provider credentials and workspace preferences for the Assistant mode.
          Saved values persist to
          <code className="ml-1 rounded bg-muted px-1 font-mono text-xs">~/.config/logic-loom/config.toml</code>.
        </p>
      </header>

      <form
        className="flex flex-col gap-6"
        onSubmit={(e) => {
          e.preventDefault();
          saveMutation.mutate();
        }}
      >
        <section className="flex flex-col gap-3 rounded-lg border bg-card p-4">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-semibold">OpenAlex</h2>
            <button
              type="button"
              onClick={() => runProviderTest("openalex")}
              className="rounded border px-3 py-1 text-xs hover:bg-accent/40"
              data-testid="test-connection-openalex"
            >
              Test connection
            </button>
          </div>

          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium">Polite-pool email</span>
            <input
              type="email"
              name="openalex_user_email"
              value={draft.openalex_user_email}
              onChange={(e) =>
                setDraft({ ...draft, openalex_user_email: e.target.value })
              }
              placeholder="you@example.org"
              className="rounded border px-3 py-2 font-mono text-xs"
              data-testid="input-openalex-email"
            />
            <span className="text-xs text-muted-foreground">
              Sent as the OpenAlex User-Agent contact. Required when discovery is enabled.
            </span>
          </label>

          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium">API key (optional)</span>
            <input
              type="password"
              name="openalex_api_key"
              value={draft.openalex_api_key}
              onFocus={(e) => e.currentTarget.select()}
              onChange={(e) =>
                setDraft({ ...draft, openalex_api_key: e.target.value })
              }
              placeholder="leave blank for polite-pool tier"
              className="rounded border px-3 py-2 font-mono text-xs"
              data-testid="input-openalex-key"
            />
          </label>
          <TestConnectionRow state={openalexTest} />
        </section>

        <section className="flex flex-col gap-3 rounded-lg border bg-card p-4">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-semibold">Semantic Scholar</h2>
            <button
              type="button"
              onClick={() => runProviderTest("semantic_scholar")}
              className="rounded border px-3 py-1 text-xs hover:bg-accent/40"
              data-testid="test-connection-s2"
            >
              Test connection
            </button>
          </div>

          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium">API key (optional)</span>
            <input
              type="password"
              name="semantic_scholar_api_key"
              value={draft.semantic_scholar_api_key}
              onFocus={(e) => e.currentTarget.select()}
              onChange={(e) =>
                setDraft({
                  ...draft,
                  semantic_scholar_api_key: e.target.value,
                })
              }
              placeholder="leave blank for the public endpoint"
              className="rounded border px-3 py-2 font-mono text-xs"
              data-testid="input-s2-key"
            />
            <span className="text-xs text-muted-foreground">
              Public endpoint rate limits apply when blank.
            </span>
          </label>
          <TestConnectionRow state={s2Test} />
        </section>

        <section className="flex flex-col gap-3 rounded-lg border bg-card p-4">
          <h2 className="text-base font-semibold">Workspace</h2>

          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium">Assistant workspace (optional override)</span>
            <input
              type="text"
              name="assistant_workspace"
              value={draft.assistant_workspace}
              onChange={(e) =>
                setDraft({ ...draft, assistant_workspace: e.target.value })
              }
              placeholder={cfg.assistant_workspace_effective}
              className="rounded border px-3 py-2 font-mono text-xs"
              data-testid="input-assistant-workspace"
            />
            <span className="text-xs text-muted-foreground">
              Leave blank to use the resolver default. Current effective path:
              <code className="ml-1 rounded bg-muted px-1 font-mono">
                {cfg.assistant_workspace_effective}
              </code>
            </span>
          </label>

          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={draft.assistant_workspace_colocate}
              onChange={(e) =>
                setDraft({
                  ...draft,
                  assistant_workspace_colocate: e.target.checked,
                })
              }
              data-testid="input-colocate"
            />
            <span>
              Co-locate under <code className="font-mono">&lt;akms_workspace&gt;/assistant/</code> instead of the sibling Assistant-Workspace.
            </span>
          </label>
        </section>

        <div className="flex items-center gap-3">
          <button
            type="submit"
            disabled={!dirty || saveMutation.isPending}
            className="rounded border border-foreground/20 bg-foreground px-4 py-2 text-sm font-medium text-background hover:bg-foreground/90 disabled:opacity-50"
            data-testid="save-button"
          >
            {saveMutation.isPending ? "Saving…" : "Save"}
          </button>
          {saveMessage && (
            <span className="text-xs text-muted-foreground" data-testid="save-message">
              {saveMessage}
            </span>
          )}
        </div>
      </form>
    </main>
  );
}
