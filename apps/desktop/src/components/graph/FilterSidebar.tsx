import type { GraphDirection, GraphNode, GraphStatus } from "../../api/graph";
import { IndeterminateProgressBar } from "../IndeterminateProgressBar";

export function getRefreshButtonLabel(cacheExists: boolean | undefined, refreshPending: boolean) {
  if (refreshPending) {
    return cacheExists ? "Regenerating…" : "Generating…";
  }
  return cacheExists ? "Regenerate" : "Generate";
}

export function getIssueLogButtonLabel(issueCount: number) {
  return issueCount > 0 ? `Review skipped files (${issueCount})` : "View last scan log";
}

export type GraphViewMode = "filtered" | "ego" | "cluster";

interface Props {
  status: GraphStatus | undefined;
  allNodes: GraphNode[];
  viewMode: GraphViewMode;
  onViewModeChange: (mode: GraphViewMode) => void;
  selectedNodeId: string | null;
  onSelectedNodeChange: (nodeId: string | null) => void;
  depth: number;
  onDepthChange: (depth: number) => void;
  direction: GraphDirection;
  onDirectionChange: (direction: GraphDirection) => void;
  query: string;
  onQueryChange: (value: string) => void;
  domain: string;
  onDomainChange: (value: string) => void;
  statusFilter: string;
  onStatusFilterChange: (value: string) => void;
  tag: string;
  onTagChange: (value: string) => void;
  origin: string;
  onOriginChange: (value: string) => void;
  onClearFilters: () => void;
  onRefresh: () => void;
  refreshPending: boolean;
  progressLabel?: string | null;
  progressPercent?: number | null;
  issueCount?: number;
  issueLogAvailable?: boolean;
  onOpenIssueLog?: () => void;
  errorMessage?: string | null;
}

