"""Provider error taxonomy for the Assistant Discovery clients.

Every provider client (OpenAlex, Semantic Scholar) raises one of these
exceptions on upstream failure. Callers can either:

* catch the umbrella :class:`ProviderError` (for blanket fallback / logging),
* or catch a specific subclass (e.g. :class:`ProviderRateLimited` to surface
  a "try again in N seconds" banner without dropping into a stack trace).

The hierarchy lets routes pattern-match without sniffing string messages.
"""

from __future__ import annotations


class ProviderError(Exception):
    """Umbrella for any error originating from an Assistant Discovery
    provider client.

    Carries optional ``provider`` and ``status_code`` attributes so callers
    can log + render uniformly. Specific upstream failure modes live in
    the subclasses below.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code


class ProviderRateLimited(ProviderError):
    """Raised when the provider returns HTTP 429.

    The ``retry_after`` attribute is the integer number of seconds the
    upstream told us to wait (parsed from the ``Retry-After`` header).
    If the header is absent or unparseable, ``retry_after`` is 0 — callers
    treat 0 as "back off using your own policy".
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        retry_after: int = 0,
    ) -> None:
        super().__init__(message, provider=provider, status_code=429)
        self.retry_after = retry_after


class ProviderNotFound(ProviderError):
    """Raised when the provider returns HTTP 404 for a specific resource."""

    def __init__(self, message: str, *, provider: str | None = None) -> None:
        super().__init__(message, provider=provider, status_code=404)


class ProviderTimeout(ProviderError):
    """Raised when an upstream request times out (network or read).

    No ``status_code`` — the request never reached the application layer.
    """

    def __init__(self, message: str, *, provider: str | None = None) -> None:
        super().__init__(message, provider=provider, status_code=None)


__all__ = [
    "ProviderError",
    "ProviderNotFound",
    "ProviderRateLimited",
    "ProviderTimeout",
]
