#!/usr/bin/env bash
set -euo pipefail

# scripts/release.sh — cut a Logic-Loom release under the two-repo model.
#
# Release architecture (see README "## Releases"):
#   * the source repository (this one) — on a `v*` tag push, its
#     `release.yml` cuts a SOURCE-ONLY GitHub Release (`.tar.gz` + `.zip` +
#     SHA256SUMS). No binaries.
#   * CEmM2/Logic-Loom-releases (public) — on the same `v*` tag pushed there,
#     its `build-binaries.yml` checks out the private source at the tag (via the
#     SOURCE_REPO_PAT secret), builds macOS/Linux/Windows bundles, and attaches
#     them to that tag's Release.
#
# So a release is just "push the tag to both repos". This script does exactly
# that, in order, idempotently, with a dry-run mode.
#
#   bash scripts/release.sh --tag v0.1.0-alpha.6
#   bash scripts/release.sh --tag v0.1.0-alpha.6 --source-ref origin/master
#   bash scripts/release.sh --tag v0.1.0-alpha.6 --replace
#   bash scripts/release.sh --tag v0.1.0-alpha.6 --watch
#   bash scripts/release.sh --tag v0.1.0-alpha.6 --dry-run
#
# Prereqs:
#   * `git` + `gh` (authenticated for both repos) on PATH.
#   * The public release repo cloned locally
#     (default: $HOME/Github/Group/Logic-Loom-releases).
#   * docs/release_notes/<tag>.md present (both workflows use it as the body).
#   * The CEmM2 repo has the SOURCE_REPO_PAT secret configured (read Contents on
#     the source repo) — required by build-binaries.yml.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# ───────────────────────────── defaults ──────────────────────────────

DEFAULT_RELEASE_REPO="$HOME/Github/Group/Logic-Loom-releases"
RELEASE_REPO="${LOGIC_LOOM_RELEASE_REPO:-$DEFAULT_RELEASE_REPO}"

TAG_RAW=""
SOURCE_REPO=""
TARGET_REPO=""
SOURCE_REF="origin/master"   # where to create the source tag if it doesn't exist
MIRROR_BRANCH="main"         # branch the mirror tag is created from
PUSH_SOURCE=1
PUSH_MIRROR=1
REPLACE=0
WATCH=0
DRY_RUN=0

# ───────────────────────────── helpers ───────────────────────────────

log() { printf '[release] %s\n' "$*"; }
warn() { printf '[release] warning: %s\n' "$*" >&2; }
die() { printf '[release] error: %s\n' "$*" >&2; exit 1; }

run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '+ '; printf '%q ' "$@"; printf '\n'
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
Cut a Logic-Loom release (two-repo model: source-only on the source repo,
binaries built on the public release repo).

Usage:
  bash scripts/release.sh --tag <vX.Y.Z[-suffix]> [options]

Options:
  --tag <tag>                Release tag (with or without leading 'v'). Required.
  --source-ref <ref>         Commit/ref to create the SOURCE tag at, if it does
                             not already exist. Default: $SOURCE_REF.
  --release-repo <path>      Local clone of the public release repo
                             (default: \$LOGIC_LOOM_RELEASE_REPO or
                             $DEFAULT_RELEASE_REPO).
  --source-repo <owner/name> Source repo. Default: auto-detect from this
                             checkout's 'origin' remote.
  --target-repo <owner/name> Public release repo. Default: auto-detect from the
                             release-repo's 'origin' remote.
  --push-source <yes|no>     Create+push the tag on the source repo (source-only
                             release). Default: yes.
  --push-mirror <yes|no>     Create+push the tag on the release repo (binary
                             build + release). Default: yes.
  --replace                  Delete the existing Release(s) + tag(s) on both
                             repos first, then recreate. Default: no.
  --watch                    After pushing, watch the CEmM2 binary-build run to
                             completion. Default: no.
  --dry-run                  Print planned actions without changing anything.
  -h, --help                 Show this help.

Examples:
  # Cut v0.1.0-alpha.6 — tag master, push to both repos.
  bash scripts/release.sh --tag v0.1.0-alpha.6

  # Re-cut an existing tag (delete releases+tags on both repos, recreate).
  bash scripts/release.sh --tag v0.1.0-alpha.6 --replace

  # Only (re)trigger the binary build on the public repo.
  bash scripts/release.sh --tag v0.1.0-alpha.6 --push-source no

  # Dry-run.
  bash scripts/release.sh --tag v0.1.0-alpha.6 --dry-run
EOF
}