export function FilterSidebar({
  status,
  allNodes,
  viewMode,
  onViewModeChange,
  selectedNodeId,
  onSelectedNodeChange,
  depth,
  onDepthChange,
  direction,
  onDirectionChange,
  query,
  onQueryChange,
  domain,
  onDomainChange,
  statusFilter,
  onStatusFilterChange,
  tag,
  onTagChange,
  origin,
  onOriginChange,
  onClearFilters,
  onRefresh,
  refreshPending,
  progressLabel,
  progressPercent,
  issueCount = 0,
  issueLogAvailable = false,
  onOpenIssueLog,
  errorMessage,
}: Props) {
  const domains = unique(allNodes.map((node) => node.domain).filter(Boolean) as string[]);
  const tags = unique(allNodes.flatMap((node) => node.tags));
  const nodeIds = unique(allNodes.map((node) => node.id));
  const statuses = unique(allNodes.map((node) => node.status).filter(Boolean) as string[]);
  const origins = unique(allNodes.map((node) => node.node_origin).filter(Boolean) as string[]);
  const refreshLabel = getRefreshButtonLabel(status?.cache_exists, refreshPending);
  const issueLogLabel = getIssueLogButtonLabel(issueCount);

  return (
    <div className="flex h-full flex-col overflow-y-auto border-r border-border bg-background">
      <div className="border-b border-border px-3 py-3">
        <div className="flex items-center justify-between gap-2">
          <div>
            <h2 className="text-sm font-semibold">Graph controls</h2>
            <p className="text-[11px] text-muted-foreground">Generate, filter, and slice the cached AKMS graph.</p>
          </div>
          <button
            type="button"
            onClick={onRefresh}
            disabled={refreshPending}
            className="rounded border border-border px-2 py-1 text-xs hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
          >
            {refreshLabel}
          </button>
        </div>
        <div className="mt-3 rounded border border-border bg-muted/30 p-2 text-[11px] text-muted-foreground">
          <div>{status?.node_count ?? 0} nodes · {status?.edge_count ?? 0} edges</div>
          <div className="truncate" title={status?.generated_at ?? undefined}>
            Cached: {status?.generated_at ?? "not built yet"}
          </div>
        </div>
        {refreshPending && (
          <IndeterminateProgressBar
            label={
              progressLabel
              ?? (status?.cache_exists ? "Regenerating AKMS graph…" : "Generating AKMS graph…")
            }
            progressPercent={progressPercent}
            className="mt-3"
          />
        )}
        {issueLogAvailable && onOpenIssueLog && (
          <button
            type="button"
            onClick={onOpenIssueLog}
            className="mt-3 rounded border border-border px-2 py-1 text-xs hover:bg-muted"
          >
            {issueLogLabel}
          </button>
        )}
      </div>

      <div className="flex flex-col gap-4 p-3 text-sm">
        <section className="flex flex-col gap-2">
          <label className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">View mode</label>
          <select
            value={viewMode}
            onChange={(e) => onViewModeChange(e.target.value as GraphViewMode)}
            className="rounded border border-border bg-background px-2 py-1 text-sm"
          >
            <option value="filtered">Filtered graph</option>
            <option value="ego">Ego graph</option>
            <option value="cluster">Cluster</option>
          </select>
        </section>

        <section className="flex flex-col gap-2">
          <label className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Focus node</label>
          <input
            value={selectedNodeId ?? ""}
            onChange={(e) => onSelectedNodeChange(e.target.value || null)}
            list="graph-node-ids"
            placeholder="Node id for inspector / ego view"
            className="rounded border border-border bg-background px-2 py-1 font-mono text-xs"
          />
          <datalist id="graph-node-ids">
            {nodeIds.map((nodeId) => (
              <option key={nodeId} value={nodeId} />
            ))}
          </datalist>
          {viewMode === "ego" && (
            <>
              <label className="text-xs text-muted-foreground">Depth</label>
              <input
                type="number"
                min={0}
                max={6}
                value={depth}
                onChange={(e) => onDepthChange(Number(e.target.value) || 0)}
                className="rounded border border-border bg-background px-2 py-1 font-mono text-xs"
              />
              <label className="text-xs text-muted-foreground">Direction</label>
              <select
                value={direction}
                onChange={(e) => onDirectionChange(e.target.value as GraphDirection)}
                className="rounded border border-border bg-background px-2 py-1 text-sm"
              >
                <option value="both">Both</option>
                <option value="in">Incoming</option>
                <option value="out">Outgoing</option>
              </select>
            </>
          )}
        </section>

        <section className="flex flex-col gap-2 border-t border-border pt-4">
          <div className="flex items-center justify-between gap-2">
            <label className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Filters</label>
            <button
              type="button"
              onClick={onClearFilters}
              className="text-[11px] text-blue-600 hover:underline"
            >
              Clear
            </button>
          </div>
          <Field label="Search" value={query} onChange={onQueryChange} placeholder="title, id, tag…" />
          <Field label="Domain" value={domain} onChange={onDomainChange} placeholder="computational-mechanics" datalist="graph-domains" />
          <Field label="Tag" value={tag} onChange={onTagChange} placeholder="plasticity" datalist="graph-tags" />
          <Field label="Status" value={statusFilter} onChange={onStatusFilterChange} placeholder="established" datalist="graph-statuses" />
          <Field label="Origin" value={origin} onChange={onOriginChange} placeholder="global" datalist="graph-origins" />
          <datalist id="graph-domains">{domains.map((value) => <option key={value} value={value} />)}</datalist>
          <datalist id="graph-tags">{tags.map((value) => <option key={value} value={value} />)}</datalist>
          <datalist id="graph-statuses">{statuses.map((value) => <option key={value} value={value} />)}</datalist>
          <datalist id="graph-origins">{origins.map((value) => <option key={value} value={value} />)}</datalist>
          <p className="text-[11px] text-muted-foreground">
            Filtered mode uses all fields. Cluster mode uses tag/domain. Ego mode uses the selected node, depth, and direction.
          </p>
        </section>

        {errorMessage && (
          <div className="rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
            {errorMessage}
          </div>
        )}
      </div>
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  placeholder,
  datalist,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  datalist?: string;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs text-muted-foreground">{label}</span>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        list={datalist}
        className="rounded border border-border bg-background px-2 py-1 font-mono text-xs"
      />
    </label>
  );
}

function unique(values: string[]) {
  return [...new Set(values)].sort((a, b) => a.localeCompare(b));
}
