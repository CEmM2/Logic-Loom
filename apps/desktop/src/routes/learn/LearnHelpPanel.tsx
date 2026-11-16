/**
 * Learn help / documentation panel.
 *
 * A collapsible <details> panel explaining source provenance, what the provenance
 * fields mean, where artifacts live, and the generated-content policy.
 *
 * Content is static and accurate to the actual provenance fields from
 * `LearnProvenanceSummary` (api/learn.ts) and the compile/cache model
 * (routes/learn.py, adapters/learn_adapter.py).
 *
 * Fields documented (mirrors LearnProvenanceSummary):
 *   - request_hash       — SHA256 of the normalised compile request fields
 *   - graph_hash         — SHA256 of the graph slice (nodes + edges)
 *   - topic              — learning topic string from the compile request
 *   - generation_option  — which generation mode was used
 *   - node_count         — number of graph nodes in the learning slice
 *   - edge_count         — number of graph edges in the learning slice
 *
 * Artifact/cache model:
 *   - Artifacts live under `<cache_path>/learning/{artifact_id}/`
 *     (default: `~/.cache/logic-loom/learning/{artifact_id}/`).
 *   - artifact_id = sha256(f"{request_hash}:{graph_hash}")[:32] — deterministic,
 *     so the same request + graph slice always produces the same id (cache-hit).
 *     (backend: routes/learn.py `_derive_artifact_id`.)
 *   - The packet JSON (`{request_hash}.json`) holds the full provenance record.
 *   - Artifacts are NEVER written into the AKMS repo or global vault.
 *
 * Generated-content policy:
 *   - When `generation_option == "llm_expanded"`, the response carries
 *     `generated_content: true` in export metadata and a `generated_content`-
 *     category warning — review LLM-generated sections before use.
 */

import { useState } from "react";

export function LearnHelpPanel() {
  const [open, setOpen] = useState(false);

  return (
    <section data-testid="learn-help-panel" className="rounded border border-border">
      <button
        type="button"
        data-testid="learn-help-toggle"
        aria-expanded={open}
        onClick={() => setOpen((prev) => !prev)}
        className="flex w-full items-center justify-between px-4 py-2 text-left text-sm font-medium hover:bg-muted/40"
      >
        <span>About source provenance &amp; artifacts</span>
        <span
          data-testid="learn-help-chevron"
          className={`text-muted-foreground transition-transform ${open ? "rotate-180" : ""}`}
          aria-hidden
        >
          ▾
        </span>
      </button>

      {open ? (
        <div
          data-testid="learn-help-content"
          className="space-y-4 px-4 pb-4 pt-2 text-sm text-muted-foreground"
        >
          {/* ── How the learning source is generated ─────────────────────── */}
          <div>
            <h3 className="mb-1 font-semibold text-foreground">
              How the learning source is generated
            </h3>
            <p>
              When you compile, Logic-Loom slices the AKMS knowledge graph around the
              selected node (or the explicit graph slice you provide), then passes that
              slice to the <strong>akms-learn</strong> plugin together with your topic,
              goal, and generation mode. The plugin assembles a{" "}
              <em>learning source packet</em> — a structured Markdown document with
              optional notebook, HTML, and assessment exports — and writes everything
              under the artifact cache directory.
            </p>
          </div>

          {/* ── Provenance fields ─────────────────────────────────────────── */}
          <div>
            <h3 className="mb-1 font-semibold text-foreground">Provenance fields</h3>
            <dl
              data-testid="learn-help-provenance-fields"
              className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs"
            >
              <dt className="font-medium text-foreground">request_hash</dt>
              <dd>
                SHA-256 of the normalised compile request (topic, goal, mode, seed tags,
                exporters). The same request always hashes identically — used for
                cache-hit detection.
              </dd>

              <dt className="font-medium text-foreground">graph_hash</dt>
              <dd>
                SHA-256 of the graph slice (node ids + edge triples). Changes whenever
                the underlying graph is updated, so a stale cache is never silently
                reused for a modified graph.
              </dd>

              <dt className="font-medium text-foreground">topic</dt>
              <dd>The learning topic string from the compile request.</dd>

              <dt className="font-medium text-foreground">artifact_id</dt>
              <dd>
                Deterministic id derived from{" "}
                <span className="font-mono">
                  sha256(&quot;{"{request_hash}"}:{"{graph_hash}"}&quot;)[:32]
                </span>
                . Identifies the artifact directory on disk.
              </dd>

              <dt className="font-medium text-foreground">node_count</dt>
              <dd>Number of graph nodes in the learning slice.</dd>

              <dt className="font-medium text-foreground">edge_count</dt>
              <dd>Number of graph edges in the learning slice.</dd>

              <dt className="font-medium text-foreground">generation_option</dt>
              <dd>
                The generation mode used (e.g.{" "}
                <span className="font-mono">deterministic_outline</span>,{" "}
                <span className="font-mono">node_anthology</span>,{" "}
                <span className="font-mono">llm_expanded</span>). Available modes come
                from <span className="font-mono">GET /api/learn/profiles</span>.
              </dd>
            </dl>
          </div>

          {/* ── Where artifacts live ─────────────────────────────────────── */}
          <div>
            <h3 className="mb-1 font-semibold text-foreground">Where artifacts live</h3>
            <p>
              All artifacts are written under the Logic-Loom cache directory:{" "}
              <span className="font-mono">
                ~/.cache/logic-loom/learning/{"{"}<em>artifact_id</em>
                {"}"}
                /
              </span>{" "}
              (the exact path is shown in the <em>Packet / Provenance JSON</em> panel
              as <span className="font-mono">cache_dir</span>). You can open this folder
              directly with the <strong>Open export folder</strong> button (desktop app
              only).
            </p>
            <p className="mt-1">
              Artifacts are <strong>never</strong> written into the AKMS repository, the
              global vault, or any source tree — they live in the cache only.
            </p>
          </div>

          {/* ── Generated-content policy ────────────────────────────────── */}
          <div>
            <h3 className="mb-1 font-semibold text-foreground">
              Generated content — review before use
            </h3>
            <p>
              When the <span className="font-mono">llm_expanded</span> generation mode is
              selected, some sections are written by an LLM. These are marked in two
              places:
            </p>
            <ul className="mt-1 list-disc space-y-1 pl-4">
              <li>
                Export metadata:{" "}
                <span className="font-mono">generated_content: true</span> on the
                relevant artifact entry (visible in the Notebook Metadata panel for{" "}
                <span className="font-mono">.ipynb</span> exports).
              </li>
              <li>
                A <span className="font-mono">generated_content</span>-category warning
                in the <em>Generated Content Warnings</em> panel — review these sections
                before distributing the artifact.
              </li>
            </ul>
            <p className="mt-1">
              All other generation modes (
              <span className="font-mono">deterministic_outline</span>,{" "}
              <span className="font-mono">node_anthology</span>, etc.) produce content
              deterministically from the graph — no LLM generation.
            </p>
          </div>

          {/* ── Session history note ────────────────────────────────────── */}
          <div>
            <h3 className="mb-1 font-semibold text-foreground">Session history</h3>
            <p>
              The <em>Session history</em> panel above lists every artifact compiled
              during the current session. It is held in React state only — it is not
              persisted across page reloads or app restarts, because no backend history
              listing endpoint exists.
            </p>
          </div>
        </div>
      ) : null}
    </section>
  );
}
