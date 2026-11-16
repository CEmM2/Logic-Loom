import { Link } from "react-router-dom";

const SECTIONS = [
  {
    to: "/akms/batches",
    label: "Batches",
    desc: "Manage AKMS batches: sources, nodes, notebooks, and per-batch workflow runs.",
  },
  {
    to: "/akms/picker",
    label: "Picker",
    desc: "Legacy paper picker — curate source candidates from the AKMS generation plan.",
  },
  {
    to: "/akms/nodes",
    label: "Nodes",
    desc: "Browse and promote AKMS knowledge nodes from the nodes vault.",
  },
  {
    to: "/akms/graph",
    label: "Graph",
    desc: "Interactive AKMS knowledge graph — ego, cluster, and filtered views.",
  },
  {
    to: "/akms/prompts",
    label: "Prompts",
    desc: "Edit AKMS prompt variants (node-gen skill / command / digest refiner).",
  },
  {
    to: "/akms/workflows",
    label: "Workflows",
    desc: "Define and run Claude-executor workflows over AKMS batches.",
  },
  {
    to: "/akms/runs",
    label: "Runs",
    desc: "Run history with live logs, side-by-side compare, and rerun.",
  },
  {
    to: "/akms/settings",
    label: "Settings",
    desc: "Claude assets editor — bundled defaults, edits, and per-file reset.",
  },
];

export default function AkmsOverviewRoute() {
  return (
    <main className="flex h-full flex-col gap-6 p-8">
      <header>
        <h1 className="text-2xl font-semibold">AKMS</h1>
        <p className="text-sm text-muted-foreground max-w-prose">
          Generate and review knowledge nodes. Pick papers, run node-gen
          workflows, and inspect the resulting graph.
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
