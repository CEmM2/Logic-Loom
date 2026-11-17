import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Group, Panel, Separator } from "react-resizable-panels";
import { useNavigate, useSearchParams } from "react-router-dom";

import { ApiError } from "../../api/client";
import { IndeterminateProgressBar } from "../../components/IndeterminateProgressBar";
import { GraphReviewRequiredError, getGraphNodes, type GraphIssueLog } from "../../api/graph";
import { copyToVault, listNodes, openInEditor, readNode, type NodeFile } from "../../api/nodes";
import { GraphIssueLogDialog } from "../../components/graph/GraphIssueLogDialog";
import { NodeMarkdownPreview } from "../../components/NodeMarkdownPreview";
import { EstablishedBadge, ReportIssueLink, isEstablished } from "../../components/nodes/EstablishedBadge";
import { PromoteDialog } from "../../components/nodes/PromoteDialog";
import { useGraphIssueLog, useGraphRefreshStream, useScanGraphIssueLog, useSetGraphIssueIgnored } from "../../hooks/graph";

// Strip YAML frontmatter block before passing to the markdown renderer.
function stripFrontmatter(content: string): string {
  const m = content.match(/^---\s*\n[\s\S]*?\n---\s*\n/);
  return m ? content.slice(m[0].length) : content;
}

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

function StatusBadge({ status }: { status: string }) {
  const cls =
    status === "established"
      ? "text-emerald-600"
      : status === "tentative"
        ? "text-amber-600"
        : status === "deprecated"
          ? "text-red-500"
          : "text-muted-foreground";
  return <span className={cls}>{status}</span>;
}

