import type { GraphEdge, GraphNode } from "../../api/graph";
import { EstablishedBadge, ReportIssueLink, isEstablished } from "../nodes/EstablishedBadge";

interface Props {
  node: GraphNode | null;
  edges: GraphEdge[];
  actionError?: string | null;
  onFocusEgo: () => void;
  onOpenInNodes: () => void;
  onOpenInEditor: () => void;
  /** Called when the user clicks "Copy to vault" on an established node. */
  onCopyToVault?: () => void;
  /** Called when the user clicks "Send to Learn" (node-depth form B). */
  onSendToLearn?: () => void;
  /**
   * Called when the user clicks "Send slice to Learn" (explicit-slice form A).
   * Only shown when truthy.
   */
  onSendSliceToLearn?: () => void;
}

export function NodeInspector({
  node,
  edges,
  actionError,
  onFocusEgo,
  onOpenInNodes,
  onOpenInEditor,
  onCopyToVault,
  onSendToLearn,
  onSendSliceToLearn,
}: Props) {
  if (!node) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-sm text-muted-foreground">
        Select a node in the graph to inspect its metadata.
      </div>
    );
  }

  const incoming = edges.filter((edge) => edge.target === node.id);
  const outgoing = edges.filter((edge) => edge.source === node.id);

  return (
    <div className="flex h-full flex-col overflow-y-auto bg-background">
      <div className="border-b border-border px-4 py-3">
        <div className="flex items-center justify-between">
          <div className="text-xs uppercase tracking-wide text-muted-foreground">Inspector</div>
          <EstablishedBadge node={node} />
        </div>
        <h2 className="mt-1 text-lg font-semibold">{node.title || node.id}</h2>
        <div className="mt-1 font-mono text-xs text-muted-foreground">{node.id}</div>
      </div>

      <div className="flex flex-col gap-4 px-4 py-4 text-sm">
        <div className="flex flex-wrap gap-2 text-xs">
          {node.status && <Chip>{node.status}</Chip>}
          {node.domain && <Chip>{node.domain}</Chip>}
          {node.subdomain && <Chip>{node.subdomain}</Chip>}
          {node.node_origin && <Chip>{node.node_origin}</Chip>}
          {node.confidence != null && <Chip>confidence {node.confidence.toFixed(2)}</Chip>}
        </div>

        {node.tags.length > 0 && (
          <section>
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Tags</div>
            <div className="flex flex-wrap gap-2">
              {node.tags.map((tag) => (
                <Chip key={tag}>#{tag}</Chip>
              ))}
            </div>
          </section>
        )}

        <section>
          <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Metadata</div>
          <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-2 text-sm">
            <Row label="Source" value={node.source} />
            <Row label="File path" value={node.file_path} mono />
            <Row label="Incoming" value={String(incoming.length)} />
            <Row label="Outgoing" value={String(outgoing.length)} />
          </dl>
        </section>

        <section>
          <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Actions</div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={onFocusEgo}
              className="rounded border border-border px-3 py-1 text-xs hover:bg-muted"
            >
              Focus ego graph
            </button>
            <button
              type="button"
              onClick={onOpenInNodes}
              disabled={!node.file_path}
              className="rounded border border-border px-3 py-1 text-xs hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
            >
              Open in Nodes
            </button>
            {isEstablished(node) ? (
              /* Established (Tier A) — read-only: offer copy instead of edit. */
              <button
                type="button"
                data-testid="node-inspector-copy-to-vault"
                onClick={onCopyToVault}
                disabled={!node.file_path || !onCopyToVault}
                title="Clone this established node into your vault as an editable draft."
                className="rounded border border-border px-3 py-1 text-xs hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
              >
                Copy to vault
              </button>
            ) : (
              /* Draft/Generated (Tier B) — user owns it, offer direct edit. */
              <button
                type="button"
                onClick={onOpenInEditor}
                disabled={!node.file_path}
                className="rounded border border-border px-3 py-1 text-xs hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
              >
                Open in Editor
              </button>
            )}
            {onSendToLearn && (
              <button
                type="button"
                data-testid="node-inspector-send-to-learn"
                onClick={onSendToLearn}
                className="rounded border border-border px-3 py-1 text-xs hover:bg-muted"
              >
                Send to Learn
              </button>
            )}
            {onSendSliceToLearn && (
              <button
                type="button"
                data-testid="node-inspector-send-slice-to-learn"
                onClick={onSendSliceToLearn}
                className="rounded border border-border px-3 py-1 text-xs hover:bg-muted"
              >
                Send slice to Learn
              </button>
            )}
            <ReportIssueLink node={node} />
          </div>
          {actionError && <p className="mt-2 text-xs text-red-600">{actionError}</p>}
        </section>

        <section>
          <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Connected edges</div>
          <div className="flex flex-col gap-3 text-xs">
            <EdgeList title="Incoming" edges={incoming} />
            <EdgeList title="Outgoing" edges={outgoing} />
          </div>
        </section>
      </div>
    </div>
  );
}

function Chip({ children }: { children: React.ReactNode }) {
  return <span className="rounded-full border border-border bg-muted/50 px-2 py-0.5">{children}</span>;
}

function Row({ label, value, mono = false }: { label: string; value: string | null; mono?: boolean }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className={mono ? "break-all font-mono text-xs" : "break-all"}>{value ?? "—"}</dd>
    </>
  );
}

function EdgeList({ title, edges }: { title: string; edges: GraphEdge[] }) {
  if (edges.length === 0) {
    return (
      <div>
        <div className="font-medium text-foreground">{title}</div>
        <div className="text-muted-foreground">None</div>
      </div>
    );
  }

  return (
    <div>
      <div className="font-medium text-foreground">{title}</div>
      <ul className="mt-1 space-y-1">
        {edges.slice(0, 8).map((edge) => (
          <li key={`${edge.source}:${edge.target}:${edge.type ?? "edge"}`} className="rounded border border-border bg-muted/20 px-2 py-1">
            <div className="font-mono text-[11px]">
              {edge.source} → {edge.target}
            </div>
            <div className="text-muted-foreground">
              {edge.type ?? "edge"}
              {edge.weight != null ? ` · w=${edge.weight.toFixed(2)}` : ""}
              {edge.note ? ` · ${edge.note}` : ""}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
