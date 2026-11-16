"""Assistant output store service stub.

The Assistant outputs API (``/api/assistant/outputs``, see
:mod:`logic_loom_api.routes.assistant_outputs`) is served from the shared
run registry; this facade is not wired yet. The intended design is a
workspace-scoped output browser, rerun + comparison hooks, and the
markdown / JSON / PDF artefact layer over `<assistant_workspace>/
outputs/`.

This is kept as a distinct service rather than folded into `runner`
because the outputs API is built independently of the run dispatcher.
"""

from __future__ import annotations

from typing import Any

from ..config import Settings


class OutputStoreService:
    """Workflow-output browser facade (stub).

    The real outputs API over `<assistant_workspace>/outputs/` lives in
    :mod:`logic_loom_api.routes.assistant_outputs`.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def list_outputs(self, **filters: Any) -> Any:
        raise NotImplementedError("OutputStoreService.list_outputs is not implemented")

    def read_output(self, output_id: str) -> Any:
        raise NotImplementedError("OutputStoreService.read_output is not implemented")
