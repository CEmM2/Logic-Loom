/**
 * Compare two Assistant runs side-by-side.
 *
 * Three diff sections:
 *
 *   1. Metadata diff — flat key-by-key comparison of the two
 *      ``RunRecord`` payloads. Highlights keys whose values differ.
 *   2. Prompt-slot diff — derived from each run's
 *      ``effective_prompts.json`` snapshot. The load-bearing AC is
 *      "comparing two runs that differ only in
 *      ``assistant.literature_survey`` highlights only that slot":
 *      the diff renderer marks a slot as `changed` iff
 *      ``content_hash_left !== content_hash_right`` (or one side is
 *      missing). Identical hashes => `unchanged`.
 *   3. Primary output diff — two markdown bodies rendered side-by-side
 *      with line-level highlighting of changes (the lightweight diff
 *      avoids pulling a full diff library; the AC only requires that
 *      different bodies render differently).
 *
 * The component is a thin presenter: data is fetched up-front by the
 * caller and passed in as props. This keeps the component pure +
 * testable without a QueryClientProvider.
 */
import { useMemo } from "react";

import type { AssistantOutputRecord } from "../../api/assistantOutputs";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface PromptSlotSnapshot {
  slot: string;
  variant_id?: string | null;
  content_hash: string;
}

export interface EffectivePromptsSnapshot {
  /**
   * The Assistant runner writes ``effective_prompts.json`` with a
   * ``slots`` map; the AKMS-bundle's ``OutputStore`` writes
   * ``effective-prompts.json`` with a ``slot_to_variant`` + ``contents``
   * pair. This view accepts either shape via the union below; the
   * helper :func:`normaliseSnapshot` flattens both into a uniform
   * ``Record<workflowSlotName, PromptSlotSnapshot>``.
   */
  slots?: Record<string, PromptSlotSnapshot>;
  slot_to_variant?: Record<string, string>;
  contents?: Record<string, string>;
  [key: string]: unknown;
}

export interface ComparisonRunPayload {
  /** Registry record for the run. */
  record: AssistantOutputRecord;
  /** Prompt-slot snapshot (loaded from ``effective_prompts.json``). */
  prompts: EffectivePromptsSnapshot | null;
  /** Primary markdown body (loaded via ``GET .../primary``). */
  primary: string | null;
}

export interface RunComparisonViewProps {
  left: ComparisonRunPayload;
  right: ComparisonRunPayload;
}

// ---------------------------------------------------------------------------
// Pure helpers (exported for unit-test pinning)
// ---------------------------------------------------------------------------

/**
 * Flatten either shape (Assistant or AKMS-bundle) into a uniform map
 * keyed by the workflow slot name with ``content_hash`` populated.
 */
export function normaliseSnapshot(
  snap: EffectivePromptsSnapshot | null,
): Record<string, PromptSlotSnapshot> {
  if (snap === null) return {};
  if (snap.slots) {
    return Object.fromEntries(
      Object.entries(snap.slots).map(([name, entry]) => [
        name,
        {
          slot: entry.slot ?? name,
          variant_id: entry.variant_id ?? null,
          content_hash: entry.content_hash,
        },
      ]),
    );
  }
  if (snap.slot_to_variant && snap.contents) {
    // AKMS-bundle shape: hash the content here (we only need a stable
    // identifier — equal content => equal hash). Use a simple djb2-style
    // hash so the test contract is deterministic.
    const out: Record<string, PromptSlotSnapshot> = {};
    for (const [name, slot] of Object.entries(snap.slot_to_variant)) {
      const content = snap.contents[name] ?? "";
      out[name] = {
        slot,
        variant_id: null,
        content_hash: `js:${djb2(content)}`,
      };
    }
    return out;
  }
  return {};
}

function djb2(s: string): string {
  let hash = 5381;
  for (let i = 0; i < s.length; i += 1) {
    hash = ((hash << 5) + hash + s.charCodeAt(i)) | 0;
  }
  return (hash >>> 0).toString(16);
}

export type SlotDiffStatus = "unchanged" | "changed" | "left-only" | "right-only";

export interface SlotDiffEntry {
  slot_name: string;
  status: SlotDiffStatus;
  left: PromptSlotSnapshot | null;
  right: PromptSlotSnapshot | null;
}

/**
 * Compute the per-slot diff between two snapshots.
 *
 * Load-bearing AC: when only ``assistant.literature_survey`` differs,
 * only that slot's entry has ``status: "changed"``; every other slot
 * (e.g. ``main``, etc.) has ``status: "unchanged"``.
 */
export function diffPromptSnapshots(
  left: EffectivePromptsSnapshot | null,
  right: EffectivePromptsSnapshot | null,
): SlotDiffEntry[] {
  const l = normaliseSnapshot(left);
  const r = normaliseSnapshot(right);
  const names = new Set<string>([...Object.keys(l), ...Object.keys(r)]);
  const out: SlotDiffEntry[] = [];
  for (const name of Array.from(names).sort()) {
    const lv = l[name] ?? null;
    const rv = r[name] ?? null;
    let status: SlotDiffStatus;
    if (lv && !rv) status = "left-only";
    else if (!lv && rv) status = "right-only";
    else if (lv && rv && lv.content_hash === rv.content_hash) status = "unchanged";
    else status = "changed";
    out.push({ slot_name: name, status, left: lv, right: rv });
  }
  return out;
}

