import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";

import { getZsumIndex, type CollectionIndex, type ItemIndex } from "../../api/zsum";

function digestBadge(state: ItemIndex["card_state"] | CollectionIndex["digest_state"]) {
  const map: Record<string, string> = {
    ok: "text-emerald-700",
    cached: "text-emerald-700",
    pending: "text-amber-700",
    stale: "text-amber-700",
    degraded: "text-red-600",
    missing: "text-red-600",
    no_pdf: "text-muted-foreground",
  };
  return map[state] ?? "text-muted-foreground";
}

export default function CollectionsRoute() {
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedKey = searchParams.get("collection");
  const [filter, setFilter] = useState("");

  const { data: indexResp, isLoading, error: indexError } = useQuery({
    queryKey: ["zsum", "index"],
    queryFn: getZsumIndex,
  });

  const collections = useMemo(() => {
    if (indexResp?.kind !== "ok") return [];
    return Object.entries(indexResp.index.collections)
      .filter(([key]) => key !== "root")
      .sort(([, a], [, b]) => a.name.localeCompare(b.name));
  }, [indexResp]);

  const selectedCollection = useMemo(() => {
    if (!selectedKey || indexResp?.kind !== "ok") return null;
    return indexResp.index.collections[selectedKey] ?? null;
  }, [selectedKey, indexResp]);

  const papers = useMemo(() => {
    if (!selectedKey || indexResp?.kind !== "ok") return [];
    const items: ItemIndex[] = Object.values(indexResp.index.items);
    const f = filter.trim().toLowerCase();
    return items
      .filter((it) => it.collections.includes(selectedKey))
      .filter((it) =>
        f
          ? it.citekey.toLowerCase().includes(f) ||
            (it.venue?.toLowerCase().includes(f) ?? false)
          : true,
      )
      .sort((a, b) => (b.year ?? 0) - (a.year ?? 0));
  }, [selectedKey, indexResp, filter]);

  const selectCollection = (key: string | null) => {
    const next = new URLSearchParams(searchParams);
    if (key) next.set("collection", key);
    else next.delete("collection");
    setSearchParams(next, { replace: true });
    setFilter("");
  };

  if (isLoading) {
    return (
      <main className="flex h-full items-center justify-center p-8 text-sm text-muted-foreground">
        Loading collections…
      </main>
    );
  }

  if (indexError || !indexResp || indexResp.kind !== "ok") {
    const missing = !indexError && indexResp?.kind === "missing";
    const detail = indexError
      ? indexError instanceof Error
        ? indexError.message
        : String(indexError)
      : indexResp && indexResp.kind !== "ok"
        ? indexResp.message
        : null;
    return (
      <main className="flex h-full flex-col gap-3 p-8" data-testid="collections-empty">
        <h1 className="text-2xl font-semibold">Collections</h1>
        <p className="max-w-prose text-sm text-muted-foreground">
          {missing
            ? "No zsum library yet. Collections appear here after the zsum pipeline has run once and written its index."
            : "The zsum library index could not be read."}
        </p>
        <div className="flex flex-wrap gap-2">
          <Link
            to="/zsum/pipeline"
            className="rounded border border-border px-3 py-1 text-xs font-medium hover:bg-muted"
          >
            Open Pipeline
          </Link>
          <Link
            to="/zsum/settings"
            className="rounded border border-border px-3 py-1 text-xs font-medium hover:bg-muted"
          >
            Check zsum settings
          </Link>
        </div>
        {detail && (
          <p className="break-all font-mono text-xs text-muted-foreground">{detail}</p>
        )}
      </main>
    );
  }

  return (
    <main className="flex h-full" aria-label="Zsum Collections">
      <aside className="flex w-72 flex-col border-r" aria-label="Collection list">
        <header className="border-b px-4 py-3">
          <h1 className="text-sm font-semibold">Collections</h1>
          <p className="text-xs text-muted-foreground">{collections.length} total</p>
        </header>
        <ul className="flex-1 overflow-auto" role="listbox">
          {collections.map(([key, col]) => (
            <li key={key}>
              <button
                type="button"
                role="option"
                aria-selected={selectedKey === key}
                onClick={() => selectCollection(key)}
                data-testid={`collection-row-${key}`}
                className={
                  "w-full border-b px-3 py-2 text-left text-sm hover:bg-accent " +
                  (selectedKey === key ? "bg-accent" : "")
                }
              >
                <div className="truncate font-medium">{col.name}</div>
                <div className="mt-0.5 flex justify-between text-[10px] text-muted-foreground">
                  <span>{col.item_count} papers</span>
                  <span className={digestBadge(col.digest_state)}>{col.digest_state}</span>
                </div>
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <section className="flex flex-1 flex-col overflow-hidden">
        {!selectedCollection ? (
          <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
            Select a collection to view its papers.
          </div>
        ) : (
          <>
            <header className="flex items-center justify-between border-b px-6 py-3">
              <div>
                <h2 className="text-lg font-semibold">{selectedCollection.name}</h2>
                <p className="text-xs text-muted-foreground">
                  {papers.length}/{selectedCollection.item_count} papers
                  {filter && ` matching "${filter}"`}
                </p>
              </div>
              <div className="flex gap-2">
                <input
                  type="search"
                  placeholder="Filter (citekey / venue)…"
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                  className="rounded border px-2 py-1 text-xs"
                />
                <Link
                  to={`/zsum/vault/file/${encodeURIComponent(
                    `Collections/${selectedCollection.name}.md`,
                  )}`}
                  className="rounded border px-2 py-1 text-xs hover:bg-accent"
                  title="Open collection digest in vault"
                >
                  View digest
                </Link>
              </div>
            </header>
            <div className="flex-1 overflow-auto">
              {papers.length === 0 ? (
                <p className="p-6 text-sm text-muted-foreground">
                  No papers match this filter.
                </p>
              ) : (
                <table className="w-full text-sm">
                  <thead className="sticky top-0 bg-muted/40 text-left text-xs uppercase text-muted-foreground">
                    <tr>
                      <th className="px-3 py-2">Citekey</th>
                      <th className="px-3 py-2 w-20">Year</th>
                      <th className="px-3 py-2">Venue</th>
                      <th className="px-3 py-2 w-24">Card</th>
                      <th className="px-3 py-2 w-24"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {papers.map((it) => (
                      <tr key={it.citekey} className="border-t">
                        <td className="px-3 py-1.5 font-mono text-xs">{it.citekey}</td>
                        <td className="px-3 py-1.5 text-xs">{it.year || "—"}</td>
                        <td className="px-3 py-1.5 text-xs truncate max-w-md">
                          {it.venue || "—"}
                        </td>
                        <td className={`px-3 py-1.5 text-xs ${digestBadge(it.card_state)}`}>
                          {it.card_state}
                        </td>
                        <td className="px-3 py-1.5 text-xs">
                          <Link
                            to={`/zsum/vault/file/${encodeURIComponent(
                              `Papers/@${it.citekey}.md`,
                            )}`}
                            className="text-blue-600 hover:underline"
                          >
                            view
                          </Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </>
        )}
      </section>
    </main>
  );
}
