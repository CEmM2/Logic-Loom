"""adapters/zsum_adapter.py — map the host LLM choice → zsum provider/model/key.

zsum (the installed ``zotero-summarizer`` package) selects its LLM through its OWN
``zsum.config.yaml`` (``llm.provider`` + a per-provider section) and reads API
keys from the process env (``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY`` /
``GEMINI_API_KEY``). This adapter is the **bridge** between Logic-Loom's unified
host-LLM choice (``llm_provider`` / ``zsum_llm_provider`` resolved through
``host_llm.routing``) and that surface — without re-parsing the YAML by hand and
without ever editing vendored code (hard rule: vendored code is never edited).

What it does and nothing more (YAGNI):

1. :func:`resolve_zsum_runtime` — translate the resolved host choice into a
   :class:`ZsumRuntime` (zsum provider string + model + the key env var to
   inject + the dotted ``zsum.config.yaml`` paths to set). Built on
   ``host_llm.routing.resolve_choice`` / ``route_for`` + ``host_llm.model_map`` —
   the SAME cross-consumer resolution surface Learn and node-gen use.
2. :func:`apply_runtime_to_config` — apply a runtime onto a *constructed*
   ``zsum.config.Config`` object (zsum's public effective-config surface). This
   is the public override path (never ``zsum/llm.py``).
3. :func:`runtime_config_doc_updates` — the same runtime as a flat list of
   comment-preserving ``zsum.config.yaml`` updates, for the route to apply only
   to *blank* fields (mirrors ``routes/zsum.py::_apply_local_model_to_zsum_config``).

Degrade-gracefully (a malformed host LLM config must never 500 the zsum
request path):

* ``resolve_choice`` / ``route_for`` / ``available_cli_providers`` already
  reuse ``host_llm.discovery._safe_split`` so a malformed ``learn_llm_custom_clis``
  value (unbalanced quote, persisted by ConfigUpdate) returns ``None`` rather
  than raising ``shlex`` ``ValueError``. This adapter adds
  a second belt: :func:`resolve_zsum_runtime` wraps the whole resolution in a
  try/except and returns ``None`` on ANY error — so even a future un-guarded
  raise (e.g. ``route_for``'s API bucket calling ``api_model_for`` on a
  resolvable-but-unmapped custom label → ``UnknownProviderError``) degrades to
  "no zsum override", never a 500 into the ``/api/zsum/runs`` request path.
* When no host/zsum choice resolves (and nothing maps), :func:`resolve_zsum_runtime`
  returns ``None`` and the route injects NOTHING — zsum reads its own
  ``zsum.config.yaml`` exactly as before. That no-op back-compat is sacred
  (a regression test guards it); this module is built so it is trivially true.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..config import Settings
from ..host_llm import model_map
from ..host_llm.routing import HostLLMProvider, resolve_choice

if TYPE_CHECKING:  # pragma: no cover - typing only
    from zsum.config import Config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Host-provider → zsum-provider-string mapping.
# ---------------------------------------------------------------------------
#
# zsum's own LLM provider strings are exactly four (vendored
# ``zsum/config.py`` in the installed package — ``LLMConfig`` +
# ``config_for_provider``): ``anthropic`` / ``openai_compat`` / ``codex_exec`` /
# ``gemini_cli``. The keys here are the host labels that flow OUT of
# ``host_llm.routing`` — both the CLI-agent registry names (``claude-cli`` /
# ``codex-cli``, from ``host_llm.discovery._BUILTIN_CLIS``) and the API-family /
# completion names a ``Transport`` can carry (``akms`` completion, plus the
# ``anthropic`` / ``openai`` / ``google`` families ``model_map`` knows). A host
# label that isn't in this table degrades to "no zsum override" (``None``) —
# never an exception into the request path.
#
# FORWARD-LOOKING ROWS — ``openai`` / ``google`` are listed to keep the mapping
# complete, but ``host_llm.routing._resolve_explicit_provider``
# does NOT resolve those family labels TODAY (it resolves only ``akms`` / ``nlm``
# / built-in CLIs / custom labels). So ``Settings(zsum_llm_provider="openai")``
# does not route to ``openai_compat`` yet — it activates only when host routing
# gains those API-family labels. The mapping is in place so it works the moment
# routing does; the test suite asserts both the dict mapping AND that the rows are
# not yet routing-resolvable, so no one mistakes them for working end-to-end.
_HOST_TO_ZSUM_PROVIDER: dict[str, str] = {
    # --- resolvable through host_llm.routing today ---
    # claude-cli (agent CLI) and akms (completion/LiteLLM) both summarise via the
    # Anthropic path — zsum's ``anthropic`` provider shells out to ``claude -p``.
    "claude-cli": "anthropic",
    "akms": "anthropic",
    "anthropic": "anthropic",
    # codex-cli (agent CLI) → zsum's Codex-exec backend.
    "codex-cli": "codex_exec",
    # --- forward-looking (not routing-resolvable yet; see note above) ---
    # OpenAI-API family → zsum's OpenAI-compatible endpoint provider.
    "openai": "openai_compat",
    # google / gemini family → zsum's Gemini CLI backend.
    "google": "gemini_cli",
}

#: zsum reads its completion API key from the process env. Map a zsum provider
#: string to the env var name zsum's own config defaults to for that provider.
#: Only the env-routable (API) providers have a key; the agent-CLI providers
#: (``codex_exec`` / ``gemini_cli``) authenticate through their own CLI session
#: and need no key injected.
#:
#: Deliberately NOT reusing ``host_llm.routing._zsum_completion_env_key``: that
#: seam keys off the YAML's *current* ``llm.provider`` (the pre-override state),
#: whereas this adapter needs the key for the *host-resolved target* provider we are
#: about to switch zsum to. Reusing it would return the wrong (old-provider) key.
_ZSUM_PROVIDER_KEY_ENV: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai_compat": "OPENAI_API_KEY",
}

#: zsum provider string → the ``model_map`` API-family name to look a concrete
#: model id up under. Only the env-routable (API) providers get a concrete model
#: written; the agent-CLI providers (``codex_exec`` / ``gemini_cli``) auto-select
#: their own and are absent here. The model lookup is best-effort: an
#: ``UnknownProviderError`` from ``model_map`` degrades to "no model override"
#: (the provider string alone still switches zsum's backend) — never a raise.
_ZSUM_PROVIDER_MODEL_FAMILY: dict[str, str] = {
    "anthropic": "anthropic",
    "openai_compat": "openai",
}


@dataclass(slots=True, frozen=True)
class ZsumRuntime:
    """The host-derived zsum runtime: a resolved choice mapped to zsum's surface.

    ``provider`` is one of zsum's four provider strings. ``model`` is the concrete
    API model id (from ``host_llm.model_map``) for the API providers, or ``None``
    for the agent-CLI providers (``codex_exec`` / ``gemini_cli``), whose model zsum
    auto-selects. ``env_overrides`` is the (already-present) key env var to forward
    to the zsum subprocess — empty when the provider needs no key. ``config_overrides``
    is the flat dotted-path → value map applied to ``zsum.config.yaml`` /
    a constructed ``Config`` (``llm.provider`` + the per-provider ``model``).
    """

    provider: str
    model: str | None = None
    env_overrides: dict[str, str] = field(default_factory=dict)
    config_overrides: dict[str, str] = field(default_factory=dict)


def _zsum_provider_for(choice: HostLLMProvider) -> str | None:
    """Map a resolved :class:`HostLLMProvider` to a zsum provider string, or ``None``.

    Keys off ``choice.provider`` (the host registry/family label) through
    :data:`_HOST_TO_ZSUM_PROVIDER`. An unmapped label (a custom agent CLI, a
    grounded ``nlm`` provider, or any future family zsum has no backend for)
    returns ``None`` so resolution degrades to the unchanged YAML path.
    """
    return _HOST_TO_ZSUM_PROVIDER.get(choice.provider)


def _zsum_model_for(zsum_provider: str) -> str | None:
    """Return the concrete model id for an API zsum provider, or ``None``.

    Looks the zsum provider's API-family name up in ``model_map.api_model_for``
    (consumer ``"zsum"``). Agent-CLI providers (``codex_exec`` / ``gemini_cli``)
    are absent from :data:`_ZSUM_PROVIDER_MODEL_FAMILY` and return ``None`` — they
    auto-select their own model. An ``UnknownProviderError`` (or any error) from
    ``model_map`` degrades to ``None`` so the provider switch still applies
    without a model override — never raise into the zsum request path.
    """
    family = _ZSUM_PROVIDER_MODEL_FAMILY.get(zsum_provider)
    if family is None:
        return None
    try:
        return model_map.api_model_for(family, "zsum")
    except Exception as exc:  # noqa: BLE001 — degrade to no-model, never raise
        logger.debug("zsum model lookup for %r skipped: %s", family, exc)
        return None


def resolve_zsum_runtime(settings: Settings | None) -> ZsumRuntime | None:
    """Resolve the host choice into a zsum runtime, or ``None`` for "no override".

    Resolution path:

    1. ``host_llm.routing.resolve_choice(settings, "zsum")`` resolves the
       effective zsum choice (per-consumer ``zsum_llm_provider`` override →
       global ``llm_provider`` → legacy ``learn_llm_provider`` alias) to a
       :class:`HostLLMProvider`. Returns ``None`` when nothing is configured —
       the back-compat no-op path (zsum keeps reading its own YAML).
    2. The host label is mapped to one of zsum's four provider strings
       (:func:`_zsum_provider_for`). An unmapped label → ``None``.
    3. The concrete model is looked up DEFENSIVELY for the API providers
       (:func:`_zsum_model_for`, via ``model_map.api_model_for``); agent-CLI
       providers (``codex_exec`` / ``gemini_cli``) carry no model — zsum picks one.
    4. The matching key env var (if present in env) is forwarded; the
       ``llm.provider`` + per-provider ``model`` dotted-path overrides are built.

    ``resolve_choice`` is deliberately used over ``route_for`` here: ``route_for``
    eagerly calls ``api_model_for(provider, "zsum")`` for an API bucket, which
    raises ``UnknownProviderError`` for ``akms`` (no ``("akms","zsum")`` mapping)
    and for any resolvable-but-unmapped custom label. We map the host LABEL first
    (``resolve_choice`` never raises) and look the model up separately so ``akms``
    maps cleanly to ``anthropic`` instead of being swallowed.

    NEVER raises into the caller ("never 500 into the zsum request path"):
    the entire resolution is wrapped in a try/except that degrades to ``None`` —
    a belt over the brace (``resolve_choice`` already reuses ``_safe_split`` for
    malformed custom CLIs).
    """
    if settings is None:
        return None
    try:
        choice = resolve_choice(settings, "zsum")
        if choice is None:
            return None
        zsum_provider = _zsum_provider_for(choice)
        if zsum_provider is None:
            logger.debug(
                "zsum runtime: host provider %r has no zsum backend; "
                "leaving zsum.config.yaml unchanged",
                choice.provider,
            )
            return None

        model = _zsum_model_for(zsum_provider)

        config_overrides: dict[str, str] = {"llm.provider": zsum_provider}
        if model:
            config_overrides[f"llm.{zsum_provider}.model"] = model

        env_overrides: dict[str, str] = {}
        key_env = _ZSUM_PROVIDER_KEY_ENV.get(zsum_provider)
        if key_env:
            value = os.environ.get(key_env)
            if value:
                env_overrides[key_env] = value

        return ZsumRuntime(
            provider=zsum_provider,
            model=model,
            env_overrides=env_overrides,
            config_overrides=config_overrides,
        )
    except Exception as exc:  # noqa: BLE001 — degrade to YAML, never raise
        logger.warning(
            "zsum runtime resolution failed (%s); leaving zsum.config.yaml "
            "unchanged",
            exc,
        )
        return None


def apply_runtime_to_config(cfg: Config, runtime: ZsumRuntime) -> Config:
    """Apply a :class:`ZsumRuntime` onto a constructed ``zsum.config.Config``.

    This is zsum's PUBLIC override surface — the effective ``Config`` object —
    NOT a patch to ``zsum/llm.py`` (vendored code is never edited). Mutates and returns the
    same ``cfg`` so callers can chain. Sets ``cfg.llm.provider`` to the resolved
    zsum provider string and, for the API providers, the per-provider
    ``model`` field (``cfg.llm.<provider>.model``). Agent-CLI providers
    (``codex_exec`` / ``gemini_cli``) carry no model, so the model write is
    skipped and zsum auto-selects.

    Used by the test suite to assert the resolved provider/model land on a real
    zsum ``Config`` with no network — the acceptance contract.
    """
    cfg.llm.provider = runtime.provider
    if runtime.model:
        provider_cfg = cfg.llm.config_for_provider(runtime.provider)
        # All zsum provider-config dataclasses carry a ``model`` field.
        if hasattr(provider_cfg, "model"):
            provider_cfg.model = runtime.model
    return cfg


def runtime_config_doc_updates(runtime: ZsumRuntime) -> list[Any]:
    """Return the runtime's overrides as comment-preserving ``Update`` objects.

    The flat dotted-path overrides become ``zsum_pipeline.config_doc.Update``
    rows so the route can apply them through ``write_doc`` — the same
    comment-preserving editor ``_apply_local_model_to_zsum_config`` uses. The
    route applies them only to *blank* fields so an explicit user value is never
    clobbered. Import is lazy so this module imports cleanly without the route's
    deps.
    """
    from ..zsum_pipeline.config_doc import Update

    return [Update(path=path, value=value) for path, value in runtime.config_overrides.items()]


__all__ = [
    "ZsumRuntime",
    "resolve_zsum_runtime",
    "apply_runtime_to_config",
    "runtime_config_doc_updates",
]
