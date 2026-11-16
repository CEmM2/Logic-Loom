"""Provider clients for the Assistant Discovery surface.

Two async HTTP clients live here:

* :class:`OpenAlexClient` — wraps ``https://api.openalex.org/works`` with the
  polite-pool ``mailto=`` contact pulled from settings.
* :class:`SemanticScholarClient` — wraps the Semantic Scholar Graph API
  (search + recommendations); the optional API key from settings switches
  the request to the authenticated rate-limit pool.

Both raise the same error taxonomy (see :mod:`.errors`):

* :class:`ProviderRateLimited` (429, ``retry_after`` integer attribute)
* :class:`ProviderNotFound` (404)
* :class:`ProviderTimeout` (request timeout)
* :class:`ProviderError` (umbrella — also wraps other 4xx/5xx)

Key principle: ``only_with_pdf`` defaults to ``False`` in
:class:`ProviderSearchFilters`, so search never silently drops papers that
lack an open-access PDF. Pinned by test ``test_only_with_pdf_default_is_false``.
"""

from __future__ import annotations

from .errors import (
    ProviderError,
    ProviderNotFound,
    ProviderRateLimited,
    ProviderTimeout,
)
from .openalex import OpenAlexClient, ProviderSearchFilters
from .semantic_scholar import SemanticScholarClient

__all__ = [
    "OpenAlexClient",
    "ProviderError",
    "ProviderNotFound",
    "ProviderRateLimited",
    "ProviderSearchFilters",
    "ProviderTimeout",
    "SemanticScholarClient",
]
