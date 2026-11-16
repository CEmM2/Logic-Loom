/**
 * Learn tab.
 *
 * Composes the core UI elements:
 *   1. plugin status card           → <LearnStatusCard>
 *   2. graph node/slice input       → controlled form (topic/goal required)
 *   3. generation mode selector     → capability registry from GET /profiles
 *   4. exporter selector            → capability registry from GET /exporters
 *   5. compile button               → useMutation(compileLearningSource)
 *   6. Markdown preview             → <MarkdownPreview>
 *   7. warnings panel               → response.warnings
 *   8. export artifact list         → <ExportArtifactList>
 *   9. provenance summary           → response.provenance_summary
 *
 * Additional panels:
 *   10. Artifact history panel      → <ArtifactHistoryPanel> (session-scoped)
 *   11. Open export folder button   → <OpenFolderButton> (Tauri-only, degrades gracefully)
 *   12. Help / provenance panel     → <LearnHelpPanel> (collapsible static doc)
 *
 * Capability-driven: the mode + exporter selectors are sourced from the
 * `GET /profiles` + `GET /exporters` endpoints (each `{id, label, status}`),
 * never hard-coded. Entries whose `status` is `unavailable` /
 * `planned` render DISABLED so they can't be submitted (which would `412`); the
 * default selected mode is always an `available` one. The compile path uses
 * `/compile` (NOT the deferred `/export` stub) — it already persists artifacts and
 * advertises `exports`.
 *
 * Composability: form state seeds from optional router-state prefill
 * (`LearnPrefillState`), the contract the Graph "Send to Learn" action targets.
 * The mode selector is a dynamic registry and the preview panels are separate
 * components, so the pieces here stay small.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useLocation } from "react-router-dom";

import type { GraphDirection } from "../../api/graph";
import {
  compileLearningSource,
  type LearnCompileRequest,
  type LearnCompileResponse,
  type LearnGraphSlice,
  type LearnStatus,
} from "../../api/learn";
import { MarkdownPreview } from "../../components/MarkdownPreview";
import { useLearnExporters, useLearnProfiles } from "../../hooks/app";

import {
  ArtifactHistoryPanel,
  type ArtifactHistoryEntry,
} from "./ArtifactHistoryPanel";
import {
  buildExporterRegistry,
  buildModeRegistry,
  defaultSelectableModeId,
} from "./capabilities";
import { DomainPackPanel } from "./DomainPackPanel";
import { ExportArtifactList } from "./ExportArtifactList";
import { LearnHelpPanel } from "./LearnHelpPanel";
import { LearnStatusCard } from "./LearnStatusCard";
import {
  DEFAULT_MODE_OPTIONS,
  ModeOptionsPanel,
  type ModeOptions,
} from "./ModeOptionsPanel";
import { OpenFolderButton } from "./OpenFolderButton";
import {
  AssessmentPresencePanel,
  GeneratedWarningsPanel,
  NotebookMetaPanel,
  ProvenanceJsonPanel,
} from "./PassBPanels";
import { isLearnPrefillState } from "./prefill";

interface Props {
  status: LearnStatus;
}

const DIRECTIONS: GraphDirection[] = ["in", "out", "both"];

const inputClass =
  "w-full rounded border border-border bg-background px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-primary/40";

export function LearnTab({ status }: Props) {
  const location = useLocation();
  const prefill = isLearnPrefillState(location.state) ? location.state : null;

  // Capability-driven registry: modes from /profiles, exporters from
  // /exporters. The status-bearing endpoints are the PRIMARY source so the UI
  // can render unavailable/planned entries DISABLED. A degrade-gracefully
  // fallback to status.generation_modes / status.capabilities keeps the
  // selectors populated if an endpoint is empty/unavailable (see capabilities.ts).
  const profilesQuery = useLearnProfiles(status.akms_learn_available);
  const exportersQuery = useLearnExporters(status.akms_learn_available);

  const modes = useMemo(
    () => buildModeRegistry(profilesQuery.data?.profiles, status),
    [profilesQuery.data, status],
  );
  const exporters = useMemo(
    () => buildExporterRegistry(exportersQuery.data?.exporters, status),
    [exportersQuery.data, status],
  );

  // ── Compile-request form state (element 2) ────────────────────────────────
  const [topic, setTopic] = useState(prefill?.topic ?? "");
  const [goal, setGoal] = useState(prefill?.goal ?? "");
  const [audience, setAudience] = useState("");
  const [depth, setDepth] = useState("");
  const [seedTagsRaw, setSeedTagsRaw] = useState("");
  const [node, setNode] = useState(prefill?.node ?? "");
  const [maxDepth, setMaxDepth] = useState<number>(prefill?.maxDepth ?? 2);
  const [direction, setDirection] = useState<GraphDirection>(
    prefill?.direction ?? "both",
  );
  // An explicit slice (source form A) only arrives via prefill.
  const [graphSlice] = useState<LearnGraphSlice | undefined>(prefill?.graphSlice);

  // ── Mode + exporter selection (elements 3 & 4) ────────────────────────────
  // The default selection is always an AVAILABLE mode — never an unavailable /
  // planned one (which would 412 on compile). Initial exporter selection is the
  // selectable (available) exporters only.
  const [generationOption, setGenerationOption] = useState<string>(() =>
    defaultSelectableModeId(modes),
  );
  const [selectedExporters, setSelectedExporters] = useState<string[]>(() =>
    exporters.filter((exporter) => exporter.selectable).map((exporter) => exporter.id),
  );
  // Tracks whether the /exporters registry has settled once. The "select all
  // available" default runs only on that first settle — so a user who later
  // deselects every exporter keeps their empty choice across registry updates.
  const exportersLoadedRef = useRef(false);

  // ── Mode-specific options ─────────────────────────────────────────────────
  // LearnTab owns the wireable mode options the panels collect (granularity,
  // policy, learner_profile, include_code_links). The panel is a CONTROLLED
  // component reading `modeOptions` / writing via `setModeOptions`. We re-seed
  // to conservative defaults whenever the selected mode changes, so switching
  // modes resets the panel to that mode's safe baseline (DEFAULT_MODE_OPTIONS).
  const [modeOptions, setModeOptions] = useState<ModeOptions>(DEFAULT_MODE_OPTIONS);
  useEffect(() => {
    setModeOptions(DEFAULT_MODE_OPTIONS);
  }, [generationOption]);

  // Keep the selected mode valid + selectable when the registry settles
  // underneath us (e.g. /profiles resolves after first render). Re-seed to the
  // first available mode whenever the current selection isn't an available one.
  useEffect(() => {
    const stillSelectable = modes.some(
      (mode) => mode.id === generationOption && mode.selectable,
    );
    if (!stillSelectable) {
      setGenerationOption(defaultSelectableModeId(modes));
    }
  }, [modes, generationOption]);

  // Re-seed exporter selection to the selectable set once the /exporters
  // registry settles (the useState initializer only runs on first render, when
  // the query may still be pending). Prune any now-unselectable ids; default to
  // all-selectable when the user hasn't deselected anything yet.
  const exporterRegistryKey = useMemo(
    () => exporters.map((exporter) => `${exporter.id}:${exporter.status}`).join("|"),
    [exporters],
  );
  useEffect(() => {
    const selectableIds = exporters
      .filter((exporter) => exporter.selectable)
      .map((exporter) => exporter.id);
    // First settle only (registry has selectable exporters and we haven't
    // seeded yet) → select all available exporters. Gated on a ref, NOT on
    // `prev.length === 0`, so a deliberate deselect-all isn't re-selected on a
    // later registry update.
    if (selectableIds.length > 0 && !exportersLoadedRef.current) {
      exportersLoadedRef.current = true;
      setSelectedExporters(selectableIds);
      return;
    }
    // Otherwise keep the user's choices, dropping any no-longer-selectable ids.
    setSelectedExporters((prev) => {
      const selectableSet = new Set(selectableIds);
      const pruned = prev.filter((id) => selectableSet.has(id));
      return pruned.length === prev.length ? prev : pruned;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [exporterRegistryKey]);

  const toggleExporter = (id: string) => {
    // Only selectable exporters can be toggled; unavailable/planned are disabled.
    if (!exporters.some((exporter) => exporter.id === id && exporter.selectable)) {
      return;
    }
    setSelectedExporters((prev) =>
      prev.includes(id) ? prev.filter((value) => value !== id) : [...prev, id],
    );
  };

  // ── Session artifact history ─────────────────────────────────────────────
  // Accumulates each successful compile response for the current session.
  // SESSION-SCOPED ONLY: no backend history endpoint exists (routes/learn.py
  // has no /history or /list route). Persisting cross-session history would
  // require a new backend listing endpoint.
  const [artifactHistory, setArtifactHistory] = useState<ArtifactHistoryEntry[]>([]);

  // ── Compile mutation (element 5) ──────────────────────────────────────────
  const compile = useMutation<LearnCompileResponse, Error, LearnCompileRequest>({
    mutationFn: (req) => compileLearningSource(req),
    onSuccess: (data) => {
      // Append to session history on every successful compile.
      const entry: ArtifactHistoryEntry = {
        artifact_id: data.artifact_id,
        topic: data.provenance_summary?.topic ?? null,
        generation_option: data.provenance_summary?.generation_option ?? null,
        cache_dir: data.cache_dir,
        exports: data.exports,
        timestamp: Date.now(),
      };
      setArtifactHistory((prev) => [...prev, entry]);
    },
  });

  const seedTags = useMemo(
    () =>
      seedTagsRaw
        .split(",")
        .map((tag) => tag.trim())
        .filter((tag) => tag.length > 0),
    [seedTagsRaw],
  );

  const hasSource = graphSlice ? true : node.trim().length > 0;
  const canCompile =
    topic.trim().length > 0 &&
    goal.trim().length > 0 &&
    generationOption.length > 0 &&
    hasSource &&
    !compile.isPending;

  // Map the lifted ModeOptions onto the compile request, including ONLY the
  // field(s) the SELECTED mode actually consumes. A mode with no wireable
  // options (e.g. deterministic_outline, derivation_first, notebook_source,
  // assessment_first) contributes nothing — its panel controls are display-only.
  const modeRequestFields = useMemo((): Partial<LearnCompileRequest> => {
    switch (generationOption) {
      case "multi_granularity":
      case "pedagogical_template":
        return { granularity: modeOptions.granularity };
      case "llm_expanded":
        return { policy: modeOptions.policy };
      case "adaptive_path":
        return { learner_profile: modeOptions.learnerProfile };
      case "implementation_first":
        return { include_code_links: modeOptions.includeCodeLinks };
      default:
        return {};
    }
  }, [generationOption, modeOptions]);

  const handleCompile = () => {
    if (!canCompile) return;
    const req: LearnCompileRequest = {
      generation_option: generationOption,
      topic: topic.trim(),
      goal: goal.trim(),
      ...(audience.trim() ? { audience: audience.trim() } : {}),
      ...(depth.trim() ? { depth: depth.trim() } : {}),
      ...(seedTags.length > 0 ? { seed_tags: seedTags } : {}),
      ...(selectedExporters.length > 0 ? { exporters: selectedExporters } : {}),
      // Per-mode options (only the selected mode's wireable field(s)).
      ...modeRequestFields,
      ...(graphSlice
        ? { graph_slice: graphSlice }
        : { node: node.trim(), max_depth: maxDepth, direction }),
    };
    compile.mutate(req);
  };

  const response = compile.data;

  return (
    <main className="flex h-full flex-col gap-6 overflow-y-auto p-8">
      <header>
        <h1 className="text-2xl font-semibold">Learn</h1>
        <p className="max-w-prose text-sm text-muted-foreground">
          Compile a learning source from a graph node (or slice). Pick a generation
          mode and exporters, then compile to preview the Markdown and the exported
          artifacts.
        </p>
      </header>

      {/* 12. Help / provenance documentation panel (collapsible) */}
      <LearnHelpPanel />

      {/* 1 + 9 status / capability badges / warnings */}
      <LearnStatusCard status={status} />

      {/* Domain-pack UI: status card + source-pack table +
          capability badges + static-only warnings, driven ENTIRELY by the
          backend `status.domain_packs` payload. Absent on the unavailable
          path / older payloads → defaults to [] → renders an empty state. */}
      <DomainPackPanel domainPacks={status.domain_packs ?? []} />

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Left column: the compile request form (elements 2–5). */}
        <section className="flex flex-col gap-4">
          {/* 2. Required compile fields */}
          <div className="grid gap-3">
            <label className="flex flex-col gap-1">
              <span className="text-xs uppercase tracking-wide text-muted-foreground">
                Topic <span className="text-red-600">*</span>
              </span>
              <input
                data-testid="learn-topic"
                className={inputClass}
                value={topic}
                onChange={(event) => setTopic(event.target.value)}
                placeholder="What is being learned?"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-xs uppercase tracking-wide text-muted-foreground">
                Goal <span className="text-red-600">*</span>
              </span>
              <input
                data-testid="learn-goal"
                className={inputClass}
                value={goal}
                onChange={(event) => setGoal(event.target.value)}
                placeholder="Why — the learning outcome"
              />
            </label>
          </div>

          {/* 2. Graph node / slice input */}
          <fieldset className="grid gap-3 rounded border border-border p-3">
            <legend className="px-1 text-xs uppercase tracking-wide text-muted-foreground">
              Graph source
            </legend>
            {graphSlice ? (
              <p
                data-testid="learn-slice-prefill"
                className="text-sm text-muted-foreground"
              >
                Using an explicit graph slice ({graphSlice.nodes.length} node
                {graphSlice.nodes.length === 1 ? "" : "s"}, {graphSlice.edges.length} edge
                {graphSlice.edges.length === 1 ? "" : "s"}) provided by the graph view.
              </p>
            ) : (
              <>
                <label className="flex flex-col gap-1">
                  <span className="text-xs uppercase tracking-wide text-muted-foreground">
                    Node id <span className="text-red-600">*</span>
                  </span>
                  <input
                    data-testid="learn-node"
                    className={inputClass}
                    value={node}
                    onChange={(event) => setNode(event.target.value)}
                    placeholder="graph node id"
                  />
                </label>
                <div className="grid grid-cols-2 gap-3">
                  <label className="flex flex-col gap-1">
                    <span className="text-xs uppercase tracking-wide text-muted-foreground">
                      Max depth
                    </span>
                    <input
                      data-testid="learn-max-depth"
                      type="number"
                      min={0}
                      className={inputClass}
                      value={maxDepth}
                      onChange={(event) =>
                        setMaxDepth(Number(event.target.value) || 0)
                      }
                    />
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="text-xs uppercase tracking-wide text-muted-foreground">
                      Direction
                    </span>
                    <select
                      data-testid="learn-direction"
                      className={inputClass}
                      value={direction}
                      onChange={(event) =>
                        setDirection(event.target.value as GraphDirection)
                      }
                    >
                      {DIRECTIONS.map((value) => (
                        <option key={value} value={value}>
                          {value}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
              </>
            )}
          </fieldset>

          {/* 2. Optional fields */}
          <div className="grid grid-cols-2 gap-3">
            <label className="flex flex-col gap-1">
              <span className="text-xs uppercase tracking-wide text-muted-foreground">
                Audience
              </span>
              <input
                data-testid="learn-audience"
                className={inputClass}
                value={audience}
                onChange={(event) => setAudience(event.target.value)}
                placeholder="optional"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-xs uppercase tracking-wide text-muted-foreground">
                Depth
              </span>
              <input
                data-testid="learn-depth"
                className={inputClass}
                value={depth}
                onChange={(event) => setDepth(event.target.value)}
                placeholder="optional"
              />
            </label>
          </div>
          <label className="flex flex-col gap-1">
            <span className="text-xs uppercase tracking-wide text-muted-foreground">
              Seed tags
            </span>
            <input
              data-testid="learn-seed-tags"
              className={inputClass}
              value={seedTagsRaw}
              onChange={(event) => setSeedTagsRaw(event.target.value)}
              placeholder="comma,separated,tags (optional)"
            />
          </label>

          {/* 3. Generation mode selector (capability-derived) */}
          <fieldset
            data-testid="learn-mode-selector"
            className="grid gap-2 rounded border border-border p-3"
          >
            <legend className="px-1 text-xs uppercase tracking-wide text-muted-foreground">
              Generation mode
            </legend>
            {modes.length === 0 ? (
              <span className="text-sm text-muted-foreground">
                No generation modes advertised.
              </span>
            ) : (
              modes.map((mode) => (
                <label
                  key={mode.id}
                  className={`flex items-center gap-2 text-sm ${
                    mode.selectable ? "" : "opacity-50"
                  }`}
                  title={
                    mode.selectable
                      ? undefined
                      : `Mode '${mode.id}' is ${mode.status} in this build.`
                  }
                >
                  <input
                    type="radio"
                    name="learn-generation-option"
                    data-testid={`learn-mode-${mode.id}`}
                    value={mode.id}
                    checked={generationOption === mode.id}
                    disabled={!mode.selectable}
                    onChange={() => {
                      if (mode.selectable) setGenerationOption(mode.id);
                    }}
                  />
                  <span>{mode.label}</span>
                  {mode.selectable ? null : (
                    <span
                      data-testid={`learn-mode-status-${mode.id}`}
                      className="rounded bg-muted px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground"
                    >
                      {mode.status}
                    </span>
                  )}
                </label>
              ))
            )}
          </fieldset>

          {/* 3b. Mode-specific option panel — swaps with mode selection.
               Returns null for unmapped modes; no crash, no empty box.
               Controlled: the wireable options live in `modeOptions` here and
               feed handleCompile per the selected mode (granularity / policy /
               learner_profile / include_code_links). */}
          <ModeOptionsPanel
            selectedModeId={generationOption}
            value={modeOptions}
            onChange={setModeOptions}
          />

          {/* 4. Exporter selector (capability-derived, multi-select) */}
          <fieldset
            data-testid="learn-exporter-selector"
            className="grid gap-2 rounded border border-border p-3"
          >
            <legend className="px-1 text-xs uppercase tracking-wide text-muted-foreground">
              Exporters
            </legend>
            {exporters.length === 0 ? (
              <span className="text-sm text-muted-foreground">
                No exporters advertised.
              </span>
            ) : (
              exporters.map((exporter) => (
                <label
                  key={exporter.id}
                  className={`flex items-center gap-2 text-sm ${
                    exporter.selectable ? "" : "opacity-50"
                  }`}
                  title={
                    exporter.selectable
                      ? undefined
                      : `Exporter '${exporter.id}' is ${exporter.status} in this build.`
                  }
                >
                  <input
                    type="checkbox"
                    data-testid={`learn-exporter-${exporter.id}`}
                    checked={selectedExporters.includes(exporter.id)}
                    disabled={!exporter.selectable}
                    onChange={() => toggleExporter(exporter.id)}
                  />
                  <span>{exporter.label}</span>
                  {exporter.selectable ? null : (
                    <span
                      data-testid={`learn-exporter-status-${exporter.id}`}
                      className="rounded bg-muted px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground"
                    >
                      {exporter.status}
                    </span>
                  )}
                </label>
              ))
            )}
          </fieldset>

          {/* 5. Compile button */}
          <div className="flex items-center gap-3">
            <button
              type="button"
              data-testid="learn-compile"
              onClick={handleCompile}
              disabled={!canCompile}
              className="rounded bg-foreground px-4 py-1.5 text-sm text-background hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {compile.isPending ? "Compiling…" : "Compile"}
            </button>
            {compile.isError ? (
              <span data-testid="learn-compile-error" className="text-xs text-red-600">
                {compile.error.message}
              </span>
            ) : null}
          </div>
        </section>

        {/* Right column: compile results (elements 6–9). */}
        <section className="flex flex-col gap-4">
          {response ? (
            <>
              {/* 9. provenance summary */}
              {response.provenance_summary ? (
                <div
                  data-testid="learn-provenance"
                  className="rounded border border-border bg-muted/30 p-3 text-xs text-muted-foreground"
                >
                  <div className="mb-1 font-semibold uppercase tracking-wide">
                    Provenance
                  </div>
                  <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
                    {response.provenance_summary.topic != null ? (
                      <>
                        <dt className="font-medium">Topic</dt>
                        <dd>{response.provenance_summary.topic}</dd>
                      </>
                    ) : null}
                    {response.provenance_summary.generation_option != null ? (
                      <>
                        <dt className="font-medium">Mode</dt>
                        <dd>{response.provenance_summary.generation_option}</dd>
                      </>
                    ) : null}
                    {response.provenance_summary.node_count != null ? (
                      <>
                        <dt className="font-medium">Nodes</dt>
                        <dd>{response.provenance_summary.node_count}</dd>
                      </>
                    ) : null}
                    {response.provenance_summary.edge_count != null ? (
                      <>
                        <dt className="font-medium">Edges</dt>
                        <dd>{response.provenance_summary.edge_count}</dd>
                      </>
                    ) : null}
                    {response.provenance_summary.request_hash != null ? (
                      <>
                        <dt className="font-medium">Request hash</dt>
                        <dd className="truncate font-mono">
                          {response.provenance_summary.request_hash}
                        </dd>
                      </>
                    ) : null}
                    {response.provenance_summary.graph_hash != null ? (
                      <>
                        <dt className="font-medium">Graph hash</dt>
                        <dd className="truncate font-mono">
                          {response.provenance_summary.graph_hash}
                        </dd>
                      </>
                    ) : null}
                  </dl>
                </div>
              ) : null}

              {/* 7. warnings panel — flat list (all categories; generated_content
                   also surfaces separately in GeneratedWarningsPanel below) */}
              {response.warnings.length > 0 ? (
                <ul
                  data-testid="learn-warnings"
                  className="list-disc space-y-1 rounded border border-amber-200 bg-amber-50 p-3 pl-7 text-sm text-amber-800"
                >
                  {response.warnings.map((w, index) => (
                    <li key={`${w.code}-${index}`}>
                      [{w.severity}] {w.message}
                      {w.source_ref ? ` — ${w.source_ref}` : ""}
                    </li>
                  ))}
                </ul>
              ) : null}

              {/* Preview panel: generated-section warnings
                   Surfaces generated_content-category warnings distinctly. */}
              <GeneratedWarningsPanel warnings={response.warnings} />

              {/* 8. export artifact list */}
              <div>
                <div className="mb-1 text-xs uppercase tracking-wide text-muted-foreground">
                  Artifacts
                </div>
                <ExportArtifactList
                  artifactId={response.artifact_id}
                  exports={response.exports}
                />
              </div>

              {/* 11. Open export folder.
                   Uses Tauri opener plugin (tauri-plugin-opener / openPath);
                   degrades gracefully (disabled + hint) outside the desktop app. */}
              <OpenFolderButton folderPath={response.cache_dir} />

              {/* Preview panel: notebook metadata */}
              <NotebookMetaPanel exports={response.exports} />

              {/* Preview panel: assessment public prompts presence
                   Presence flags only — rubric/answer-key content never shown. */}
              <AssessmentPresencePanel exports={response.exports} />

              {/* Preview panel: raw provenance/packet JSON
                   JSON.stringify into <pre> — zero HTML injection risk. */}
              <ProvenanceJsonPanel response={response} />

              {/* 6. Markdown preview (kept last for scroll/readability) */}
              <div
                data-testid="learn-preview"
                className="rounded border border-border p-4"
              >
                <MarkdownPreview content={response.markdown} />
              </div>
            </>
          ) : (
            <div className="flex h-full items-center justify-center rounded border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
              Compile a learning source to preview the result.
            </div>
          )}
        </section>
      </div>

      {/* 10. Session artifact history.
           Accumulates each successful compile in React state.
           SESSION-SCOPED ONLY — no backend history endpoint exists (routes/learn.py
           has no /history or /list route). A future backend listing endpoint would
           be the right place to persist cross-session history. */}
      <ArtifactHistoryPanel entries={artifactHistory} />
    </main>
  );
}
