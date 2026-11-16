#!/usr/bin/env bash
set -euo pipefail

# scripts/promote_macos_release.sh — local-only macOS build + install.
#
# Builds the macOS sidecar + Tauri app + DMG, then installs the .app into
# /Applications and removes local build artifacts. Used by developers to
# stand up a freshly-built version on their own machine.
#
# This script does NOT touch the public release repo and does NOT publish a
# GitHub Release — that pipeline lives in scripts/release.sh and is fed by
# CI-built binaries.
#
# Usage:
#   bash scripts/promote_macos_release.sh
#   bash scripts/promote_macos_release.sh --install no
#   bash scripts/promote_macos_release.sh --clean no
#   bash scripts/promote_macos_release.sh --dry-run

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# ───────────────────────────── defaults ──────────────────────────────

INSTALL_APP=1
CLEAN_ARTIFACTS=1
DRY_RUN=0

# ───────────────────────────── helpers ───────────────────────────────

log() { printf '[promote:macos] %s\n' "$*"; }
die() { printf '[promote:macos] error: %s\n' "$*" >&2; exit 1; }

run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '+ '
    printf '%q ' "$@"
    printf '\n'
    return 0
  fi
  "$@"
}

parse_bool() {
  local flag_name="$1" raw="$2" norm
  norm="$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]')"
  case "$norm" in
    yes|true|1)  printf '1' ;;
    no|false|0)  printf '0' ;;
    *) die "invalid value for ${flag_name}: ${raw} (expected yes|no)" ;;
  esac
}

usage() {
  cat <<EOF
Build Logic-Loom locally on macOS and install it into /Applications.

This script will:
  1. build the macOS sidecar, Tauri app, and DMG (pnpm logic-loom:package:macos)
  2. copy the resulting .app bundle into /Applications (unless --install no)
  3. remove local build artifacts that are no longer needed (unless --clean no)

It is for local installs only. Public releases are produced by CI and shipped
via scripts/release.sh.

Usage:
  bash scripts/promote_macos_release.sh [options]

Options:
  --install <yes|no>   Copy the built .app into /Applications. Default: yes.
  --clean   <yes|no>   Remove local build artifacts when finished. Default: yes.
  --dry-run            Print planned actions without changing anything.
  -h, --help           Show this help.

Examples:
  bash scripts/promote_macos_release.sh
  bash scripts/promote_macos_release.sh --install no
  bash scripts/promote_macos_release.sh --clean no
  bash scripts/promote_macos_release.sh --dry-run
EOF
}

# ───────────────────────────── parse args ────────────────────────────

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install)     INSTALL_APP="$(parse_bool --install "${2:-}")"; shift 2 ;;
    --install=*)   INSTALL_APP="$(parse_bool --install "${1#*=}")"; shift ;;
    --clean)       CLEAN_ARTIFACTS="$(parse_bool --clean "${2:-}")"; shift 2 ;;
    --clean=*)     CLEAN_ARTIFACTS="$(parse_bool --clean "${1#*=}")"; shift ;;
    --dry-run)     DRY_RUN=1; shift ;;
    -h|--help)     usage; exit 0 ;;
    *)             echo "Unknown argument: $1" >&2; usage >&2; exit 1 ;;
  esac
done

# ───────────────────────────── preflight ─────────────────────────────

[[ "$(uname -s)" == "Darwin" ]] \
  || die "scripts/promote_macos_release.sh must be run on macOS (uname -s is $(uname -s))"

command -v pnpm    >/dev/null 2>&1 || die "pnpm not found on PATH"
command -v python3 >/dev/null 2>&1 || die "python3 not found on PATH"

read_tauri_config_value() {
  local key="$1"
  python3 - "$key" <<'PY'
import json, sys
from pathlib import Path
key = sys.argv[1]
data = json.loads(Path("apps/desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
value = data.get(key)
if value is None:
    raise SystemExit(f"Missing required key '{key}' in tauri.conf.json")
sys.stdout.write(str(value))
PY
}

PRODUCT_NAME="$(read_tauri_config_value productName)"
APP_VERSION="$(read_tauri_config_value version)"
APP_SOURCE="$ROOT/apps/desktop/src-tauri/target/release/bundle/macos/${PRODUCT_NAME}.app"
APP_DEST="/Applications/${PRODUCT_NAME}.app"

CLEAN_PATHS=(
  "$ROOT/.venv-sidecar-build"
  "$ROOT/.pyinstaller-build"
  "$ROOT/.pyinstaller-dist"
  "$ROOT/logic-loom-api.spec"
  "$ROOT/apps/desktop/dist"
  "$ROOT/apps/desktop/src-tauri/target"
)

BINARY_DIR="$ROOT/apps/desktop/src-tauri/binaries"

log "product:         $PRODUCT_NAME"
log "app version:     $APP_VERSION"
log "install to /Applications: $( [[ "$INSTALL_APP"     -eq 1 ]] && printf yes || printf no )"
log "clean artifacts: $( [[ "$CLEAN_ARTIFACTS" -eq 1 ]] && printf yes || printf no )"
[[ "$DRY_RUN" -eq 1 ]] && log "dry-run mode enabled"

# ───────────────────────────── build ─────────────────────────────────

log "building macOS sidecar + Tauri app + DMG (pnpm logic-loom:package:macos)"
run pnpm logic-loom:package:macos

# ───────────────────────────── install ───────────────────────────────

if [[ "$INSTALL_APP" -eq 1 ]]; then
  [[ -d "$APP_SOURCE" || "$DRY_RUN" -eq 1 ]] \
    || die "built app not found: $APP_SOURCE"

  if pgrep -f "$APP_DEST/Contents/MacOS/" >/dev/null 2>&1; then
    log "stopping currently running installed app"
    run pkill -f "$APP_DEST/Contents/MacOS/"
  fi

  log "installing app to /Applications"
  run rm -rf "$APP_DEST"
  run ditto "$APP_SOURCE" "$APP_DEST"
else
  log "skipping /Applications install (per --install no)"
fi

# ───────────────────────────── cleanup ───────────────────────────────

if [[ "$CLEAN_ARTIFACTS" -eq 1 ]]; then
  log "cleaning local build artifacts"
  for path in "${CLEAN_PATHS[@]}"; do
    if [[ -e "$path" ]]; then
      run rm -rf "$path"
    fi
  done
  if [[ -d "$BINARY_DIR" ]]; then
    # Keep the tracked placeholder; remove only generated sidecar binaries.
    find "$BINARY_DIR" -mindepth 1 -maxdepth 1 ! -name .gitkeep -exec rm -rf {} +
  fi
else
  log "leaving local build artifacts in place (per --clean no)"
fi

log "done"
if [[ "$INSTALL_APP" -eq 1 ]]; then
  log "installed app: $APP_DEST"
fi
