"""Tests for unified reload — rewire_host_llm rewires all consumers.

Integration tests verifying:
1. POST /api/llm/providers/reload returns a per-consumer summary.
2. /api/learn/providers/reload and /api/llm/providers/reload are true aliases
   (identical payload shape, both 200).
3. A rewire failure degrades gracefully — 200 + warnings entry, never 503.
4. Repeated reloads are idempotent (inject_api_keys_into_env(force=True) is safe
   to call multiple times).
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from logic_loom_api.deps import reload_settings
from logic_loom_api.main import api


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    """TestClient with an isolated config so tests don't touch the real config."""
    with patch.dict(
        os.environ,
        {
            "LOGIC_LOOM_CONFIG": str(tmp_path / "config.toml"),
            "LOGIC_LOOM_VAULT_PATH": str(tmp_path),
            "LOGIC_LOOM_CACHE_PATH": str(tmp_path / "cache"),
        },
    ):
        reload_settings()
        yield TestClient(api)
        reload_settings()


class TestLlmProvidersReload:
    """Tests for the unified reload endpoint and rewire_host_llm."""

    def test_reload_endpoint_returns_per_consumer_summary(self, client: TestClient) -> None:
        """POST /api/llm/providers/reload → 200 with per-consumer summary keys.

        Requirement: response body contains learn/zsum/nodegen keys (each a list).
        """
        r = client.post("/api/llm/providers/reload")
        assert r.status_code == 200
        body = r.json()
        assert body["reloaded"] is True
        assert isinstance(body["learn"], list), "learn must be a list"
        assert isinstance(body["zsum"], list), "zsum must be a list"
        assert isinstance(body["nodegen"], list), "nodegen must be a list"
        # injected + registered flat keys must also be present (back-compat)
        assert "injected" in body
        assert "registered" in body
        # providers list + selected choice
        assert isinstance(body["providers"], list)
        assert "selected" in body
        assert "warnings" in body

    def test_alias_parity_learn_vs_llm_reload(self, client: TestClient) -> None:
        """Both reload endpoints share the wiring side-effect (_reload_core) and the
        same structural payload — they differ ONLY on `selected`.

        Requirement: both 200, both carry reloaded/providers/injected/registered/learn/zsum/
        nodegen/warnings, and those structural fields are value-for-value equal.
        `selected` is intentionally NOT part of the parity set: /api/learn returns
        the RAW learn_llm_provider (legacy-frozen) while /api/llm returns the EFFECTIVE
        choice — see test_learn_surface_selected_is_consistent_and_raw.
        """
        r_learn = client.post("/api/learn/providers/reload")
        r_llm = client.post("/api/llm/providers/reload")

        assert r_learn.status_code == 200
        assert r_llm.status_code == 200

        body_learn = r_learn.json()
        body_llm = r_llm.json()

        required_keys = {
            "reloaded",
            "providers",
            "selected",
            "warnings",
            "injected",
            "registered",
            "learn",
            "zsum",
            "nodegen",
        }
        assert required_keys <= set(body_learn.keys()), (
            f"learn endpoint missing keys: {required_keys - set(body_learn.keys())}"
        )
        assert required_keys <= set(body_llm.keys()), (
            f"llm endpoint missing keys: {required_keys - set(body_llm.keys())}"
        )

        # Both payloads must agree on type shapes
        assert body_learn["reloaded"] is True
        assert body_llm["reloaded"] is True
        assert isinstance(body_learn["learn"], list)
        assert isinstance(body_llm["learn"], list)
        assert isinstance(body_learn["zsum"], list)
        assert isinstance(body_llm["zsum"], list)
        assert isinstance(body_learn["nodegen"], list)
        assert isinstance(body_llm["nodegen"], list)

        # Shared wiring side-effect: every STRUCTURAL field is equal value-for-value
        # across both endpoints (`selected` excluded — it follows the raw/effective
        # split asserted separately).
        for key in (
            "reloaded",
            "providers",
            "warnings",
            "injected",
            "registered",
            "learn",
            "zsum",
            "nodegen",
        ):
            assert body_learn[key] == body_llm[key], (
                f"reload side-effect divergence on '{key}': "
                f"{body_learn[key]!r} != {body_llm[key]!r}"
            )

    def test_learn_surface_selected_is_consistent_and_raw(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression guard for a sibling-endpoint inconsistency.

        The two /api/learn/* siblings (GET /providers and POST /providers/reload)
        must report the SAME `selected` — the RAW learn_llm_provider (legacy-frozen) —
        while the unified /api/llm surface reports the EFFECTIVE choice. Here the global
        llm_provider is set but learn_llm_provider is unset, so raw != effective:
        /api/learn/* selected == None (raw), /api/llm/* selected == 'akms' (effective).
        """
        monkeypatch.setenv("LOGIC_LOOM_LLM_PROVIDER", "akms")
        reload_settings()
        try:
            learn_get = client.get("/api/learn/providers").json()
            learn_reload = client.post("/api/learn/providers/reload").json()
            llm_reload = client.post("/api/llm/providers/reload").json()
            llm_get = client.get("/api/llm/providers").json()

            # /api/learn/* is legacy-frozen: selected is the RAW learn_llm_provider
            # (unset here), and the two siblings agree (the fixed inconsistency).
            assert learn_get["selected"] is None
            assert learn_reload["selected"] is None
            assert learn_get["selected"] == learn_reload["selected"]

            # /api/llm/* reports the EFFECTIVE choice (the global llm_provider).
            assert llm_reload["selected"] == "akms"
            assert llm_get["selected"] == "akms"
        finally:
            reload_settings()

    def test_bad_choice_degradation_returns_200_with_warning(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A rewire failure never 503s — returns 200 + a non-empty warnings list.

        Requirement (degrade gracefully): the handler catches rewire failures, logs, returns 200 + warning
        entry. Patch rewire_host_llm at the module where _reload_core resolves the
        name so the raise propagates through the except clause.
        """
        from logic_loom_api import learn_llm_providers

        def _raise(settings):  # noqa: ANN001
            raise RuntimeError("simulated rewire failure")

        monkeypatch.setattr(learn_llm_providers, "rewire_host_llm", _raise)

        r = client.post("/api/llm/providers/reload")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        body = r.json()
        assert body["reloaded"] is True
        assert isinstance(body["warnings"], list)
        assert len(body["warnings"]) > 0, "warnings must be non-empty on rewire failure"
        assert any("rewire skipped" in w for w in body["warnings"]), (
            f"Expected 'rewire skipped' in warnings, got: {body['warnings']}"
        )

    def test_idempotent_reload(self, client: TestClient) -> None:
        """Repeated reloads must not produce double-injection errors (idempotency).

        Calls the endpoint twice and asserts both return 200 AND that the
        injected/registered/per-consumer content is stable across calls —
        verifying inject_api_keys_into_env(force=True) is safe to repeat and
        never accumulates duplicate wiring (the task's named risk).
        """
        r1 = client.post("/api/llm/providers/reload")
        r2 = client.post("/api/llm/providers/reload")
        assert r1.status_code == 200
        assert r2.status_code == 200
        b1, b2 = r1.json(), r2.json()
        assert b1["reloaded"] is True
        assert b2["reloaded"] is True
        # Idempotency: a repeated reload must yield the identical wiring, not a
        # growing/duplicated set.
        for key in ("injected", "registered", "learn", "zsum", "nodegen"):
            assert b1[key] == b2[key], (
                f"reload not idempotent on '{key}': {b1[key]!r} != {b2[key]!r}"
            )
        assert b2["warnings"] == []

    def test_reload_degrades_on_malformed_custom_cli(self, client: TestClient) -> None:
        """Regression: a malformed custom CLI
        must not 500 the reload endpoints. `_reload_response` calls
        discover_providers OUTSIDE `_reload_core`'s rewire try/except, so discovery
        needs its own degrade-gracefully path (never raise into the request).
        """
        save = client.post(
            "/api/app/config", json={"learn_llm_custom_clis": {"bad": '"unterminated'}}
        )
        assert save.status_code == 200, f"config save failed: {save.text}"

        for path in ("/api/llm/providers/reload", "/api/learn/providers/reload"):
            r = client.post(path)
            assert r.status_code == 200, f"{path} -> {r.status_code}: {r.text}"
            body = r.json()
            assert body["reloaded"] is True
            bad = next((row for row in body["providers"] if row["name"] == "bad"), None)
            assert bad is not None and bad["available"] is False
