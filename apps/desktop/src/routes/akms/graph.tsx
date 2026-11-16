import { useEffect, useMemo, useState } from "react";
import { Group, Panel, Separator } from "react-resizable-panels";
import { useNavigate, useSearchParams } from "react-router-dom";

import { ApiError } from "../../api/client";
import {
  GraphReviewRequiredError,
  type GraphDirection,
  type GraphEdge,
  type GraphIssueLog,
  type GraphMeta,
  type GraphNode,
} from "../../api/graph";
import { copyToVault, openInEditor } from "../../api/nodes";
import { FilterSidebar, type GraphViewMode } from "../../components/graph/FilterSidebar";
import { GraphCanvas } from "../../components/graph/GraphCanvas";
import { GraphIssueLogDialog } from "../../components/graph/GraphIssueLogDialog";
import { NodeInspector } from "../../components/graph/NodeInspector";
import {
  useGraphCluster,
  useGraphEdges,
  useGraphIssueLog,
  useGraphEgo,
  useGraphNodes,
  useGraphRefreshStream,
  useScanGraphIssueLog,
  useSetGraphIssueIgnored,
  useGraphStatus,
} from "../../hooks/graph";
import { buildNodePrefill, buildSlicePrefill } from "../../components/graph/sendToLearn";

function formatApiError(error: unknown): string {
  if (error instanceof ApiError) {
    const message =
      typeof error.detail === "object" && error.detail && "error" in error.detail
        ? String((error.detail as { error?: { message?: string } }).error?.message ?? error.message)
        : error.message;
    return message;
  }
  return error instanceof Error ? error.message : String(error);
}

