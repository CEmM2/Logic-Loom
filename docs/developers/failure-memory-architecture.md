# Architecture digest — Failure Memory workspace (Logic-Loom)

_Source: feature diff `git diff --name-status becbe17..HEAD` (fork point → `d41c4b0`) · generated 2026-08-17_

Logic-Loom is a **consumer** of the `akms-failure-memory` package — read-only inspection plus an explicit,
config-gated operator surface. The package remains the sole recorder, compiler and provider; Logic-Loom
never writes the lesson registry directly. 49 files changed (33 new, 16 modified), +12839/-30 lines
(`git diff --stat becbe17..HEAD`).

## Modules

### Backend — `services/logic_loom_api/src/logic_loom_api/`
- **Read-only adapter** (`adapters/failure_memory.py`, 622 lines) — sole import boundary to `akms_failure_memory`'s public API for reads; calls `provider.resolve_provider` with a literal `write_artifacts=False` (`:465`) and `run_compiler` only in `validate`/`check` mode. Public: `load_project`, `ResolvedProject`, `resolve_preview`, `publication_problems`, `route_index`, `graph_metadata`, `bounded_project_lock`. [new]
- **Operator adapter** (`adapters/failure_memory_operator.py`, 157 lines) — the only write-capable module; `record_lesson` (`:59`) → package `record.add_lesson`, `compile_project` (`:103`) → `run_compiler(mode="write")` (`:116`), `refresh_project` (`:122`) → `refresh.refresh_project`. Not imported by any read-only module (absent from `AUDITED` in `test_failure_memory_readonly_audit.py:52-56`). [new]
- **Presentation service** (`services/failure_memory.py`, 1264 lines) — builds every read-side response model: `build_projects`, `build_status`, `build_lessons`, `build_lesson_detail`, `build_overlay`, `build_routes`, `build_resolve`, `build_check`, `derive_generated_at`. Imports the read-only adapter only. [new]
- **Currency service** (`services/failure_memory_currency.py`, 606 lines) — **single owner** of the currency verdict; `resolve_currency` (`:478`) is the only place a `current` result is produced, with every signal a required keyword argument. Owns `write_history`, `verify_graph_content` (`:286`, routed-node containment + zero-node/empty distinction + `generated_at`-vs-derived-instant, all local), `verify_generated_coverage`. Delegates published-graph agreement to the package via `adapter.publication_problems` (`:361`, wrapping `adapters/failure_memory.py:531` → `provider.validate_publication`). [new]
- **Operator service** (`services/failure_memory_operator.py`, 297 lines) — `submit_record`/`submit_compile`/`submit_refresh` (`:200,226,245`) wrap the operator adapter's calls as background jobs; imports both the read-only adapter (status reuse) and the operator adapter (writes) plus `failure_memory_currency` and `failure_memory_jobs`. [new]
- **Jobs registry** (`services/failure_memory_jobs.py`, 469 lines) — `JobRegistry` (`:151`): admission is atomic (capacity check + insert under one lock), terminal state comes only from the callable returning, `result`+`succeeded` publish together under one lock, retention only reaps terminal jobs at submission time. Journalled to disk, reloaded on restart. [new]
- **Routes** (`routes/failure_memory.py`, 448 lines) — 15 endpoints under `/api/failure-memory/*`; `_guard` (`:115`, disabled/package-absent), `_operator_guard` (`:128`, checked before project resolution or any write-capable import on every operator handler). Imports the read-only adapter, both services, and the jobs registry. [new]
- **Models** (`models/failure_memory.py`, 853 lines) — Pydantic schemas incl. `StatusResponse.currency` (`:201`), the public 5-state `Literal["current","stale","unverifiable","uncompiled","never_written"]` matching `CurrencyState` in the currency service; `RecordRequest` (`extra="forbid"`, no `id` field); `OperatorDisabledResponse`. [new]
- **Config** (`config.py`) — `FailureMemoryMode = Literal["disabled","read-only","operator"]` (`:169`), `FailureMemoryProject` (`:178`), `Settings.failure_memory_projects`/`failure_memory_operator()` (`:489,525`). [modified]
- **Main** (`main.py`) — mounts `failure_memory_routes.router` at `/api/failure-memory` (`:374-379`), wrapped in `try/except` so a mount failure degrades gracefully. [modified]
- **App routes** (`routes/app.py`) — `GET /api/app/config` exposes `failure_memory_mode`/`failure_memory_projects` read-only; deliberately absent from `ConfigUpdate` (`:95-107`). [modified]

