"""Discover search validation, per-provider failure reporting and 429 backoff."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from logic_loom_api.assistant import discovery as discovery_mod
from logic_loom_api.assistant.discovery import PaperDiscoveryService
from logic_loom_api.assistant.providers import retry as retry_mod
from logic_loom_api.assistant.providers.errors import ProviderRateLimited, ProviderTimeout
from logic_loom_api.config import Settings
from logic_loom_api.deps import get_settings
from logic_loom_api.main import api as fastapi_app
from logic_loom_api.routes.assistant_discover import get_paper_discovery_service


class _Client:
    """Fake provider client: replays a scripted list of results/exceptions."""

    def __init__(self, *script: Any) -> None:
        self.script = list(script)
        self.calls = 0

    async def search(self, query: str, *, filters: Any = None, limit: int = 25) -> Any:
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


OA_HIT = {"results": [{"id": "https://openalex.org/W1", "title": "OA paper", "doi": "https://doi.org/10.1/a"}]}
S2_EMPTY = {"data": []}


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(
        akms_workspace=tmp_path / "AKMS-Workspace",
        assistant_workspace=tmp_path / "Assistant-Workspace",
        openalex_user_email="dev@example.test",
    )


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", instant)


def _service(settings: Settings, oa: _Client, s2: _Client) -> PaperDiscoveryService:
    return PaperDiscoveryService(settings, openalex=oa, semantic_scholar=s2)  # type: ignore[arg-type]


def test_failed_provider_is_reported_not_just_logged(settings: Settings) -> None:
    oa = _Client(OA_HIT)
    s2 = _Client(ProviderTimeout("Semantic Scholar timed out", provider="semantic_scholar"))
    papers, errors = asyncio.run(_service(settings, oa, s2).search_with_status("q"))
    assert len(papers) == 1
    assert errors == [
        {
            "provider": "semantic_scholar",
            "error": "provider_error",
            "message": "Semantic Scholar timed out",
            "retry_after": None,
        }
    ]


@pytest.fixture()
def waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record backoff sleeps instead of sleeping."""
    recorded: list[float] = []

    async def record(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", record)
    return recorded


def test_rate_limit_is_retried(settings: Settings, waits: list[float]) -> None:
    oa = _Client(OA_HIT)
    s2 = _Client(ProviderRateLimited("429", provider="semantic_scholar", retry_after=2), S2_EMPTY)
    _papers, errors = asyncio.run(_service(settings, oa, s2).search_with_status("q"))
    assert s2.calls == 2
    assert errors == []
    assert waits == [2.0]  # Retry-After wins over the 1 s first step


def test_rate_limit_backs_off_exponentially(settings: Settings, waits: list[float]) -> None:
    limited = ProviderRateLimited("429", provider="semantic_scholar")
    s2 = _Client(limited, limited, limited, S2_EMPTY)
    _papers, errors = asyncio.run(_service(settings, _Client(OA_HIT), s2).search_with_status("q"))
    assert s2.calls == 4
    assert errors == []
    assert len(waits) == 3
    for wait, step in zip(waits, (1.0, 2.0, 4.0), strict=True):
        assert step <= wait <= step * (1 + retry_mod.RATE_LIMIT_JITTER)


def test_rate_limit_gives_up_after_max_attempts(settings: Settings, waits: list[float]) -> None:
    limited = ProviderRateLimited("429", provider="semantic_scholar")
    s2 = _Client(*([limited] * retry_mod.RATE_LIMIT_MAX_ATTEMPTS))
    _papers, errors = asyncio.run(_service(settings, _Client(OA_HIT), s2).search_with_status("q"))
    assert s2.calls == retry_mod.RATE_LIMIT_MAX_ATTEMPTS
    assert errors[0]["error"] == "provider_rate_limited"


def test_long_retry_after_is_reported_not_waited(settings: Settings, waits: list[float]) -> None:
    oa = _Client(OA_HIT)
    limited = ProviderRateLimited("429", provider="semantic_scholar", retry_after=30)
    s2 = _Client(limited, limited)
    _papers, errors = asyncio.run(_service(settings, oa, s2).search_with_status("q"))
    assert s2.calls == 1
    assert waits == []
    assert errors[0]["error"] == "provider_rate_limited"
    assert errors[0]["retry_after"] == 30


def test_retry_wait_is_capped(settings: Settings, waits: list[float]) -> None:
    s2 = _Client(ProviderRateLimited("429", provider="semantic_scholar", retry_after=12), S2_EMPTY)
    asyncio.run(_service(settings, _Client(OA_HIT), s2).search_with_status("q"))
    assert waits == [discovery_mod.RATE_LIMIT_RETRY_CAP_SECONDS]


def test_providers_selection_skips_the_other(settings: Settings) -> None:
    oa = _Client(OA_HIT)
    s2 = _Client()
    asyncio.run(_service(settings, oa, s2).search_with_status("q", providers=["openalex"]))
    assert s2.calls == 0


# ── Route validation ────────────────────────────────────────────────────────


@pytest.fixture()
def client(settings: Settings):
    fastapi_app.dependency_overrides[get_settings] = lambda: settings
    fastapi_app.dependency_overrides[get_paper_discovery_service] = lambda: _service(
        settings,
        _Client(OA_HIT),
        _Client(ProviderTimeout("S2 down", provider="semantic_scholar")),
    )
    yield TestClient(fastapi_app)
    fastapi_app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "body",
    [
        {"query": ""},
        {"query": "   "},
        {"query": "x" * 501},
        {"query": "ok", "limit": -5},
        {"query": "ok", "limit": 0},
        {"query": "ok", "limit": 100000},
        {"query": "ok", "providers": ["google_scholar"]},
        {"query": "ok", "providers": []},
    ],
)
def test_search_rejects_junk(client: TestClient, body: dict) -> None:
    r = client.post("/api/assistant/discover/search", json=body)
    assert r.status_code == 422, r.text


def test_search_returns_provider_errors_and_skips_cache(client: TestClient) -> None:
    r = client.post("/api/assistant/discover/search", json={"query": "plasticity"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider_errors"][0]["provider"] == "semantic_scholar"
    assert len(body["results"]) == 1
    # Not cached: a second call is a fresh search, not a cache hit.
    again = client.post("/api/assistant/discover/search", json={"query": "plasticity"}).json()
    assert again["cached"] is False
