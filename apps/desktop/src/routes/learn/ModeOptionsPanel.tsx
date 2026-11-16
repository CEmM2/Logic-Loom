/**
 * Mode-specific option panels for the Learn tab.
 *
 * Renders a panel tailored to the selected generation mode. Only the eight
 * capabilities in the mapping below have bespoke panels; all other modes
 * render nothing.
 *
 * Mode → panel mapping:
 *   pedagogical_template → mini-textbook card (granularity + layout options)
 *   derivation_first     → derivation path toggle
 *   implementation_first → code-first / concept-first selector
 *   multi_granularity    → overview / standard / deep-dive selector
 *   notebook_source      → notebook export option + no-execute checkbox
 *   assessment_first     → assessment panel with public/hidden sections
 *   llm_expanded         → source-lock policy selector + generated-content warnings
 *   adaptive_path        → learner profile form + conservative-mode toggle
 *
 * ── Controlled component ──────────────────────────────────────────────────────
 *   This panel is fully CONTROLLED: it owns no state. The parent (LearnTab) owns
 *   a single `ModeOptions` object, passes it as `value`, and receives updates via
 *   `onChange`. LearnTab re-seeds `value` to mode-specific conservative defaults
 *   whenever the selected mode changes, so switching modes resets the panel.
 *
 * ── What is wired vs. display-only ────────────────────────────────────────────
 *   WIRED to the compile request (backend CompileRequest fields):
 *     multi_granularity / pedagogical_template granularity → `granularity`
 *     llm_expanded source-lock                             → `policy`
 *       (source_locked / explanatory_only / no_new_claims — the REAL akms_learn
 *        policy values; the previous locked/relaxed strings were not real)
 *     adaptive_path learner form                           → `learner_profile`
 *     implementation_first code-first/concept-first        → `include_code_links`
 *
 *   DISPLAY-ONLY (no LearningRequest field — NOT sent to the compiler; each carries
 *   an honest inline note): pedagogical_template `layout`, notebook_source
 *   `no-execute`, assessment_first `public`/`answer-key`, derivation_first
 *   `show-full`. The mode mapping calls for these controls, but they have no
 *   backend home, so LearnTab.handleCompile never reads them.
 */

import React from "react";

// ── Controlled value shape ────────────────────────────────────────────────────

/**
 * The wireable mode options LearnTab lifts out of the panels. Every field is the
 * union of what some mode consumes; handleCompile picks only the SELECTED mode's
 * field(s) into the request. Display-only controls (layout / no-execute /
 * assessment sections / derivation show-full) are kept LOCAL UI affordances and
 * are deliberately NOT part of this shape — they have no backend request field.
 */
export interface ModeOptions {
  /** multi_granularity + pedagogical_template → CompileRequest.granularity */
  granularity: "overview" | "standard" | "deep_dive";
  /**
   * llm_expanded → CompileRequest.policy. REAL akms_learn source-lock values:
   * "source_locked" (conservative default), "explanatory_only", "no_new_claims".
   */
  policy: "source_locked" | "explanatory_only" | "no_new_claims";
  /** implementation_first: true = code-first ⇒ CompileRequest.include_code_links */
  includeCodeLinks: boolean;
  /** adaptive_path → CompileRequest.learner_profile */
  learnerProfile: {
    knows: string[];
    weak: string[];
    goals: string[];
    conservative_mode: boolean;
  };
}

/**
 * Conservative per-this-app defaults for the wireable options. LearnTab seeds the
 * initial value from these and re-seeds when the mode changes, so switching modes
 * always resets to the conservative baseline.
 */
export const DEFAULT_MODE_OPTIONS: ModeOptions = {
  granularity: "standard",
  policy: "source_locked", // conservative: anchor generated content to source nodes
  includeCodeLinks: true, // implementation_first defaults to code-first
  learnerProfile: {
    knows: [],
    weak: [],
    goals: [],
    conservative_mode: true, // conservative: safe routing by default
  },
};

// ── Shared styling ────────────────────────────────────────────────────────────

const inputClass =
  "w-full rounded border border-border bg-background px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-primary/40";

/** Honest inline note for mapped controls that have no compile-request field. */
function DisplayOnlyNote() {
  return (
    <span className="text-xs italic text-muted-foreground">
      display preference — not sent to the compiler
    </span>
  );
}

