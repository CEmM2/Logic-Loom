import { useNavigate } from "react-router-dom";

import {
  ASSISTANT_ROUTE_BATCHES,
  ASSISTANT_ROUTE_DISCOVER,
  ASSISTANT_ROUTE_OUTPUTS,
  ASSISTANT_ROUTE_WORKFLOWS,
} from "./routes";

/**
 * Assistant Overview sub-tab.
 *
 * Renders the five Assistant quick actions:
 *   1. Create a source batch      → /assistant/batches
 *   2. Search for papers          → /assistant/discover
 *   3. Find related papers        → /assistant/discover
 *   4. Run a workflow             → /assistant/workflows
 *   5. Open recent outputs        → /assistant/outputs
 *
 * Each card is a button that navigates to its target sub-tab. No API
 * work happens here — the functionality lives on the target sub-tabs.
 */

interface QuickAction {
  readonly label: string;
  readonly description: string;
  readonly target: string;
}

export const QUICK_ACTIONS: readonly QuickAction[] = [
  {
    label: "Create a source batch",
    description:
      "Group papers from Zotero, discovery, PDFs, URLs, or manual entries.",
    target: ASSISTANT_ROUTE_BATCHES,
  },
  {
    label: "Search for papers",
    description: "Discover papers via OpenAlex and Semantic Scholar.",
    target: ASSISTANT_ROUTE_DISCOVER,
  },
  {
    label: "Find related papers",
    description:
      "Pivot from a seed paper through references, citations, and shared authors.",
    target: ASSISTANT_ROUTE_DISCOVER,
  },
  {
    label: "Run a workflow",
    description:
      "Kick off a literature survey, introduction, or proposal workflow.",
    target: ASSISTANT_ROUTE_WORKFLOWS,
  },
  {
    label: "Open recent outputs",
    description: "Browse markdown, JSON, and PDF outputs from past runs.",
    target: ASSISTANT_ROUTE_OUTPUTS,
  },
];

export default function AssistantOverviewRoute() {
  const navigate = useNavigate();

  return (
    <main className="flex h-full flex-col gap-6 p-8">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold">Assistant</h1>
        <p className="text-sm text-muted-foreground max-w-prose">
          Discover papers, build source batches, and run research workflows.
          Pick a quick action below to jump straight to it.
        </p>
      </header>

      <section
        aria-label="Quick actions"
        className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3"
      >
        {QUICK_ACTIONS.map((action) => (
          <button
            key={action.label}
            type="button"
            onClick={() => navigate(action.target)}
            className="flex flex-col gap-2 rounded-lg border bg-card p-4 text-left transition hover:border-foreground/30 hover:bg-accent/40 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <h2 className="text-base font-semibold">{action.label}</h2>
            <p className="text-sm text-muted-foreground">{action.description}</p>
          </button>
        ))}
      </section>
    </main>
  );
}