### Frontend — `apps/desktop/src/`
- **API client** (`api/failureMemory.ts`, 564 lines) — typed fetch wrappers, one per backend route (`getFailureMemoryProjects`…`refreshProject`, `:381-563`); ships `DegradationEnvelope` type for the 503 absent-package case. [new]
- **Hooks** (`hooks/failureMemory.ts`, 224 lines) — react-query wrappers, incl. `useTrackedWriteJob` (`:189`, polls a 202 job to terminal state and invalidates status/lessons/routes/jobs). [new]
- **FailureMemoryGate** (`components/FailureMemoryGate.tsx`, 95 lines) — gates the whole `/failure-memory/*` subtree on `GET /projects`; distinguishes disabled / package-not-installed / no-projects-configured, each with a different fix. [new]
- **WorkspaceFrame** (`components/failure-memory/WorkspaceFrame.tsx`, 72 lines) — shared chrome: title, `ProjectSelector`, and a `ModeBadge` sourced from the backend's own `mode`/`operator` fields, not a local assumption. [new]
- **ProjectSelector, DeliveryBanner, JobPanel, OverlayCanvas, RecordForm** (`components/failure-memory/*.tsx`) — project picker; the "delivered_nowhere vs no_lessons_apply" distinction banner; non-final `queued`/`running` vs terminal `succeeded`/`failed` job status; the failure-memory graph overlay canvas (own React Flow instance, not merged into the AKMS graph view); the operator recording form (closed key set, no `id` field). [new, +2 test files: `DeliveryBanner.test.tsx`, `JobPanel.test.tsx`]
- **Route pages** (`routes/failure-memory/{overview,lessons,routes,preview,graph,operate}.tsx`) — status/staleness+diagnostics; lesson inventory with 5 filters; compiled route coverage table; resolve preview (`write_artifacts=false`) + delivery banner; graph overlay; operator record/compile/refresh (visible only when backend reports `operator: true`; also refused server-side). [new]
- **App.tsx** — registers `/failure-memory/*` under `FailureMemoryGate`, 6 lazy routes (`:179-201`). [modified]
- **SectionSideNav.tsx / TopModeNav.tsx** — adds the `failure-memory` top-level mode and a conditional "Record" sub-nav entry shown only when `operator: true`. [modified]

### Adoption config (pre-existing, unchanged by this diff)
- **`.failure-memory/config.toml`, `lessons.json`, `generated/routes.json`** — Logic-Loom's own project registration under `akms-failure-memory`; tracked in git, present before the fork point (`git diff becbe17..HEAD -- .failure-memory` is empty). The workspace reads this project like any other configured project. [stable]

### Verification / contracts (tests, grounding the invariants above)
- `services/logic_loom_api/tests/test_failure_memory_readonly_audit.py` (341 lines) — AST audit over 4 read-only modules: no filesystem write, no forbidden import root (`repo2md`, `akms`), only the adapter touches the package, `write_artifacts` must be a literal `False` (not a rebindable name), no module-level write toggle, compiler mode ⊆ `{validate, check}`, and no read-only module may even *reference* `add_lesson`/`record_lesson`/`refresh_project`/`compile_project`/`init_project`/`_publish_transaction` as identifiers. [new]
- `test_failure_memory_operator.py` (1724 lines) — the write-boundary/bypass audit for the operator module, plus job-terminal-state semantics, against a real project. [new]
- `test_failure_memory_currency_audit.py` (517), `test_failure_memory_conformance.py` (1175, checks against the shared consumer-contract pack, pinned digest `8efa2a0…`), `test_failure_memory.py` (357). [new]

## Edges