function NodeList({
  nodes,
  selected,
  onSelect,
}: {
  nodes: NodeFile[];
  selected: string | null;
  onSelect: (path: string) => void;
}) {
  const [filter, setFilter] = useState("");

  const filtered = nodes.filter(
    (n) =>
      !filter ||
      n.title.toLowerCase().includes(filter.toLowerCase()) ||
      n.name.toLowerCase().includes(filter.toLowerCase()) ||
      n.domain.toLowerCase().includes(filter.toLowerCase()),
  );

  // Group by top-level folder label (last path segment of folder)
  const byFolder = new Map<string, NodeFile[]>();
  for (const n of filtered) {
    const arr = byFolder.get(n.folder) ?? [];
    arr.push(n);
    byFolder.set(n.folder, arr);
  }

  return (
    <div className="flex h-full flex-col border-r border-border">
      <div className="border-b border-border p-2">
        <input
          type="search"
          placeholder="Filter nodes…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="w-full rounded border border-border bg-background px-2 py-1 text-xs"
        />
      </div>
      <div className="flex-1 overflow-y-auto">
        {nodes.length === 0 ? (
          <div className="p-4 text-xs text-muted-foreground">
            No nodes found. Add node folders in Settings → Nodes Review.
          </div>
        ) : filtered.length === 0 ? (
          <div className="p-4 text-xs text-muted-foreground">No matches.</div>
        ) : (
          Array.from(byFolder.entries()).map(([folder, nodeList]) => (
            <div key={folder}>
              <div
                className="border-b border-t border-border bg-muted/40 px-2 py-1 font-mono text-[10px] font-semibold uppercase tracking-wider text-muted-foreground"
                title={folder}
              >
                {folder.split("/").pop() ?? folder}
              </div>
              {nodeList.map((n) => (
                <button
                  key={n.path}
                  type="button"
                  onClick={() => onSelect(n.path)}
                  className={`w-full border-b border-border/40 px-3 py-2 text-left hover:bg-muted/50 ${
                    selected === n.path ? "bg-muted font-medium" : ""
                  }`}
                >
                  <div className="flex items-center justify-between gap-1">
                    <span className="truncate text-sm">{n.title || n.name}</span>
                    <EstablishedBadge node={n} />
                  </div>
                  <div className="flex gap-2 pt-0.5 text-[10px] text-muted-foreground">
                    {n.domain && <span className="truncate">{n.domain}</span>}
                    {n.status && <StatusBadge status={n.status} />}
                  </div>
                </button>
              ))}
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function NodeFrontmatterBar({ fm }: { fm: Record<string, string> }) {
  const fields = [
    { key: "id", label: "ID" },
    { key: "domain", label: "Domain" },
    { key: "subdomain", label: "Subdomain" },
    { key: "status", label: "Status" },
    { key: "confidence", label: "Confidence" },
    { key: "source", label: "Source" },
  ].filter(({ key }) => fm[key]);

  if (fields.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-x-5 gap-y-1 border-b border-border bg-muted/20 px-4 py-2 text-[11px]">
      {fields.map(({ key, label }) => (
        <span key={key} className="text-muted-foreground">
          <span className="font-medium text-foreground">{label}: </span>
          {key === "status" ? <StatusBadge status={fm[key]} /> : fm[key]}
        </span>
      ))}
    </div>
  );
}

export default function NodesRoute() {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const [selectedPath, setSelectedPath] = useState<string | null>(searchParams.get("path"));
  const [promoteOpen, setPromoteOpen] = useState(false);
  const [graphActionError, setGraphActionError] = useState<string | null>(null);
  const [copyToVaultStatus, setCopyToVaultStatus] = useState<string | null>(null);
  const [issueDialogOpen, setIssueDialogOpen] = useState(false);
  const [issueDialogReport, setIssueDialogReport] = useState<GraphIssueLog | null>(null);
  const [issueDialogError, setIssueDialogError] = useState<string | null>(null);
  const [pendingIssuePath, setPendingIssuePath] = useState<string | null>(null);
  const refreshGraph = useGraphRefreshStream();
  const issueLogQuery = useGraphIssueLog();
  const scanIssueLog = useScanGraphIssueLog();
  const toggleIgnoredIssue = useSetGraphIssueIgnored();

  useEffect(() => {
    const path = searchParams.get("path");
    setSelectedPath(path);
  }, [searchParams]);

  const {
    data: nodes = [],
    isLoading,
    error,
  } = useQuery({
    queryKey: ["nodes", "files"],
    queryFn: listNodes,
  });

  const { data: nodeContent, isLoading: contentLoading } = useQuery({
    queryKey: ["nodes", "file", selectedPath],
    queryFn: () => readNode(selectedPath!),
    enabled: !!selectedPath,
  });

  const handleOpen = () => {
    if (selectedPath) openInEditor(selectedPath).catch(() => {});
  };

  const handleCopyToVault = async () => {
    if (!selectedPath) return;
    setCopyToVaultStatus(null);
    try {
      const result = await copyToVault(selectedPath);
      setCopyToVaultStatus(`Copied to vault: ${result.copy_path}`);
    } catch (err) {
      setCopyToVaultStatus(err instanceof Error ? err.message : "Copy to vault failed.");
    }
  };

  const handleSelect = (path: string) => {
    setSelectedPath(path);
    const next = new URLSearchParams(searchParams);
    next.set("path", path);
    setSearchParams(next, { replace: true });
  };

  const selectedNode = nodes.find((node) => node.path === selectedPath) ?? null;
  // Only a file whose frontmatter declares an `id` is a graph node. The node
  // list falls back to the file name (e.g. "README") for files without one,
  // and sending that to the graph view only produced "Unknown node".
  const frontmatterId = nodeContent?.frontmatter.id;
  const graphNodeId =
    typeof frontmatterId === "string" && frontmatterId.trim() ? frontmatterId : null;
  // Is the id in the compiled graph? A node can be missing when its file is
  // outside the graph source vault or the graph predates it. While the graph
  // is not compiled (or the check fails) the buttons stay enabled and the
  // graph view explains what to do.
  const graphMembership = useQuery({
    queryKey: ["graph", "nodes", "membership", graphNodeId],
    queryFn: () => getGraphNodes({ query: graphNodeId ?? "" }),
    enabled: Boolean(graphNodeId),
    retry: false,
    staleTime: 30_000,
  });
  const notInGraph =
    graphMembership.isSuccess && !graphMembership.data.some((n) => n.id === graphNodeId);
  const selectedNodeEstablished = selectedNode ? isEstablished(selectedNode) : false;

  const handleOpenGraph = () => {
    if (!graphNodeId) return;
    setGraphActionError(null);
    navigate(`/akms/graph?mode=ego&node=${encodeURIComponent(graphNodeId)}`);
  };

  const handleGenerateGraph = async () => {
    if (!graphNodeId) return;
    setGraphActionError(null);
    setIssueDialogError(null);
    try {
      const report = await scanIssueLog.mutateAsync();
      setIssueDialogReport(report);
      void issueLogQuery.refetch();
      if (report.issue_count > 0) {
        setIssueDialogOpen(true);
        return;
      }
      await refreshGraph.start();
      navigate(`/akms/graph?mode=ego&node=${encodeURIComponent(graphNodeId)}`);
    } catch (error) {
      if (error instanceof GraphReviewRequiredError) {
        setIssueDialogReport(error.report);
        setIssueDialogOpen(true);
        return;
      }
      setGraphActionError(formatApiError(error));
    }
  };

  const handleContinueGraphGeneration = async () => {
    if (!graphNodeId) return;
    setGraphActionError(null);
    setIssueDialogError(null);
    try {
      await refreshGraph.start({ skipInvalidFiles: true });
      await issueLogQuery.refetch();
      setIssueDialogOpen(false);
      navigate(`/akms/graph?mode=ego&node=${encodeURIComponent(graphNodeId)}`);
    } catch (error) {
      if (error instanceof GraphReviewRequiredError) {
        setIssueDialogReport(error.report);
        setIssueDialogOpen(true);
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

  const graphRefreshPending = scanIssueLog.isPending || refreshGraph.isPending;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {isLoading && (
        <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
          Loading nodes…
        </div>
      )}
      {error && (
        <div className="flex h-full items-center justify-center text-sm text-red-600">
          Failed to load nodes. Check that nodes folders are configured in Settings.
        </div>
      )}
      {!isLoading && !error && (
        <Group orientation="horizontal" className="flex-1">
          {/* ── Left: node list ──────────────────────────────── */}
          <Panel defaultSize={28} minSize={18}>
            <NodeList nodes={nodes} selected={selectedPath} onSelect={handleSelect} />
          </Panel>

          <Separator className="w-px bg-border transition-colors hover:bg-primary/30" />

          {/* ── Right: content viewer ────────────────────────── */}
          <Panel defaultSize={72} minSize={40}>
            {!selectedPath ? (
              <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                Select a node to review
              </div>
            ) : contentLoading ? (
              <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                Loading…
              </div>
            ) : nodeContent ? (
              <div className="flex h-full flex-col">
                {/* Action bar */}
                <div className="flex items-center justify-between border-b border-border px-4 py-2">
                  <div className="flex items-center gap-2">
                    <h2 className="text-sm font-semibold">
                      {nodeContent.frontmatter.title ?? nodeContent.frontmatter.id ?? nodeContent.path.split("/").pop()}
                    </h2>
                    {selectedNode && <EstablishedBadge node={selectedNode} />}
                  </div>
                  <div className="flex items-center gap-2">
                    {selectedNode && <ReportIssueLink node={selectedNode} />}
                    <button
                      type="button"
                      onClick={handleGenerateGraph}
                      disabled={!graphNodeId || graphRefreshPending}
                      className="rounded border border-border px-3 py-1 text-xs hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
                      title="Compile the latest AKMS graph and open this node in the graph view"
                    >
                      {scanIssueLog.isPending ? "Scanning…" : refreshGraph.isPending ? "Generating…" : "Generate & View Graph"}
                    </button>
                    <button
                      type="button"
                      onClick={handleOpenGraph}
                      disabled={!graphNodeId || notInGraph}
                      title={
                        !graphNodeId
                          ? "This file has no frontmatter id, so it is not a graph node."
                          : notInGraph
                            ? "Not in the compiled graph yet. Use Generate & View Graph to rebuild it."
                            : undefined
                      }
                      className="rounded border border-border px-3 py-1 text-xs hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      View in Graph
                    </button>
                    {selectedNodeEstablished ? (
                      /* Established (Tier A) — read-only: offer copy instead of edit. */
                      <button
                        type="button"
                        onClick={handleCopyToVault}
                        title="Clone this established node into your vault as an editable draft."
                        className="rounded border border-border px-3 py-1 text-xs hover:bg-muted"
                      >
                        Copy to vault
                      </button>
                    ) : (
                      /* Draft/Generated (Tier B) — user owns it, offer direct edit. */
                      <>
                        <button
                          type="button"
                          onClick={handleOpen}
                          className="rounded border border-border px-3 py-1 text-xs hover:bg-muted"
                        >
                          Open in Editor
                        </button>
                        <button
                          type="button"
                          onClick={() => setPromoteOpen(true)}
                          className="rounded bg-indigo-600 px-3 py-1 text-xs text-white hover:bg-indigo-700"
                        >
                          Promote…
                        </button>
                      </>
                    )}
                  </div>
                </div>

                {nodeContent && !graphNodeId && (
                  <div className="border-b border-border bg-muted/20 px-4 py-2 text-xs text-muted-foreground">
                    This file has no <code>id</code> in its frontmatter, so it is not a node
                    in the graph.
                  </div>
                )}
                {notInGraph && (
                  <div className="border-b border-border bg-muted/20 px-4 py-2 text-xs text-muted-foreground">
                    <code>{graphNodeId}</code> is not in the compiled graph. Generate the
                    graph to include it; if it is still missing, its file is outside the
                    graph source vault (Settings ▸ AKMS global vault).
                  </div>
                )}
                {graphActionError && (
                  <div className="border-b border-red-200 bg-red-50 px-4 py-2 text-xs text-red-700">
                    {graphActionError}
                  </div>
                )}

                {copyToVaultStatus && (
                  <div className="border-b border-border bg-muted/20 px-4 py-2 text-xs text-muted-foreground">
                    {copyToVaultStatus}
                  </div>
                )}

                {graphRefreshPending && (
                  <div className="border-b border-border px-4 py-2">
                    <IndeterminateProgressBar
                      label={scanIssueLog.isPending
                        ? "Scanning graph source files for issues before generation…"
                        : refreshGraph.progress?.message ?? "Generating the AKMS graph before opening graph view…"}
                      progressPercent={scanIssueLog.isPending ? null : refreshGraph.progress?.percent ?? null}
                    />
                  </div>
                )}

                {/* Frontmatter metadata strip */}
                <NodeFrontmatterBar fm={nodeContent.frontmatter} />

                {/* Rendered content */}
                <div className="flex-1 overflow-y-auto px-6 py-4">
                  <NodeMarkdownPreview content={stripFrontmatter(nodeContent.content)} />
                </div>
              </div>
            ) : null}
          </Panel>
        </Group>
      )}

      {promoteOpen && selectedPath && (
        <PromoteDialog filePath={selectedPath} onClose={() => setPromoteOpen(false)} />
      )}

      <GraphIssueLogDialog
        open={issueDialogOpen}
        report={issueDialogReport}
        mode="review"
        busy={refreshGraph.isPending}
        pendingIssuePath={pendingIssuePath}
        errorMessage={issueDialogError}
        onClose={() => {
          setIssueDialogOpen(false);
          setIssueDialogError(null);
        }}
        onContinue={handleContinueGraphGeneration}
        onOpenIssuePath={handleOpenIssuePath}
        onToggleIgnore={handleToggleIgnoreIssue}
      />
    </div>
  );
}
