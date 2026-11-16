"""Tests for filtering prompt schemas by slot family.

Previously the ``GET /api/prompts/schemas`` endpoint returned every schema
regardless of which family page the user was on, so the bundled PaperCard
schema (a zsum thing) showed up under ``/akms/prompts``. The fix adds an
``x-logicloom-family`` key to bundled schema files plus a ``family`` field
on :class:`PromptSchema` and an optional ``?family=`` filter on the
listing endpoint.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "akms-workspace"
    monkeypatch.setenv("LOGIC_LOOM_AKMS_WORKSPACE", str(workspace))
    import importlib

    import logic_loom_api.main as main_module

    importlib.reload(main_module)
    with TestClient(main_module.api) as c:
        yield c


class TestSchemaFamilyFilter:
    def test_bundled_paper_card_is_tagged_zsum(self, client: TestClient) -> None:
        """The shipped PaperCard schema must carry ``family=zsum`` so it
        only surfaces under the zsum prompts page (the original source of the bug).
        """
        r = client.get("/api/prompts/schemas")
        assert r.status_code == 200
        bundled = r.json()["bundled"]
        paper_card = next((s for s in bundled if s["id"] == "paper_card_v1"), None)
        assert paper_card is not None
        assert paper_card["family"] == "zsum"

    def test_family_filter_hides_zsum_schemas_from_akms(
        self, client: TestClient
    ) -> None:
        """Requesting ``?family=akms`` must drop schemas tagged ``zsum``.

        PaperCard is the canonical regression target — before the fix it
        appeared on the AKMS prompts page.
        """
        r = client.get("/api/prompts/schemas?family=akms")
        assert r.status_code == 200
        ids = [s["id"] for s in r.json()["bundled"]]
        assert "paper_card_v1" not in ids

    def test_family_filter_keeps_zsum_schemas_for_zsum(
        self, client: TestClient
    ) -> None:
        r = client.get("/api/prompts/schemas?family=zsum")
        assert r.status_code == 200
        ids = [s["id"] for s in r.json()["bundled"]]
        assert "paper_card_v1" in ids

    def test_unscoped_user_schema_visible_under_every_family(
        self, client: TestClient
    ) -> None:
        """Schemas created without a family tag stay visible everywhere
        so users don't lose track of what they've authored."""
        body = {
            "name": "Floating Schema",
            "description": "no family tag",
            "content": {"type": "object"},
        }
        r = client.post("/api/prompts/schemas", json=body)
        assert r.status_code == 200, r.text

        for fam in ("zsum", "akms", "assistant"):
            r = client.get(f"/api/prompts/schemas?family={fam}")
            assert r.status_code == 200
            assert any(
                s["id"] == "floating-schema" for s in r.json()["schemas"]
            ), f"missing under family={fam}"

    def test_create_with_family_pins_schema(self, client: TestClient) -> None:
        """User schemas created with a family tag stay scoped to it."""
        r = client.post(
            "/api/prompts/schemas",
            json={
                "name": "AKMS pinned",
                "family": "akms",
                "content": {"type": "object"},
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["family"] == "akms"

        # Pinned schema does NOT appear under zsum…
        r = client.get("/api/prompts/schemas?family=zsum")
        assert all(s["id"] != "akms-pinned" for s in r.json()["schemas"])
        # …but does under akms.
        r = client.get("/api/prompts/schemas?family=akms")
        assert any(s["id"] == "akms-pinned" for s in r.json()["schemas"])

    def test_rejects_invalid_family_value(self, client: TestClient) -> None:
        """Schema POST refuses unknown families so typos can't silently
        hide a schema from every page."""
        r = client.post(
            "/api/prompts/schemas",
            json={
                "name": "bad family",
                "family": "not-a-family",
                "content": {"type": "object"},
            },
        )
        assert r.status_code == 422
