/**
 * Learn preview panels.
 *
 * Exports four structured panels that render the compile response's
 * per-artifact metadata, warning categories and provenance:
 *
 *   1. ProvenanceJsonPanel   — raw packet/provenance JSON via JSON.stringify
 *                              into a <pre> (NEVER dangerouslySetInnerHTML).
 *   2. NotebookMetaPanel     — notebook exports with cell_count + executed flags.
 *   3. AssessmentPresencePanel — assessment exports with has_public/has_answer_key
 *                              presence flags ONLY. Rubric/answer-key content is
 *                              NEVER read or rendered.
 *   4. GeneratedWarningsPanel — warnings whose category === "generated_content"
 *                              surfaced distinctly from other warnings.
 *
 * All panels degrade gracefully when data is absent:
 *   - ProvenanceJsonPanel: always renders — provenance_summary is a required
 *     field of LearnCompileResponse (backend always emits it; may be {}).
 *   - NotebookMetaPanel:   hidden when no exports carry notebook metadata.
 *   - AssessmentPresencePanel: hidden when no exports carry assessment metadata.
 *   - GeneratedWarningsPanel:  hidden when no warnings have category === "generated_content".
 *
 * Security note: JSON panel uses JSON.stringify(…, null, 2) rendered as text
 * content inside a <pre> — no HTML injection possible. Assessment panel shows
 * presence booleans only, never raw file content.
 */

import type { LearnArtifact, LearnCompileResponse, LearnWarning } from "../../api/learn";

// ── 1. Provenance JSON panel ─────────────────────────────────────────────────

interface ProvenancePanelProps {
  response: LearnCompileResponse;
}

/**
 * Renders the raw provenance/packet JSON for inspection.
 *
 * Surfaces provenance_summary, packet_path, and cache_dir in a single
 * readable JSON block. No HTML injection — JSON.stringify() output into
 * a <pre> text node.
 *
 * `provenance_summary` is a required field of LearnCompileResponse, so this
 * panel always renders when a compile response exists (no nullish guard).
 */
export function ProvenanceJsonPanel({ response }: ProvenancePanelProps) {
  const payload = {
    provenance_summary: response.provenance_summary,
    packet_path: response.packet_path ?? null,
    cache_dir: response.cache_dir ?? null,
  };

  // JSON.stringify into a text node — zero HTML injection risk.
  const jsonText = JSON.stringify(payload, null, 2);

  return (
    <div
      data-testid="learn-provenance-json"
      className="rounded border border-border bg-muted/30 p-3"
    >
      <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Packet / Provenance JSON
      </div>
      <pre
        data-testid="learn-provenance-json-pre"
        className="overflow-x-auto whitespace-pre-wrap break-all text-xs text-foreground"
      >
        {jsonText}
      </pre>
    </div>
  );
}

// ── 2. Notebook metadata panel ───────────────────────────────────────────────

interface NotebookMetaPanelProps {
  exports: LearnArtifact[];
}

/**
 * Renders metadata for notebook (.ipynb) exports.
 *
 * Shows cell_count and executed status from exports[].metadata where the
 * metadata carries notebook-family keys (cell_count present). Hides when no
 * notebook exports are present.
 */
