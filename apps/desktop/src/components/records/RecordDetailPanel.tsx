import { Link } from "react-router-dom";

import { useRecord } from "../../hooks/records";
import { PayloadView } from "./PayloadView";

const RECORD_REF = /^(rec-[0-9]{8}-[0-9a-hjkmnp-tv-z]{8})(?:@[1-9][0-9]*)?$/;

interface Props {
  store: string;
  recordId: string;
  onSelectRecord: (recordId: string) => void;
  onClose?: () => void;
}

/** One record: envelope, payload (schema-aware), source refs, history. */
export function RecordDetailPanel({ store, recordId, onSelectRecord, onClose }: Props) {
  const { data, isLoading, error } = useRecord(store, recordId);

  if (isLoading) return <p className="text-sm text-muted-foreground">Loading record…</p>;
  if (error || !data) {
    return (
      <p className="text-sm text-red-600 dark:text-red-400">
        Could not load {recordId}: {error instanceof Error ? error.message : "unknown error"}
      </p>
    );
  }

  const { record, state, history, schema, schema_error } = data;
  const graphLink = `/records/graph?store=${encodeURIComponent(store)}&record=${encodeURIComponent(recordId)}`;
  const neighbours = data.neighbours.nodes.filter((n) => n.id !== recordId && n.kind === "record");

  return (
    <article className="space-y-4">
      <header className="space-y-1">
        <div className="flex items-start justify-between gap-2">
          <h2 className="text-base font-semibold">{record.summary}</h2>
          {onClose && (
            <button className="text-xs text-muted-foreground hover:text-foreground" onClick={onClose}>
              Close
            </button>
          )}
        </div>
        <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
          <code className="font-mono">{record.record_id}</code>
          <span>rev {record.revision}</span>
          <Badge>{record.schema_ref}</Badge>
          <Badge tone={record.lifecycle === "active" ? "ok" : "muted"}>{record.lifecycle}</Badge>
          <Badge>{record.review_state}</Badge>
          <Badge tone={record.sensitivity === "sensitive" ? "warn" : "muted"}>{record.sensitivity}</Badge>
          <Link to={graphLink} className="underline hover:text-foreground">
            Show in graph
          </Link>
        </div>
        {record.tags.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {record.tags.map((tag) => (
              <span key={tag} className="rounded bg-muted px-1.5 py-0.5 text-[11px]">
                #{tag}
              </span>
            ))}
          </div>
        )}
      </header>

      {state.superseded_by && (
        <p className="rounded border border-red-500/30 bg-red-500/5 p-2 text-xs">
          Superseded by{" "}
          <button className="font-mono underline" onClick={() => onSelectRecord(state.superseded_by as string)}>
            {state.superseded_by}
          </button>
          {state.reason ? ` — ${state.reason}` : ""}
        </p>
      )}
      {state.promoted_to && (
        <p className="rounded border border-emerald-500/30 bg-emerald-500/5 p-2 text-xs">
          Promoted to AKMS node <code className="font-mono">{state.promoted_to.node_id}</code> (from
          revision {state.promoted_to.revision})
        </p>
      )}

      <section className="space-y-2">
        <h3 className="text-sm font-semibold">
          Payload{" "}
          <span className="font-normal text-muted-foreground">
            {schema?.title ? `— ${schema.title}` : ""}
          </span>
        </h3>
        {schema_error && (
          <p className="text-xs text-amber-700 dark:text-amber-300">
            Schema unavailable ({schema_error.code}); showing raw fields.
          </p>
        )}
        <div className="rounded border border-border p-3">
          <PayloadView value={record.payload} schema={schema?.schema ?? null} />
        </div>
      </section>

      <section className="space-y-1">
        <h3 className="text-sm font-semibold">Source references</h3>
        {record.source_refs.length === 0 ? (
          <p className="text-xs text-muted-foreground">None.</p>
        ) : (
          <ul className="space-y-1 text-xs">
            {record.source_refs.map((ref, index) => {
              const match = RECORD_REF.exec(ref.ref);
              return (
                <li key={index} className="flex flex-wrap gap-2">
                  <Badge>{ref.kind}</Badge>
                  {match ? (
                    <button className="font-mono underline" onClick={() => onSelectRecord(match[1])}>
                      {ref.ref}
                    </button>
                  ) : /^https?:\/\//.test(ref.ref) ? (
                    <a className="break-all underline" href={ref.ref} target="_blank" rel="noreferrer">
                      {ref.ref}
                    </a>
                  ) : (
                    <span className="break-all font-mono">{ref.ref}</span>
                  )}
                  {ref.note && <span className="text-muted-foreground">{ref.note}</span>}
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {neighbours.length > 0 && (
        <section className="space-y-1">
          <h3 className="text-sm font-semibold">Linked records</h3>
          <ul className="space-y-1 text-xs">
            {neighbours.map((node) => {
              const edge = data.neighbours.edges.find(
                (e) =>
                  (e.source === recordId && e.target === node.id) ||
                  (e.target === recordId && e.source === node.id),
              );
              const direction = edge?.source === recordId ? "→" : "←";
              return (
                <li key={node.id}>
                  <span className="text-muted-foreground">
                    {direction} {edge?.label}{" "}
                  </span>
                  <button className="underline" onClick={() => onSelectRecord(node.id)}>
                    {node.label}
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      <section className="space-y-1">
        <h3 className="text-sm font-semibold">History ({state.events} events)</h3>
        <ol className="space-y-1 text-xs">
          {history.map((event, index) => (
            <li key={index}>
              <details>
                <summary className="cursor-pointer">
                  <span className="font-mono">{String(event.kind ?? "event")}</span>{" "}
                  <span className="text-muted-foreground">
                    {String(event.at ?? event.recorded_at ?? "")}
                    {event.actor ? ` · ${String(event.actor)}` : ""}
                  </span>
                </summary>
                <pre className="mt-1 max-h-64 overflow-auto rounded bg-muted p-2 text-[11px]">
                  {JSON.stringify(event, null, 2)}
                </pre>
              </details>
            </li>
          ))}
        </ol>
      </section>

      <p className="text-[11px] text-muted-foreground">
        origin {record.origin.kind} · {record.origin.actor_id} · recorded {record.recorded_at} ·
        updated {state.updated_at}
      </p>
    </article>
  );
}

function Badge({ children, tone = "muted" }: { children: React.ReactNode; tone?: "ok" | "warn" | "muted" }) {
  const cls =
    tone === "ok"
      ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
      : tone === "warn"
        ? "bg-amber-500/15 text-amber-700 dark:text-amber-300"
        : "bg-muted text-muted-foreground";
  return <span className={`rounded px-1.5 py-0.5 text-[11px] ${cls}`}>{children}</span>;
}
