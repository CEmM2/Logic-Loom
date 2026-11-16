#!/usr/bin/env bash
# Regenerate src/api/openapi.ts from a running logic_loom_api instance.
#
# Usage:
#   pnpm gen:types                                       # uses default URL
#   LOGIC_LOOM_API_BASE=http://127.0.0.1:55555 pnpm gen:types
#
# The script requires a running logic-loom-api on the given URL. If you're not
# already running one, start it in another terminal:
#   uv run logic-loom-api --port 17321

set -euo pipefail

BASE="${LOGIC_LOOM_API_BASE:-http://127.0.0.1:17321}"
OUT="src/api/openapi.ts"

echo "Generating types from ${BASE}/openapi.json -> ${OUT}"
exec npx --yes openapi-typescript "${BASE}/openapi.json" -o "${OUT}"