export function NotebookMetaPanel({ exports }: NotebookMetaPanelProps) {
  // Notebook artifacts are those whose metadata carries cell_count.
  // `?? []` guards against a null/undefined exports array (older/malformed
  // response) — .filter() on undefined would throw a TypeError.
  const notebookExports = (exports ?? []).filter(
    (artifact) => artifact.metadata != null && typeof artifact.metadata.cell_count === "number",
  );

  if (notebookExports.length === 0) return null;

  return (
    <div
      data-testid="learn-notebook-meta"
      className="rounded border border-border bg-muted/30 p-3"
    >
      <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Notebook Metadata
      </div>
      <ul className="space-y-2">
        {notebookExports.map((artifact, index) => {
          const meta = artifact.metadata!;
          // ES2020-safe, cross-platform last-segment extraction (no Array.at()).
          // Split on both POSIX `/` and Windows `\` so basenames render correctly
          // when the sidecar emits Windows-style cache paths.
          const segments = artifact.path.split(/[/\\]/).filter(Boolean);
          const basename = segments.length > 0 ? segments[segments.length - 1] : artifact.path;
          return (
            <li key={`${artifact.path}-${index}`} className="text-sm">
              <span className="font-mono text-xs text-muted-foreground">{basename}</span>
              <dl
                data-testid={`learn-notebook-meta-item-${index}`}
                className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs"
              >
                <dt className="font-medium">Cells</dt>
                <dd data-testid={`learn-notebook-cell-count-${index}`}>
                  {meta.cell_count ?? 0}
                </dd>
                <dt className="font-medium">Executed</dt>
                <dd data-testid={`learn-notebook-executed-${index}`}>
                  {meta.executed ? (
                    <span className="text-green-700">Yes</span>
                  ) : (
                    <span className="text-muted-foreground">No</span>
                  )}
                </dd>
                {meta.generated_content ? (
                  <>
                    <dt className="font-medium">Generated content</dt>
                    <dd
                      data-testid={`learn-notebook-generated-${index}`}
                      className="text-amber-700"
                    >
                      Yes — LLM-generated
                    </dd>
                  </>
                ) : null}
              </dl>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// ── 3. Assessment public prompts panel ───────────────────────────────────────

interface AssessmentPresencePanelProps {
  exports: LearnArtifact[];
}

/**
 * Renders assessment export presence flags.
 *
 * Shows has_public / has_answer_key from exports[].metadata for assessment
 * family artifacts. PRESENCE FLAGS ONLY — the rubric/answer-key file content
 * is NEVER read or rendered. Hides when no assessment exports are present.
 */
export function AssessmentPresencePanel({ exports }: AssessmentPresencePanelProps) {
  // Assessment artifacts are those whose metadata carries has_public or has_answer_key.
  // `?? []` guards against a null/undefined exports array (.filter would throw).
  const assessmentExports = (exports ?? []).filter(
    (artifact) =>
      artifact.metadata != null &&
      (typeof artifact.metadata.has_public === "boolean" ||
        typeof artifact.metadata.has_answer_key === "boolean"),
  );

  if (assessmentExports.length === 0) return null;

  // Deduplicate by sibling-file presence: multiple artifact entries (assessment.md,
  // assessment.json, rubric.md) may share the same has_public/has_answer_key pair.
  // Show a single consolidated presence panel — not one entry per file.
  const meta = assessmentExports[0].metadata!;
  const { has_public, has_answer_key, generated_content } = meta;

  return (
    <div
      data-testid="learn-assessment-presence"
      className="rounded border border-border bg-muted/30 p-3"
    >
      <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Assessment Public Prompts
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        <dt className="font-medium">Public prompts</dt>
        <dd data-testid="learn-assessment-has-public">
          {has_public ? (
            <span className="text-green-700">Present</span>
          ) : (
            <span className="text-muted-foreground">Not present</span>
          )}
        </dd>
        <dt className="font-medium">Answer key</dt>
        <dd data-testid="learn-assessment-has-answer-key">
          {has_answer_key ? (
            <span className="text-amber-700">Present (not shown)</span>
          ) : (
            <span className="text-muted-foreground">Not present</span>
          )}
        </dd>
        {generated_content ? (
          <>
            <dt className="font-medium">Generated content</dt>
            <dd data-testid="learn-assessment-generated" className="text-amber-700">
              Yes — LLM-generated
            </dd>
          </>
        ) : null}
      </dl>
    </div>
  );
}

// ── 4. Generated-section warnings panel ─────────────────────────────────────

interface GeneratedWarningsPanelProps {
  warnings: LearnWarning[];
}

/**
 * Renders warnings whose category === "generated_content" distinctly.
 *
 * These are separated from other warnings (which remain in the flat warnings
 * list in LearnTab) so users can quickly identify LLM-generated-content
 * concerns. Hidden when no generated_content-category warnings are present.
 */
export function GeneratedWarningsPanel({ warnings }: GeneratedWarningsPanelProps) {
  // `?? []` guards against a null/undefined warnings array (.filter would throw).
  const generatedWarnings = (warnings ?? []).filter((w) => w.category === "generated_content");

  if (generatedWarnings.length === 0) return null;

  return (
    <div
      data-testid="learn-generated-warnings"
      className="rounded border border-amber-300 bg-amber-50 p-3"
    >
      <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-amber-800">
        Generated Content Warnings
      </div>
      <ul
        data-testid="learn-generated-warnings-list"
        className="list-disc space-y-1 pl-4 text-sm text-amber-800"
      >
        {generatedWarnings.map((w, index) => (
          <li key={`${w.code}-${index}`} data-testid={`learn-generated-warning-item-${index}`}>
            [{w.severity}] {w.message}
            {w.source_ref ? ` — ${w.source_ref}` : ""}
          </li>
        ))}
      </ul>
    </div>
  );
}
