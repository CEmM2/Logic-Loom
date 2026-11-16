import { useState } from "react";

interface Props {
  data: Record<string, unknown>;
}

export function FrontmatterCard({ data }: Props) {
  const [open, setOpen] = useState(false);
  const keys = Object.keys(data);
  if (keys.length === 0) return null;
  return (
    <div className="rounded border border-border bg-muted/40 px-3 py-2 text-xs">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between text-left text-muted-foreground"
      >
        <span>Frontmatter ({keys.length} keys)</span>
        <span>{open ? "−" : "+"}</span>
      </button>
      {open && (
        <dl className="mt-2 grid grid-cols-[8rem_1fr] gap-x-2 gap-y-1 font-mono">
          {keys.map((k) => (
            <div key={k} className="contents">
              <dt className="text-muted-foreground">{k}</dt>
              <dd className="break-all">{formatValue(data[k])}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

function formatValue(v: unknown): string {
  if (v == null) return "—";
  if (Array.isArray(v)) return v.map((x) => String(x)).join(", ");
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}