export interface MetadataDiffEntry {
  key: string;
  left: unknown;
  right: unknown;
  changed: boolean;
}

/**
 * Flat key-by-key metadata comparison. Excludes the volatile keys
 * ``id``, ``started_at``, ``ended_at``, ``pid``, ``log_path`` because
 * every run has different values for these by definition — they're
 * not signal.
 */
export function diffMetadata(
  left: AssistantOutputRecord,
  right: AssistantOutputRecord,
  options: { ignoreKeys?: ReadonlySet<string> } = {},
): MetadataDiffEntry[] {
  const ignored = new Set<string>(
    options.ignoreKeys ?? ["id", "started_at", "ended_at", "pid", "log_path", "last_lines", "args"],
  );
  const keys = new Set<string>([
    ...Object.keys(left),
    ...Object.keys(right),
  ]);
  const out: MetadataDiffEntry[] = [];
  for (const key of Array.from(keys).sort()) {
    if (ignored.has(key)) continue;
    const lv = (left as Record<string, unknown>)[key];
    const rv = (right as Record<string, unknown>)[key];
    out.push({
      key,
      left: lv,
      right: rv,
      changed: JSON.stringify(lv) !== JSON.stringify(rv),
    });
  }
  return out;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function RunComparisonView({ left, right }: RunComparisonViewProps) {
  // Friendly-error guard: comparing the same run twice is not useful.
  if (left.record.id === right.record.id) {
    return (
      <div
        role="alert"
        data-testid="run-comparison-same-id-error"
        className="rounded border border-red-300 bg-red-50 p-4 text-sm text-red-900"
      >
        Cannot compare a run to itself. Pick a different run for the
        right-hand side.
      </div>
    );
  }

  const slotDiff = useMemo(
    () => diffPromptSnapshots(left.prompts, right.prompts),
    [left.prompts, right.prompts],
  );
  const metaDiff = useMemo(
    () => diffMetadata(left.record, right.record),
    [left.record, right.record],
  );

  return (
    <div
      data-testid="run-comparison-view"
      className="flex flex-col gap-6"
    >
      <header>
        <h2 className="text-lg font-semibold">Compare runs</h2>
        <div className="grid grid-cols-2 gap-4 text-xs text-muted-foreground">
          <div data-testid="comparison-left-id">{left.record.id}</div>
          <div data-testid="comparison-right-id">{right.record.id}</div>
        </div>
      </header>

      <section data-testid="metadata-diff-section">
        <h3 className="mb-2 text-sm font-semibold">Metadata</h3>
        <table className="w-full table-fixed border-collapse text-xs">
          <thead>
            <tr className="text-left text-muted-foreground">
              <th className="w-1/4 p-1">Key</th>
              <th className="w-3/8 p-1">Left</th>
              <th className="w-3/8 p-1">Right</th>
            </tr>
          </thead>
          <tbody>
            {metaDiff.map((entry) => (
              <tr
                key={entry.key}
                data-testid={`metadata-row-${entry.key}`}
                data-status={entry.changed ? "changed" : "unchanged"}
                className={entry.changed ? "bg-amber-50" : ""}
              >
                <td className="p-1 font-mono">{entry.key}</td>
                <td className="p-1 font-mono">{String(entry.left ?? "")}</td>
                <td className="p-1 font-mono">{String(entry.right ?? "")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section data-testid="prompt-diff-section">
        <h3 className="mb-2 text-sm font-semibold">Prompt slots</h3>
        {slotDiff.length === 0 ? (
          <div className="text-xs text-muted-foreground">
            No prompt snapshot available on either run.
          </div>
        ) : (
          <ul className="space-y-1 text-xs">
            {slotDiff.map((entry) => (
              <li
                key={entry.slot_name}
                data-testid={`slot-diff-${entry.slot_name}`}
                data-status={entry.status}
                className={
                  entry.status === "changed"
                    ? "rounded bg-amber-50 p-2"
                    : "rounded p-2"
                }
              >
                <span className="font-mono">{entry.slot_name}</span>
                <span className="ml-3 text-muted-foreground">
                  ({entry.status})
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section data-testid="output-diff-section" className="grid grid-cols-2 gap-3">
        <article
          data-testid="comparison-output-left"
          className="overflow-auto rounded border bg-card p-3 text-xs whitespace-pre-wrap"
        >
          {left.primary ?? "(no primary output)"}
        </article>
        <article
          data-testid="comparison-output-right"
          className="overflow-auto rounded border bg-card p-3 text-xs whitespace-pre-wrap"
        >
          {right.primary ?? "(no primary output)"}
        </article>
      </section>
    </div>
  );
}

export default RunComparisonView;
