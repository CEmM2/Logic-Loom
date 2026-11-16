import type { Paper } from "../../api/picker";
import { cn } from "../../lib/utils";

interface Props {
  papers: Paper[];
  total: number;
  selectedKeys: Set<string>;
  onToggle: (citekey: string) => void;
  busy?: boolean;
}

export function PaperGrid({ papers, total, selectedKeys, onToggle, busy }: Props) {
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-border px-3 py-2 text-xs text-muted-foreground">
        <span>
          Showing {papers.length} / {total} papers
          {busy && " · loading…"}
        </span>
        <span>{selectedKeys.size} selected</span>
      </div>
      <ul className="flex-1 overflow-y-auto">
        {papers.map((p) => {
          const selected = selectedKeys.has(p.citekey);
          return (
            <li
              key={p.citekey}
              onClick={() => onToggle(p.citekey)}
              className={cn(
                "flex cursor-pointer flex-col gap-0.5 border-b border-border px-3 py-2 text-sm hover:bg-muted",
                selected && "bg-muted",
              )}
            >
              <div className="flex items-start gap-2">
                <input
                  type="checkbox"
                  checked={selected}
                  readOnly
                  className="mt-1"
                  onClick={(e) => e.stopPropagation()}
                />
                <div className="flex-1">
                  <div className="font-medium">{p.title || p.citekey}</div>
                  <div className="text-xs text-muted-foreground">
                    {p.authors.slice(0, 3).join(", ")}
                    {p.authors.length > 3 && " et al."}
                    {p.year ? ` · ${p.year}` : ""}
                    {p.has_pdf && " · PDF"}
                  </div>
                  {p.collections.length > 0 && (
                    <div className="text-xs text-muted-foreground">
                      {p.collections.join(" / ")}
                    </div>
                  )}
                  {p.assigned_to.length > 0 && (
                    <div className="text-xs text-amber-600">
                      Already in: {p.assigned_to.join(", ")}
                    </div>
                  )}
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