# ───────────────────────────── parse args ────────────────────────────

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag)             TAG_RAW="${2:-}"; shift 2 ;;
    --tag=*)           TAG_RAW="${1#*=}"; shift ;;
    --source-ref)      SOURCE_REF="${2:-}"; shift 2 ;;
    --source-ref=*)    SOURCE_REF="${1#*=}"; shift ;;
    --release-repo)    RELEASE_REPO="${2:-}"; shift 2 ;;
    --release-repo=*)  RELEASE_REPO="${1#*=}"; shift ;;
    --source-repo)     SOURCE_REPO="${2:-}"; shift 2 ;;
    --source-repo=*)   SOURCE_REPO="${1#*=}"; shift ;;
    --target-repo)     TARGET_REPO="${2:-}"; shift 2 ;;
    --target-repo=*)   TARGET_REPO="${1#*=}"; shift ;;
    --push-source)     PUSH_SOURCE="$(parse_bool --push-source "${2:-}")"; shift 2 ;;
    --push-source=*)   PUSH_SOURCE="$(parse_bool --push-source "${1#*=}")"; shift ;;
    --push-mirror)     PUSH_MIRROR="$(parse_bool --push-mirror "${2:-}")"; shift 2 ;;
    --push-mirror=*)   PUSH_MIRROR="$(parse_bool --push-mirror "${1#*=}")"; shift ;;
    --replace)         REPLACE=1; shift ;;
    --watch)           WATCH=1; shift ;;
    --dry-run)         DRY_RUN=1; shift ;;
    -h|--help)         usage; exit 0 ;;
    *)                 echo "Unknown argument: $1" >&2; usage >&2; exit 1 ;;
  esac
done

[[ -n "$TAG_RAW" ]] || { usage >&2; die "--tag is required"; }

# ───────────────────────────── preflight ─────────────────────────────

command -v git >/dev/null 2>&1 || die "git not found on PATH"
command -v gh  >/dev/null 2>&1 || die "gh CLI not found on PATH"
gh auth status >/dev/null 2>&1 || die "gh is not authenticated; run 'gh auth login'"

resolve_repo_slug() {
  local origin="$1"
  origin="${origin%.git}"
  origin="${origin#git@github.com:}"
  origin="${origin#https://github.com/}"
  origin="${origin#http://github.com/}"
  printf '%s' "$origin"
}

[[ -z "$SOURCE_REPO" ]] && SOURCE_REPO="$(resolve_repo_slug "$(git -C "$ROOT" remote get-url origin)")"

if [[ "$PUSH_MIRROR" -eq 1 || -n "$TARGET_REPO" ]]; then
  [[ -d "$RELEASE_REPO/.git" ]] || die "release repo not found or not a git repo: $RELEASE_REPO"
  [[ -z "$TARGET_REPO" ]] && TARGET_REPO="$(resolve_repo_slug "$(git -C "$RELEASE_REPO" remote get-url origin)")"
fi

# Normalize tag forms.
RELEASE_VERSION="${TAG_RAW#v}"
RELEASE_TAG="v${RELEASE_VERSION}"

NOTES_FILE="docs/release_notes/${RELEASE_TAG}.md"

log "source repo:     $SOURCE_REPO"
log "target repo:     ${TARGET_REPO:-<skipped>}"
log "release repo:    $RELEASE_REPO"
log "release tag:     $RELEASE_TAG"
log "source ref:      $SOURCE_REF (used only if the tag must be created)"
log "push source:     $( [[ "$PUSH_SOURCE" -eq 1 ]] && printf yes || printf no )"
log "push mirror:     $( [[ "$PUSH_MIRROR" -eq 1 ]] && printf yes || printf no )"
log "replace:         $( [[ "$REPLACE"     -eq 1 ]] && printf yes || printf no )"
log "watch:           $( [[ "$WATCH"       -eq 1 ]] && printf yes || printf no )"
[[ "$DRY_RUN" -eq 1 ]] && log "dry-run mode enabled"

# The installers ship THIRD_PARTY_NOTICES.md; it must match the lockfiles.
if ! python3 "$ROOT/scripts/gen_third_party_notices.py" --check; then
  if [[ "$DRY_RUN" -eq 1 ]]; then
    warn "THIRD_PARTY_NOTICES.md is stale; a real run would stop here"
  else
    die "regenerate THIRD_PARTY_NOTICES.md (uv run python scripts/gen_third_party_notices.py), merge it, then tag"
  fi
fi

# Release notes are the body for BOTH workflows; warn early if missing.
if [[ -f "$ROOT/$NOTES_FILE" ]]; then
  log "release notes:   $NOTES_FILE"
else
  warn "no release notes at $NOTES_FILE — both workflows will fall back to a generic body. Add it and re-tag if you want real notes."
fi

# ───────────────────────────── tag helpers ───────────────────────────

remote_tag_exists() {  # <repo_dir> <tag>
  [[ -n "$(git -C "$1" ls-remote --tags origin "refs/tags/$2" 2>/dev/null)" ]]
}

