# Logic-Loom API

FastAPI façade service for the Logic-Loom desktop app. It owns the stable `/api/*` namespace, wraps the `zsum` CLI with run streaming, mounts the AKMS picker, serves vault and node-review routes, and now exposes a read-only AKMS graph API.

## Responsibilities

- `/api/app/*` — health, version, config, first-run state, AKMS readiness
- `/api/zsum/*` — pipeline runs and streamed logs
- `/api/vault/*` — read-only vault browser, resolve, and search
- `/api/nodes/*` — AKMS node-review file browsing and editor launch
- `/api/picker/*` — mounted AKMS batch-picker app
- `/api/nlm/*` — NotebookLM-related background tasks
- `/api/graph/*` — read-only AKMS graph refresh and slice queries

The graph API uses AKMS core `build_graph()` / `load_graph()` and writes the compiled graph into Logic-Loom's cache rather than mutating the AKMS repo's own graph output.

## Run standalone

```bash
uv run logic-loom-api --port 17321
curl localhost:17321/api/app/health
```

## Optional AKMS support

Graph, picker, and node-review features require the optional AKMS extra:

```bash
uv sync --extra akms
```

That installs both:

- `akms`
- `akms-nodes-gen`

Without it, the AKMS-facing endpoints degrade gracefully and the frontend shows availability placeholders.

## Graph endpoints

Current graph surface:

- `GET /api/graph/status`
- `POST /api/graph/refresh`
- `GET /api/graph/nodes`
- `GET /api/graph/edges`
- `GET /api/graph/ego`
- `GET /api/graph/cluster`

Graph compilation inputs are resolved from:

1. `akms_repo`
2. `akms_global_vault` (or its fallback chain)
3. `cache_path`

The effective graph source vault resolves in this order:

1. explicit `akms_global_vault`
2. `<akms_repo>/Packages/Nodes_Vault`
3. `AKMS_GLOBAL_VAULT`
4. `~/.claude/akms/nodes`

## Useful standalone checks

```bash
curl localhost:17321/api/app/health
curl localhost:17321/api/app/akms-status
curl localhost:17321/api/graph/status
curl -X POST localhost:17321/api/graph/refresh
curl 'localhost:17321/api/graph/ego?node=plasticity-von-mises&depth=2&direction=both'
```

## Validation

Backend coverage currently includes app/config and graph-route tests:

```bash
uv run pytest tests/test_app.py tests/test_graph.py -q
```
