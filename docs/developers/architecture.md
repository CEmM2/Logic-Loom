# Architecture

## Two processes and a handshake

The desktop app is a Tauri shell that starts the Python sidecar as a child process.

1. The shell generates a random per-launch token and starts the sidecar with it in
   `LOGIC_LOOM_API_TOKEN`: `uv run logic-loom-api --auto-port` in development, or the
   bundled `logic-loom-api` binary from the app's resources in a release build.
2. The sidecar picks a free port and prints `LOGIC_LOOM_API_READY:<port>` on stdout.
3. The shell parses that line, stores the port and emits the `logic-loom://ready` event.
4. On window close the shell sends SIGTERM, then SIGKILL if the sidecar does not exit.

The handshake line is a contract between `handshake.py` and `apps/desktop/src-tauri/src/sidecar.rs`.
Changing its format means changing both.

The frontend finds the API in `apps/desktop/src/api/client.ts`, trying in order: polling
the `get_api_port` Tauri command, the `logic-loom://ready` event, and the
`VITE_LOGIC_LOOM_API_BASE` variable (development only, for Vite without Tauri). The port
is random on every launch, so it is never hard-coded.

## Local API authentication

The API listens on loopback, which any local process and, through DNS rebinding, any web
page can reach. So when `LOGIC_LOOM_API_TOKEN` is set (the desktop app always sets it),
`auth.py` refuses a request unless:

- its `Host` header names `127.0.0.1`, `localhost` or `[::1]` (421 otherwise), and
- it carries the token in the `X-Logic-Loom-Token` header, or in the `llt` query
  parameter for EventSource streams and links that cannot set headers (401 otherwise).

`GET /api/app/health` and CORS preflights are exempt. The sidecar removes the token from
its environment at startup so the tools it launches do not inherit it. The frontend gets
the token from the `get_api_token` Tauri command; use `api()`, `apiFetch()` or `apiUrl()`
in `client.ts` rather than building API URLs by hand.

A sidecar started without the variable (`uv run logic-loom-api --port 17321`) accepts
unauthenticated requests and logs a warning, for standalone debugging only.

## The sidecar

The FastAPI app is assembled in `services/logic_loom_api/src/logic_loom_api/main.py`.

- Core routers are always mounted: `app`, `graph`, `vault`, `zsum`, `nlm`, `nodes`.
- Every other router (workspace, prompts, batches, workflows, runs, AKMS, Assistant,
  MechDSL, Failure Memory, Records) is mounted inside try/except. If its optional extra
  is missing, the mount logs a warning and is skipped; the rest of the API keeps working.
- The AKMS batch picker is a mounted sub-app at `/api/picker`, with its own lifespan.
  If it cannot be built, it is skipped the same way.
- The lifespan bootstraps the AKMS workspace, then the Assistant workspace, then the run
  registry. A workspace bootstrap failure never stops the sidecar from starting.
- CORS is limited to the Tauri and localhost origins. Every response carries an
  `X-Correlation-ID`, and errors share one envelope.

All routes live under `/api/*`; see the [HTTP API reference](../reference/http-api.md).

## Settings

`config.py` resolves settings from constructor arguments, then `LOGIC_LOOM_*`
environment variables, then the user TOML file, then defaults. See
[Configuration](../install/configuration.md) and the
[configuration reference](../reference/configuration.md).

## One LLM setting

`host_llm/` is the single source of truth for which LLM each feature uses. The global
`llm_provider` and the `zsum_llm_provider` / `nodegen_llm_provider` overrides are applied
where Logic-Loom calls into AKMS and zotero-summarizer, through their public config, env
and CLI surfaces only. Node generation gets its environment per subprocess; Logic-Loom
never changes the user's config files or its own process environment for it.

## The frontend

`apps/desktop/src/App.tsx` defines six modes: `/zsum`, `/akms`, `/assistant`, `/mechdsl`,
`/failure-memory` and `/records`. Each gated mode is wrapped in a gate component that
shows setup guidance when its extra or configuration is missing. Older deep links
(`/picker`, `/nodes`, `/graph`, `/pipeline`, `/vault`) redirect to their new places. The
API client modules in `src/api/*.ts` mirror the backend route groups, and
`src/api/openapi.ts` holds the generated types.

## Failure Memory internals

The read-only and operator split, the background-job model and the graph overlay are
described in [Failure Memory architecture](failure-memory-architecture.md).
