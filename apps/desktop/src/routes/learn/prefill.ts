/**
 * Router-state prefill contract for the Learn tab.
 *
 * The Graph "Send to Learn" action navigates to `/akms/learn` with
 * router state, seeding this tab with either a node-depth selection
 * (`node`/`maxDepth`/`direction`) or an explicit `graphSlice`. The Learn tab reads
 * `useLocation().state` on mount and, if a payload tagged `learnPrefill: true` is
 * present, seeds the form fields from it.
 *
 * This is a stable shape the Graph action targets — it is intentionally tolerant (every field
 * optional) so the handoff can pass whichever source form it has.
 */

import type { GraphDirection } from "../../api/graph";
import type { LearnGraphSlice } from "../../api/learn";

/** Tagged router-state payload consumed on Learn-tab mount. */
export type LearnPrefillState = {
  /** Discriminant — must be `true` for the Learn tab to honor the payload. */
  learnPrefill: true;
  /** Source form B: node-depth selection (resolved server-side via ego_slice). */
  node?: string;
  maxDepth?: number;
  direction?: GraphDirection;
  /** Source form A: an explicit graph slice (takes precedence when present). */
  graphSlice?: LearnGraphSlice;
  /** Optional prefill for the required compile fields. */
  topic?: string;
  goal?: string;
};

/** Type guard for the tagged prefill payload (router state is `unknown`). */
export function isLearnPrefillState(state: unknown): state is LearnPrefillState {
  return (
    typeof state === "object" &&
    state !== null &&
    (state as { learnPrefill?: unknown }).learnPrefill === true
  );
}
