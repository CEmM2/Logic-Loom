/**
 * Discover page badges.
 *
 * - ZoteroStatusBadge: 'In Zotero (certain)', 'In Zotero (possible)',
 *   or 'Not in Zotero', driven by the pinned confidence thresholds
 *   (>=0.95 certain, 0.65-0.95 possible, <0.65 no match).
 * - PdfAvailabilityBadge: 'PDF available' or 'No PDF' based on pdf_url.
 */
import type { DiscoveredPaperWithMatch } from "../../../api/assistantDiscover";

export interface ZoteroStatusBadgeProps {
  match: DiscoveredPaperWithMatch["zotero_match"];
}

export function ZoteroStatusBadge({ match }: ZoteroStatusBadgeProps) {
  let label = "Not in Zotero";
  let className =
    "inline-flex items-center rounded-full border px-2 py-0.5 text-xs text-muted-foreground";
  if (match.in_zotero) {
    if (match.confidence >= 0.95) {
      label = "In Zotero (certain)";
      className =
        "inline-flex items-center rounded-full border border-green-600 bg-green-50 px-2 py-0.5 text-xs text-green-800";
    } else {
      label = "In Zotero (possible)";
      className =
        "inline-flex items-center rounded-full border border-amber-600 bg-amber-50 px-2 py-0.5 text-xs text-amber-800";
    }
  }
  return (
    <span data-testid="zotero-status-badge" className={className}>
      {label}
    </span>
  );
}

export interface PdfAvailabilityBadgeProps {
  pdfUrl: string | null | undefined;
}

export function PdfAvailabilityBadge({ pdfUrl }: PdfAvailabilityBadgeProps) {
  const has = Boolean(pdfUrl);
  return (
    <span
      data-testid="pdf-availability-badge"
      className={
        has
          ? "inline-flex items-center rounded-full border border-sky-600 bg-sky-50 px-2 py-0.5 text-xs text-sky-800"
          : "inline-flex items-center rounded-full border px-2 py-0.5 text-xs text-muted-foreground"
      }
    >
      {has ? "PDF available" : "No PDF"}
    </span>
  );
}
