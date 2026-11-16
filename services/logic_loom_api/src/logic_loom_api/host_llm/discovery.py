"""Host-side agent-CLI LLM providers for the AKMS Learn ``llm_expanded`` mode.

akms_learn exposes a public provider registry (``register(name, provider)``) and
a ``runtime_checkable`` ``LLMProvider`` Protocol — a provider is any callable
``(topic, active_node_ids, policy=None, *, sources=None) -> list[GeneratedSection]``.
The built-in ``akms`` provider routes to LiteLLM (API key, env-sourced); ``nlm``
shells out to the NotebookLM CLI. This module adds **agent-CLI** providers —
``claude -p``, ``codex exec``, and user-defined custom commands — so a host with
a logged-in agent CLI (but no API key) can still drive LLM expansion.

Routing precedence ("key → API, else → CLI") lives in
``learn_adapter.resolve_host_llm_provider``; this module only builds, discovers,
and registers the CLI providers, and injects configured API keys into the env.

All akms_learn imports are lazy (inside functions) so this module is importable
in a core-only sidecar; registration is itself wrapped in try/except by the
caller (FastAPI lifespan), preserving the degrade-gracefully invariant.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import shutil
import subprocess
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from ..config import Settings

logger = logging.getLogger(__name__)

#: subprocess wall-clock cap per expansion call.
_CLI_TIMEOUT_S = 120

#: Built-in agent CLIs: registry name → (binary, command template). A ``{prompt}``
#: token is substituted at argv level (no shell); absent → prompt piped to stdin.
_BUILTIN_CLIS: dict[str, tuple[str, str]] = {
    "claude-cli": ("claude", "claude -p --output-format text"),
    "codex-cli": ("codex", "codex exec {prompt}"),
}

#: Completion API-key env vars (mirror ``learn_adapter._COMPLETION_ENV_VARS``)
#: paired with the Settings field that backs each.
_KEY_ENV_TO_FIELD: dict[str, str] = {
    "ANTHROPIC_API_KEY": "anthropic_api_key",
    "OPENAI_API_KEY": "openai_api_key",
    "GOOGLE_API_KEY": "google_api_key",
}


# --------------------------------------------------------------------------- #
# Prompt + transport
# --------------------------------------------------------------------------- #
def _build_prompt(topic: str, valid_ids: tuple[str, ...], policy: Any) -> str:
    """Flattened equivalent of ``akms_completion``'s system + user prompt."""
    return (
        "You expand a Learning Source Packet (LSP) section for a learner. "
        "Cite ONLY the provided source node ids. Introduce no claim that is not "
        "grounded in those sources — paraphrase, do not invent. "
        f"Expansion policy: {policy or 'source_locked'}.\n\n"
        f"Topic: {topic}\n"
        f"Source node ids (cite only these): {', '.join(valid_ids)}\n"
        "Write one concise explanatory paragraph grounded in those sources."
    )


#: A markdown code fence wrapping the ENTIRE output (optional language tag).
_FULL_FENCE_RE = re.compile(r"^```[^\n]*\n(.*)\n```$", re.DOTALL)


def _normalize_cli_output(raw: str) -> str:
    """Reduce agent-CLI stdout to a plain-prose section body.

    Agents sometimes wrap their answer in a single markdown code fence or pad it
    with blank lines. We unwrap a fence only when it encloses the *entire*
    output, and collapse runs of blank lines. Deliberately conservative — no
    agent-specific banner heuristics that could eat real content.
    ``GeneratedSection.content`` is contractually plain text.
    """
    text = raw.strip()
    fence = _FULL_FENCE_RE.match(text)
    if fence:
        text = fence.group(1).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)  # collapse blank-line runs
    return text.strip()