- `Desktop UI (routes+components+hooks)` → `api/failureMemory.ts` — typed fetch calls · one function per backend route, no logic in the wrapper (`api/failureMemory.ts:1-9`).
- `api/failureMemory.ts` → `routes/failure_memory.py` — HTTP GET/POST `/api/failure-memory/*` · 503 degradation envelope when package absent, 403 `operator_mode_required` on operator endpoints outside operator mode (`routes/failure_memory.py:128-152`).
- `routes/failure_memory.py` → `services/failure_memory.py` — read-side response building · `_guard` runs first (`:115-121`).
- `routes/failure_memory.py` → `services/failure_memory_operator.py` + `services/failure_memory_jobs.py` — writes as background jobs · `_operator_guard` runs before project resolution or any write-capable import (`:128-152`).
- `services/failure_memory.py` → `adapters/failure_memory.py` — all package reads · adapter is the only import site (`test_only_the_adapter_touches_the_package_at_all`, readonly_audit.py:163-176).
- `services/failure_memory.py` → `services/failure_memory_currency.py` — currency verdict forwarded, not re-derived (`services/failure_memory.py:297`).
- `services/failure_memory_operator.py` → `adapters/failure_memory_operator.py` — the only writes in the feature · literal `mode="write"` at the call site (`adapters/failure_memory_operator.py:116`).
- `services/failure_memory_operator.py` → `adapters/failure_memory.py` — status/diagnostics reuse (read side) (`services/failure_memory_operator.py:19`).
- `adapters/failure_memory.py` → `akms_failure_memory` package — public API only (`config`, `compiler`, `provider`, `refresh`, `errors`, `locks`); `write_artifacts=False` literal (`:465`); allowlist enforced by AST (`readonly_audit.py:97-113,196-215`).
- `adapters/failure_memory_operator.py` → `akms_failure_memory` package — `record.add_lesson`, `compiler.run_compiler(mode="write")`, `refresh.refresh_project`; nothing else writes (module docstring, `:11-19`).
- `akms_failure_memory` package → `.failure-memory/{lessons.json,generated/routes.json}` — sole recorder/compiler/provider; Logic-Loom never opens the registry for writing (adapter module docstring, `adapters/failure_memory.py:26-31`) — **uncertain**: the package-internal write mechanics are outside this repo and not directly verified here.

## Entry points

- `GET /api/failure-memory/projects` (`routes/failure_memory.py:161-168`) — always 200; the workspace-gate discovery call.
- `GET|POST /api/failure-memory/{project}/*` (`routes/failure_memory.py:170-433`) — per-project read/resolve/check endpoints, 503 on disabled or package-absent, 404 `unknown_project`.
- `POST /api/failure-memory/{project}/record|compile|refresh` (`routes/failure_memory.py:359-433`) — operator-only, 202 + `Job`.
- `/failure-memory/*` (`apps/desktop/src/App.tsx:179-201`) — desktop route tree, gated by `FailureMemoryGate`.

## Extension points

- `[[failure_memory_projects]]` in `config.toml` (`config.py:178-197,489`) — the only way a project becomes visible; no filesystem discovery/globbing.
- `failure_memory_mode` (`config.py:169`) — `disabled`/`read-only`/`operator`; not settable over HTTP (`routes/app.py:95-99`).
- `ALLOWED_PACKAGE_ATTRS` in `test_failure_memory_readonly_audit.py:97-113` — the enforced ceiling on what the read-only adapter may call; extending package usage requires updating this allowlist deliberately.

## Uncertain / unverified

- The prompt's framing that the operator adapter uses "its own literal `True`" does not match the source: the write capability is expressed as `mode="write"` (a string literal) at `adapters/failure_memory_operator.py:116`, mirroring the read side's `write_artifacts=False` (a bool literal) — same principle (literal-not-name), different literal shape. Corrected above.
- Internal write mechanics of `akms_failure_memory` itself (atomic registry replace, id allocation, generated-node compilation) are asserted by adapter docstrings and exercised by `test_failure_memory_operator.py` against a real install, but the package's own source is outside this repository and was not read directly for this snapshot.
