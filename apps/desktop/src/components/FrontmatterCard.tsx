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
              <dd className="whitespace-pre-wrap break-all">{formatValue(data[k])}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

function isScalar(v: unknown): boolean {
  return v == null || typeof v !== "object";
}

function scalar(v: unknown): string {
  return v == null ? "—" : String(v);
}

/**
 * Frontmatter values as readable text. Scalars and lists of scalars stay on
 * one line; nested objects and lists of objects are shown YAML-style, one key
 * per line, instead of "[object Object]".
 */
export function formatValue(v: unknown, indent = 0): string {
  const pad = "  ".repeat(indent);
  if (isScalar(v)) return scalar(v);
  if (Array.isArray(v)) {
    if (v.length === 0) return "[]";
    if (v.every(isScalar)) return v.map(scalar).join(", ");
    return v
      .map((item) => `${pad}- ${formatValue(item, indent + 1).trimStart()}`)
      .join("\n");
  }
  const entries = Object.entries(v as Record<string, unknown>);
  if (entries.length === 0) return "{}";
  return entries
    .map(([key, value]) => {
      if (isScalar(value) || (Array.isArray(value) && value.every(isScalar))) {
        return `${pad}${key}: ${formatValue(value)}`;
      }
      return `${pad}${key}:\n${formatValue(value, indent + 1)}`;
    })
    .join("\n");
}
