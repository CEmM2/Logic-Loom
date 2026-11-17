/**
 * Sources table — lists current sources, removes individual rows, and
 * adds new sources via a Zotero-library picker or a manual-add form.
 *
 * Behaviour:
 *   - Picker is search-driven (FTS5 over BBT JSON) with sectioned
 *     results: Library + Already-processed cards.
 *   - Each row shows title (primary), author·year (secondary), citekey
 *     (mono); a side drawer previews the BBT abstract + card body.
 *   - Manual add for kind=zotero replaces the free-text title input
 *     with the same inline search so only resolved citekeys can be
 *     added.
 */
import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import type {
  AssistantBatch,
  BatchSourceItem,
  SourceItemKind,
} from "../../../api/assistantBatches";
import {
  getPickerPreview,
  searchPicker,
  type PickerHit,
  type PickerPreview,
  type PickerSearchEnvelope,
} from "../../../api/assistantZoteroPicker";

export interface BatchSourcesTableProps {
  batch: AssistantBatch;
  onAdd: (sources: BatchSourceItem[]) => void;
  onRemove: (ids: string[]) => void;
  busy?: boolean;
}

const KIND_OPTIONS: SourceItemKind[] = [
  "zotero",
  "discovered",
  "external_pdf",
  "url",
  "manual",
];

export function BatchSourcesTable({
  batch,
  onAdd,
  onRemove,
  busy = false,
}: BatchSourcesTableProps) {
  return (
    <div className="flex flex-col gap-3" data-testid="batch-sources-table">
      <SourcesGrid batch={batch} onRemove={onRemove} busy={busy} />

      <ZoteroPicker
        existing={new Set(
          batch.sources.map((s) => s.citekey).filter(Boolean) as string[],
        )}
        onAdd={onAdd}
        busy={busy}
      />

      <ManualAdd
        existing={new Set(
          batch.sources.map((s) => s.citekey).filter(Boolean) as string[],
        )}
        onAdd={onAdd}
        busy={busy}
      />
    </div>
  );
}

