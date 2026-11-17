import { useEffect, useMemo, useState } from "react";

import type { GraphIssueLog, GraphSourceIssue } from "../../api/graph";

type Mode = "view" | "review";
type VisibilityFilter = "active" | "ignored" | "all";
type GroupBy = "source_kind" | "error_type";

interface Props {
  open: boolean;
  report: GraphIssueLog | null;
  mode?: Mode;
  busy?: boolean;
  pendingIssuePath?: string | null;
  errorMessage?: string | null;
  onClose: () => void;
  onContinue?: () => void;
  onOpenIssuePath: (path: string) => void;
  onToggleIgnore?: (path: string, ignored: boolean) => void | Promise<void>;
}

export function GraphIssueLogDialog({
  open,
  report,
  mode = "view",
  busy = false,
  pendingIssuePath = null,
  errorMessage,
  onClose,
  onContinue,
  onOpenIssuePath,
  onToggleIgnore,
}: Props) {
  const [query, setQuery] = useState("");
  const [sourceFilter, setSourceFilter] = useState<"all" | GraphSourceIssue["source_kind"]>("all");
  const [visibilityFilter, setVisibilityFilter] = useState<VisibilityFilter>("active");
  const [groupBy, setGroupBy] = useState<GroupBy>("source_kind");
  const [dismissedPaths, setDismissedPaths] = useState<string[]>([]);
  const [copyStatus, setCopyStatus] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !report) return;
    setDismissedPaths([]);
    setCopyStatus(null);
    setQuery("");
    setSourceFilter("all");
    setGroupBy("source_kind");
    setVisibilityFilter(report.issue_count > 0 ? "active" : report.ignored_issue_count > 0 ? "all" : "active");
  }, [open, report?.generated_at, report?.issue_count, report?.ignored_issue_count]);

  const visibleIssues = useMemo(() => {
    if (!report) return [];
    const dismissed = new Set(dismissedPaths);
    const queryNorm = query.trim().toLowerCase();

    return report.issues
      .filter((issue) => !dismissed.has(issue.path))
      .filter((issue) => {
        if (visibilityFilter === "active") return !issue.ignored;
        if (visibilityFilter === "ignored") return issue.ignored;
        return true;
      })
      .filter((issue) => sourceFilter === "all" || issue.source_kind === sourceFilter)
      .filter((issue) => {
        if (!queryNorm) return true;
        const haystack = [issue.path, issue.relative_path, issue.reason, issue.error_type, issue.source_kind]
          .join(" ")
          .toLowerCase();
        return haystack.includes(queryNorm);
      })
      .slice()
      .sort((a, b) => a.path.localeCompare(b.path));
  }, [dismissedPaths, query, report, sourceFilter, visibilityFilter]);

  const groupedIssues = useMemo(() => {
    const groups = new Map<string, GraphSourceIssue[]>();
    for (const issue of visibleIssues) {
      const key = groupBy === "source_kind" ? formatSourceKind(issue.source_kind) : issue.error_type;
      const bucket = groups.get(key) ?? [];
      bucket.push(issue);
      groups.set(key, bucket);
    }
    return Array.from(groups.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  }, [groupBy, visibleIssues]);

  if (!open || !report) return null;

  const canContinue = mode === "review" && report.total_issue_count > 0 && !!onContinue;

  const handleDismiss = (path: string) => {
    setDismissedPaths((current) => (current.includes(path) ? current : [...current, path]));
  };

  const handleResetDismissed = () => {
    setDismissedPaths([]);
  };

  const handleCopyVisibleIssues = async () => {
    try {
      const text = formatIssueList(report, visibleIssues);
      await copyText(text);
      setCopyStatus(`Copied ${visibleIssues.length} issue${visibleIssues.length === 1 ? "" : "s"} to the clipboard.`);
      window.setTimeout(() => setCopyStatus(null), 2500);
    } catch (error) {
      setCopyStatus(error instanceof Error ? error.message : String(error));
      window.setTimeout(() => setCopyStatus(null), 2500);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
      <div className="flex max-h-[85vh] w-full max-w-6xl flex-col overflow-hidden rounded border border-border bg-background shadow-xl">
        <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
          <div>
            <h2 className="text-lg font-semibold">
              {mode === "review" ? "Review skipped graph files" : "Graph issue log"}
            </h2>
            <p className="mt-1 text-sm text-muted-foreground">
              {report.issue_count > 0
                ? `The latest graph scan found ${report.issue_count} actionable markdown issue${report.issue_count === 1 ? "" : "s"}.`
                : report.ignored_issue_count > 0
                  ? "All current issues are ignored, so generation can proceed while those files stay skipped."
                  : "The latest graph scan found no problematic markdown files."}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={handleCopyVisibleIssues}
              className="rounded border border-border px-3 py-1.5 text-xs hover:bg-muted"
            >
              Copy issue list
            </button>
            <button
              type="button"
              onClick={onClose}
              className="rounded border border-border px-3 py-1.5 text-xs hover:bg-muted"
            >
              Close
            </button>
          </div>
        </div>

        {report.issue_count > 0 && (
          <div
            data-testid="graph-issue-how-to-fix"
            className="border-b border-border px-5 py-3 text-xs text-muted-foreground"
          >
            <span className="font-medium text-foreground">How to fix:</span> a graph
            source file must start with valid AKMS node frontmatter, and the reason under
            each file says what is wrong. Open a file to add or correct it. If a file is not meant
            to be a node, such as a README or a scratch note, choose{" "}
            <strong>Ignore in future</strong> so later scans skip it.
          </div>
        )}
        <div className="grid gap-3 border-b border-border bg-muted/20 px-5 py-3 text-xs text-muted-foreground md:grid-cols-5">
          <Info label="Scanned" value={`${report.total_markdown_files} markdown file${report.total_markdown_files === 1 ? "" : "s"}`} />
          <Info label="Valid" value={`${report.valid_markdown_files}`} />
          <Info label="Actionable" value={`${report.issue_count}`} />
          <Info label="Ignored" value={`${report.ignored_issue_count}`} />
          <Info label="Generated" value={report.generated_at ?? "Not yet generated"} mono />
          <div className="md:col-span-5">
            <span className="font-medium text-foreground">Log file:</span>{" "}
            <span className="font-mono text-[11px]">{report.log_path}</span>
          </div>
        </div>

        <div className="grid gap-3 border-b border-border px-5 py-3 text-xs md:grid-cols-[minmax(0,2fr)_repeat(3,minmax(0,1fr))]">
          <label className="flex flex-col gap-1">
            <span className="font-medium text-muted-foreground">Search</span>
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search paths, reasons, or error types…"
              className="rounded border border-border bg-background px-2 py-1 text-xs"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="font-medium text-muted-foreground">Source</span>
            <select
              value={sourceFilter}
              onChange={(event) => setSourceFilter(event.target.value as "all" | GraphSourceIssue["source_kind"])}
              className="rounded border border-border bg-background px-2 py-1 text-xs"
            >
              <option value="all">All sources</option>
              <option value="global-vault">Global vault</option>
              <option value="local-nodes">Local nodes</option>
              <option value="code-mirror">Code mirror</option>
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="font-medium text-muted-foreground">Visibility</span>
            <select
              value={visibilityFilter}
              onChange={(event) => setVisibilityFilter(event.target.value as VisibilityFilter)}
              className="rounded border border-border bg-background px-2 py-1 text-xs"
            >
              <option value="active">Actionable only</option>
              <option value="ignored">Ignored only</option>
              <option value="all">All listed issues</option>
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="font-medium text-muted-foreground">Group by</span>
            <select
              value={groupBy}
              onChange={(event) => setGroupBy(event.target.value as GroupBy)}
              className="rounded border border-border bg-background px-2 py-1 text-xs"
            >
              <option value="source_kind">Source kind</option>
              <option value="error_type">Error type</option>
            </select>
          </label>
        </div>

        <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-2 text-xs text-muted-foreground">
          <div>
            Showing {visibleIssues.length} of {report.total_issue_count} listed issue{report.total_issue_count === 1 ? "" : "s"}
            {dismissedPaths.length > 0 ? ` · ${dismissedPaths.length} dismissed for now` : ""}
          </div>
          {dismissedPaths.length > 0 && (
            <button
              type="button"
              onClick={handleResetDismissed}
              className="rounded border border-border px-2 py-1 hover:bg-muted"
            >
              Reset dismissed
            </button>
          )}
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          {report.total_issue_count === 0 ? (
            <div className="rounded border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
              No files need to be skipped right now. The graph builder’s social calendar is blissfully empty.
            </div>
          ) : visibleIssues.length === 0 ? (
            <div className="rounded border border-border bg-muted/20 px-4 py-3 text-sm text-muted-foreground">
              No issues match the current filters. Try changing the search, visibility, or source filters.
            </div>
          ) : (
            <div className="space-y-5">
              {groupedIssues.map(([groupLabel, issues]) => (
                <section key={groupLabel} className="space-y-3">
                  <div className="sticky top-0 z-10 rounded border border-border bg-background/95 px-3 py-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground backdrop-blur">
                    {groupLabel} · {issues.length}
                  </div>
                  <div className="space-y-3">
                    {issues.map((issue) => {
                      const issueBusy = pendingIssuePath === issue.path;
                      return (
                        <div key={`${issue.path}:${issue.error_type}`} className="rounded border border-border bg-background p-4">
                          <div className="flex flex-wrap items-start justify-between gap-3">
                            <div className="min-w-0 flex-1">
                              <div className="flex flex-wrap items-center gap-2 text-xs">
                                <span className="rounded bg-muted px-2 py-0.5 font-medium text-foreground">
                                  {formatSourceKind(issue.source_kind)}
                                </span>
                                <span className="rounded border border-border px-2 py-0.5 font-mono text-[11px] text-muted-foreground">
                                  {issue.error_type}
                                </span>
                                {issue.ignored && (
                                  <span className="rounded border border-emerald-300 bg-emerald-50 px-2 py-0.5 font-medium text-emerald-700">
                                    Ignored
                                  </span>
                                )}
                              </div>
                              <div className="mt-2 break-all font-mono text-xs text-foreground">{issue.path}</div>
                              <div className="mt-1 font-mono text-[11px] text-muted-foreground">{issue.relative_path}</div>
                            </div>
                            <div className="flex flex-wrap gap-2">
                              <button
                                type="button"
                                onClick={() => onOpenIssuePath(issue.path)}
                                className="rounded border border-border px-3 py-1 text-xs hover:bg-muted"
                                disabled={busy}
                              >
                                Open in Editor
                              </button>
                              {onToggleIgnore && (
                                <button
                                  type="button"
                                  onClick={() => void onToggleIgnore(issue.path, !issue.ignored)}
                                  disabled={busy || issueBusy}
                                  className="rounded border border-border px-3 py-1 text-xs hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
                                >
                                  {issueBusy
                                    ? issue.ignored ? "Restoring…" : "Ignoring…"
                                    : issue.ignored ? "Stop ignoring" : "Ignore in future"}
                                </button>
                              )}
                              <button
                                type="button"
                                onClick={() => handleDismiss(issue.path)}
                                className="rounded border border-border px-3 py-1 text-xs hover:bg-muted"
                                disabled={busy || issueBusy}
                              >
                                Dismiss for now
                              </button>
                            </div>
                          </div>
                          <div className="mt-3 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs whitespace-pre-wrap text-amber-800">
                            {issue.reason}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </section>
              ))}
            </div>
          )}
        </div>

        {(errorMessage || copyStatus) && (
          <div className="border-t border-border px-5 py-3 text-xs">
            {errorMessage && (
              <div className="rounded border border-red-200 bg-red-50 px-3 py-2 text-red-700">
                {errorMessage}
              </div>
            )}
            {copyStatus && (
              <div className={`${errorMessage ? "mt-2 " : ""}rounded border px-3 py-2 ${copyStatus.startsWith("Copied") ? "border-emerald-200 bg-emerald-50 text-emerald-700" : "border-amber-200 bg-amber-50 text-amber-800"}`}>
                {copyStatus}
              </div>
            )}
          </div>
        )}

        <div className="flex items-center justify-between gap-3 border-t border-border px-5 py-4">
          <p className="text-xs text-muted-foreground">
            {canContinue
              ? report.issue_count > 0
                ? "Fix the files first, ignore the ones you want to suppress, or continue and build the graph without the listed files for this run."
                : "All remaining problematic files are ignored. Continue to generate the graph with those files skipped."
              : "Review the latest scan log, then close this dialog when you’re done."}
          </p>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded border border-border px-3 py-1.5 text-sm hover:bg-muted"
              disabled={busy}
            >
              {canContinue ? "Fix files first" : "Close"}
            </button>
            {canContinue && (
              <button
                type="button"
                onClick={onContinue}
                disabled={busy}
                className="rounded bg-foreground px-3 py-1.5 text-sm text-background hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {busy
                  ? "Generating…"
                  : `Generate without ${report.total_issue_count} skipped file${report.total_issue_count === 1 ? "" : "s"}`}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function Info({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div>
      <div className="font-medium uppercase tracking-wide">{label}</div>
      <div className={mono ? "mt-1 font-mono text-[11px] text-foreground" : "mt-1 text-foreground"}>{value}</div>
    </div>
  );
}

function formatSourceKind(sourceKind: string) {
  switch (sourceKind) {
    case "global-vault":
      return "Global vault";
    case "local-nodes":
      return "Local nodes";
    case "code-mirror":
      return "Code mirror";
    default:
      return sourceKind;
  }
}

function formatIssueList(report: GraphIssueLog, issues: GraphSourceIssue[]) {
  const lines = [
    `Graph issue log — generated ${report.generated_at ?? "unknown time"}`,
    `Actionable: ${report.issue_count} | Ignored: ${report.ignored_issue_count} | Total listed: ${report.total_issue_count}`,
    `Showing ${issues.length} issue${issues.length === 1 ? "" : "s"}`,
    `Log file: ${report.log_path}`,
    "",
  ];

  for (const issue of issues) {
    lines.push(`[${issue.ignored ? "ignored" : "active"}] ${formatSourceKind(issue.source_kind)} · ${issue.error_type}`);
    lines.push(issue.path);
    lines.push(issue.reason);
    lines.push("");
  }

  return lines.join("\n");
}

async function copyText(text: string) {
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }

  if (typeof document === "undefined") {
    throw new Error("Clipboard access is unavailable in this environment.");
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "true");
  textarea.style.position = "absolute";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  textarea.select();
  const ok = document.execCommand("copy");
  document.body.removeChild(textarea);
  if (!ok) {
    throw new Error("Unable to copy the issue list to the clipboard.");
  }
}
