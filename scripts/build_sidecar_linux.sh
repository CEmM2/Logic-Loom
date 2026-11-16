#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_VENV="$ROOT/.venv-sidecar-build"
BIN_DIR="$ROOT/apps/desktop/src-tauri/binaries"
PYI_WORK="$ROOT/.pyinstaller-build"
PYI_DIST="$ROOT/.pyinstaller-dist"
ENTRY="$ROOT/packaging/pyinstaller/logic_loom_api_entry.py"
LOCKED_REQUIREMENTS="$ROOT/.sidecar-build-requirements.txt"

# What goes into the sidecar. The default is the full bundle: the AKMS tabs and
# Learn (`akms` extra), the MechDSL emit bridge (`mechdsl`), Failure Memory
# (`failure-memory`) and the Records browser (`records`). Every one of those
# packages comes from the package index at the version uv.lock records, so no
# source checkout of AKMS, MechDSL or zotero-summarizer is involved.
#
# LOGIC_LOOM_SIDECAR_CORE_ONLY=1 builds the zsum-only sidecar instead. The app
# then reports the AKMS-family modes as unavailable, which is the same state as
# a development install without the extras.
CORE_ONLY="${LOGIC_LOOM_SIDECAR_CORE_ONLY:-0}"
SIDECAR_EXTRAS=(akms mechdsl failure-memory records)

echo "[sidecar] root: $ROOT"

cd "$ROOT"

# Security guard: assert no Nodes_Vault paths leaked into the
# packaged resources tree. MUST run before any build step so a leak halts
# the pipeline before the wheel/PyInstaller stages copy the tree.
echo "[sidecar] running resources/ Nodes_Vault security guard"
python3 "$ROOT/scripts/check_resources_no_vault.py"

# Sync curated established nodes into the shippable resources tree.
# Runs unconditionally — verified_nodes/ is always present in the repo.
# Must run after the vault guard (which checks accumulated state from prior
# syncs) and before the build venv is created. Invoked via `uv run` because
# the sync imports third-party python-frontmatter (a logic-loom-api dep) that
# is NOT on the bare system interpreter on a clean build host.
echo "[sidecar] syncing curated established nodes via sync_akms_nodes.py"
uv run python "$ROOT/scripts/sync_akms_nodes.py"

# Established-only guard: assert every node in the synced established tree carries
# status: established AND passes the AKMS GlobalNodeFrontmatter schema compile.
# The guard FAILS CLOSED when akms is not importable, so the full build runs it
# with `--extra akms` (full schema compile). Only a core-only build, which
# installs no AKMS package at all, passes --allow-missing-akms so the
# status-only fallback runs instead of wedging the build.
echo "[sidecar] running established-only build guard"
if [[ "$CORE_ONLY" != "1" ]]; then
  uv run --package logic-loom-api --extra akms python "$ROOT/scripts/check_resources_established_only.py"
else
  uv run python "$ROOT/scripts/check_resources_established_only.py" --allow-missing-akms
fi

echo "[sidecar] cleaning old build output"
rm -rf "$BUILD_VENV" "$PYI_WORK" "$PYI_DIST" "$LOCKED_REQUIREMENTS"
mkdir -p "$BIN_DIR"

echo "[sidecar] creating isolated build venv"
uv venv "$BUILD_VENV"

# shellcheck disable=SC1091
source "$BUILD_VENV/bin/activate"

# The build installs exactly what uv.lock records: `uv export --locked` fails if
# the lockfile is out of date with pyproject.toml, and the exported pins are the
# builds the test suite ran against. The extras are the ones declared in
# services/logic_loom_api/pyproject.toml, so the version ranges live in one
# place only.
EXPORT_ARGS=(
  --locked
  --no-hashes
  --no-dev
  --no-emit-workspace
  --package logic-loom-api
  --output-file "$LOCKED_REQUIREMENTS"
)

PYINSTALLER_ARGS=(
  --clean
  --noconfirm
  --onefile
  --name logic-loom-api
  --workpath "$PYI_WORK"
  --distpath "$PYI_DIST"
  --collect-all logic_loom_api
  --collect-all zsum
  --hidden-import uvicorn.protocols.http.auto
  --hidden-import uvicorn.protocols.websockets.auto
  --hidden-import uvicorn.lifespan.on
)

if [[ "$CORE_ONLY" != "1" ]]; then
  echo "[sidecar] full bundle: AKMS + Learn, MechDSL emit bridge, Failure Memory, Records"
  for extra in "${SIDECAR_EXTRAS[@]}"; do
    EXPORT_ARGS+=(--extra "$extra")
  done
  PYINSTALLER_ARGS+=(
    # AKMS tabs (Picker, Nodes, Graph, Batches) and Learn.
    --collect-all akms
    --collect-all akms_nodes_gen
    --collect-all akms_learn
    --collect-data litellm
    # Failure Memory workspace and the Records browser.
    --collect-all akms_failure_memory
    --collect-all akms_records
    # MechDSL emit bridge: compile_from_sources / transpile via
    # compmech-reference-pack[mechdsl] -> mechdsl-core + algo2code. Taichi-free:
    # taichi lives in mechdsl-core's [verify] extra, which is never requested,
    # so only run_verify=True would need it and that is gated off. opt-einsum
    # (compile-time contraction optimizer) rides along.
    --collect-all compmech_reference_pack
    --collect-all mechdsl
    --collect-all algo2code
    --collect-all opt_einsum
    --collect-all nrpylatex
    --exclude-module taichi
    # pip rides along so the in-app "Install taichi" action (MechDSL Overview)
    # can side-install taichi 1.7.4 at runtime via the binary's hidden
    # --install-runtime-pkg re-entry. Taichi itself stays excluded (~170 MB).
    --collect-all pip
  )
else
  echo "[sidecar] LOGIC_LOOM_SIDECAR_CORE_ONLY=1: building the core-only sidecar (no AKMS-family packages)"
fi

echo "[sidecar] exporting the locked dependency set"
uv export "${EXPORT_ARGS[@]}" >/dev/null

echo "[sidecar] installing PyInstaller + the locked dependencies"
uv pip install pyinstaller -r "$LOCKED_REQUIREMENTS"

if [[ "$CORE_ONLY" != "1" ]]; then
  # The curated AKMS agent assets are read from the `akms` package that was just
  # installed, so the bundle carries the assets of exactly the AKMS version it
  # freezes. This must run before logic-loom-api is installed below: that
  # install copies the resources tree the sync writes into.
  echo "[sidecar] syncing curated AKMS Claude assets via sync_akms_claude_assets.py"
  python "$ROOT/scripts/sync_akms_claude_assets.py"
fi

echo "[sidecar] installing the Logic-Loom API"
uv pip install --no-deps ./services/logic_loom_api

echo "[sidecar] freezing logic-loom-api"
pyinstaller \
  "${PYINSTALLER_ARGS[@]}" \
  "$ENTRY"

echo "[sidecar] copying binary into Tauri resources"
cp "$PYI_DIST/logic-loom-api" "$BIN_DIR/logic-loom-api"
chmod +x "$BIN_DIR/logic-loom-api"

echo "[sidecar] smoke test: binary exists"
ls -lh "$BIN_DIR/logic-loom-api"

rm -f "$LOCKED_REQUIREMENTS"

echo "[sidecar] done"