export default function GraphRoute() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  const selectedNodeId = searchParams.get("node");
  const modeParam = searchParams.get("mode");

  const [viewMode, setViewMode] = useState<GraphViewMode>(
    modeParam === "cluster" || modeParam === "ego" || modeParam === "filtered"
      ? modeParam
      : selectedNodeId
        ? "ego"
        : "filtered",
  );
  const [query, setQuery] = useState("");
  const [domain, setDomain] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [tag, setTag] = useState("");
  const [origin, setOrigin] = useState("");
  const [depth, setDepth] = useState(2);
  const [direction, setDirection] = useState<GraphDirection>("both");
  const [inspectorActionError, setInspectorActionError] = useState<string | null>(null);
  const [graphActionError, setGraphActionError] = useState<string | null>(null);
  const [issueDialogOpen, setIssueDialogOpen] = useState(false);
  const [issueDialogMode, setIssueDialogMode] = useState<"view" | "review">("view");
  const [issueDialogReport, setIssueDialogReport] = useState<GraphIssueLog | null>(null);
  const [issueDialogError, setIssueDialogError] = useState<string | null>(null);
  const [pendingIssuePath, setPendingIssuePath] = useState<string | null>(null);

  useEffect(() => {
    if (modeParam === "cluster" || modeParam === "ego" || modeParam === "filtered") {
      setViewMode(modeParam);
    } else if (selectedNodeId) {
      setViewMode("ego");
    }
  }, [modeParam, selectedNodeId]);

  const statusQuery = useGraphStatus();
  const issueLogQuery = useGraphIssueLog();
  const scanIssueLog = useScanGraphIssueLog();
  const toggleIgnoredIssue = useSetGraphIssueIgnored();
  const refresh = useGraphRefreshStream();
  const graphDataEnabled = Boolean(statusQuery.data?.cache_exists);
  const refreshPending = scanIssueLog.isPending || refresh.isPending;
  const allNodesQuery = useGraphNodes({}, graphDataEnabled);
  const filteredNodesQuery = useGraphNodes(
    {
      query: query || undefined,
      domain: domain || undefined,
      status: statusFilter || undefined,
      tag: tag || undefined,
      origin: origin || undefined,
    },
    graphDataEnabled && viewMode === "filtered",
  );
  const allEdgesQuery = useGraphEdges({}, graphDataEnabled && viewMode === "filtered");
  const egoQuery = useGraphEgo(
    {
      node: selectedNodeId ?? "",
      depth,
      direction,
    },
    graphDataEnabled && viewMode === "ego" && !!selectedNodeId,
  );
  const clusterQuery = useGraphCluster(
    {
      tag: tag || undefined,
      domain: domain || undefined,
    },
    graphDataEnabled && viewMode === "cluster" && (!!tag || !!domain),
  );

  const latestIssueLog = useMemo(() => {
    if (!issueDialogReport) return issueLogQuery.data ?? null;
    if (!issueLogQuery.data?.generated_at) return issueDialogReport;
    return issueLogQuery.data.generated_at === issueDialogReport.generated_at
      ? issueLogQuery.data
      : issueDialogReport;
  }, [issueDialogReport, issueLogQuery.data]);

  const active = useMemo<{
    nodes: GraphNode[];
    edges: GraphEdge[];
    meta: GraphMeta | null;
    loading: boolean;
    error: string | null;
    emptyMessage: string | null;
  }>(() => {
    if (!graphDataEnabled) {
      return {
        nodes: [],
        edges: [],
        meta: null,
        loading: statusQuery.isLoading,
        error: null,
        emptyMessage: statusQuery.data
          ? "Generate the graph to inspect it. Logic-Loom will scan for problematic markdown first."
          : null,
      };
    }

    if (viewMode === "ego") {
      if (!selectedNodeId) {
        return {
          nodes: [],
          edges: [],
          meta: null,
          loading: false,
          error: null,
          emptyMessage: "Choose a node id to view an ego graph.",
        };
      }
      return {
        nodes: egoQuery.data?.nodes ?? [],
        edges: egoQuery.data?.edges ?? [],
        meta: egoQuery.data?.meta ?? null,
        loading: egoQuery.isLoading,
        error: egoQuery.error ? formatApiError(egoQuery.error) : null,
        emptyMessage: egoQuery.data && egoQuery.data.nodes.length === 0 ? "No nodes found for this ego graph." : null,
      };
    }

    if (viewMode === "cluster") {
      if (!tag && !domain) {
        return {
          nodes: [],
          edges: [],
          meta: null,
          loading: false,
          error: null,
          emptyMessage: "Set a tag or domain to view a cluster slice.",
        };
      }
      return {
        nodes: clusterQuery.data?.nodes ?? [],
        edges: clusterQuery.data?.edges ?? [],
        meta: clusterQuery.data?.meta ?? null,
        loading: clusterQuery.isLoading,
        error: clusterQuery.error ? formatApiError(clusterQuery.error) : null,
        emptyMessage: clusterQuery.data && clusterQuery.data.nodes.length === 0 ? "No nodes matched this cluster." : null,
      };
    }

    const nodes = filteredNodesQuery.data ?? [];
    const visible = new Set(nodes.map((node) => node.id));
    const edges = (allEdgesQuery.data ?? []).filter(
      (edge) => visible.has(edge.source) && visible.has(edge.target),
    );
    return {
      nodes,
      edges,
      meta: statusQuery.data
        ? {
            generated_at: statusQuery.data.generated_at,
            node_count: statusQuery.data.node_count,
            edge_count: statusQuery.data.edge_count,
            repo_id: statusQuery.data.repo_id,
            global_vault: statusQuery.data.akms_global_vault,
            cache_path: statusQuery.data.cache_path,
            scope: "filtered",
            selected_node: selectedNodeId,
            depth: null,
            direction: null,
            tag,
            domain,
          }
        : null,
      loading: filteredNodesQuery.isLoading || allEdgesQuery.isLoading,
      error: filteredNodesQuery.error
        ? formatApiError(filteredNodesQuery.error)
        : allEdgesQuery.error
          ? formatApiError(allEdgesQuery.error)
          : null,
      emptyMessage: filteredNodesQuery.data && filteredNodesQuery.data.length === 0 ? "No nodes matched the current filters." : null,
    };
  }, [
    viewMode,
    selectedNodeId,
    egoQuery.data,
    egoQuery.error,
    egoQuery.isLoading,
    clusterQuery.data,
    clusterQuery.error,
    clusterQuery.isLoading,
    filteredNodesQuery.data,
    filteredNodesQuery.error,
    filteredNodesQuery.isLoading,
    allEdgesQuery.data,
    allEdgesQuery.error,
    allEdgesQuery.isLoading,
    graphDataEnabled,
    statusQuery.isLoading,
    statusQuery.data,
    tag,
    domain,
  ]);

  const selectedNode = useMemo(
    () => active.nodes.find((node) => node.id === selectedNodeId) ?? allNodesQuery.data?.find((node) => node.id === selectedNodeId) ?? null,
    [active.nodes, allNodesQuery.data, selectedNodeId],
  );
  const selectedEdges = useMemo(
    () => (selectedNode ? active.edges.filter((edge) => edge.source === selectedNode.id || edge.target === selectedNode.id) : []),
    [active.edges, selectedNode],
  );

  const setMode = (mode: GraphViewMode) => {
    setViewMode(mode);
    const next = new URLSearchParams(searchParams);
    next.set("mode", mode);
    setSearchParams(next, { replace: true });
  };

  const setSelectedNode = (nodeId: string | null) => {
    const next = new URLSearchParams(searchParams);
    if (nodeId) next.set("node", nodeId);
    else next.delete("node");
    setSearchParams(next, { replace: true });
  };

  const clearFilters = () => {
    setQuery("");
    setDomain("");
    setStatusFilter("");
    setTag("");
    setOrigin("");
  };

  const handleOpenInNodes = () => {
    if (!selectedNode?.file_path) return;
    navigate(`/akms/nodes?path=${encodeURIComponent(selectedNode.file_path)}`);
  };

  const handleOpenInEditor = async () => {
    if (!selectedNode?.file_path) return;
    setInspectorActionError(null);
    try {
      await openInEditor(selectedNode.file_path);
    } catch (error) {
      setInspectorActionError(formatApiError(error));
    }
  };

  const handleCopyToVault = async () => {
    if (!selectedNode?.file_path) return;
    setInspectorActionError(null);
    try {
      const result = await copyToVault(selectedNode.file_path);
      // NodeInspector surfaces only actionError; report the copy path there as
      // a non-error confirmation so the user knows where the editable copy landed.
      setInspectorActionError(`Copied to your vault: ${result.copy_path}`);
    } catch (error) {
      setInspectorActionError(formatApiError(error));
    }
  };

  const openIssueDialog = (report: GraphIssueLog, mode: "view" | "review") => {
    setIssueDialogError(null);
    setIssueDialogReport(report);
    setIssueDialogMode(mode);
    setIssueDialogOpen(true);
  };

  const handleOpenIssueLog = async () => {
    const report = latestIssueLog ?? (await issueLogQuery.refetch()).data ?? null;
    if (!report) {
      setGraphActionError("No graph scan log is available yet.");
      return;
    }
    openIssueDialog(report, "view");
  };

  const handleRefresh = async () => {
    setGraphActionError(null);
    setIssueDialogError(null);
    try {
      const report = await scanIssueLog.mutateAsync();
      setIssueDialogReport(report);
      void issueLogQuery.refetch();
      if (report.issue_count > 0) {
        openIssueDialog(report, "review");
        return;
      }
      await refresh.start();
    } catch (error) {
      if (error instanceof GraphReviewRequiredError) {
        openIssueDialog(error.report, "review");
        return;
      }
      setGraphActionError(formatApiError(error));
    }
  };

  const handleContinueRefresh = async () => {
    setGraphActionError(null);
    setIssueDialogError(null);
    try {
      await refresh.start({ skipInvalidFiles: true });
      await issueLogQuery.refetch();
      setIssueDialogOpen(false);
    } catch (error) {
      if (error instanceof GraphReviewRequiredError) {
        openIssueDialog(error.report, "review");
        return;
      }
      const message = formatApiError(error);
      setGraphActionError(message);
      setIssueDialogError(message);
    }
  };

  const handleOpenIssuePath = async (path: string) => {
    setIssueDialogError(null);
    try {
      await openInEditor(path);
    } catch (error) {
      setIssueDialogError(formatApiError(error));
    }
  };

  const handleToggleIgnoreIssue = async (path: string, ignored: boolean) => {
    setIssueDialogError(null);
    setGraphActionError(null);
    setPendingIssuePath(path);
    try {
      const report = await toggleIgnoredIssue.mutateAsync({ path, ignored });
      setIssueDialogReport(report);
      await issueLogQuery.refetch();
    } catch (error) {
      const message = formatApiError(error);
      setIssueDialogError(message);
      setGraphActionError(message);
    } finally {
      setPendingIssuePath(null);
    }
  };

  const handleFocusEgo = () => {
    if (!selectedNode) return;
    setSelectedNode(selectedNode.id);
    setMode("ego");
  };

  // ── Graph-to-Learn handoff ────────────────────────────────────────────────
  /** Form B: send the selected node + current depth + direction to Learn. */
  const handleSendToLearn = () => {
    if (!selectedNodeId) return;
    navigate("/akms/learn", {
      state: buildNodePrefill(selectedNodeId, depth, direction),
    });
  };

  /**
   * Form A: send the active ego/cluster/filtered slice to Learn.
   * Only wired (via JSX prop) when the slice has ≥1 node and meta is present.
   */
  const handleSendSliceToLearn = () => {
    if (active.nodes.length === 0 || !active.meta) return;
    const meta = active.meta;
    navigate("/akms/learn", {
      state: buildSlicePrefill({
        nodes: active.nodes,
        edges: active.edges,
        meta,
      }),
    });
  };

  return (
    <Group orientation="horizontal" className="h-full">
      <Panel defaultSize={22} minSize={18}>
        <FilterSidebar
          status={statusQuery.data}
          allNodes={allNodesQuery.data ?? []}
          viewMode={viewMode}
          onViewModeChange={setMode}
          selectedNodeId={selectedNodeId}
          onSelectedNodeChange={setSelectedNode}
          depth={depth}
          onDepthChange={setDepth}
          direction={direction}
          onDirectionChange={setDirection}
          query={query}
          onQueryChange={setQuery}
          domain={domain}
          onDomainChange={setDomain}
          statusFilter={statusFilter}
          onStatusFilterChange={setStatusFilter}
          tag={tag}
          onTagChange={setTag}
          origin={origin}
          onOriginChange={setOrigin}
          onClearFilters={clearFilters}
          onRefresh={() => {
            void handleRefresh();
          }}
          refreshPending={refreshPending}
          progressLabel={scanIssueLog.isPending ? "Scanning graph source files for issues…" : refresh.progress?.message ?? null}
          progressPercent={scanIssueLog.isPending ? null : refresh.progress?.percent ?? null}
          issueCount={latestIssueLog?.issue_count ?? 0}
          issueLogAvailable={Boolean(latestIssueLog?.generated_at)}
          onOpenIssueLog={handleOpenIssueLog}
          errorMessage={statusQuery.error
            ? formatApiError(statusQuery.error)
            : graphActionError
              ? graphActionError
              : scanIssueLog.error
                ? formatApiError(scanIssueLog.error)
            : refresh.error
              ? formatApiError(refresh.error)
              : active.error}
        />
      </Panel>

      <Separator className="w-1 cursor-col-resize bg-border transition-colors hover:bg-primary/30" />

      <Panel defaultSize={53} minSize={28} className="overflow-hidden border-l border-r border-border">
        {active.loading ? (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">Loading graph…</div>
        ) : active.error ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
            <p className="text-sm text-red-600">{active.error}</p>
            {/^Unknown node:/i.test(active.error) && (
              <>
                <p className="max-w-md text-xs text-muted-foreground">
                  The graph cache may be stale. Regenerate it to include the
                  latest nodes from the vault.
                </p>
                <button
                  type="button"
                  onClick={() => void handleRefresh()}
                  disabled={refreshPending}
                  className="rounded border border-border px-3 py-1 text-xs hover:bg-muted disabled:opacity-50"
                >
                  {refreshPending ? "Regenerating…" : "Regenerate graph"}
                </button>
              </>
            )}
          </div>
        ) : active.emptyMessage ? (
          <div className="flex h-full items-center justify-center p-6 text-sm text-muted-foreground">{active.emptyMessage}</div>
        ) : (
          <GraphCanvas
            nodes={active.nodes}
            edges={active.edges}
            selectedNodeId={selectedNodeId}
            onSelectNode={setSelectedNode}
          />
        )}
      </Panel>

      <Separator className="w-1 cursor-col-resize bg-border transition-colors hover:bg-primary/30" />

      <Panel defaultSize={25} minSize={18}>
        <NodeInspector
          node={selectedNode}
          edges={selectedEdges}
          actionError={inspectorActionError}
          onFocusEgo={handleFocusEgo}
          onOpenInNodes={handleOpenInNodes}
          onOpenInEditor={handleOpenInEditor}
          onCopyToVault={handleCopyToVault}
          onSendToLearn={selectedNodeId ? handleSendToLearn : undefined}
          onSendSliceToLearn={active.nodes.length > 0 && active.meta ? handleSendSliceToLearn : undefined}
        />
      </Panel>

      <GraphIssueLogDialog
        open={issueDialogOpen}
        report={issueDialogReport}
        mode={issueDialogMode}
        busy={refresh.isPending}
        pendingIssuePath={pendingIssuePath}
        errorMessage={issueDialogError}
        onClose={() => {
          setIssueDialogOpen(false);
          setIssueDialogError(null);
        }}
        onContinue={issueDialogMode === "review" ? handleContinueRefresh : undefined}
        onOpenIssuePath={handleOpenIssuePath}
        onToggleIgnore={handleToggleIgnoreIssue}
      />
    </Group>
  );
}
