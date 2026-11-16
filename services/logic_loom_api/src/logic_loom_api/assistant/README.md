# `assistant/` — research-Assistant subsystem

The Assistant subsystem is a third mode (alongside zsum and AKMS) that helps users
discover papers, build source batches, run workflows, and search the workspace.

## Quick map

```
assistant/
├── bootstrap.py        # one-time workspace materialisation in FastAPI lifespan
├── services.py         # factory: get_assistant_services(settings) → record
├── models.py           # SourceBatch, BatchSourceItem (pydantic)
│
├── batches.py          # SourceBatchService — CRUD over <ws>/batches.json
├── discovery.py        # PaperDiscoveryService — OpenAlex + Semantic Scholar
├── zotero_match.py     # ZoteroMatchService — catalog matcher
├── related.py          # RelatedPaperService — 5 modes + ranking presets
├── workflows.py        # WorkflowService — filtered library view
├── runner.py           # RunnerService — claude + notebooklm dispatch
├── output_store.py     # OutputStoreService — filtered run-registry view
│
├── providers/          # provider clients (OpenAlex, Semantic Scholar) + cache
│
└── research_index/     # FTS5-backed workspace search
    ├── schema.py       # IndexableRecord + IndexKind (closed set of 7)
    ├── ingest.py       # one adapter per kind + ADAPTERS dispatch
    ├── fts.py          # FtsIndex — SQLite + FTS5 backend
    ├── lifecycle.py    # IndexLifecycle + get_lifecycle_for(settings)
    └── __init__.py     # ResearchIndexService facade
```

## Service dependency graph

```
                 get_assistant_services(settings)
                              │
       ┌─────────────┬────────┴─────────┬──────────────┐
       │             │                  │              │
   batches      discovery         workflows ─→ runner ─→ output_store
       │             │                  │              │
       │             ↓                  │              │
       │       providers/               │              │
       │             │                  │              │
       │             ↓                  │              │
       │       zotero_match             │              │
       │             │                  │              │
       │             ↓                  │              │
       │         related                │              │
       │                                │              │
       └────────────────────────────────┴──→ research_index
                                                  │
                                            (writes back on
                                            batch mutations
                                            via the post-write
                                            hook in services.py)
```

`research_index` shares one `IndexLifecycle` per `Settings` instance — registered
in `lifecycle.py:_LIFECYCLE_REGISTRY` and resolved through `get_lifecycle_for()`
by both the HTTP route module (`routes/assistant_index.py`) and the
`SourceBatchService` post-write hook (`services.py:_make_batch_index_hook`). This
shared instance owns the 500&thinsp;ms debounce buffer and the on-disk
`<ws>/index/index.meta.json` (last-rebuild timestamp).

## On-disk shape

```
<assistant_workspace>/
├── batches.json                    # SourceBatchService — schema_version + batches list
├── discovery_cache/                # PaperDiscoveryService — one JSON per query envelope
│   └── *.json
├── related_cache/                  # RelatedPaperService — cached related-paper responses
│   └── *.json
└── index/
    ├── fts.sqlite                  # FTS5 backend
    └── index.meta.json             # last_rebuild_at — survives sidecar restart
```

`<assistant_workspace>` defaults to a sibling of `<akms_workspace>` (named
`Assistant-Workspace`) or can be overridden via `assistant_workspace_path` /
`LOGIC_LOOM_ASSISTANT_WORKSPACE`. The two workspaces share a run registry under
`cache_path` (e.g. `~/.cache/logic-loom/runs/`); `/api/assistant/outputs` filters
that registry by `ASSISTANT_CATEGORIES`.

## HTTP routes (all `/api/assistant/*` unless noted)

| Path | Owner | Notes |
|---|---|---|
| `/batches/*` | `SourceBatchService` | 9 batch actions + `send-to-akms` |
| `/discover/*` | `PaperDiscoveryService` | + `/cache/stats`, `DELETE /cache` |
| `/related/*` | `RelatedPaperService` | 5 modes (citations, references, similar, …) |
| `/workflows` | `WorkflowService` | Filtered by `ASSISTANT_CATEGORIES` |
| `/outputs/*` | `OutputStoreService` | Filtered view of the shared run registry |
| `/index/rebuild` | `IndexLifecycle.rebuild` | Walks every artefact, re-ingests via the 7 adapters |
| `/index/status` | `IndexLifecycle.status` | Per-kind counts + `last_rebuild_at` (persisted) |
| `/index/search` | `IndexLifecycle.search` | FTS5 query; powers the Cmd-K modal |

Every route module is mounted via the try/except-guarded pattern in
`main.py` — a missing optional dep logs a warning and the rest of the app
stays up. Preserve this pattern.

## Conventions to preserve

- **Workspace bootstrap is best-effort.** A failure in
  `bootstrap_assistant_workspace` must NEVER block the sidecar coming up; the
  lifespan in `main.py` wraps it in try/except for this reason.
- **No in-process cache for `batches.json`.** Reads are uncached so tests can
  swap `LOGIC_LOOM_ASSISTANT_WORKSPACE` and the service follows. Writes are
  atomic via tempfile + `os.replace` (POSIX + Windows safe).
- **Filename: `effective_prompts.json` (underscore).** Both the Assistant
  runner and the shared `runs/output_store.py` now write this single spelling.
  The HTTP path stays `/effective-prompts` (REST convention).
- **The 7 indexable kinds are a closed set.** Adding a new kind requires a
  schema bump + a new adapter in `ingest.py`. Don't
  smuggle a new `IndexKind` literal in without updating both.
- **Post-write index hooks fire from `services.py`, not from the route layer.**
  Construct `SourceBatchService` via `get_assistant_services()` so the hook
  is wired; if you bypass the factory (e.g. for tests), the hook is silently
  a no-op (`on_batch_changed=None`).

## Known follow-ups

| Item | Workaround until fixed |
|---|---|
| `RunnerService` doesn't fire `IndexLifecycle.on_change` for new outputs | Manual rebuild via the Cmd-K empty-state button |
| Non-zotero kinds (zsum cards/digests, AKMS nodes, etc.) only flow into search via the rebuild route | Manual rebuild after big imports |
| `IndexLifecycle.on_delete` is implemented but no writer fires it | Stale rows persist until rebuild clears them |
| Discovery cache invalidation is time-based, not event-based | Hit `DELETE /api/assistant/discover/cache` |
