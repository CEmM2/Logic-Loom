"""Assistant workflows service.

Real implementation: filtered view over the shared workflow library
from :mod:`logic_loom_api.workflows`. The class is kept as a thin
facade so existing callers from the service factory
(:func:`logic_loom_api.assistant.get_assistant_services`) keep working.
The HTTP surface lives in :mod:`logic_loom_api.routes.assistant_workflows`.
"""

from __future__ import annotations

from typing import Any

from ..config import Settings
from ..workflows import list_bundled_workflows


# Closed set — kept here as a re-export so callers can
# import the category set from either the service module or the routes
# module without circular-import worries.
ASSISTANT_CATEGORIES: frozenset[str] = frozenset(
    {"Literature Review", "Paper Writing", "Proposal Writing", "Analysis"}
)


class WorkflowService:
    """Assistant workflow library facade.

    Provides a listing method that filters the shared bundled-workflow
    catalog by the
    documented Assistant categories. The route layer
    (:mod:`logic_loom_api.routes.assistant_workflows`) is the canonical
    HTTP surface; this service exists so the service-factory
    record stays usable for downstream callers that want the listing
    without an HTTP round-trip.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def list_workflows(self) -> list[dict[str, Any]]:
        """Return the bundled Assistant workflows as serialisable dicts.

        Only bundled defaults are surfaced through this service path —
        user-side workflows live on the filesystem and are mediated
        through the routes layer. Callers that need user-side workflows
        (e.g. the Run dialog UI) hit
        ``GET /api/assistant/workflows`` instead.
        """
        return [
            wf.model_dump()
            for wf in list_bundled_workflows()
            if wf.category in ASSISTANT_CATEGORIES
        ]


__all__ = ["ASSISTANT_CATEGORIES", "WorkflowService"]
