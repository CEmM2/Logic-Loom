/**
 * EstablishedBadge — presentational component for node provenance tiers.
 *
 * Visually separates Tier A (Established, shipped authoritative nodes) from
 * Tier B (Draft/Generated, user-owned nodes) everywhere nodes render.
 *
 * Design mirror of StarterBadge:
 *   - Exported as a pure unit so it can be tested without the Tauri runtime,
 *     graph canvas, or API client wiring.
 *   - `isEstablished` and `nodeTier` helpers are independently testable.
 *
 * Invariant: the two tiers must NEVER be visually ambiguous.
 *   Established (Tier A) → authoritative green.
 *   Draft/Generated (Tier B) → amber, consistent with the StarterBadge treatment.
 *
 * Report-an-error affordance: present on Tier-A nodes only (established
 * content shipped by us is the only content users may want to flag to us).
 * Absent on Tier-B (user-owned draft/generated) nodes.
 */
import type { GraphNode } from "../../api/graph";
import type { NodeFile } from "../../api/nodes";

// ── Node shape accepted by the helpers ───────────────────────────────────────

/** Minimal shape the badge helpers care about — satisfied by GraphNode and NodeFile. */
export type NodeLike = Pick<GraphNode | NodeFile, "is_established" | "source_ref">;

// ── Pure helpers (testable without a DOM) ────────────────────────────────────

/**
 * Returns true when a node is Tier A (Established / shipped authoritative).
 * Undefined or false `is_established` → Tier B (Draft/Generated).
 */
export function isEstablished(node: NodeLike): boolean {
  return node.is_established === true;
}

/**
 * Human-readable tier label.
 *   Tier A → "Established"
 *   Tier B → "Draft / Generated"
 */
export function nodeTier(node: NodeLike): "Established" | "Draft / Generated" {
  return isEstablished(node) ? "Established" : "Draft / Generated";
}

/**
 * Builds a "Report an issue" URL for an established node.
 * Uses a GitHub new-issue template seeded with node id and source_ref so
 * the report lands in the right context.
 *
 * Returns null when `source_ref` is absent (shouldn't happen for Tier A, but
 * handles the gap gracefully).
 */
export function reportIssueHref(node: NodeLike & { id?: string }): string | null {
  const ref = node.source_ref;
  if (!ref) return null;
  const id = ("id" in node && typeof node.id === "string") ? node.id : undefined;
  const body = [
    `**Node:** ${id ?? "(unknown)"}`,
    `**Source ref:** ${ref}`,
    "",
    "<!-- Describe the issue with this established node below -->",
  ].join("\n");
  const params = new URLSearchParams({
    title: `[established-node] Issue with${id ? ` ${id}` : " node"}`,
    body,
    labels: "established-node,data-quality",
  });
  return `https://github.com/CEmM2/Logic-Loom/issues/new?${params.toString()}`;
}

// ── Components ────────────────────────────────────────────────────────────────

interface EstablishedBadgeProps {
  node: NodeLike & { id?: string };
}

/**
 * Renders an "Established" or "Draft / Generated" tier badge for a node.
 *
 * Always renders — the two tiers must never be silently co-mingled.
 */
export function EstablishedBadge({ node }: EstablishedBadgeProps) {
  if (isEstablished(node)) {
    return (
      <span
        data-testid="established-badge"
        title="Tier A — shipped, authoritative node verified by the Logic-Loom team."
        className="inline-flex items-center rounded-full border border-emerald-300 bg-emerald-50 px-2 py-0.5 text-[10px] font-medium text-emerald-800"
      >
        Established
      </span>
    );
  }

  return (
    <span
      data-testid="draft-badge"
      title="Tier B — draft or generated node. You own it and can edit it."
      className="inline-flex items-center rounded-full border border-amber-300 bg-amber-50 px-2 py-0.5 text-[10px] font-medium text-amber-800"
    >
      Draft / Generated
    </span>
  );
}

interface ReportIssueProps {
  node: NodeLike & { id?: string };
}

/**
 * Renders a "Report an issue" link for Established (Tier A) nodes only.
 * Absent on Draft/Generated (Tier B) nodes — only report issues in content
 * shipped by us, not user-authored content.
 */
export function ReportIssueLink({ node }: ReportIssueProps) {
  if (!isEstablished(node)) return null;

  const href = reportIssueHref(node);
  if (!href) return null;

  return (
    <a
      data-testid="report-issue-link"
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
    >
      Report an issue
    </a>
  );
}
