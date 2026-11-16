"""Unified host-LLM provider routes. ``/api/llm/*`` generalises the legacy
``/api/learn/*`` provider chooser to all consumers (learn/zsum/nodegen). Mounted
via try/except in main.py — degrade-gracefully: never 503.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter

router = APIRouter()
logger = logging.getLogger(__name__)


def _reload_core() -> tuple[Any, dict[str, list[str]], list[str]]:
    """Shared reload side-effect for BOTH reload endpoints.

    Reloads settings from disk and re-applies host-LLM wiring for all consumers,
    returning ``(settings, rewired_summary, warnings)``. Degrade-gracefully:
    a rewire failure logs + records a warning entry and still returns — never
    raises into the request path.
    """
    from ..deps import get_settings, reload_settings
    from ..learn_llm_providers import rewire_host_llm

    reload_settings()
    settings = get_settings()
    rewired: dict[str, list[str]] = {
        "injected": [],
        "registered": [],
        "learn": [],
        "zsum": [],
        "nodegen": [],
    }
    warnings: list[str] = []
    try:
        rewired = rewire_host_llm(settings)
    except Exception as exc:  # noqa: BLE001 — preserve degrade-gracefully
        logger.warning("host LLM provider rewire skipped: %s", exc)
        warnings.append(f"rewire skipped: {exc.__class__.__name__}")
    return settings, rewired, warnings


def _reload_response(
    settings: Any,
    rewired: dict[str, list[str]],
    warnings: list[str],
    *,
    selected: str | None,
) -> dict[str, Any]:
    """Format a reload response from a :func:`_reload_core` result.

    ``selected`` is surface-specific: the unified ``/api/llm`` reload passes the
    EFFECTIVE Learn choice (``effective_llm_provider("learn")``), while the
    frozen legacy ``/api/learn`` reload passes the RAW ``learn_llm_provider`` so its
    ``selected`` stays consistent with ``GET /api/learn/providers``
    (back-compat). The wiring side-effect itself is identical for both — only the
    reported ``selected`` differs, mirroring the GET endpoints' raw/effective split.
    """
    from ..learn_llm_providers import discover_providers

    return {
        "reloaded": True,
        "providers": discover_providers(settings),
        "selected": selected,
        "warnings": warnings,
        **rewired,
    }


def _discovery_payload() -> dict[str, Any]:
    """Body for ``GET /api/llm/providers`` (the unified discovery endpoint).

    Read-only — lists provider availability + the effective per-consumer choice.
    Degrade-gracefully: ``discover_providers`` already degrades; nothing here
    can raise into the request path. Never 503.

    NOTE: ``GET /api/learn/providers`` does NOT call this helper — it is kept
    independently in its frozen legacy shape (``{providers, selected}`` with the raw
    ``learn_llm_provider``; see ``routes/learn.py::learn_providers``, which is
    kept byte-identical for back-compat). This payload is the superset: ``selected`` mirrors
    ``effective["learn"]`` (a reading convenience) and ``effective`` is the new
    per-consumer map.
    """
    from ..deps import get_settings
    from ..learn_llm_providers import discover_providers

    settings = get_settings()
    return {
        "providers": discover_providers(settings),
        # Convenience: `selected` mirrors effective["learn"] (the effective Learn
        # choice) so a client reading just the Learn row needn't index `effective`.
        "selected": settings.effective_llm_provider("learn"),
        # Per-consumer effective map (feeds the frontend provider chooser).
        "effective": {
            "learn": settings.effective_llm_provider("learn"),
            "zsum": settings.effective_llm_provider("zsum"),
            "nodegen": settings.effective_llm_provider("nodegen"),
        },
    }


@router.get("/providers")
def llm_providers() -> dict[str, Any]:
    """List all host-LLM providers with availability + the effective per-consumer choice.

    Returns ``providers`` (list of provider rows), ``selected`` (effective Learn
    choice), and ``effective`` (per-consumer map: ``learn`` / ``zsum`` /
    ``nodegen``). Read-only, never 503. This is the generalisation of the legacy
    ``GET /api/learn/providers``. The two endpoints are NOT payload-identical:
    ``/api/learn/providers`` is retained at its old path in its frozen legacy shape
    (``{providers, selected}`` with the raw ``learn_llm_provider``, no
    ``effective`` map — kept byte-identical for back-compat, see
    ``routes/learn.py::learn_providers``); this endpoint is the superset that adds
    the per-consumer ``effective`` map. They share the same ``providers``
    availability source.
    """
    return _discovery_payload()


@router.post("/providers/reload")
def llm_providers_reload() -> dict[str, Any]:
    """Re-apply host-LLM provider wiring for all consumers without an app restart.

    Reloads settings from disk, force-injects API keys, re-registers CLI
    providers, and returns a per-consumer rewire summary. ``selected`` is the
    EFFECTIVE Learn choice. The legacy ``POST /api/learn/providers/reload`` shares
    the same reload side-effect (:func:`_reload_core`) but returns the RAW
    ``learn_llm_provider`` for ``selected`` (back-compat). Degrade-gracefully:
    never 503 — rewire failures become a ``warnings`` entry.
    """
    settings, rewired, warnings = _reload_core()
    return _reload_response(
        settings, rewired, warnings, selected=settings.effective_llm_provider("learn")
    )
