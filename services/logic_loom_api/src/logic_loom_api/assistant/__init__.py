"""Assistant mode services.

Contents:
  - workspace bootstrap
  - services (discovery, zotero_match, related, batches, workflows,
    runner, output_store, research_index) + the
    get_assistant_services(settings) factory

The package itself stays lightweight — submodules are imported lazily by
callers (routes, lifespan hooks) so this top-level file does not pull in
httpx, sqlite, etc.
"""

from .bootstrap import bootstrap_assistant_workspace
from .services import AssistantServices, get_assistant_services

__all__ = [
    "AssistantServices",
    "bootstrap_assistant_workspace",
    "get_assistant_services",
]
