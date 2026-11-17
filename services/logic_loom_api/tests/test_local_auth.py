"""Local API auth: per-launch token + loopback Host allowlist (auth.py)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from logic_loom_api import auth
from logic_loom_api.auth import LocalAuthMiddleware, host_is_allowed

TOKEN = "a" * 64
ORIGIN = "http://tauri.localhost"


def _app(token: str | None) -> FastAPI:
    app = FastAPI()
    # Same order as main.py: auth added first, CORS outermost.
    app.add_middleware(LocalAuthMiddleware, token=token)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[ORIGIN],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/app/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/app/config")
    def config() -> dict[str, str]:
        return {"secret": "x"}

    sub = FastAPI()

    @sub.get("/api/batches")
    def batches() -> list[str]:
        return ["b"]

    app.mount("/api/picker", sub)
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app(TOKEN), base_url="http://127.0.0.1:5555")


def test_missing_token_is_401_with_error_envelope(client: TestClient) -> None:
    r = client.get("/api/app/config")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_wrong_token_is_401(client: TestClient) -> None:
    r = client.get("/api/app/config", headers={"X-Logic-Loom-Token": "b" * 64})
    assert r.status_code == 401


def test_header_token_is_accepted(client: TestClient) -> None:
    r = client.get("/api/app/config", headers={"X-Logic-Loom-Token": TOKEN})
    assert r.status_code == 200


def test_query_token_is_accepted_for_event_streams(client: TestClient) -> None:
    r = client.get(f"/api/app/config?llt={TOKEN}")
    assert r.status_code == 200


def test_mounted_sub_app_is_covered(client: TestClient) -> None:
    assert client.get("/api/picker/api/batches").status_code == 401
    r = client.get("/api/picker/api/batches", headers={"X-Logic-Loom-Token": TOKEN})
    assert r.status_code == 200


def test_health_stays_open(client: TestClient) -> None:
    assert client.get("/api/app/health").status_code == 200


def test_foreign_host_is_refused_even_with_token(client: TestClient) -> None:
    # DNS rebinding: the attacker page's requests name the attacker's host.
    r = client.get(
        "/api/app/config",
        headers={"Host": "evil.example:5555", "X-Logic-Loom-Token": TOKEN},
    )
    assert r.status_code == 421
    assert r.json()["error"]["code"] == "host_not_allowed"
    assert client.get("/api/app/health", headers={"Host": "evil.example"}).status_code == 421


def test_preflight_needs_no_token(client: TestClient) -> None:
    r = client.options(
        "/api/app/config",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-logic-loom-token",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == ORIGIN


def test_rejection_carries_cors_headers(client: TestClient) -> None:
    r = client.get("/api/app/config", headers={"Origin": ORIGIN})
    assert r.status_code == 401
    assert r.headers["access-control-allow-origin"] == ORIGIN


def test_no_token_configured_means_open() -> None:
    c = TestClient(_app(None))
    assert c.get("/api/app/config").status_code == 200


@pytest.mark.parametrize(
    ("host", "ok"),
    [
        ("127.0.0.1", True),
        ("127.0.0.1:1234", True),
        ("localhost:80", True),
        ("LOCALHOST", True),
        ("[::1]:9000", True),
        ("[::1]", True),
        ("", False),
        ("evil.example", False),
        ("127.0.0.1.evil.example", False),
        ("localhost:abc", False),
        ("[::1]x", False),
        ("[::2]:80", False),
        ("0.0.0.0", False),
    ],
)
def test_host_allowlist(host: str, ok: bool) -> None:
    assert host_is_allowed(host) is ok


def test_capture_removes_token_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "_captured_token", None)
    monkeypatch.setenv(auth.TOKEN_ENV, TOKEN)
    assert auth.capture_token_from_env() == TOKEN
    assert auth.TOKEN_ENV not in __import__("os").environ
    assert auth.configured_token() == TOKEN


def test_short_token_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "_captured_token", None)
    monkeypatch.setenv(auth.TOKEN_ENV, "short")
    with pytest.raises(RuntimeError):
        auth.configured_token()


def test_main_app_installs_auth_inside_cors() -> None:
    from logic_loom_api.main import api

    classes = [m.cls for m in api.user_middleware]
    # user_middleware is outermost-first.
    assert classes.index(CORSMiddleware) < classes.index(LocalAuthMiddleware)
