# Changelog

> Detailed notes for every release from `v0.1.0-alpha.2` on are in
> [`docs/release_notes/`](./docs/release_notes/) and on the
> [documentation site](https://cemm2.github.io/Logic-Loom/); the entries below
> summarise them.

## v0.3.3

The rest of the v0.3.1 QA pass. Full notes:
[`docs/release_notes/v0.3.3.md`](./docs/release_notes/v0.3.3.md).

- **No dead ends:** mode setup messages keep the mode switcher and side menu,
  with a Copy snippet action for Failure Memory and Records; related papers,
  batch run history, View in Graph and zsum Collections lead somewhere.
- **The Picker lists every batch** (app-created and starter batches were
  missing) and picks up changes without a restart.
- **Fixed:** vault preview math and nested frontmatter, prompt Preview (always
  empty), graph review message, starter zsum config, hidden vault entries.
- **Clearer messages:** Discover provider failures, provider test advice,
  Zotero not connected, Open in Editor result, MechDSL status.
- **API:** Discover input validation and `provider_errors`, picker routes in
  OpenAPI, graph refresh stream is POST, UTC graph status, zsum test errors,
  learn export stub removed.
- **Polish:** app title and icon, Linux package description, Logo switch for
  the backdrop logo, dark-mode contrast, Ctrl-K fixes.

## v0.3.2

Security and reliability fixes from the v0.3.1 QA pass. Full notes:
[`docs/release_notes/v0.3.2.md`](./docs/release_notes/v0.3.2.md).

- **Local API requires a per-launch token** and a loopback `Host`, closing
  access by other local programs and by web pages through DNS rebinding.
- **Hardened inputs:** prompt and workflow ids can't escape the library, node
  file read/open is limited to the node and vault folders, saved API keys are
  masked, command settings must name a real program, and error responses carry
  no tracebacks.
- **No orphaned sidecars:** the sidecar exits when the app dies for any reason;
  Windows kills the whole process tree; a second launch focuses the first window.
- **Windows sidecar serves again:** in v0.3.1 it crashed right after starting
  (uvicorn rebuilt the listening socket as a Unix socket).
- **Docs:** install links point at the installer releases; checksum steps per OS.

## v0.3.1

Linux packages that run on current distributions. Full notes:
[`docs/release_notes/v0.3.1.md`](./docs/release_notes/v0.3.1.md).

- **Linux builds need glibc 2.35, not 2.43.** v0.3.0's AppImage and `.deb`
  were built on the newest runner and did not start on Ubuntu 24.04 or
  Debian 13. They are now built on Ubuntu 22.04, and the release build fails
  if a shipped binary needs a newer glibc or the AppImage lacks its bundled
  WebKitGTK.
- **Docs:** Linux requirements, AppImage vs `.deb`, and running from source
  in a browser without WebKitGTK.

## v0.3.0

The first public-source release. Full notes:
[`docs/release_notes/v0.3.0.md`](./docs/release_notes/v0.3.0.md). It also carries
everything prepared as `v0.2.1`, which was never tagged.

- **Records mode**: read-only browse of explicitly configured akms-records
  stores, their schemas and their record graph.
- **No source checkouts.** AKMS, MechDSL and zotero-summarizer are installed
  from PyPI; the picker, nodes, batches and graph work without an AKMS checkout.

- **The packaged app supports all of AKMS 0.4.** The sidecar build installs the
  dependency set `uv.lock` records and bundles every optional mode: the AKMS
  tabs and Learn, the MechDSL emit bridge, Failure Memory and the Records
  browser. Earlier builds pinned AKMS below 0.4 and left Failure Memory and
  Records out of the frozen binary.
- **No vendored source trees.** The bundled agent assets and the zsum prompts are
  read from the installed `akms` and `zotero-summarizer` packages, and the old
  vendored `thirdparty/` snapshots are deleted from the repository. `LOGIC_LOOM_SIDECAR_CORE_ONLY=1` builds a zsum-only
  sidecar.
- **Every version field agrees** (`scripts/check_versions.py`; the API package
  and `tauri.conf.json` had drifted), and the release workflow refuses a tag
  that does not match them or has no changelog section.
- **Security upgrades in the lockfile.** The new `uv audit` gate reported 30
  advisories in nine transitive packages; all are cleared by upgrading anyio,
  cryptography, filelock (with litellm, which capped it), fsspec, multidict,
  pydantic-settings, pypdf, starlette and urllib3. No AKMS, MechDSL or
  zotero-summarizer pin moved.
- **Documentation is a website** at <https://cemm2.github.io/Logic-Loom/>, built
  from `docs/` and published on each release tag. It replaces the HTML design
  guides that used to live in `docs/` and the docs pages in the
  `CEmM2/Logic-Loom-releases` repository. Release notes live in
  `docs/release_notes/` and are part of the site.
- **Repository hygiene for a public tree:** `LICENSE` (MIT), `SECURITY.md`,
  `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SUPPORT.md`, a pull-request template,
  an always-on CI workflow, a security workflow (dependency review, lockfile
  audits, CodeQL), Dependabot, read-only workflow tokens, and
  `scripts/check_public_tree.py`, which fails on private working folders,
  private repository references, real home-directory paths and secrets.
- **Third-party notices.** `THIRD_PARTY_NOTICES.md`, generated by
  `scripts/gen_third_party_notices.py`, lists every bundled Python, JavaScript
  and Rust package with their licenses and license texts, and ships inside the
  installers with `LICENSE`. The release refuses a tag while it is out of date
  with the lockfiles.

## v0.2.1

Patch release; no Logic-Loom feature changes. Refreshes the vendored AKMS
toolchain (`7e07ace` → `f32d81f`) and moves the `akms` / `akms-failure-memory`
git pins (`72494ac` → `f32d81f`), so the bundled agent assets and the
failure-memory package are current for the first time since `v0.1.0-alpha.7`.

Newly bundled agent assets, now sourced from AKMS's published root paths rather
than its internal `.claude/` tree: `skills/akms`, `skills/deepwiki-nodes`,
`skills/akms-spec-check`, `hooks/` (vault read-only + frozen-schema guards), and
`docs/akms/spec/invariants.md`. Existing assets refreshed — which picks up an
upstream fix to `akms-spec-reviewer` / `akms-spec-check`, both of which had been
reading a `design_docs` path that does not exist.

`akms-failure-memory` 0.1.0 → 0.3.0: preflight no longer enforces artifact
identity on every operation, and the provider fingerprint no longer tracks AKMS
source bytes.

Known issue unchanged from `v0.2.0`: `Backend E2E (windows-latest)` exits 1 after
the suite passes, from non-daemon `ThreadPoolExecutor` workers at interpreter
shutdown (lesson `L001`). No test fails; product behaviour unaffected.

Full notes: [`docs/release_notes/v0.2.1.md`](./docs/release_notes/v0.2.1.md)

## v0.2.0

Logic-Loom graduates out of alpha. Adds the **Failure Memory workspace** — a
read-only surface for inspecting `.failure-memory` lesson registries, plus an
off-by-default operator mode for recording and compiling them — and fixes
AKMS node-extraction batch runs hanging in the packaged app. Known issue:
backend E2E on `windows-latest` exits 1 after all tests pass (tracked in this
repo's own failure-memory ledger). Full detail in
[`docs/release_notes/v0.2.0.md`](./docs/release_notes/v0.2.0.md).

## v0.1.0-alpha.4 — 2026-07-01

### Learn — host LLM provider chooser (AKMS ▸ Settings)

Unblocks the LLM-gated Learn modes (`adaptive_path`, `llm_expanded`) with a
provider chooser in **AKMS ▸ Settings**, built on `akms-learn`'s public provider
registry (no upstream change):

- Pick the **API** route (LiteLLM, keyed by `anthropic_api_key` /
  `openai_api_key` / `google_api_key`) or an **agent-CLI** route — `claude -p`,
  `codex exec`, or a user-defined custom command (`{prompt}` arg-substituted,
  else piped to stdin). `nlm` grounded generation stays; `copilot` /
  `antigravity` ship as empty user-fillable command slots.
- CLI agents activate **only on an explicit choice** (never on mere PATH
  presence), so the no-provider gate is unchanged. Agent stdout is normalized (a
  single enclosing markdown code-fence is unwrapped) before it becomes the
  generated section body.
- Saved keys / CLIs go live in-process via a **Reload backend** action — no app
  restart (`POST /api/learn/providers/reload`). Choosing a not-yet-ready
  provider surfaces a warning naming exactly what to fill or install.
- New read-only discovery endpoint `GET /api/learn/providers`.

### MechDSL — in-app Taichi install (verify backend)

The MechDSL Overview gains an **Install taichi 1.7.4** action: the packaged
sidecar ships the emit bridge Taichi-free, and the pinned wheel is side-installed
at runtime into an ABI-namespaced dir added to the sidecar's `sys.path`, so
`verify` is unblocked without re-freezing. `emit`/`transpile` keep working with
no Taichi.

## v0.1.0-alpha.3 — Research Assistant (Learn), LLM providers, MechDSL bridge

Third alpha. Adds the **Learn** workflow to the Research Assistant, activates
**LLM-backed** learning modes through a host-provided provider, and ships the
**MechDSL executable bridge** — LaTeX → FEM-solver-code generation, bundled
Taichi-free. Also pulls a large `zotero-summarizer` upgrade (see below).

### AKMS backend re-vendor (2026-06-17 — alpha.3 re-cut)

The vendored AKMS stack was advanced to upstream `main` (`017996ec`) and the
alpha.3 binaries rebuilt from it. No UI changes — the AKMS generation backend is
overhauled under the hood:

- **`akms-nodes-gen` dropped its headless-browser dependency.** It no longer
  hard-depends on `notebooklm-py[browser]`/Playwright; node generation is now
  LLM-provider-pluggable through **LiteLLM** (an optional `[llm]` extra) and
  CLI-grounded (the `nlm` CLI / an Anthropic MCP-server URL). This removes the
  heavy browser stack from the sidecar and **fixes the long-standing
  `uv sync --extra akms` install hang** — the full `akms` extra (Picker / Nodes /
  Graph) now installs cleanly.
- **`akms` core gained CLI/agent backends** — `claude -p`, `codex exec`, and a
  local OpenAI-compatible backend, selectable via `--backend` (pulls
  `litellm` + `openai-agents` + `claude-agent-sdk` + `opentelemetry-*` into the
  bundled sidecar in place of the browser stack).
- **`akms-learn` baked `nbformat`/`jinja2` into its base dependencies** (the
  `[notebook]`/`[html]` extras are now no-ops) and auto-selects an LLM provider.
- Added `scripts/install_nlm.sh` to provision the NotebookLM (`nlm`) CLI.
- **MechDSL** vendored packages (`mechdsl-core` + `algo2code`) bumped to `0.2.0`.

### Research Assistant — Learn tab (AKMS-Learn)

A new **Learn** tab compiles AKMS knowledge into learning artifacts via the
vendored `akms-learn` engine. The UI is fully capability-driven — modes and
exporters come from the backend (`/api/learn/profiles`, `/exporters`) and
unavailable/planned modes render disabled rather than failing on submit.

- Backend `/api/learn/*` surface (status, profiles, exporters, compile,
  artifacts) with per-mode request validation and a capability gate.
- Pass B: mode-specific forms, preview panels (notebook / assessment /
  provenance-JSON / generated-warnings), artifact history with
  open-export-folder, and a domain-pack status card (source-pack table +
  capability badges, payload-driven).
- "Send to Learn" handoff from the Graph view.
- Structured generation warnings (generated-content / skipped-prereq /
  missing-dep) and a Learning Source Review Bundle export.
- Compile artifacts cache under `learning/<id>` with a global-vault write guard;
  LF-canonical artifact text for cross-platform reproducibility.

### LLM provider activation

Learning modes that need an LLM (e.g. `llm_expanded`) now activate when the
**host** supplies a provider — no separate key wiring in the engine.

- The host-resolved LLM provider is injected into `akms-learn` `LearningRequest`s
  at compile time; capability gates and `/profiles` reflect real host
  availability so a provider-less host degrades gracefully instead of erroring.

### MechDSL executable bridge (Tier 3)

New **MechDSL** mode (`/api/mechdsl/*` + Overview / Compile / Artifacts UI,
behind `MechdslGate`): compile `% mechanics` LaTeX problems into FEM solver
code and transpile algorithm boxes, via `compmech-reference-pack` + `mechdsl-core`.

- **Bundled Taichi-free.** The emit/transpile/capabilities surface needs no
  Taichi runtime (it lives in `mechdsl-core`'s optional `[verify]` extra, not
  installed); only `run_verify=True` would, and it is gated off. Keeps the
  sidecar ~170 MB lighter — `/api/mechdsl/status` reports `available: true` with
  `taichi_required_for: ["verify"]`.
- `akms-learn` is now bundled in the sidecar (compmech's dependency), so the
  Learn modes above ship in the packaged app.

### Vendored dependencies — MechDSL (new)

`mechdsl-core` + `algo2code` are vendored under `thirdparty/MechDSL/` as a
partial source mirror (refresh with `scripts/sync_mechdsl_vendor.sh`).

### Vendored dependencies — zotero-summarizer

The bundled zotero-summarizer has been updated with a large upstream merge
(`thirdparty/zotero-summarizer` via `git subtree pull --squash`). All items
below are available in the `zsum` CLI bundled with Logic-Loom; Pipeline UI
integration for starred items is in progress.

#### New: NotebookLM pipeline (`zsum nlm`) ★

Full integration of NotebookLM as the LLM backend for card and digest
generation. Activate with `extraction.mode: notebooklm` in `config.yaml`.

- `zsum nlm upload|cards|digests|all|status|cleanup` command group
- Parent collections use direct-member PDFs + child digest `.md` files
  as notebook sources (max 30 sources per notebook, configurable)
- Automatic fallback to page-range extraction for NLM-failed papers, with
  `extraction_method: fallback_pages` provenance threaded through cards,
  cache, publish, and the Obsidian plugin (amber indicator + tooltip)
- `notebooklm.parse_locally` option pipes the NLM answer through a local
  LLM for JSON normalization when NotebookLM's raw response is unparseable
- Cache manifests: `cache/nlm_notebooks.json`, `cache/nlm_digests.json`,
  `cache/nlm_redigests.json`

#### New: Section-aware PDF extraction ★

`extraction.mode: sections` uses TOC boundaries and fuzzy heading matching
(via `rapidfuzz`) to extract the conceptually relevant portion of each PDF
rather than fixed page ranges. Falls back to `pages` mode when fewer than
two target sections are found. Three modes now available:

| Mode | Behaviour |
|---|---|
| `pages` (default) | First N + last M pages of each PDF |
| `sections` | TOC/heading-detected sections with fuzzy matching |
| `notebooklm` | Delegates entirely to NLM; page-range fallback for failures |

#### New: Enrichment commands ★

Three standalone analysis commands that augment an existing vault without
a full pipeline rerun:

- `zsum citations` — citation counts from OpenAlex + Semantic Scholar;
  cached with configurable staleness; surfaces in paper frontmatter and
  per-collection `## Most Cited Papers` section
- `zsum reviews` — heuristic review/survey/meta-analysis/tutorial
  classifier; renders in collection notes and sets `paper_type` frontmatter
- `zsum restructure` — single-LLM-call advisory on collection hierarchy;
  proposes renames, splits, merges; cached to `cache/restructure_report.md`

#### New: LLM providers

- `codex_exec` — Codex CLI backend (`codex exec`) with `--output-schema`
  for schema-constrained digest generation
- `gemini_cli` — Gemini CLI (`gemini -p`) backend
- `notebooklm` — see NotebookLM pipeline above

#### New: Setup command ★

- `zsum setup-model` — one-command Ollama configuration: checks daemon,
  pulls a model by size tier (small/medium/large/xlarge defined in an
  external `model_tiers.yaml`), writes `config.yaml`, verifies the model
  responds. Flags: `--size`, `--model`, `--list`, `--dry-run`.

### Vendored dependencies — AKMS

#### New: AKMS Learn ★ (specced, API + UI in progress)

`akms-learn` is vendored alongside `akms` core. It is a separate learner-
facing projection layer over the AKMS graph: given a graph slice and a
learning request, it compiles a **Learning Source Packet (LSP)** — a
reproducible YAML artifact with reading order, per-node section extraction,
pitfalls, assessment items, code links, and a validation block. Twelve
generation modes are defined (deterministic outline through adaptive paths);
modes 1 and 12 are implemented in v0.1. Logic-Loom API routes (`/api/learn/*`)
and the Learn tab are in progress.

**Pass B — backend (Plan 4 Phase 4).** The `/api/learn/*` surface now describes
its own capabilities so the UI can be driven entirely from backend status. All
additions are **consumer-only** (the backend reads `akms_learn`'s catalog and
descriptor data; it never reimplements compiler logic) and **additive** (Pass-A
response shapes are unchanged):

- `GET /api/learn/profiles` and `/exporters` now return the full catalogue,
  each entry `{id, label, status}` with availability (`available` /
  `unavailable` / `planned`) derived from `capabilities_with_status()` and
  optional-dependency state — never a hard-coded list.
- `POST /api/learn/compile` validates the requested generation mode up front:
  unknown mode → `422 unknown_mode`, unavailable mode → `412
  capability_unavailable`, missing `topic`/`goal` → `422
  compile_precondition_error`. Structured envelopes only — never a 500.
- Compile responses carry per-family **artifact metadata** (notebook
  cell-count/executed, HTML static/size, assessment public/answer-key presence)
  and **structured warning categories** (generated-content, skipped-prerequisite,
  missing-optional-dependency, source-quality).
- `GET /api/learn/status` now includes a `domain_packs[]` array (domain/source
  pack ids, capabilities, and per-source-pack role + status), sourced from
  domain-pack descriptor YAML — with no companion-package import and no
  hard-coded domain concepts.
- `GET /api/learn/status` also includes a `generation_modes[]` array — the
  filtered, user-selectable modes the compile gate accepts (exporters,
  execution-backend adapters, and infra capabilities such as
  `learning_source_packet` excluded). The mode selector derives from this, so
  the default selection can no longer POST an infra capability and be rejected
  `422 unknown_mode`.
- Compile/export remain **synchronous** (measured sub-100 ms); background-run
  support is deferred with a documented re-evaluation trigger.

Backend test infrastructure: pytest now uses `--import-mode=importlib`, and a
session fixture materialises the bundled AKMS Claude assets when the vendored
source is present, so the full `pytest services/logic_loom_api/tests` suite runs
green on a fresh checkout.

### Release pipeline

- New `scripts/release.sh` — single-script entrypoint that picks up CI-built
  binaries from the latest successful Release workflow run (or a pinned
  `--run-id`), normalizes filenames into the canonical
  `Logic-Loom-<ver>-<platform>-<arch>.<ext>` scheme, stages them under the
  public release repo, regenerates `SHA256SUMS.txt` + per-file
  `.sha256.txt`, copies release notes into place, commits + pushes the
  release repo, and creates (or, with `--replace`, recreates) the GitHub
  Release with all assets attached. Auto-detects prerelease from
  `-alpha`/`-beta`/`-rc` suffixes. See `README.md` → "Releases".
- Added `--download-artifacts yes|no` flag to skip re-downloading CI
  artifacts when iterating on a failed release attempt. The 500 MB download
  is cached under `.tmp/release-<tag>-<runid>/` and reused on the next run.
- README.md and CHANGELOG.md are now shipped with every release: the
  script stages versioned copies (`Logic-Loom-<ver>-README.md`,
  `Logic-Loom-<ver>-CHANGELOG.md`) alongside the binaries, includes them
  in the checksum manifest, and attaches them to the GitHub Release.

### CI

- `release.yml` no longer relies on bash `globstar`, which the
  `macos-latest` runner's system bash 3.2 doesn't support. The
  artifact-staging glob now uses brace expansion + `nullglob`.

## 0.1.0-alpha.2 — 2026-05-19

Second multi-platform alpha. Full release notes:
[`docs/release_notes/v0.1.0-alpha.2.md`](docs/release_notes/v0.1.0-alpha.2.md).

### Three-mode shell

Logic-Loom is now a three-mode desktop app: **zsum**, **akms** (gated), and **assistant**. Each mode has its own sub-routes (`/zsum/*`, `/akms/*`, `/assistant/*`); legacy deep links (`/picker`, `/nodes`, `/graph`, `/pipeline`, `/vault`) are kept as redirects.

### Assistant mode

Brand-new subsystem for paper discovery + source batching + workflow runs, living under `services/logic_loom_api/src/logic_loom_api/assistant/` and `apps/desktop/src/routes/assistant/`.

- **Source batches** — `SourceBatch` model with nine §8.3 actions (create, edit, duplicate, archive, add/remove/reorder sources, send to AKMS) persisted to `<assistant_workspace>/batches.json`
- **Paper discovery** — Multi-provider clients (OpenAlex, Semantic Scholar) with normalised result merging, on-disk cache observability (`<assistant_workspace>/discovery_cache/`), and Zotero catalog matching
- **Related papers** — Five exploration modes (citations, references, similar, co-citation, co-reference) with ranking presets and a cache lifecycle
- **Workflow runs** — Five bundled Assistant workflows + prompt-slot resolution + dispatcher supporting both `claude` and `notebooklm` executors; reproducibility artefacts under `<assistant_workspace>/assistant/outputs/<batch_id>/<workflow_id>/<run_id>/`
- **Outputs** — Filtered run-output browser with four-tab detail view + rerun/comparison flows; `/api/assistant/outputs` is a filtered view over the shared run registry
- **HTTP surface** — Five route modules under `/api/assistant/*`: `batches`, `discover`, `workflows`, `outputs`, `index`. All mounted via the degrade-gracefully try/except pattern in `main.py`
- **Workspace bootstrap** — `bootstrap_assistant_workspace` runs in the FastAPI lifespan alongside the AKMS bootstrap; both wrapped in try/except so a workspace failure never blocks the sidecar coming up

### Workspace search (Cmd-K)

A SQLite FTS5-backed research-memory index plus an end-to-end Cmd-K modal.

- **Seven indexable artefact kinds** — `zotero`, `abstract`, `zsum_card`, `zsum_digest`, `assistant_output`, `akms_node`, `run_metadata` (closed set in `assistant/research_index/schema.py`)
- **HTTP endpoints**:
  - `POST /api/assistant/index/search` — full-text search
  - `POST /api/assistant/index/rebuild` — walk every artefact and re-ingest
  - `GET /api/assistant/index/status` — per-kind counts + `last_rebuild_at`
- **On-disk state** — `<assistant_workspace>/index/fts.sqlite` (records + FTS5 virtual table) plus `index.meta.json` so `last_rebuild_at` survives sidecar restarts
- **Frontend** — Cmd-K / Ctrl-K modal in `AppShell` with seven kind-filter chips, ranked result list, deep-link routing to the owning Assistant route, and an empty-state **Rebuild index** affordance
- **Auto-updates** — A shared `IndexLifecycle` singleton (per `Settings` instance) is wired into `SourceBatchService` and `RunnerService` so the index stays current across the four hot paths:
  - Batch create / edit fires `on_change` for every zotero source
  - Batch `remove_sources` fires `on_delete` for evicted sources + `on_change` for the remaining state
  - Batch `delete` fires `on_delete` for every zotero source on the deleted batch
  - Workflow runs fire `on_change` for `run_metadata` at start and for `assistant_output` on subprocess completion (via a new `on_done` callback on `RunRegistry.attach_workflow_subprocess`)
- **500&thinsp;ms debounce** — Rapid bulk writes collapse into a single FTS5 flush
- **Adapter robustness** — The `assistant_output` + `run_metadata` rebuild walkers prefer the global `RunRegistry` (canonical source of truth via `log_path.parent`) and fall back to the legacy filesystem walk so workspace layout changes don't break the rebuild path

### Effective-prompts filename unified

The shared `OutputStore` previously wrote `effective-prompts.json` (dash) while the Assistant runner wrote `effective_prompts.json` (underscore). Both writers now use the underscore form. The HTTP route stays at `/effective-prompts` for REST convention.

### Packaging and release workflow

- Added PyInstaller-based sidecar entrypoint at `packaging/pyinstaller/logic_loom_api_entry.py`
- Added platform-specific sidecar build scripts:
  - `scripts/build_sidecar_macos.sh`
  - `scripts/build_sidecar_linux.sh`
  - `scripts/build_sidecar_windows.ps1`
- Updated Tauri bundling to include `apps/desktop/src-tauri/binaries/*` as packaged resources
- Updated the Rust sidecar launcher to use `uv run ...` in debug builds and the bundled `logic-loom-api` executable in packaged builds
- Added `scripts/logic_loom_build.mjs` so `pnpm logic-loom:build` now auto-builds the sidecar for the current operating system before invoking the Tauri release build
- Added release-build artifact ignores for `.venv-sidecar-build/`, `.pyinstaller-build/`, `.pyinstaller-dist/`, `logic-loom-api.spec`, and `apps/desktop/src-tauri/binaries/`
- Verified locally on macOS:
  - PyInstaller sidecar build succeeded
  - direct sidecar launch + `/api/app/health` check succeeded
  - packaged app and `.dmg` build succeeded

### Vendoring

AKMS and zotero-summarizer are now vendored under `thirdparty/` via `git subtree --squash`. Path deps in `pyproject.toml [tool.uv.sources]` point at the vendored copies for both editable dev installs and reproducible packaged builds. The `Nodes_Vault` subtree lives in the vendored AKMS but is explicitly excluded from the wheel (Phase 12 `scripts/check_resources_no_vault.py` guard).

### Documentation

- New `services/logic_loom_api/src/logic_loom_api/assistant/README.md` — one-page module map, service dependency graph, on-disk shape, and known follow-ups for the Assistant subsystem
- New `CLAUDE.md` covers the three-mode shell, the full mounted-routes list (including the try/except-guarded routers), and the Assistant subsystem gotcha

## 0.2.0 — 2026-05-10

### Graph visualization

Logic-Loom v0.2 adds a read-only AKMS graph explorer backed by the AKMS core graph compiler and a Logic-Loom-owned cache.

Backend:

- New `/api/graph/*` namespace:
  - `GET /api/graph/status`
  - `POST /api/graph/refresh`
  - `GET /api/graph/nodes`
  - `GET /api/graph/edges`
  - `GET /api/graph/ego`
  - `GET /api/graph/cluster`
- New graph adapter compiles via `akms.graph.build_graph.build_graph()` and loads cached graphs via `load_graph()`
- Graph cache is owned by Logic-Loom (`cache_path/graph/graph.json`) rather than written into the AKMS repo
- Graph nodes are enriched with `file_path` metadata for inspector/editor integration

Frontend:

- New **Graph** tab guarded by AKMS graph readiness
- React Flow canvas with dagre layout
- Filtered graph, ego-graph, and cluster views
- Inspector with metadata, connected-edge summary, **Open in Nodes**, and **Open in Editor** actions
- Nodes review page can now jump directly into the Graph tab for the selected node

Configuration:

- Added `akms_global_vault` setting with fallback resolution:
  1. explicit config
  2. `<akms_repo>/Packages/Nodes_Vault`
  3. `AKMS_GLOBAL_VAULT`
  4. `~/.claude/akms/nodes`
- `GET /api/app/config` now exposes graph cache and effective AKMS graph-vault paths
- `GET /api/app/akms-status` now distinguishes picker availability from graph availability

### AKMS is now optional

Logic-Loom can run standalone with just zotero-summarizer. The `akms-nodes-gen`
integration moved behind `[project.optional-dependencies]`. The optional extra
now installs both `akms` and `akms-nodes-gen`.

Install with `uv sync --extra akms` to enable the Picker, Nodes, and Graph tabs.
Without it, those tabs show a clear availability placeholder instead of failing.

- `akms_repo` removed from first-run required fields
- New `GET /api/app/akms-status` endpoint reports package + repo availability
- Frontend `<AkmsGate>` wrapper on Picker, Nodes, and Graph routes

### NotebookLM integration

New `/api/nlm/*` route module with four task types, all running as background
subprocesses through the shared RunRegistry with SSE log streaming:

- **`POST /api/nlm/notebook`** — Create NotebookLM notebook and upload PDFs
  (wraps `prepare_notebook.py`)
- **`POST /api/nlm/generate`** — Generate nodes via Claude API with optional
  NLM MCP grounding (wraps `generate_nodes_pipeline.py`)
- **`POST /api/nlm/nlm-batch`** — Generate nodes directly via NotebookLM,
  no Claude tokens (wraps `akms_nodes_gen.nlm_batch`)
- **`POST /api/nlm/validate`** — Validate nodes against source notebooks
  (wraps `validate_nodes.py`)

Picker UI changes:

- **Generate (Claude)** and **Generate (NLM)** buttons in BatchDetailPane
- **Validate Nodes** button in BatchDetailPane
- Buttons unlock progressively after notebook creation
- `NlmBatchDialog` collects plan path, prompt file, and options before launch
- `NlmRunDialog` streams subprocess output in real time

### Config path fix

Fixed `getBatch()` not reading `notebook_id` from the AKMS assignment response,
which caused Generate/Validate buttons to stay disabled after notebook creation.

### Settings

- Added `nlm_mcp_url` setting for NotebookLM MCP server URL
- Added `akms_global_vault` setting for AKMS graph source resolution

## 0.1.0 — 2026-05-09

Initial release: zsum pipeline runner, vault browser, AKMS batch picker,
settings page, first-run setup dialog.
