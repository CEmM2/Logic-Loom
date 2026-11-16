#!/usr/bin/env bash
set -euo pipefail

# scripts/build_release_macos.sh — build Logic-Loom macOS release artifacts.
#
# Wraps pnpm logic-loom:package:macos (sidecar + Tauri app + DMG). Intended
# for local CI-equivalent builds: it does not install into /Applications and
# does not publish to the release repo.
#
# For a local build + /Applications install + artifact cleanup, use:
#   bash scripts/promote_macos_release.sh
#
# For publishing CI-built binaries to the public release repo + GitHub
# Release, use:
#   bash scripts/release.sh --tag vX.Y.Z

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

log() { printf '[build-release:macos] %s\n' "$*"; }
die() { printf '[build-release:macos] error: %s\n' "$*" >&2; exit 1; }

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
Build Logic-Loom macOS release artifacts (sidecar + Tauri app + DMG).

This is a build-only wrapper. To also install into /Applications and clean
up afterwards, use scripts/promote_macos_release.sh. To publish CI binaries
to the public release repo, use scripts/release.sh.

Usage:
  bash scripts/build_release_macos.sh [options]

Options:
  --clean <yes|no>   Remove local build artifacts when finished. Default: no.
  --dry-run          Print planned actions without changing anything.
  -h, --help         Show this help.

Examples:
  bash scripts/build_release_macos.sh
  bash scripts/build_release_macos.sh --clean yes
  bash scripts/build_release_macos.sh --dry-run
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

[[ "$(uname -s)" == "Darwin" ]] \
  || die "scripts/build_release_macos.sh must be run on macOS (uname -s is $(uname -s))"

command -v pnpm >/dev/null 2>&1 || die "pnpm not found on PATH"

log "clean artifacts: $( [[ "$CLEAN_ARTIFACTS" -eq 1 ]] && printf yes || printf no )"
[[ "$DRY_RUN" -eq 1 ]] && log "dry-run mode enabled"

# ───────────────────────────── build ─────────────────────────────────

log "building macOS sidecar + Tauri app + DMG (pnpm logic-loom:package:macos)"
run pnpm logic-loom:package:macos

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