def _run_cli_template(template: str, prompt: str, *, timeout: int = _CLI_TIMEOUT_S) -> str:
    """Run an agent CLI from a command template and return stdout.

    No shell: ``shlex.split`` the template; if any token contains ``{prompt}``
    substitute it (prompt becomes one argv element), else pipe the prompt to
    stdin. Raises ``RuntimeError`` on missing binary, timeout, or non-zero exit.
    """
    parts = shlex.split(template)
    if not parts:
        raise RuntimeError("empty CLI command")
    if any("{prompt}" in p for p in parts):
        argv = [p.replace("{prompt}", prompt) for p in parts]
        stdin_data: str | None = None
    else:
        argv = parts
        stdin_data = prompt
    if shutil.which(argv[0]) is None:
        raise RuntimeError(f"CLI not found on PATH: {argv[0]}")
    try:
        proc = subprocess.run(
            argv, input=stdin_data, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"CLI timed out after {timeout}s: {argv[0]}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-400:]
        raise RuntimeError(f"CLI failed (rc={proc.returncode}): {detail}")
    return proc.stdout.strip()


def _make_provider(name: str, template: str) -> Callable[..., list]:
    """Build an ``LLMProvider``-shaped callable backed by an agent CLI."""

    def provider(topic, active_node_ids, policy=None, *, sources=None):  # noqa: ANN001
        from akms_learn.models.llm_expansion import (
            GeneratedSection,
            compute_content_hash,
        )

        valid_ids = tuple(str(nid) for nid in active_node_ids if nid)
        if not valid_ids:
            return []
        content = _normalize_cli_output(
            _run_cli_template(template, _build_prompt(topic, valid_ids, policy))
        )
        if not content:
            return []
        section_id = f"{valid_ids[0]}::{name}"
        return [
            GeneratedSection(
                id=section_id,
                generator=name,
                model=name,
                source_node_ids=valid_ids,
                validation_status="valid",
                content_hash=compute_content_hash(
                    section_id=section_id,
                    source_node_ids=valid_ids,
                    content=content,
                ),
                content=content,
            )
        ]

    provider.__name__ = f"{name.replace('-', '_')}_provider"
    return provider


# --------------------------------------------------------------------------- #
# Discovery + registration
# --------------------------------------------------------------------------- #
def _custom_clis(settings: Settings) -> dict[str, str]:
    return dict(getattr(settings, "learn_llm_custom_clis", None) or {})


def _safe_split(command: str) -> list[str] | None:
    """``shlex.split`` a custom-CLI command, returning ``None`` (never raising) on a
    malformed command such as an unbalanced quote.

    A user-entered ``learn_llm_custom_clis`` value reaches discovery, registration
    and resolution straight from ``ConfigUpdate`` (the route accepts + persists any
    string). A ``shlex`` ``ValueError`` must degrade to "this provider is
    unavailable", never bubble into a request path, the resolver, or sidecar
    startup — CLAUDE.md degrade-gracefully invariant. Centralised here so the
    three call sites (availability, discovery, registration) handle it identically.
    """
    try:
        return shlex.split(command)
    except ValueError:
        return None


def available_cli_providers(settings: Settings) -> list[str]:
    """Registry names of CLI providers usable right now (binary on PATH).

    Order is the resolution preference: built-in agents first (claude, codex),
    then configured custom labels. A custom label whose command is malformed
    (unparseable) is treated as unavailable, never raised.
    """
    out: list[str] = []
    for name, (binary, _template) in _BUILTIN_CLIS.items():
        if shutil.which(binary) is not None:
            out.append(name)
    for label, command in _custom_clis(settings).items():
        cmd = (command or "").strip()
        if not cmd:
            continue
        argv = _safe_split(cmd)
        if argv and shutil.which(argv[0]) is not None:
            out.append(label)
    return out


def _api_key_set(settings: Settings, env_var: str) -> bool:
    """True if a completion key is reachable via env or the Settings field."""
    if os.environ.get(env_var):
        return True
    return bool(getattr(settings, _KEY_ENV_TO_FIELD[env_var], None))


def discover_providers(settings: Settings) -> list[dict[str, Any]]:
    """Per-provider availability for the Settings chooser (read-only)."""
    out: list[dict[str, Any]] = []

    keys_present = [v for v in _KEY_ENV_TO_FIELD if _api_key_set(settings, v)]
    out.append(
        {
            "name": "akms",
            "label": "API (LiteLLM)",
            "route": "api",
            "available": bool(keys_present),
            "detail": ", ".join(keys_present) if keys_present else "no API key set",
        }
    )

    for name, (binary, _template) in _BUILTIN_CLIS.items():
        on_path = shutil.which(binary) is not None
        out.append(
            {
                "name": name,
                "label": binary,
                "route": "cli",
                "available": on_path,
                "detail": f"{binary} on PATH" if on_path else f"{binary} not found",
            }
        )

    nlm_ok = shutil.which("nlm") is not None
    out.append(
        {
            "name": "nlm",
            "label": "nlm (NotebookLM)",
            "route": "cli",
            "available": nlm_ok,
            "detail": "nlm on PATH" if nlm_ok else "nlm not found",
        }
    )

    for label, command in _custom_clis(settings).items():
        cmd = (command or "").strip()
        if not cmd:
            out.append(
                {
                    "name": label,
                    "label": label,
                    "route": "cli",
                    "available": False,
                    "detail": "not configured (empty command)",
                }
            )
            continue
        argv = _safe_split(cmd)
        if argv is None:
            # Malformed command (e.g. unbalanced quote) — degrade to an unavailable
            # row instead of raising into the request path.
            out.append(
                {
                    "name": label,
                    "label": label,
                    "route": "cli",
                    "available": False,
                    "detail": "invalid command (unparseable)",
                }
            )
            continue
        on_path = bool(argv) and shutil.which(argv[0]) is not None
        out.append(
            {
                "name": label,
                "label": label,
                "route": "cli",
                "available": on_path,
                "detail": cmd if on_path else f"{argv[0] if argv else '?'} not found",
            }
        )

    return out


def inject_api_keys_into_env(settings: Settings, *, force: bool = False) -> list[str]:
    """Export configured completion keys into the process env.

    Lights up the existing LiteLLM ``akms`` route + akms_learn's runtime gate
    without changing their code. By default only fills an env var that is unset
    (startup path); ``force=True`` overwrites it — used by the in-app reload so a
    changed key takes effect without an app restart. Returns the env-var names
    written.
    """
    injected: list[str] = []
    for env_var, field in _KEY_ENV_TO_FIELD.items():
        val = getattr(settings, field, None)
        if val and (force or not os.environ.get(env_var)):
            os.environ[env_var] = val
            injected.append(env_var)
    return injected


def register_host_llm_providers(settings: Settings) -> list[str]:
    """Register on-PATH built-in agents + configured custom CLIs. Returns names.

    A custom CLI is registered only when its binary is on PATH — aligned with
    :func:`available_cli_providers`, so "registered" and "available" never
    disagree about which custom providers are usable.
    """
    from akms_learn.llm.registry import register

    registered: list[str] = []
    for name, (binary, template) in _BUILTIN_CLIS.items():
        if shutil.which(binary) is not None:
            register(name, _make_provider(name, template))
            registered.append(name)
    for label, command in _custom_clis(settings).items():
        cmd = (command or "").strip()
        if not cmd:
            continue
        argv = _safe_split(cmd)
        if argv and shutil.which(argv[0]) is not None:
            register(label, _make_provider(label, cmd))
            registered.append(label)
    if registered:
        logger.info("registered host LLM CLI providers: %s", ", ".join(registered))
    return registered


def rewire_host_llm(settings: Settings) -> dict[str, list[str]]:
    """Re-apply provider wiring after a config change — no app restart.

    Force-injects configured keys into the env (overwriting stale values) and
    re-registers providers ONCE (idempotent). Returns a per-consumer summary
    alongside the flat ``injected``/``registered`` keys kept for back-compat.

    Per-consumer dispatch points:
    - ``learn``   — wired (injected + registered).
    - ``zsum``    — always empty: its provider is applied per run at the adapter
      boundary (``adapters/zsum_adapter.py``), not through the process env.
    - ``nodegen`` — always empty: its provider env is applied per dispatch via
      the subprocess ``env=`` (``akms_dispatch.py``), not through the process env.

    The returned dict shape:
    ``{"injected": [...], "registered": [...], "learn": [...], "zsum": [], "nodegen": []}``.
    """
    injected = inject_api_keys_into_env(settings, force=True)
    registered = register_host_llm_providers(settings)
    return {
        "injected": injected,
        "registered": registered,
        # Per-consumer summaries. Only learn is wired through the process env;
        # zsum and nodegen resolve their provider per run at the adapter boundary.
        "learn": injected + registered,
        "zsum": [],
        "nodegen": [],
    }


__all__ = [
    "_build_prompt",
    "_normalize_cli_output",
    "_run_cli_template",
    "_make_provider",
    "_custom_clis",
    "available_cli_providers",
    "_api_key_set",
    "discover_providers",
    "inject_api_keys_into_env",
    "register_host_llm_providers",
    "rewire_host_llm",
    # module-level names the existing test suite accesses via `p.shutil`
    "shutil",
    # constants accessed by tests indirectly
    "_CLI_TIMEOUT_S",
    "_BUILTIN_CLIS",
    "_KEY_ENV_TO_FIELD",
    "_FULL_FENCE_RE",
    "logger",
]