/** Parse a comma-separated free-text field into a trimmed, non-empty list. */
function parseList(raw: string): string[] {
  return raw
    .split(",")
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

interface PanelProps {
  value: ModeOptions;
  onChange: (next: ModeOptions) => void;
}

// ── Individual mode panels ────────────────────────────────────────────────────

/**
 * pedagogical_template → mini-textbook mode card.
 * granularity is WIRED (→ CompileRequest.granularity). layout is DISPLAY-ONLY
 * (no backend field) and kept LOCAL — it never reaches the request.
 */
function PedagogicalTemplatePanel({ value, onChange }: PanelProps) {
  const [layout, setLayout] = React.useState<"textbook" | "reference">("textbook");

  return (
    <div
      data-testid="learn-modeform-pedagogical_template"
      className="flex flex-col gap-3"
    >
      <p className="text-xs text-muted-foreground">
        Generates a structured mini-textbook with sections, definitions, and
        worked examples. Select a granularity and preferred layout.
      </p>
      <label className="flex flex-col gap-1">
        <span className="text-xs uppercase tracking-wide text-muted-foreground">
          Granularity
        </span>
        <select
          data-testid="learn-modeform-pedagogical_template-granularity"
          className={inputClass}
          value={value.granularity}
          onChange={(e) =>
            onChange({
              ...value,
              granularity: e.target.value as ModeOptions["granularity"],
            })
          }
        >
          <option value="overview">Overview — high-level summary</option>
          <option value="standard">Standard — balanced depth</option>
          <option value="deep_dive">Deep dive — full elaboration</option>
        </select>
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-xs uppercase tracking-wide text-muted-foreground">
          Layout style
        </span>
        <select
          data-testid="learn-modeform-pedagogical_template-layout"
          className={inputClass}
          value={layout}
          onChange={(e) => setLayout(e.target.value as "textbook" | "reference")}
        >
          <option value="textbook">Textbook — narrative + examples</option>
          <option value="reference">Reference — concise definitions</option>
        </select>
        <DisplayOnlyNote />
      </label>
    </div>
  );
}

/**
 * derivation_first → derivation path toggle.
 * DISPLAY-ONLY: no backend field for derivation_path — kept LOCAL.
 */
function DerivationFirstPanel() {
  const [showFull, setShowFull] = React.useState(true);

  return (
    <div
      data-testid="learn-modeform-derivation_first"
      className="flex flex-col gap-3"
    >
      <p className="text-xs text-muted-foreground">
        Structures the lesson around the derivation path — each concept
        introduced from its prerequisites.
      </p>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          data-testid="learn-modeform-derivation_first-full-path"
          checked={showFull}
          onChange={(e) => setShowFull(e.target.checked)}
        />
        <span>Show full derivation path (all prerequisite steps)</span>
      </label>
      <DisplayOnlyNote />
    </div>
  );
}

/**
 * implementation_first → code-first / concept-first selector.
 * WIRED: code-first ⇒ include_code_links=true, concept-first ⇒ false.
 */
function ImplementationFirstPanel({ value, onChange }: PanelProps) {
  return (
    <div
      data-testid="learn-modeform-implementation_first"
      className="flex flex-col gap-3"
    >
      <p className="text-xs text-muted-foreground">
        Leads with concrete code examples before introducing the underlying
        theory and concepts.
      </p>
      <fieldset className="flex gap-4">
        <legend className="sr-only">Implementation policy</legend>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="radio"
            data-testid="learn-modeform-implementation_first-code-first"
            name="impl-policy"
            value="code_first"
            checked={value.includeCodeLinks}
            onChange={() => onChange({ ...value, includeCodeLinks: true })}
          />
          Code first
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="radio"
            data-testid="learn-modeform-implementation_first-concept-first"
            name="impl-policy"
            value="concept_first"
            checked={!value.includeCodeLinks}
            onChange={() => onChange({ ...value, includeCodeLinks: false })}
          />
          Concept first
        </label>
      </fieldset>
    </div>
  );
}

/**
 * multi_granularity → overview / standard / deep-dive selector.
 * WIRED → CompileRequest.granularity.
 */
function MultiGranularityPanel({ value, onChange }: PanelProps) {
  return (
    <div
      data-testid="learn-modeform-multi_granularity"
      className="flex flex-col gap-3"
    >
      <p className="text-xs text-muted-foreground">
        Generates the learning source at multiple levels of detail so you can
        choose depth post-compile.
      </p>
      <fieldset className="flex flex-col gap-2">
        <legend className="sr-only">Granularity level</legend>
        {(
          [
            ["overview", "Overview — high-level summary only"],
            ["standard", "Standard — balanced depth (recommended)"],
            ["deep_dive", "Deep dive — full elaboration of all concepts"],
          ] as const
        ).map(([granValue, label]) => (
          <label key={granValue} className="flex items-center gap-2 text-sm">
            <input
              type="radio"
              data-testid={`learn-modeform-multi_granularity-${granValue}`}
              name="multi-granularity"
              value={granValue}
              checked={value.granularity === granValue}
              onChange={() => onChange({ ...value, granularity: granValue })}
            />
            {label}
          </label>
        ))}
      </fieldset>
    </div>
  );
}

/**
 * notebook_source → notebook export option + no-execute checkbox.
 * DISPLAY-ONLY: no backend field for notebook_execute — kept LOCAL.
 * Conservative default: no-execute ON.
 */
function NotebookSourcePanel() {
  const [noExecute, setNoExecute] = React.useState(true); // conservative default ON

  return (
    <div
      data-testid="learn-modeform-notebook_source"
      className="flex flex-col gap-3"
    >
      <p className="text-xs text-muted-foreground">
        Generates a Jupyter Notebook export alongside the standard artifacts.
        By default, code cells are NOT executed — review before running.
      </p>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          data-testid="learn-modeform-notebook_source-no-execute"
          checked={noExecute}
          onChange={(e) => setNoExecute(e.target.checked)}
        />
        <span>
          Do not execute notebook cells{" "}
          <span className="text-xs text-muted-foreground">(conservative default — review code before running)</span>
        </span>
      </label>
      <DisplayOnlyNote />
    </div>
  );
}

/**
 * assessment_first → assessment panel with public/hidden section controls.
 * DISPLAY-ONLY: no backend field for assessment_sections — kept LOCAL.
 */
function AssessmentFirstPanel() {
  const [includePublic, setIncludePublic] = React.useState(true);
  const [includeAnswerKey, setIncludeAnswerKey] = React.useState(false);

  return (
    <div
      data-testid="learn-modeform-assessment_first"
      className="flex flex-col gap-3"
    >
      <p className="text-xs text-muted-foreground">
        Generates the lesson as an assessment with public prompts and an
        optional hidden answer key. Select which sections to include.
      </p>
      <div className="flex flex-col gap-2">
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            data-testid="learn-modeform-assessment_first-public"
            checked={includePublic}
            onChange={(e) => setIncludePublic(e.target.checked)}
          />
          <span>Public section — assessment prompts visible to learners</span>
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            data-testid="learn-modeform-assessment_first-answer-key"
            checked={includeAnswerKey}
            onChange={(e) => setIncludeAnswerKey(e.target.checked)}
          />
          <span>
            Hidden answer key{" "}
            <span className="text-xs text-muted-foreground">(rubric — not shown to learners)</span>
          </span>
        </label>
        <DisplayOnlyNote />
      </div>
    </div>
  );
}

/**
 * llm_expanded → source-lock policy selector + generated-content warnings.
 *
 * WIRED → CompileRequest.policy. The selector now uses the REAL akms_learn
 * source-lock policy values (source_locked / explanatory_only / no_new_claims),
 * NOT the prior placeholder locked/relaxed strings. Conservative default:
 * source_locked. The generated-content warning is always visible for this mode.
 */
function LlmExpandedPanel({ value, onChange }: PanelProps) {
  return (
    <div
      data-testid="learn-modeform-llm_expanded"
      className="flex flex-col gap-3"
    >
      {/* Generated-content warning — always shown for this mode */}
      <div
        data-testid="learn-modeform-llm_expanded-warning"
        className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800"
        role="alert"
      >
        <strong>Generated content notice:</strong> This mode produces
        LLM-generated sections that may not be grounded in the source graph.
        Review all output before use in a learning context.
      </div>

      <label className="flex flex-col gap-1">
        <span className="text-xs uppercase tracking-wide text-muted-foreground">
          Source-lock policy
        </span>
        <select
          data-testid="learn-modeform-llm_expanded-source-lock"
          className={inputClass}
          value={value.policy}
          onChange={(e) =>
            onChange({ ...value, policy: e.target.value as ModeOptions["policy"] })
          }
        >
          <option value="source_locked">
            Source locked — generated content anchored to source nodes (conservative)
          </option>
          <option value="explanatory_only">
            Explanatory only — expand wording but introduce no new claims
          </option>
          <option value="no_new_claims">
            No new claims — paraphrase without adding facts
          </option>
        </select>
      </label>
    </div>
  );
}

/**
 * adaptive_path → learner profile form + conservative-mode toggle.
 *
 * WIRED → CompileRequest.learner_profile ({knows, weak, goals, conservative_mode}).
 * The three free-text fields are comma-separated lists. Conservative default:
 * conservative_mode ON.
 */
function AdaptivePathPanel({ value, onChange }: PanelProps) {
  const profile = value.learnerProfile;
  // Mirror the parsed lists back into the inputs as comma-joined strings so the
  // controlled value is the single source of truth (no separate raw-text state).
  const setList = (
    key: "knows" | "weak" | "goals",
    raw: string,
  ) =>
    onChange({
      ...value,
      learnerProfile: { ...profile, [key]: parseList(raw) },
    });

  return (
    <div
      data-testid="learn-modeform-adaptive_path"
      className="flex flex-col gap-3"
    >
      <p className="text-xs text-muted-foreground">
        Adapts the lesson path to the learner's existing knowledge. Provide an
        optional learner profile (comma-separated node ids or knowledge claims).
      </p>
      <label className="flex flex-col gap-1">
        <span className="text-xs uppercase tracking-wide text-muted-foreground">
          Already knows
        </span>
        <input
          data-testid="learn-modeform-adaptive_path-knows"
          className={inputClass}
          value={profile.knows.join(", ")}
          onChange={(e) => setList("knows", e.target.value)}
          placeholder="node_id_a, node_id_b, … (optional)"
        />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-xs uppercase tracking-wide text-muted-foreground">
          Weak on
        </span>
        <input
          data-testid="learn-modeform-adaptive_path-weak"
          className={inputClass}
          value={profile.weak.join(", ")}
          onChange={(e) => setList("weak", e.target.value)}
          placeholder="node_id_c, … (optional)"
        />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-xs uppercase tracking-wide text-muted-foreground">
          Goals
        </span>
        <input
          data-testid="learn-modeform-adaptive_path-goals"
          className={inputClass}
          value={profile.goals.join(", ")}
          onChange={(e) => setList("goals", e.target.value)}
          placeholder="target_node_id, … (optional)"
        />
      </label>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          data-testid="learn-modeform-adaptive_path-conservative-mode"
          checked={profile.conservative_mode}
          onChange={(e) =>
            onChange({
              ...value,
              learnerProfile: {
                ...profile,
                conservative_mode: e.target.checked,
              },
            })
          }
        />
        <span>
          Conservative mode{" "}
          <span className="text-xs text-muted-foreground">
            (safe routing — include all prerequisite nodes; recommended)
          </span>
        </span>
      </label>
    </div>
  );
}

// ── Public export ─────────────────────────────────────────────────────────────

interface Props {
  /** The currently selected generation mode id (from the mode registry). */
  selectedModeId: string;
  /** Controlled mode-option value (LearnTab-owned). */
  value: ModeOptions;
  /** Update callback — LearnTab folds the next value into its state. */
  onChange: (next: ModeOptions) => void;
}

/**
 * Renders the mapped option panel for `selectedModeId`, wrapped in a
 * labelled fieldset. Returns null for unmapped modes (no crash, no empty box).
 *
 * Controlled: wireable controls read/write `value`/`onChange`; display-only
 * controls (no backend field) keep small LOCAL state inside their sub-panel.
 */
export function ModeOptionsPanel({ selectedModeId, value, onChange }: Props) {
  let panel: React.ReactElement | null = null;

  switch (selectedModeId) {
    case "pedagogical_template":
      panel = <PedagogicalTemplatePanel value={value} onChange={onChange} />;
      break;
    case "derivation_first":
      panel = <DerivationFirstPanel />;
      break;
    case "implementation_first":
      panel = <ImplementationFirstPanel value={value} onChange={onChange} />;
      break;
    case "multi_granularity":
      panel = <MultiGranularityPanel value={value} onChange={onChange} />;
      break;
    case "notebook_source":
      panel = <NotebookSourcePanel />;
      break;
    case "assessment_first":
      panel = <AssessmentFirstPanel />;
      break;
    case "llm_expanded":
      panel = <LlmExpandedPanel value={value} onChange={onChange} />;
      break;
    case "adaptive_path":
      panel = <AdaptivePathPanel value={value} onChange={onChange} />;
      break;
    default:
      return null;
  }

  return (
    <fieldset
      data-testid={`learn-modeform-panel-${selectedModeId}`}
      className="grid gap-3 rounded border border-border p-3"
    >
      <legend className="px-1 text-xs uppercase tracking-wide text-muted-foreground">
        Mode options
      </legend>
      {panel}
    </fieldset>
  );
}
