"""Cross-cutting check: every error path returns the uniform envelope shape.

The shape is: ``{"error": {"code": str, "message": str, "detail": object|None}}``.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    cfg = tmp_path / "config.toml"
    with patch.dict(
        os.environ,
        {"LOGIC_LOOM_CONFIG": str(cfg), "LOGIC_LOOM_VAULT_PATH": str(tmp_path)},
    ):
        from logic_loom_api.deps import reload_settings

        reload_settings()
        from logic_loom_api.main import api

        with TestClient(api) as c:
            yield c
        reload_settings()


def _assert_envelope(body: object) -> None:
    assert isinstance(body, dict)
    err = body.get("error")
    assert isinstance(err, dict), f"missing 'error' key: {body!r}"
    assert set(err.keys()) == {"code", "message", "detail"}
    assert isinstance(err["code"], str)
    assert isinstance(err["message"], str)
    # detail may be null, dict, or list — anything non-string is fine.


@pytest.mark.parametrize(
    "method,path,kwargs,expected_status",
    [
        ("GET", "/api/app/does-not-exist", {}, 404),
        ("GET", "/api/zsum/runs/missing-id", {}, 404),
        ("POST", "/api/zsum/runs/missing-id/cancel", {}, 409),
        ("POST", "/api/app/config", {"json": {"search_backend": "bogus"}}, 422),
        ("GET", "/api/vault/file", {"params": {"path": "../etc/passwd"}}, 403),
        ("GET", "/api/vault/search", {"params": {"q": "x", "backend": "nope"}}, 400),
        # Resolve takes a min_length=1 query → 422 envelope on validation error
        ("GET", "/api/vault/resolve", {"params": {"wikilink": ""}}, 422),
    ],
)
def test_envelope_shape_on_error(
    client: TestClient,
    method: str,
    path: str,
    kwargs: dict,
    expected_status: int,
) -> None:
    r = client.request(method, path, **kwargs)
    assert r.status_code == expected_status, f"{method} {path} → {r.status_code} (want {expected_status})"
    _assert_envelope(r.json())
