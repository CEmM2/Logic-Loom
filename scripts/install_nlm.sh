#!/usr/bin/env bash
set -euo pipefail

# scripts/install_nlm.sh — ensure the `nlm` (NotebookLM) CLI is installed so
# akms-learn's LLM section-expansion can ground lessons against a NotebookLM
# notebook.
#
# akms-learn never bundles `nlm`: it probes the binary at runtime via
# shutil.which("nlm") and degrades to a deterministic, no-LLM packet when it
# (or a provider key) is absent (see learn_adapter.resolve_host_llm_provider).
# `nlm` ships as the `notebooklm-mcp-cli` package and is installed as a uv tool
# (shim on ~/.local/bin), matching the repo's uv-centric tooling.
#
# Idempotent + build-friendly: if `nlm` is already on PATH this is a no-op
# (unless --upgrade). Safe to call from the sidecar build scripts as a soft
# step:
#
#     bash scripts/install_nlm.sh || echo "[sidecar] nlm unavailable; LLM expansion disabled"
#
# Usage:
#   bash scripts/install_nlm.sh              # install if missing
#   bash scripts/install_nlm.sh --upgrade    # (re)install latest
#   NLM_PACKAGE=notebooklm-mcp-cli bash scripts/install_nlm.sh

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PACKAGE="${NLM_PACKAGE:-notebooklm-mcp-cli}"
UPGRADE=0

for arg in "$@"; do
  case "$arg" in
    -u|--upgrade) UPGRADE=1 ;;
    -h|--help)
      sed -n '4,26p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) printf '[install-nlm] error: unknown arg: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

log() { printf '[install-nlm] %s\n' "$*"; }
die() { printf '[install-nlm] error: %s\n' "$*" >&2; exit 1; }

# Already present? Honour idempotency unless an upgrade was requested.
if command -v nlm >/dev/null 2>&1 && [[ "$UPGRADE" -eq 0 ]]; then
  log "nlm already installed: $(nlm --version 2>/dev/null || echo 'unknown version') ($(command -v nlm))"
  log "pass --upgrade to reinstall the latest. Skipping."
  exit 0
fi

command -v uv >/dev/null 2>&1 || die "uv not found on PATH — install uv first (https://docs.astral.sh/uv/), then re-run."

if [[ "$UPGRADE" -eq 1 ]]; then
  log "installing/upgrading $PACKAGE via uv tool …"
  uv tool install --upgrade "$PACKAGE"
else
  log "installing $PACKAGE via uv tool …"
  uv tool install "$PACKAGE"
fi

# uv tool installs shims under ~/.local/bin; surface a PATH hint if the freshly
# installed binary is not yet resolvable in this shell.
if ! command -v nlm >/dev/null 2>&1; then
  log "nlm installed but not on PATH for this shell."
  log "run 'uv tool update-shell' (or add ~/.local/bin to PATH) and reopen your shell."
  die "nlm not resolvable on PATH after install"
fi

log "installed: $(nlm --version 2>/dev/null || echo 'unknown version') ($(command -v nlm))"
log ""
log "next steps to enable grounded LLM section-expansion:"
log "  1. authenticate:  nlm login            # opens the NotebookLM OAuth flow"
log "  2. set a notebook: export AKMS_LEARN_NLM_NOTEBOOK_ID=<notebook-id>"
log "     (akms-learn also accepts NLM_NOTEBOOK_ID)"
log "with both in place, akms-learn auto-selects the nlm-grounded provider when"
log "llm_enable is set; otherwise it falls back to an API key, then to the"
log "deterministic no-LLM stub."
