"""Picker mount sanity: routes are reachable through the /api/picker prefix."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from logic_loom_api.adapters.picker_adapter import build_picker_app
from logic_loom_api.batches.store import BatchStore
from logic_loom_api.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Picker settings over tmp dirs only — the installed ``akms_nodes_gen``
    package, no AKMS source checkout and no developer data.

    * ``akms_repo`` names a directory that does not exist: the picker must
      mount without any AKMS source checkout.
    * The workspace holds an empty batch store, the picker's plan source.
    * The vault carries its own one-item Better BibTeX export so the catalog
      never falls back to AKMS's default ``zsumbib.json`` on the developer's
      machine (a cloud-synced file that can block on open).
    """
    akms_repo = tmp_path / "no-akms-source-checkout"
    workspace = tmp_path / "akms-workspace"
    BatchStore().save(workspace)
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "zsumbib.json").write_text(
        json.dumps(
            {
                "collections": {},
                "items": [
                    {
                        "itemID": 1,
                        "citationKey": "smith2024photons",
                        "title": "Photons in disordered media",
                        "itemType": "journalArticle",
                        "date": "2024-03-15",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return Settings.load(akms_repo=akms_repo, akms_workspace=workspace, vault_path=vault)


def test_picker_app_constructs(settings: Settings) -> None:
    """The factory returns a FastAPI app with the expected routes."""
    app = build_picker_app(settings)
    paths = {r.path for r in app.routes}
    # A handful of canonical picker routes — full list is generated from openapi.
    assert "/api/papers" in paths
    assert "/api/batches" in paths
    assert "/api/saved_queries" in paths


def test_papers_endpoint_responds(settings: Settings) -> None:
    """Hitting /api/papers through the picker app returns a list (200)."""
    app = build_picker_app(settings)
    client = TestClient(app)
    r = client.get("/api/papers")
    # Response shape is owned by AKMS; just assert successful + has results.
    assert r.status_code == 200
    body = r.json()
    assert "results" in body and isinstance(body["results"], list)


def test_main_app_serves_through_prefix(settings: Settings) -> None:
    """Hitting /api/picker/api/papers via the logic-loom-api main app reaches the picker."""
    with patch.dict(
        os.environ,
        {
            "LOGIC_LOOM_AKMS_REPO": str(settings.akms_repo),
            "LOGIC_LOOM_AKMS_WORKSPACE": str(settings.akms_workspace),
            "LOGIC_LOOM_VAULT_PATH": str(settings.vault_path),
        },
    ):
        # Re-import main with env in place so the module-level mount uses our settings.
        import importlib

        import logic_loom_api.main as main_mod

        importlib.reload(main_mod)
        client = TestClient(main_mod.api)
        r = client.get("/api/picker/api/papers")
        assert r.status_code == 200
        body = r.json()
        assert "results" in body and isinstance(body["results"], list)


def test_picker_mounts_without_akms_source_checkout(settings: Settings) -> None:
    """A missing ``akms_repo`` is not an error: the picker builds from the
    installed package and the workspace, and nothing it reports points into
    the (absent) checkout."""
    assert not settings.akms_repo.exists()
    app = build_picker_app(settings)
    cfg = TestClient(app).get("/api/config").json()
    workspace = str(settings.akms_workspace)
    assert cfg.get("workspace") == workspace
    assert cfg["plan_md"] == str(settings.akms_workspace / ".cache" / "generation_plan.md")
    for value in cfg.values():
        assert str(settings.akms_repo) not in value


def test_picker_unavailable_raises_clearly(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The picker is unavailable only when the ``akms_nodes_gen`` package is
    not importable; that raises a clear RuntimeError (which ``main.py``
    catches and logs). A missing ``akms_repo`` no longer makes it
    unavailable — see ``test_picker_mounts_without_akms_source_checkout``."""
    import sys

    # A ``None`` entry in sys.modules makes the import raise ImportError.
    monkeypatch.setitem(sys.modules, "akms_nodes_gen.batch_picker.config", None)
    with pytest.raises(RuntimeError, match="akms_nodes_gen is not importable"):
        build_picker_app(settings)


def test_picker_without_plan_source_raises_file_not_found(tmp_path: Path) -> None:
    """No workspace ``batches.json`` and no ``akms_repo`` checkout → a clear
    FileNotFoundError naming the workspace source (main.py logs and skips)."""
    workspace = tmp_path / "empty-workspace"
    workspace.mkdir()
    s = Settings.load(
        akms_repo=tmp_path / "no-akms-source-checkout",
        akms_workspace=workspace,
        vault_path=tmp_path / "vault",
    )
    with pytest.raises(FileNotFoundError, match="batches.json"):
        build_picker_app(s)


# ── QA batch 5: the picker shows the workspace batches, and stays current ────


def _add_batch(workspace: Path, batch_id: str, title: str, **extra: object) -> None:
    from logic_loom_api.batches import EditableBatch

    store = BatchStore.load(workspace)
    store.add(EditableBatch(id=batch_id, title=title, **extra))
    store.save(workspace)


def test_picker_lists_slug_and_roundless_batches(settings: Settings) -> None:
    """Workspace batches with slug ids (the starter, UI-created ones) are listed."""
    workspace = Path(settings.akms_workspace)
    _add_batch(workspace, "starter-placeholder", "Starter (placeholder)")
    _add_batch(workspace, "R2_B1", "Legacy id, no round")
    client = TestClient(build_picker_app(settings))
    batches = client.get("/api/batches").json()
    titles = sorted(b["title"] for b in batches)
    assert titles == ["Legacy id, no round", "Starter (placeholder) · starter-placeholder"]
    ids = {b["title"]: b["id"] for b in batches}
    assert ids["Legacy id, no round"] == "R2_B1"
    assert ids["Starter (placeholder) · starter-placeholder"] == "R900_B1"


def test_picker_ids_for_slug_batches_are_stable(settings: Settings) -> None:
    workspace = Path(settings.akms_workspace)
    _add_batch(workspace, "second", "Second")
    _add_batch(workspace, "first", "First")
    first = {b["title"]: b["id"] for b in TestClient(build_picker_app(settings)).get("/api/batches").json()}
    # Delete one batch and rebuild: the other keeps its picker id.
    store = BatchStore.load(workspace)
    store.batches = [b for b in store.batches if b.id != "second"]
    store.save(workspace)
    second = {b["title"]: b["id"] for b in TestClient(build_picker_app(settings)).get("/api/batches").json()}
    assert second["First · first"] == first["First · first"]


def test_picker_picks_up_batches_added_after_mount(settings: Settings) -> None:
    workspace = Path(settings.akms_workspace)
    client = TestClient(build_picker_app(settings))
    assert client.get("/api/batches").json() == []
    _add_batch(workspace, "new-batch", "New batch")
    os.utime(workspace / "batches.json", ns=(1, 1_000_000_000_000))  # force a new mtime
    batches = client.get("/api/batches").json()
    assert [b["title"] for b in batches] == ["New batch · new-batch"]
