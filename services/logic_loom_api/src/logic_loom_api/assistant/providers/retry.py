"""Backoff for provider rate limits (HTTP 429).

OpenAlex and Semantic Scholar both rate-limit bursts. A single retry after a
fixed second rarely clears a limit, so :func:`call_with_backoff` retries a few
times with exponential backoff and jitter, honours ``Retry-After`` when the
provider sends one, and gives up straight away when the provider asks for a
wait longer than the request should block (the error then carries
``retry_after`` so the UI can tell the user when to try again).
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

from .errors import ProviderRateLimited

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: Total tries per call, the first one included.
RATE_LIMIT_MAX_ATTEMPTS = 4
#: First backoff step in seconds; doubles each retry (1, 2, 4).
RATE_LIMIT_BASE_SECONDS = 1.0
#: Fraction of random jitter added to each backoff step.
RATE_LIMIT_JITTER = 0.25
#: Longest single wait.
RATE_LIMIT_RETRY_CAP_SECONDS = 8.0
#: Longest total wait across retries; a longer ``Retry-After`` is not waited out.
RATE_LIMIT_BUDGET_SECONDS = 15.0


def backoff_delay(attempt: int, retry_after: float | None) -> float:
    """Seconds to wait before retry number ``attempt`` (0-based)."""
    step = RATE_LIMIT_BASE_SECONDS * (2**attempt) * (1 + random.uniform(0, RATE_LIMIT_JITTER))
    return min(max(step, float(retry_after or 0)), RATE_LIMIT_RETRY_CAP_SECONDS)


async def call_with_backoff(call: Callable[[], Awaitable[T]], *, label: str) -> T:
    """Await ``call()``, retrying on :class:`ProviderRateLimited`."""
    waited = 0.0
    for attempt in range(RATE_LIMIT_MAX_ATTEMPTS):
        try:
            return await call()
        except ProviderRateLimited as exc:
            last = attempt == RATE_LIMIT_MAX_ATTEMPTS - 1
            asked = float(exc.retry_after or 0)
            if last or asked > RATE_LIMIT_BUDGET_SECONDS - waited:
                raise
            wait = backoff_delay(attempt, exc.retry_after)
            if waited + wait > RATE_LIMIT_BUDGET_SECONDS:
                raise
            logger.info("%s rate limited; retry %d in %.1fs", label, attempt + 1, wait)
            await asyncio.sleep(wait)
            waited += wait
    raise AssertionError("unreachable")  # pragma: no cover


__all__ = [
    "RATE_LIMIT_BUDGET_SECONDS",
    "RATE_LIMIT_MAX_ATTEMPTS",
    "RATE_LIMIT_RETRY_CAP_SECONDS",
    "backoff_delay",
    "call_with_backoff",
]
