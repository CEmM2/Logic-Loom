#!/usr/bin/env bash
set -euo pipefail

# scripts/build_release_linux.sh — build Logic-Loom Linux release artifacts.
#
# Wraps pnpm logic-loom:package:linux (sidecar + Tauri app → .deb + .AppImage).
# Build-only: no install, no release-repo promotion.
#
# Publishing CI-built binaries to the public release repo + GitHub Release is
# handled by scripts/release.sh --tag vX.Y.Z.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# ───────────────────────────── defaults ──────────────────────────────

CLEAN_ARTIFACTS=0
DRY_RUN=0

CLEAN_PATHS=(
  "$ROOT/.venv-sidecar-build"
  "$ROOT/.pyinstaller-build"
  "$ROOT/.pyinstaller-dist"
  "$ROOT/logic-loom-api.spec"
  "$ROOT/apps/desktop/dist"
  "$ROOT/apps/desktop/src-tauri/binaries"
  "$ROOT/apps/desktop/src-tauri/target"
)

# ───────────────────────────── helpers ───────────────────────────────

log() { printf '[build-release:linux] %s\n' "$*"; }
die() { printf '[build-release:linux] error: %s\n' "$*" >&2; exit 1; }

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
Build Logic-Loom Linux release artifacts (sidecar + Tauri app).

Build-only. Publishing to the release repo is handled by scripts/release.sh.

Usage:
  bash scripts/build_release_linux.sh [options]

Options:
  --clean <yes|no>   Remove local build artifacts when finished. Default: no.
  --dry-run          Print planned actions without changing anything.
  -h, --help         Show this help.

Examples:
  bash scripts/build_release_linux.sh
  bash scripts/build_release_linux.sh --clean yes
  bash scripts/build_release_linux.sh --dry-run
EOF
}

# ───────────────────────────── parse args ────────────────────────────

while [[ $# -gt 0 ]]; do
  case "$1" in
    --clean)     CLEAN_ARTIFACTS="$(parse_bool --clean "${2:-}")"; shift 2 ;;
    --clean=*)   CLEAN_ARTIFACTS="$(parse_bool --clean "${1#*=}")"; shift ;;
    --dry-run)   DRY_RUN=1; shift ;;
    -h|--help)   usage; exit 0 ;;
    *)           echo "Unknown argument: $1" >&2; usage >&2; exit 1 ;;
  esac
done

# ───────────────────────────── preflight ─────────────────────────────

[[ "$(uname -s)" == "Linux" ]] \
  || die "scripts/build_release_linux.sh must be run on Linux (uname -s is $(uname -s))"

command -v pnpm >/dev/null 2>&1 || die "pnpm not found on PATH"

log "clean artifacts: $( [[ "$CLEAN_ARTIFACTS" -eq 1 ]] && printf yes || printf no )"
[[ "$DRY_RUN" -eq 1 ]] && log "dry-run mode enabled"

# ───────────────────────────── build ─────────────────────────────────

log "building Linux sidecar + Tauri app (pnpm logic-loom:package:linux)"
run pnpm logic-loom:package:linux

# ───────────────────────────── cleanup ───────────────────────────────

if [[ "$CLEAN_ARTIFACTS" -eq 1 ]]; then
  log "cleaning local build artifacts"
  for path in "${CLEAN_PATHS[@]}"; do
    if [[ -e "$path" ]]; then
      run rm -rf "$path"
    fi
  done
else
  log "leaving local build artifacts in place"
fi

log "done"