function SourcesGrid({
  batch,
  onRemove,
  busy,
}: {
  batch: AssistantBatch;
  onRemove: (ids: string[]) => void;
  busy: boolean;
}) {
  return (
    <div className="rounded border">
      <table className="w-full text-sm">
        <thead className="bg-muted/40 text-left text-xs uppercase text-muted-foreground">
          <tr>
            <th className="px-3 py-2">Kind</th>
            <th className="px-3 py-2">Title</th>
            <th className="px-3 py-2">Identifier</th>
            <th className="w-16 px-3 py-2"></th>
          </tr>
        </thead>
        <tbody>
          {batch.sources.length === 0 ? (
            <tr>
              <td
                className="px-3 py-4 text-center text-muted-foreground"
                colSpan={4}
                data-testid="sources-empty"
              >
                No sources yet.
              </td>
            </tr>
          ) : (
            batch.sources.map((s) => (
              <tr key={s.id} data-testid={`source-row-${s.id}`}>
                <td className="px-3 py-2 align-top">{s.kind}</td>
                <td className="px-3 py-2 align-top">{s.title}</td>
                <td className="px-3 py-2 align-top text-xs text-muted-foreground">
                  {s.citekey ??
                    s.doi ??
                    s.openalex_id ??
                    s.semantic_scholar_id ??
                    s.url ??
                    s.id}
                </td>
                <td className="px-3 py-2 align-top">
                  <button
                    type="button"
                    data-testid={`source-remove-${s.id}`}
                    disabled={busy}
                    onClick={() => onRemove([s.id])}
                    className="text-xs text-destructive underline disabled:opacity-50"
                  >
                    remove
                  </button>
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

// ── Search hook ────────────────────────────────────────────────────────────

function useDebounced<T>(value: T, delay = 250): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(t);
  }, [value, delay]);
  return debounced;
}

function usePickerSearch(query: string) {
  const debouncedQuery = useDebounced(query, 250);
  return useQuery<PickerSearchEnvelope>({
    queryKey: ["assistantZoteroPicker", "search", debouncedQuery],
    queryFn: () => searchPicker({ q: debouncedQuery, limit: 50 }),
    staleTime: 30_000,
  });
}

function hitToSource(hit: PickerHit): BatchSourceItem {
  return {
    id: `zotero:${hit.citekey}`,
    kind: "zotero",
    title: hit.title || hit.citekey,
    citekey: hit.citekey,
    authors: hit.authors,
    year: hit.year ?? null,
    doi: hit.doi || null,
  };
}

// ── Zotero picker ──────────────────────────────────────────────────────────

function ZoteroPicker({
  existing,
  onAdd,
  busy,
}: {
  existing: Set<string>;
  onAdd: (sources: BatchSourceItem[]) => void;
  busy: boolean;
}) {
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [previewKey, setPreviewKey] = useState<string | null>(null);
  const searchQ = usePickerSearch(query);

  const env = searchQ.data;
  const library = (env?.items ?? []).filter((h) => h.section === "library");
  const cards = (env?.items ?? []).filter((h) => h.section === "card");

  const toggle = (citekey: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(citekey)) next.delete(citekey);
      else next.add(citekey);
      return next;
    });

  const addSelected = () => {
    const sources: BatchSourceItem[] = [];
    for (const citekey of selected) {
      if (existing.has(citekey)) continue;
      const hit = env?.items.find((h) => h.citekey === citekey);
      if (!hit) continue;
      sources.push(hitToSource(hit));
    }
    if (sources.length > 0) {
      onAdd(sources);
      setSelected(new Set());
    }
  };

  return (
    <div
      className="relative rounded border bg-muted/20 p-3"
      data-testid="zotero-picker"
    >
      <header className="mb-2 flex items-start gap-3">
        <div className="flex-1">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Pick from Zotero library
          </h4>
          <p className="text-[11px] text-muted-foreground">
            Search by title, author, abstract, or citekey. Click a row to preview.
          </p>
        </div>
        <button
          type="button"
          data-testid="zotero-add-selected"
          onClick={addSelected}
          disabled={busy || selected.size === 0}
          className="rounded bg-primary px-3 py-1 text-sm text-primary-foreground disabled:opacity-50"
        >
          Add{selected.size > 0 ? ` (${selected.size})` : ""}
        </button>
      </header>

      <div className="mb-2">
        <input
          data-testid="zotero-search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search Zotero library…"
          className="w-full rounded border px-2 py-1 text-sm"
        />
      </div>

      {env?.library_reason && (
        <div className="mb-2 rounded border border-amber-400/40 bg-amber-50/60 px-2 py-1 text-[11px] text-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
          {describeLibraryReason(env.library_reason, env.bbt_path ?? null)}
        </div>
      )}

      <div
        className="max-h-72 overflow-auto rounded border bg-background"
        data-testid="zotero-results"
      >
        {searchQ.isLoading && (
          <p className="p-3 text-xs text-muted-foreground">Searching…</p>
        )}
        {!searchQ.isLoading && (library.length + cards.length === 0) && (
          <p className="p-3 text-xs text-muted-foreground">
            {query.trim()
              ? "No matches. Try a different word."
              : "Type a search term to see Zotero items."}
          </p>
        )}
        {library.length > 0 && (
          <Section
            label={`Library (${library.length})`}
            hits={library}
            existing={existing}
            selected={selected}
            busy={busy}
            onToggle={toggle}
            onPreview={setPreviewKey}
          />
        )}
        {cards.length > 0 && (
          <Section
            label={`Already processed (${cards.length})`}
            hits={cards}
            existing={existing}
            selected={selected}
            busy={busy}
            onToggle={toggle}
            onPreview={setPreviewKey}
            highlight
          />
        )}
      </div>

      {previewKey && (
        <PreviewDrawer citekey={previewKey} onClose={() => setPreviewKey(null)} />
      )}
    </div>
  );
}

function Section({
  label,
  hits,
  existing,
  selected,
  busy,
  onToggle,
  onPreview,
  highlight = false,
}: {
  label: string;
  hits: PickerHit[];
  existing: Set<string>;
  selected: Set<string>;
  busy: boolean;
  onToggle: (citekey: string) => void;
  onPreview: (citekey: string) => void;
  highlight?: boolean;
}) {
  return (
    <div>
      <div className="sticky top-0 bg-muted/60 px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <ul>
        {hits.map((hit) => {
          const inBatch = existing.has(hit.citekey);
          const isChecked = selected.has(hit.citekey);
          return (
            <li
              key={hit.citekey}
              className={`flex items-start gap-2 border-b px-2 py-1.5 text-xs last:border-b-0 ${
                highlight ? "bg-emerald-50/40 dark:bg-emerald-950/20" : ""
              }`}
              data-testid={`zotero-row-${hit.citekey}`}
            >
              <input
                type="checkbox"
                checked={isChecked}
                disabled={inBatch || busy}
                onChange={() => onToggle(hit.citekey)}
                className="mt-1"
              />
              <button
                type="button"
                onClick={() => onPreview(hit.citekey)}
                className="flex flex-1 flex-col items-start text-left"
              >
                <span className="line-clamp-2 font-medium text-foreground">
                  {hit.title || hit.citekey}
                </span>
                <span className="text-[11px] text-muted-foreground">
                  {hit.authors.length > 0 ? hit.authors[0] : "Unknown"}
                  {hit.authors.length > 1 ? ` et al.` : ""}
                  {hit.year != null ? ` · ${hit.year}` : ""}
                  {hit.venue ? ` · ${hit.venue}` : ""}
                </span>
                <span className="font-mono text-[10px] text-muted-foreground">
                  @{hit.citekey}
                </span>
              </button>
              {inBatch && (
                <span className="shrink-0 self-center text-[10px] text-emerald-700">
                  in batch
                </span>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function PreviewDrawer({
  citekey,
  onClose,
}: {
  citekey: string;
  onClose: () => void;
}) {
  const previewQ = useQuery<PickerPreview>({
    queryKey: ["assistantZoteroPicker", "preview", citekey],
    queryFn: () => getPickerPreview(citekey),
  });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      data-testid="zotero-preview-drawer"
      className="absolute right-0 top-0 z-30 h-full w-[480px] max-w-[90vw] overflow-y-auto border-l border-border bg-background shadow-xl"
    >
      <header className="sticky top-0 flex items-center gap-2 border-b border-border bg-background/95 px-3 py-2 backdrop-blur">
        <span className="font-mono text-xs text-muted-foreground">
          @{citekey}
        </span>
        <button
          type="button"
          onClick={onClose}
          className="ml-auto rounded border px-2 py-0.5 text-xs hover:bg-muted"
          aria-label="Close preview"
        >
          Close
        </button>
      </header>
      <div className="p-3 text-sm">
        {previewQ.isLoading && (
          <p className="text-muted-foreground">Loading…</p>
        )}
        {previewQ.error && (
          <p className="text-destructive">
            Could not load preview: {(previewQ.error as Error).message}
          </p>
        )}
        {previewQ.data && (
          <div className="flex flex-col gap-3">
            <div>
              <h3 className="text-base font-semibold leading-tight">
                {previewQ.data.title || previewQ.data.citekey}
              </h3>
              <p className="mt-1 text-xs text-muted-foreground">
                {previewQ.data.authors.join(", ") || "Unknown authors"}
                {previewQ.data.year != null ? ` · ${previewQ.data.year}` : ""}
              </p>
              {previewQ.data.venue && (
                <p className="text-xs text-muted-foreground">
                  {previewQ.data.venue}
                </p>
              )}
              {(previewQ.data.doi || previewQ.data.url) && (
                <p className="mt-1 text-[11px] text-muted-foreground">
                  {previewQ.data.doi && <span>doi: {previewQ.data.doi}</span>}
                </p>
              )}
            </div>
            {previewQ.data.abstract && (
              <section>
                <h4 className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                  Abstract
                </h4>
                <p className="mt-1 whitespace-pre-wrap text-xs leading-relaxed">
                  {previewQ.data.abstract}
                </p>
              </section>
            )}
            {previewQ.data.card_body && (
              <section>
                <h4 className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                  Paper card
                </h4>
                <pre className="mt-1 whitespace-pre-wrap rounded border bg-muted/40 p-2 font-sans text-[11px] leading-relaxed">
                  {previewQ.data.card_body}
                </pre>
              </section>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Manual add ─────────────────────────────────────────────────────────────

/**
 * Plain-language text for the Zotero picker's ``library_reason`` codes.
 * The raw code ("bbt-path-unset") used to be shown as is (v0.3.1 QA).
 */
export function describeLibraryReason(reason: string, bbtPath: string | null): string {
  if (reason === "bbt-path-unset") {
    return (
      "Zotero is not connected. Set the Better BibTeX export path (bbt_json) " +
      "in zsum ▸ Settings, then search again."
    );
  }
  if (reason === "bbt-file-missing") {
    return (
      `Zotero is not connected: the Better BibTeX export${bbtPath ? ` at ${bbtPath}` : ""} ` +
      "was not found. Check that Zotero is running with a Better BibTeX auto-export " +
      "to that path, or fix bbt_json in zsum ▸ Settings."
    );
  }
  return `Zotero library unavailable: ${reason}${bbtPath ? ` (${bbtPath})` : ""}`;
}

function ManualAdd({
  existing,
  onAdd,
  busy,
}: {
  existing: Set<string>;
  onAdd: (sources: BatchSourceItem[]) => void;
  busy: boolean;
}) {
  const [kind, setKind] = useState<SourceItemKind>("discovered");
  const [title, setTitle] = useState("");
  const [zoteroQuery, setZoteroQuery] = useState("");
  const searchQ = usePickerSearch(zoteroQuery);
  const candidates = useMemo(() => {
    const items = searchQ.data?.items ?? [];
    return items.filter((h) => !existing.has(h.citekey)).slice(0, 8);
  }, [searchQ.data, existing]);

  function submitFreeText() {
    const trimmed = title.trim();
    if (!trimmed) return;
    const id =
      trimmed.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") ||
      `src-${Date.now()}`;
    onAdd([{ id, kind, title: trimmed }]);
    setTitle("");
  }

  function addResolved(hit: PickerHit) {
    if (existing.has(hit.citekey)) return;
    onAdd([hitToSource(hit)]);
    setZoteroQuery("");
  }

  return (
    <details className="rounded border bg-muted/10 p-3 text-xs">
      <summary className="cursor-pointer select-none font-medium">
        Add source manually
      </summary>
      <div className="mt-2 flex flex-col gap-2">
        <label className="flex flex-col gap-1">
          <span className="text-[10px] font-medium uppercase text-muted-foreground">
            Kind
          </span>
          <select
            data-testid="add-source-kind"
            value={kind}
            onChange={(e) => setKind(e.target.value as SourceItemKind)}
            className="rounded border px-2 py-1 text-sm"
          >
            {KIND_OPTIONS.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
        </label>

        {kind === "zotero" ? (
          <div className="flex flex-col gap-1">
            <span className="text-[10px] font-medium uppercase text-muted-foreground">
              Zotero search (resolves to a citekey)
            </span>
            <input
              data-testid="add-source-zotero-search"
              value={zoteroQuery}
              onChange={(e) => setZoteroQuery(e.target.value)}
              placeholder="Title, author, citekey…"
              className="rounded border px-2 py-1 text-sm"
            />
            {zoteroQuery.trim() && (
              <ul
                className="max-h-48 overflow-auto rounded border bg-background"
                data-testid="add-source-zotero-results"
              >
                {searchQ.isLoading && (
                  <li className="p-2 text-muted-foreground">Searching…</li>
                )}
                {!searchQ.isLoading && candidates.length === 0 && (
                  <li className="p-2 text-muted-foreground">
                    No matches — only resolved citekeys can be added as Zotero
                    sources.
                  </li>
                )}
                {candidates.map((hit) => (
                  <li key={hit.citekey} className="border-b last:border-b-0">
                    <button
                      type="button"
                      onClick={() => addResolved(hit)}
                      disabled={busy}
                      className="flex w-full flex-col items-start gap-0.5 px-2 py-1.5 text-left hover:bg-muted disabled:opacity-50"
                      data-testid={`add-source-zotero-pick-${hit.citekey}`}
                    >
                      <span className="line-clamp-1 font-medium text-foreground">
                        {hit.title || hit.citekey}
                      </span>
                      <span className="text-[10px] text-muted-foreground">
                        {hit.authors[0] ?? "Unknown"}
                        {hit.authors.length > 1 ? " et al." : ""}
                        {hit.year != null ? ` · ${hit.year}` : ""} · @{hit.citekey}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ) : (
          <div className="flex items-end gap-2">
            <label className="flex flex-1 flex-col gap-1">
              <span className="text-[10px] font-medium uppercase text-muted-foreground">
                Title
              </span>
              <input
                data-testid="add-source-title"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                className="rounded border px-2 py-1 text-sm"
                placeholder="Source title"
              />
            </label>
            <button
              type="button"
              data-testid="add-source-submit"
              disabled={busy || !title.trim()}
              title={title.trim() ? undefined : "Type a source title first."}
              onClick={submitFreeText}
              className="rounded bg-primary px-3 py-1 text-sm text-primary-foreground disabled:opacity-50"
            >
              Add
            </button>
          </div>
        )}
      </div>
    </details>
  );
}
