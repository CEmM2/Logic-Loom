"""host_llm/model_map.py — resolve (provider, consumer) to a concrete API model id.

The lookup table makes per-consumer model divergence explicit and traceable.
Every model id carries a source comment pointing at the upstream file:line it was
copied from.  When upstream defaults drift, update the constants block below and
the comment — that is the entire point of this module.

Override precedence:
    1. [FUTURE] explicit ``*_llm_model`` Settings field — slot in here if
       per-consumer model overrides are added to Settings.  ``api_model_for`` already
       accepts ``task_kind`` for that extension seam.
    2. Per-consumer table entry  (``_CONSUMER_MODEL_TABLE``)
    3. Per-provider default      (``_PROVIDER_DEFAULTS``)
    4. Unknown provider          → raises ``UnknownProviderError``

Settings-field overrides are not implemented yet. If they are added, wrap ``api_model_for`` or add a higher-level resolver that calls it.
"""

from __future__ import annotations

__all__ = ["api_model_for", "UnknownProviderError"]


# ---------------------------------------------------------------------------
# Constants block — one source of truth for every model id used by this module.
# Each value MUST carry a "# from <file>:<line>" comment so drift is traceable.
# ---------------------------------------------------------------------------

# Per-provider defaults (sourced from node-gen's PROVIDERS dict — the canonical
# cross-consumer baseline).
_PROVIDER_DEFAULTS: dict[str, str] = {
    # from akms_nodes_gen/generate_nodes_pipeline.py:95
    "anthropic": "claude-sonnet-4-20250514",
    # from akms_nodes_gen/generate_nodes_pipeline.py:101
    "openai": "gpt-4o",
    # from akms_nodes_gen/generate_nodes_pipeline.py:107
    "google": "gemini-2.0-flash",
}

# Per-(provider, consumer) overrides.  Only populated where the consumer's
# documented default *diverges* from the per-provider default above (YAGNI).
_CONSUMER_MODEL_TABLE: dict[tuple[str, str], str] = {
    # --- zsum (zotero-summarizer) ---
    # from zotero-summarizer's config.example.yaml:68
    ("anthropic", "zsum"): "claude-sonnet-4-6",

    # --- nodegen (AKMS_nodes_gen) ---
    # Matches the provider default, kept explicit so the table is self-documenting.
    # from akms_nodes_gen/generate_nodes_pipeline.py:95
    ("anthropic", "nodegen"): "claude-sonnet-4-20250514",
    # from akms_nodes_gen/generate_nodes_pipeline.py:101
    ("openai", "nodegen"): "gpt-4o",
    # from akms_nodes_gen/generate_nodes_pipeline.py:107
    ("google", "nodegen"): "gemini-2.0-flash",

    # --- learn (akms_learn) ---
    # akms and nlm are grounded/router providers — their "model" is a logical id,
    # not a real API model string, but the same lookup contract applies.
    # from akms_learn/llm/providers/akms_completion.py:40
    ("akms", "learn"): "akms-router",
    # from akms_learn/llm/providers/nlm_cli.py:53
    ("nlm", "learn"): "notebooklm",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class UnknownProviderError(KeyError):
    """Raised when *provider* is not present in the provider-defaults table.

    Inherits ``KeyError`` so callers that catch ``KeyError`` still work; the
    distinct type lets callers be more specific if they choose.
    """


def api_model_for(provider: str, consumer: str, task_kind: str = "default") -> str:
    """Return the concrete API model id for *provider* / *consumer* / *task_kind*.

    Lookup precedence:
        1. ``_CONSUMER_MODEL_TABLE[(provider, consumer)]``
        2. ``_PROVIDER_DEFAULTS[provider]``
        3. ``UnknownProviderError`` — never a silent empty string.

    ``task_kind`` is accepted for forward-compatibility (future per-task model
    selection) but is currently a no-op seed — all task kinds resolve the same
    model.  When task-kind differentiation lands, add a
    ``_TASK_MODEL_TABLE[(provider, consumer, task_kind)]`` checked first.

    Args:
        provider:  API-family name (``"anthropic"``, ``"openai"``, ``"google"``,
                   ``"akms"``, ``"nlm"``).  Not the CLI-tool name — that mapping
                   is done upstream in routing.py.
        consumer:  Which subsystem is asking (``"nodegen"``, ``"zsum"``,
                   ``"learn"``).
        task_kind: Reserved for future per-task model differentiation (no-op now).

    Returns:
        A non-empty model id string.

    Raises:
        UnknownProviderError: If *provider* has no entry in the defaults table
            AND no consumer-specific table entry.
    """
    # Step 1 — per-consumer override
    table_key = (provider, consumer)
    if table_key in _CONSUMER_MODEL_TABLE:
        return _CONSUMER_MODEL_TABLE[table_key]

    # Step 2 — per-provider default
    if provider in _PROVIDER_DEFAULTS:
        return _PROVIDER_DEFAULTS[provider]

    # Step 3 — unknown provider: never return a silent empty string
    raise UnknownProviderError(
        f"No model mapping for provider={provider!r} (consumer={consumer!r}). "
        f"Known providers: {sorted(_PROVIDER_DEFAULTS)}. "
        "Add it to _PROVIDER_DEFAULTS in host_llm/model_map.py."
    )