delete_release_and_tag() {  # <repo_slug> <repo_dir> <tag>
  local slug="$1" dir="$2" tag="$3"
  if gh release view "$tag" --repo "$slug" >/dev/null 2>&1; then
    log "  deleting existing Release $tag on $slug"
    run gh release delete "$tag" --repo "$slug" --yes
  fi
  if remote_tag_exists "$dir" "$tag"; then
    log "  deleting remote tag $tag on $slug"
    run git -C "$dir" push origin ":refs/tags/$tag"
  fi
  if git -C "$dir" rev-parse -q --verify "refs/tags/$tag" >/dev/null 2>&1; then
    run git -C "$dir" tag -d "$tag"
  fi
}

# ───────────────────────────── replace ───────────────────────────────

if [[ "$REPLACE" -eq 1 ]]; then
  log "replace: removing existing releases + tags first"
  [[ "$PUSH_SOURCE" -eq 1 ]] && delete_release_and_tag "$SOURCE_REPO" "$ROOT" "$RELEASE_TAG"
  [[ "$PUSH_MIRROR" -eq 1 ]] && delete_release_and_tag "$TARGET_REPO" "$RELEASE_REPO" "$RELEASE_TAG"
fi

# ───────────────────────────── source repo ───────────────────────────
# Push the tag to the source repository → triggers the source-only release.

if [[ "$PUSH_SOURCE" -eq 1 ]]; then
  log "source: preparing tag $RELEASE_TAG on $SOURCE_REPO"
  run git -C "$ROOT" fetch origin --tags --quiet

  if remote_tag_exists "$ROOT" "$RELEASE_TAG"; then
    log "  remote tag already exists — nothing to push (use --replace to re-cut)"
  else
    if ! git -C "$ROOT" rev-parse -q --verify "refs/tags/$RELEASE_TAG" >/dev/null 2>&1; then
      log "  creating annotated tag at $SOURCE_REF"
      run git -C "$ROOT" tag -a "$RELEASE_TAG" "$SOURCE_REF" \
        -m "Logic-Loom $RELEASE_TAG"
    fi
    log "  pushing tag → source-only release workflow"
    run git -C "$ROOT" push origin "refs/tags/$RELEASE_TAG"
  fi
else
  log "source: skipped (--push-source no)"
fi

# ───────────────────────────── release repo ──────────────────────────
# Push the same tag to CEmM2/Logic-Loom-releases → triggers the binary build.

if [[ "$PUSH_MIRROR" -eq 1 ]]; then
  log "mirror: preparing tag $RELEASE_TAG on $TARGET_REPO"
  run git -C "$RELEASE_REPO" fetch origin --tags --quiet

  if remote_tag_exists "$RELEASE_REPO" "$RELEASE_TAG"; then
    log "  remote tag already exists — nothing to push (use --replace to re-cut)"
  else
    if ! git -C "$RELEASE_REPO" rev-parse -q --verify "refs/tags/$RELEASE_TAG" >/dev/null 2>&1; then
      # The tag just needs to exist to trigger CI; the build checks out the
      # SOURCE at this tag name, so the mirror commit it points at is irrelevant.
      log "  creating tag at origin/$MIRROR_BRANCH"
      run git -C "$RELEASE_REPO" tag "$RELEASE_TAG" "origin/$MIRROR_BRANCH"
    fi
    log "  pushing tag → binary-build workflow"
    run git -C "$RELEASE_REPO" push origin "refs/tags/$RELEASE_TAG"
  fi
else
  log "mirror: skipped (--push-mirror no)"
fi

# ───────────────────────────── watch (optional) ──────────────────────

if [[ "$WATCH" -eq 1 && "$PUSH_MIRROR" -eq 1 && "$DRY_RUN" -eq 0 ]]; then
  log "watch: waiting for the binary-build run on $TARGET_REPO"
  # Give GitHub a moment to register the workflow run for the tag push.
  sleep 6
  run_id="$(gh run list --repo "$TARGET_REPO" --workflow build-binaries.yml \
              --json databaseId,headBranch,createdAt \
              -q "[.[] | select(.headBranch==\"$RELEASE_TAG\")] | sort_by(.createdAt) | reverse | .[0].databaseId // empty")"
  if [[ -n "$run_id" ]]; then
    gh run watch "$run_id" --repo "$TARGET_REPO" --exit-status || \
      warn "binary-build run did not succeed — check: gh run view $run_id --repo $TARGET_REPO"
  else
    warn "could not locate the build run yet; check the Actions tab on $TARGET_REPO"
  fi
fi

# ───────────────────────────── done ──────────────────────────────────

log "done"
[[ "$PUSH_SOURCE" -eq 1 ]] && log "source release: https://github.com/$SOURCE_REPO/releases/tag/$RELEASE_TAG"
[[ "$PUSH_MIRROR" -eq 1 ]] && log "binary release: https://github.com/$TARGET_REPO/releases/tag/$RELEASE_TAG"
[[ "$PUSH_MIRROR" -eq 1 ]] && log "note: the binary build needs the SOURCE_REPO_PAT secret on $TARGET_REPO"
