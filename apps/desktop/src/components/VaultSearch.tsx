import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { searchVault, type SearchBackend } from "../api/search";

export function VaultSearch() {
  const [q, setQ] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [backend, setBackend] = useState<SearchBackend>("ripgrep");

  const { data: hits = [], isFetching, error } = useQuery({
    queryKey: ["vault", "search", submitted, backend],
    queryFn: () => searchVault(submitted, backend),
    enabled: submitted.length > 0,
  });

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitted(q);
  };

  return (
    <div className="flex flex-col gap-2 p-3">
      <form onSubmit={onSubmit} className="flex gap-2">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search vault…"
          className="flex-1 rounded border border-border bg-background px-2 py-1 text-sm"
        />
        <select
          value={backend}
          onChange={(e) => setBackend(e.target.value as SearchBackend)}
          className="rounded border border-border bg-background px-2 py-1 text-sm"
          title="Search backend"
        >
          <option value="ripgrep">ripgrep</option>
          <option value="qmd">qmd</option>
        </select>
        <button
          type="submit"
          className="rounded bg-foreground px-3 py-1 text-sm text-background hover:opacity-90"
        >
          Search
        </button>
      </form>
      {error && (
        <p className="text-xs text-red-600">
          {(error as Error).message}
        </p>
      )}
      {isFetching && <p className="text-xs text-muted-foreground">Searching…</p>}
      <ul className="flex flex-col gap-1 overflow-y-auto text-xs">
        {hits.map((h, i) => (
          <li key={`${h.path}:${h.line}:${i}`} className="rounded border border-border p-2">
            <Link
              to={`/zsum/vault/file/${encodeURIComponent(h.path)}`}
              className="font-medium text-blue-600 hover:underline"
            >
              {h.path}
              {h.line != null && `:${h.line}`}
            </Link>
            <pre className="mt-1 max-h-24 overflow-y-auto whitespace-pre-wrap break-words text-muted-foreground">
              {h.snippet}
            </pre>
          </li>
        ))}
      </ul>
    </div>
  );
}
