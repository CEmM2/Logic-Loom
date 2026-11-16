/**
 * StarterBadge — presentational component for starter-pack batches.
 *
 * Marks a batch that originated from the bundled starter pack (origin ===
 * "starter"). A starter batch run produces DRAFT (Tier B) content that the
 * user owns and can edit — not Established content verified by us.
 *
 * Exported as a pure unit so it can be tested without the Tauri runtime or
 * the React Query API client wiring that BatchesPage needs.
 */
import type { BatchOrigin } from "../../api/batches";

// ── Pure helpers (testable without a DOM) ────────────────────────────────────

/** Returns true when a batch should display the starter affordance. */
export function isStarterBatch(origin: BatchOrigin | undefined): boolean {
  return origin === "starter";
}

/**
 * The short Tier-B ownership copy shown alongside the starter badge.
 * Communicates that running a starter batch generates the user's own draft
 * nodes — unverified-by-us, editable, the user is accountable.
 */
export function starterRunCopy(): string {
  return "Running this batch generates your own draft nodes — unverified, editable, yours to keep.";
}

// ── Component ────────────────────────────────────────────────────────────────

interface StarterBadgeProps {
  origin?: BatchOrigin;
}

/**
 * Renders a "Starter" badge with Tier-B ownership copy when origin ===
 * "starter". Renders nothing for user-authored batches (or batches with no
 * origin field — treated as "user" per the Two-Tier Trust Model invariant).
 */
export function StarterBadge({ origin }: StarterBadgeProps) {
  if (!isStarterBatch(origin)) return null;

  return (
    <span
      data-testid="starter-badge"
      title={starterRunCopy()}
      className="inline-flex items-center rounded-full border border-amber-300 bg-amber-50 px-2 py-0.5 text-[10px] font-medium text-amber-800"
    >
      Starter
    </span>
  );
}
