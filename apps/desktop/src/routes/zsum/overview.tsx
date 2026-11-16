import { Link } from "react-router-dom";

const SECTIONS = [
  {
    to: "/zsum/pipeline",
    label: "Pipeline",
    desc: "Run zsum stages (cards, digests, restructure) against the Zotero library.",
  },
  {
    to: "/zsum/vault",
    label: "Vault",
    desc: "Browse the published Obsidian vault (papers, collections, wikilinks).",
  },
  {
    to: "/zsum/collections",
    label: "Collections",
    desc: "Inspect zsum collections and their per-paper digest state.",
  },
  {
    to: "/zsum/prompts",
    label: "Prompts",
    desc: "Edit zsum prompt variants and profiles (paper card, digests, restructure).",
  },
  {
    to: "/zsum/runs",
    label: "Runs",
    desc: "History of pipeline runs with logs, status, and rerun.",
  },
  {
    to: "/zsum/settings",
    label: "Settings",
    desc: "Project paths, command, cost estimation, nodes review.",
  },
];

export default function OverviewRoute() {
  return (
    <main className="flex h-full flex-col gap-6 p-8">
      <header>
        <h1 className="text-2xl font-semibold">Zsum</h1>
        <p className="text-sm text-muted-foreground max-w-prose">
          Summarize and publish Zotero literature. Run the zsum pipeline,
          browse the published vault, and tune prompts.
        </p>
      </header>
      <section className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {SECTIONS.map((s) => (
          <Link
            key={s.to}
            to={s.to}
            className="rounded border border-border bg-background/40 p-4 hover:bg-muted/50"
          >
            <div className="text-base font-semibold">{s.label}</div>
            <div className="mt-1 text-xs text-muted-foreground">{s.desc}</div>
          </Link>
        ))}
      </section>
    </main>
  );
}
