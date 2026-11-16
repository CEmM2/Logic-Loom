/**
 * Discover page results table.
 *
 * Per-row checkbox for multi-select; "Add to batch" button per row.
 * Bulk-add and create-batch-from-results live in the parent toolbar
 * (so the table component stays purely presentational).
 */
import type { DiscoveredPaperWithMatch } from "../../../api/assistantDiscover";
import { ZoteroStatusBadge, PdfAvailabilityBadge } from "./Badges";

export interface DiscoveryResultsTableProps {
  results: DiscoveredPaperWithMatch[];
  selectedIds: Set<string>;
  onToggleSelected: (id: string) => void;
  onAddOne: (paper: DiscoveredPaperWithMatch) => void;
}

export function DiscoveryResultsTable({
  results,
  selectedIds,
  onToggleSelected,
  onAddOne,
}: DiscoveryResultsTableProps) {
  if (results.length === 0) {
    return (
      <div
        data-testid="discovery-results-empty"
        className="rounded-md border bg-card p-6 text-sm text-muted-foreground"
      >
        No results yet — try a search.
      </div>
    );
  }
  return (
    <table
      data-testid="discovery-results-table"
      className="w-full border-collapse text-left text-sm"
    >
      <thead>
        <tr className="border-b">
          <th className="px-2 py-1"></th>
          <th className="px-2 py-1">Title</th>
          <th className="px-2 py-1">Year</th>
          <th className="px-2 py-1">Source</th>
          <th className="px-2 py-1">Status</th>
          <th className="px-2 py-1"></th>
        </tr>
      </thead>
      <tbody>
        {results.map((paper) => (
          <tr
            key={paper.id}
            data-testid={`discovery-row-${paper.id}`}
            className="border-b"
          >
            <td className="px-2 py-1">
              <input
                type="checkbox"
                aria-label={`Select ${paper.title}`}
                checked={selectedIds.has(paper.id)}
                onChange={() => onToggleSelected(paper.id)}
              />
            </td>
            <td className="px-2 py-1">
              <div className="font-medium">{paper.title}</div>
              <div className="text-xs text-muted-foreground">
                {(paper.authors ?? []).slice(0, 3).join(", ")}
                {(paper.authors ?? []).length > 3 ? " et al." : ""}
              </div>
            </td>
            <td className="px-2 py-1">{paper.year ?? "—"}</td>
            <td className="px-2 py-1">{paper.source}</td>
            <td className="px-2 py-1">
              <div className="flex flex-wrap gap-1">
                <ZoteroStatusBadge match={paper.zotero_match} />
                <PdfAvailabilityBadge pdfUrl={paper.pdf_url} />
              </div>
            </td>
            <td className="px-2 py-1">
              <button
                type="button"
                className="rounded border px-2 py-0.5 text-xs"
                onClick={() => onAddOne(paper)}
              >
                Add to batch
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
