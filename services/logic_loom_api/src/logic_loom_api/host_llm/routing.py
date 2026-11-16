"""host_llm/routing.py — centralised host-LLM provider resolution + transport.

This module is the single source of truth for "which LLM does consumer X use,
and how do I reach it".  It holds the original Learn resolver cluster
(``HostLLMProvider`` + the ``_resolve_*`` helpers + ``resolve_host_llm_provider``)
and adds two cross-consumer abstractions on top:

* :func:`resolve_choice` — per-consumer override → global ``llm_provider`` →
  the auto precedence (explicit registry name > completion key > CLI on PATH
  > nlm > None).  ``resolve_host_llm_provider`` is now a thin delegation to
  ``resolve_choice(settings, "learn")`` so the two can never drift.
* :func:`route_for` — resolves a choice and maps it to a :class:`Transport`
  descriptor, delegating concrete API-model selection to
  :func:`host_llm.model_map.api_model_for`.

Import discipline: every ``akms_learn`` / ``zsum`` import is lazy
(inside a function) so this module imports cleanly in a core-only sidecar, and
each resolver branch degrades to ``None`` rather than raising.

``resolve_host_llm_provider`` stays importable from
``adapters/learn_adapter.py`` (re-export) for back-compat — its external callers
(e.g. the provider-bridge tests) keep resolving unchanged.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from . import model_map

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..config import Settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Registry-name constants (moved verbatim from learn_adapter.py).
# ---------------------------------------------------------------------------
#
# Registry names mirror akms_learn's public provider-name constants
# (``akms_learn.llm.providers.akms_completion.AKMS_PROVIDER_NAME`` and
# ``…nlm_cli.NLM_PROVIDER_NAME``). They are kept as literals here rather than
# imported so this module still imports cleanly when ``akms_learn`` is absent
# (the find_spec / lazy-import discipline the rest of the code follows — no
# eager ``akms_learn`` import at module load). A drift-guard in the test
# suite asserts these stay equal to the upstream constants, so an upstream rename
# fails CI rather than silently breaking resolution.
#: akms_learn registry name for the completion provider (over
#: ``akms.orchestrator.call_llm``).
_COMPLETION_PROVIDER_NAME = "akms"
#: akms_learn registry name for the grounded NotebookLM provider.
_NLM_PROVIDER_NAME = "nlm"

#: Completion API-key env vars akms_learn's gate AND the ``akms.orchestrator``
#: LLM router (LiteLLM) honour. Mirrors
#: ``akms_learn.capability_gates._LLM_PROVIDER_ENV_VARS`` — kept as a local
#: constant so resolution does not depend on a private upstream symbol.
_COMPLETION_ENV_VARS: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
)
#: Env vars naming a configured NotebookLM notebook (akms_learn grounded gate).
_NLM_NOTEBOOK_ENV_VARS: tuple[str, ...] = (
    "AKMS_LEARN_NLM_NOTEBOOK_ID",
    "NLM_NOTEBOOK_ID",
)


@dataclass(slots=True, frozen=True)
class HostLLMProvider:
    """A host-resolved LLM provider for learning expansion.

    ``provider`` is an akms_learn registry name (:data:`_COMPLETION_PROVIDER_NAME`
    or :data:`_NLM_PROVIDER_NAME`). ``sources`` is the grounding bundle threaded
    onto ``LearningRequest.sources`` (``{"notebook_id": …}`` for nlm; ``None`` for
    completion). ``origin`` is a short human-readable description for logs.
    """

    provider: str
    sources: dict[str, Any] | None = None
    origin: str = ""


@dataclass(frozen=True)
class Transport:
    """How to actually reach a resolved provider.

    ``kind="api"`` providers (``akms`` + the LiteLLM families) carry a concrete
    ``model`` id (from :func:`host_llm.model_map.api_model_for`) and no
    ``argv_template``. ``kind="cli"`` agent providers (``claude-cli`` / ``codex-cli``
    / a custom label) carry an ``argv_template`` (from discovery) and no ``model``.
    ``origin`` is carried through from the resolved :class:`HostLLMProvider` so the
    resolution trace survives into transport logs.
    """

    kind: Literal["api", "cli"]
    provider: str  # registry/host name, e.g. "akms", "claude-cli"
    model: str | None  # concrete API model id (api) or None (cli)
    argv_template: str | None  # CLI template, e.g. "claude -p --output-format text"
    origin: str  # human-readable resolution trace, for logs


def _completion_env_key_present() -> str | None:
    """Return the first completion API-key env var that is set, else ``None``."""
    for var in _COMPLETION_ENV_VARS:
        if os.environ.get(var):
            return var
    return None


def _zsum_completion_env_key(settings: Settings | None) -> str | None:
    """Return the host zsum LLM key's env-var name when usably set, else ``None``.

    Honours the Settings ▸ LLM choice by reading the host's ``zsum.config.yaml``
    through zsum's OWN loader (``zsum.config.load_config``) — never by re-parsing
    the YAML here. Returns the active provider's ``api_key_env`` only when it is
    one of the standard completion vars (:data:`_COMPLETION_ENV_VARS`) AND that
    var is present in the process env — i.e. a key the akms completion adapter
    (Anthropic/LiteLLM, env-sourced) can actually use. Inline-key or
    non-env-routable providers (e.g. an ``openai_compat`` base_url) are not
    supported here and degrade to ``None`` rather than a mid-compile failure. Any
    error (no config, zsum absent, unexpected shape) degrades to ``None`` — this
    helper never raises.
    """
    if settings is None:
        return None
    try:
        from zsum.config import load_config
    except ImportError:
        return None
    try:
        path = settings.effective_zsum_config_path()
        if not path.exists():
            return None
        cfg = load_config(path)
        llm = cfg.llm
        provider_cfg = llm.config_for_provider(llm.provider)
        api_key_env = getattr(provider_cfg, "api_key_env", None)
    except Exception as exc:  # degrade-gracefully — host config is advisory here
        logger.debug("zsum llm config resolution skipped: %s", exc)
        return None
    if api_key_env in _COMPLETION_ENV_VARS and os.environ.get(api_key_env):
        return api_key_env
    return None


def _resolve_completion_provider(settings: Settings | None) -> HostLLMProvider | None:
    """Resolve the host completion provider, or ``None`` when unusable.

    The akms_learn completion provider authenticates via
    ``akms.orchestrator.call_llm`` → LiteLLM, which reads its credential from the
    process env, so "completion configured" means a usable key is reachable in
    env. The host's Settings ▸ LLM choice is honoured FIRST
    (:func:`_zsum_completion_env_key`, which consults ``zsum.config.yaml`` through
    zsum's own loader) so a configured provider yields precise provenance; a key
    merely exported into the sidecar (:func:`_completion_env_key_present`) is the
    fallback. Both resolve to one of :data:`_COMPLETION_ENV_VARS`, which is
    exactly what akms_learn's runtime gate + LiteLLM read — so a resolved
    completion provider always compiles end-to-end.
    """
    zsum_var = _zsum_completion_env_key(settings)
    if zsum_var is not None:
        return HostLLMProvider(
            provider=_COMPLETION_PROVIDER_NAME, origin=f"zsum llm config ({zsum_var})"
        )
    env_var = _completion_env_key_present()
    if env_var is not None:
        return HostLLMProvider(
            provider=_COMPLETION_PROVIDER_NAME, origin=f"completion env ({env_var})"
        )
    return None


def _resolve_nlm_provider(settings: Settings | None) -> HostLLMProvider | None:
    """Resolve the grounded NotebookLM provider, or ``None`` when unusable.

    Grounded generation needs the ``nlm`` CLI on PATH and a configured notebook
    id. The id is read from the akms_learn grounded env vars
    (:data:`_NLM_NOTEBOOK_ENV_VARS`) — the same vars akms_learn's runtime gate
    checks — and threaded onto the request as ``sources={"notebook_id": …}`` so
    the grounded provider can attach the notebook's sources. Returns ``None``
    when the CLI is missing or no notebook id is configured.
    """
    if shutil.which("nlm") is None:
        return None
    notebook_id = next(
        (os.environ[v] for v in _NLM_NOTEBOOK_ENV_VARS if os.environ.get(v)),
        None,
    )
    if not notebook_id:
        return None
    return HostLLMProvider(
        provider=_NLM_PROVIDER_NAME,
        sources={"notebook_id": notebook_id},
        origin="nlm workflow",
    )


def _resolve_explicit_provider(
    settings: Settings | None, choice: str | None
) -> HostLLMProvider | None:
    """Resolve an explicit provider *choice*, or ``None``.

    *choice* is the already-computed effective provider string (per-consumer
    override → global ``llm_provider``) — passing it in rather than re-reading a
    hard-coded Settings field is the only change from the original Learn-only
    ``_resolve_explicit_provider(settings)``; the branch logic is identical.

    This is the ONLY path that activates an agent CLI (``claude-cli`` /
    ``codex-cli`` / a custom label) — mirroring the ``nlm`` rule, which also
    requires explicit configuration (a notebook id) rather than mere CLI
    presence. So a host with ``claude`` on PATH but no chosen provider keeps the
    modes gated, and existing no-provider behaviour is unchanged. ``akms`` and
    ``nlm`` choices are validated through their own resolvers so the gate never
    reports a provider that could not actually compile.
    """
    choice = (choice or "").strip()
    if not choice or choice == "auto":
        return None
    if choice == "akms":
        return _resolve_completion_provider(settings)  # requires a usable key
    if choice == "nlm":
        return _resolve_nlm_provider(settings)  # requires nlm CLI + notebook id
    # Agent CLI or custom label — usable only when its binary is on PATH.
    try:
        from ..learn_llm_providers import available_cli_providers

        if choice in available_cli_providers(settings):
            return HostLLMProvider(provider=choice, origin=f"explicit ({choice})")
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("explicit CLI provider resolution skipped: %s", exc)
    return None


def resolve_choice(
    settings: Settings | None,
    consumer: Literal["learn", "zsum", "nodegen"],
) -> HostLLMProvider | None:
    """Resolve the effective host LLM provider for *consumer*.

    Precedence (the cross-consumer generalisation of the original Learn resolver):

    1. Explicit choice — the per-consumer override field
       ``settings.<consumer>_llm_provider`` wins, falling back to the global
       ``settings.llm_provider``, then finally the legacy ``settings.learn_llm_provider``
       name (back-compat alias — fires only when both the per-consumer override
       and ``llm_provider`` are unset). All three are read **defensively** via
       ``getattr`` so the resolver keeps working with ``SimpleNamespace``/stub
       settings in tests. For ``consumer="learn"`` the per-consumer field *is*
       ``learn_llm_provider`` — exactly the field Learn always used — so its behaviour
       is byte-for-byte unchanged (the alias term is redundant for ``learn`` and
       only activates the global/alias fallback for ``zsum``/``nodegen``).
    2. Auto-resolution — unchanged from the original Learn resolver: grounded ``nlm`` is preferred over
       ``akms`` completion when both are configured (grounding yields cited,
       source-locked expansion).

    Returns ``None`` when nothing is configured. Never raises: each branch
    degrades to ``None`` on error. CLI agents are never auto-selected on mere
    PATH presence (only the explicit path activates them — see
    :func:`_resolve_explicit_provider`).
    """
    # 1. Explicit choice: per-consumer override field, then global llm_provider,
    #    then the legacy learn_llm_provider name (back-compat alias). The alias
    #    term is purely additive — it only fires when both the override and
    #    llm_provider are None, so Learn's precedence stays byte-for-byte (its
    #    override field already *is* learn_llm_provider). getattr keeps this
    #    working with SimpleNamespace/stub settings. Kept equal-by-construction
    #    with Settings.effective_llm_provider.
    effective = (
        getattr(settings, f"{consumer}_llm_provider", None)
        or getattr(settings, "llm_provider", None)
        or getattr(settings, "learn_llm_provider", None)
    )
    explicit = _resolve_explicit_provider(settings, effective)
    if explicit is not None:
        return explicit
    # 2. Auto precedence (nlm grounded > akms completion).
    return _resolve_nlm_provider(settings) or _resolve_completion_provider(settings)


def resolve_host_llm_provider(settings: Settings | None) -> HostLLMProvider | None:
    """Resolve the host's configured LLM provider for learning expansion.

    Thin delegation to ``resolve_choice(settings, "learn")`` — defined this way so
    the original public function and the per-consumer resolver are identical *by
    construction* and can never drift. Precedence: an explicit
    ``learn_llm_provider`` choice wins; otherwise auto-resolution is
    unchanged — grounded ``nlm`` is preferred over ``akms`` completion when both
    are configured. Returns ``None`` when nothing is configured, leaving the
    ``llm_expanded`` / ``adaptive_path`` modes disabled. CLI agents are never
    auto-selected on mere PATH presence. Never raises.
    """
    return resolve_choice(settings, "learn")


def route_for(
    settings: Settings | None,
    consumer: Literal["learn", "zsum", "nodegen"],
    *,
    task_kind: str = "default",
) -> Transport | None:
    """Resolve *consumer*'s provider and map it to a :class:`Transport`.

    Returns ``None`` when no provider resolves (same gate as
    :func:`resolve_choice`). Otherwise classifies the resolved provider:

    * **CLI provider** — the resolved name appears in
      :func:`host_llm.discovery.available_cli_providers` (built-in agents +
      configured custom labels). ``kind="cli"`` with the ``argv_template`` sourced
      from the discovery registry and ``model=None``.
    * **API / grounded provider** — everything else (``akms`` completion, the
      LiteLLM families, and the grounded ``nlm`` provider). ``kind="api"`` with
      ``model`` delegated to :func:`host_llm.model_map.api_model_for` and
      ``argv_template=None``.

    ``nlm`` is bucketed as ``kind="api"`` deliberately: although it shells out to
    the ``nlm`` CLI, akms_learn treats it as a *grounded* provider with a logical
    model id (``"notebooklm"``, via model_map), not as a generic argv agent-CLI.
    route_for's ``cli`` bucket is reserved for the ``{prompt}``/stdin agent-CLI
    transport; ``nlm``'s model-id contract fits the ``api`` bucket cleanly.
    """
    hp = resolve_choice(settings, consumer)
    if hp is None:
        return None

    # Lazy import keeps this module importable in a core-only sidecar and avoids
    # any import cycle through discovery.
    from .discovery import available_cli_providers

    if hp.provider in available_cli_providers(settings):
        argv_template = cli_argv_template(settings, hp.provider)
        return Transport(
            kind="cli",
            provider=hp.provider,
            model=None,
            argv_template=argv_template,
            origin=hp.origin,
        )

    # API / grounded provider: concrete model id comes from model_map (the test
    # pins this delegation). Unknown providers raise UnknownProviderError there.
    return Transport(
        kind="api",
        provider=hp.provider,
        model=model_map.api_model_for(hp.provider, consumer, task_kind),
        argv_template=None,
        origin=hp.origin,
    )


def cli_argv_template(settings: Settings | None, provider: str) -> str | None:
    """Source a CLI provider's argv template from the discovery registry.

    Built-in agents come from ``_BUILTIN_CLIS`` (name → (binary, template));
    custom labels come from ``settings.learn_llm_custom_clis`` via
    ``_custom_clis``. Returns ``None`` when *provider* is not a known CLI
    provider (``route_for`` only reaches this for names already in
    ``available_cli_providers``, so it never returns ``None`` from there).

    Public cross-module seam: ``akms_dispatch`` reuses this for node-gen
    dispatch so the built-in-then-custom sourcing rule lives in ONE place
    (no duplicated copy to drift). The lazy ``discovery`` import keeps this
    module importable in a core-only sidecar and avoids an import cycle.
    """
    from .discovery import _BUILTIN_CLIS, _custom_clis

    builtin = _BUILTIN_CLIS.get(provider)
    if builtin is not None:
        return builtin[1]
    return _custom_clis(settings).get(provider)


__all__ = [
    "HostLLMProvider",
    "Transport",
    "resolve_choice",
    "route_for",
    "cli_argv_template",
    "resolve_host_llm_provider",
    "_resolve_explicit_provider",
    "_resolve_nlm_provider",
    "_resolve_completion_provider",
    "_completion_env_key_present",
    "_zsum_completion_env_key",
    "_COMPLETION_PROVIDER_NAME",
    "_NLM_PROVIDER_NAME",
    "_COMPLETION_ENV_VARS",
    "_NLM_NOTEBOOK_ENV_VARS",
    "logger",
]
