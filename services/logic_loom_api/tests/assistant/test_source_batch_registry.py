"""Tests for the Assistant source-batch registry.

Covers:
  1. All documented batch actions are implemented and round-trip
     through batches.json (create, add_sources, remove_sources,
     reorder_sources, duplicate, archive, create_from_discovery,
     create_from_zotero_collection).
  2. send_to_akms produces a valid EditableBatch readable by the
     AKMS-bundle BatchStore.load().
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from logic_loom_api.assistant.batches import SourceBatchService
from logic_loom_api.assistant.bootstrap import bootstrap_assistant_workspace
from logic_loom_api.assistant.models import BatchSourceItem, SourceBatch
from logic_loom_api.batches.store import BatchStore
from logic_loom_api.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Settings pointing at an isolated tmp Assistant + AKMS workspace.

    The AKMS workspace is materialised because ``send_to_akms`` uses the
    AKMS-bundle ``BatchStore`` rooted at ``settings.akms_workspace``.
    """
    akms_ws = tmp_path / "akms-ws"
    assistant_ws = tmp_path / "assistant-ws"
    akms_ws.mkdir(parents=True, exist_ok=True)
    s = Settings(
        akms_workspace=akms_ws,
        assistant_workspace=assistant_ws,
    )
    # Materialise the Assistant tree (creates batches.json as `{}` seed).
    bootstrap_assistant_workspace(s)
    return s


class TestSourceBatchRegistry:
    """Tests for the SourceBatchService registry."""

    @pytest.mark.integration
    def test_create_edit_duplicate_archive_round_trip(
        self, settings: Settings
    ) -> None:
        """Verifies: create -> add -> reorder -> duplicate -> archive,
        then reload over the same workspace and observe identical state.

        Requirement: All documented batch actions round-trip through
        batches.json.
        """
        svc = SourceBatchService(settings)
        batch = svc.create(title="My Batch", description="initial")
        assert batch.id == "my-batch"
        assert batch.status == "active"

        # add_sources — accepts both dicts and BatchSourceItems
        svc.add_sources(
            batch.id,
            [
                BatchSourceItem(id="a", kind="manual", title="A"),
                {"kind": "url", "title": "B", "url": "https://example.com/b"},
            ],
        )
        # remove_sources
        svc.remove_sources(batch.id, ids=["a"])
        # reorder_sources — fetch ids first, reorder
        with_sources = svc.get(batch.id)
        assert with_sources is not None
        ids = [s.id for s in with_sources.sources]
        # Add a 2nd source so reorder is meaningful
        svc.add_sources(
            batch.id,
            [BatchSourceItem(id="z", kind="manual", title="Z")],
        )
        ids_after_add = [s.id for s in svc.get(batch.id).sources]
        reversed_ids = list(reversed(ids_after_add))
        svc.reorder_sources(batch.id, reversed_ids)
        after_reorder = svc.get(batch.id)
        assert [s.id for s in after_reorder.sources] == reversed_ids

        # duplicate
        clone = svc.duplicate(batch.id)
        assert clone.id != batch.id
        assert clone.title == "My Batch (copy)"
        assert clone.status == "draft"
        assert [s.id for s in clone.sources] == reversed_ids
        # Mutating clone's sources doesn't touch the original (deep-copy).
        clone.sources.clear()
        original_still = svc.get(batch.id)
        assert original_still is not None
        assert [s.id for s in original_still.sources] == reversed_ids

        # archive
        archived = svc.archive(batch.id)
        assert archived.status == "archived"

        # Reload service over the same workspace — state survives.
        reloaded = SourceBatchService(settings)
        round_trip = reloaded.get(batch.id)
        assert round_trip is not None
        assert round_trip.status == "archived"
        assert [s.id for s in round_trip.sources] == reversed_ids
        # batches.json envelope is well-formed
        path = settings.assistant_batches_path()
        data = json.loads(path.read_text())
        assert data["schema_version"] == 1
        ids_on_disk = {b["id"] for b in data["batches"]}
        assert batch.id in ids_on_disk and clone.id in ids_on_disk

    @pytest.mark.integration
    def test_create_from_discovery_seeds_sources(
        self, settings: Settings
    ) -> None:
        """Verifies: create_from_discovery(results, title) with two
        DiscoveredPaper-shaped fixtures yields a batch whose sources list
        has two BatchSourceItem entries with kind='discovered'.

        Requirement: create_from_discovery is implemented.
        """
        svc = SourceBatchService(settings)
        # Two duck-typed discovery results: one dict, one SimpleNamespace.
        results = [
            {
                "title": "Paper One",
                "authors": ["A. One"],
                "year": 2023,
                "doi": "10.1/one",
                "openalex_id": "W1",
            },
            SimpleNamespace(
                title="Paper Two",
                authors=["B. Two"],
                year=2024,
                doi="10.2/two",
                openalex_id="W2",
            ),
        ]
        batch = svc.create_from_discovery(results, title="Discovery Seed")

        assert batch.title == "Discovery Seed"
        assert len(batch.sources) == 2
        kinds = {s.kind for s in batch.sources}
        assert kinds == {"discovered"}
        titles = {s.title for s in batch.sources}
        assert titles == {"Paper One", "Paper Two"}
        # Round-trip through disk.
        reloaded = SourceBatchService(settings).get(batch.id)
        assert reloaded is not None
        assert len(reloaded.sources) == 2

    @pytest.mark.integration
    def test_create_from_zotero_collection_uses_citekeys(
        self, settings: Settings
    ) -> None:
        """Verifies: create_from_zotero_collection(key, citekeys=[...]) seeds
        a batch with one BatchSourceItem per citekey, kind='zotero'.
        """
        svc = SourceBatchService(settings)
        batch = svc.create_from_zotero_collection(
            "COLL_ABC",
            title="Zotero seeded",
            citekeys=["smith2024", "doe2023"],
        )
        assert batch.title == "Zotero seeded"
        assert "zotero-collection" in batch.tags
        assert {s.kind for s in batch.sources} == {"zotero"}
        assert {s.citekey for s in batch.sources} == {"smith2024", "doe2023"}

    @pytest.mark.integration
    def test_send_to_akms_overwrites_existing_batch_on_resend(
        self, settings: Settings
    ) -> None:
        """Verifies: re-sending the same Assistant batch updates the
        existing AKMS batch in place instead of minting a duplicate.

        Pre-T4-followup the second send returned a different
        ``akms_batch_id`` and the original AKMS batch (often without the
        user's later ``default_workflow_id`` edit) lingered. The fix
        makes the operation idempotent on the Assistant batch id.
        """
        svc = SourceBatchService(settings)
        batch = svc.create(title="Resend me")

        first = svc.send_to_akms(batch.id)
        assert first["akms_batch_id"] == batch.id

        # User updates the workflow on the Assistant side.
        svc.update(batch.id, default_workflow_id="akms.node_extraction")

        # Re-send.
        second = svc.send_to_akms(batch.id)
        assert second["akms_batch_id"] == batch.id  # SAME id, not a duplicate

        store = BatchStore.load(Path(second["akms_workspace_path"]))
        # Exactly ONE batch with this id, carrying the latest workflow.
        matching = [b for b in store.batches if b.id == batch.id]
        assert len(matching) == 1
        assert matching[0].default_workflow_id == "akms.node_extraction"

    def test_send_to_akms_round_trips_through_batchstore(
        self, settings: Settings
    ) -> None:
        """Verifies: send_to_akms(batch_id) writes a valid EditableBatch
        into the AKMS workspace and BatchStore.load(workspace) yields back
        a batch with matching id and title.

        Requirement: send_to_akms output round-trips through AKMS BatchStore.load.
        """
        svc = SourceBatchService(settings)
        batch = svc.create(
            title="Send To AKMS",
            description="cross-mode send-to-AKMS fixture",
            sources=[
                BatchSourceItem(
                    id="s1",
                    kind="zotero",
                    title="Cite One",
                    citekey="cite1",
                ),
                BatchSourceItem(
                    id="s2",
                    kind="discovered",
                    title="Disc Two",
                    doi="10.0/2",
                ),
            ],
            prompt_profile_id="prof-x",
            prompt_overrides={"system": "go"},
        )

        result = svc.send_to_akms(batch.id)

        assert set(result.keys()) == {"akms_batch_id", "akms_workspace_path"}
        akms_id = result["akms_batch_id"]
        akms_path = Path(result["akms_workspace_path"])
        assert akms_path == settings.akms_workspace.expanduser()
        # AKMS BatchStore can read it.
        store = BatchStore.load(akms_path)
        editable = store.get(akms_id)
        assert editable is not None
        assert editable.title == "Send To AKMS"
        assert editable.source == "imported"
        assert editable.papers == ["cite1", "10.0/2"]
        assert editable.prompt_profile_id == "prof-x"
        assert editable.prompt_overrides == {"system": "go"}
        # Assistant batch is unmodified — send-to-akms is one-way.
        original = svc.get(batch.id)
        assert original is not None
        assert original.status == "active"

    @pytest.mark.integration
    def test_remove_get_delete_and_missing_id_raises(
        self, settings: Settings
    ) -> None:
        """Sanity: get() returns None on unknown id; archive/remove/reorder
        raise ValueError on unknown id; reorder rejects partial orderings.
        """
        svc = SourceBatchService(settings)
        assert svc.get("nonexistent") is None
        with pytest.raises(ValueError):
            svc.archive("nonexistent")
        with pytest.raises(ValueError):
            svc.remove_sources("nonexistent", ids=["x"])

        batch = svc.create(title="Partial Order")
        svc.add_sources(
            batch.id,
            [
                BatchSourceItem(id="a", kind="manual", title="A"),
                BatchSourceItem(id="b", kind="manual", title="B"),
            ],
        )
        with pytest.raises(ValueError):
            svc.reorder_sources(batch.id, ["a"])  # missing b
        with pytest.raises(ValueError):
            svc.reorder_sources(batch.id, ["a", "b", "c"])  # extra c

    @pytest.mark.integration
    def test_handles_legacy_empty_seed(self, settings: Settings) -> None:
        """The Assistant workspace bootstrap seeds batches.json as ``{}``. The service
        should treat that as an empty registry and start writing the v1
        envelope on first save.
        """
        path = settings.assistant_batches_path()
        assert json.loads(path.read_text()) == {}

        svc = SourceBatchService(settings)
        assert svc.list_batches() == []
        svc.create(title="First")
        data = json.loads(path.read_text())
        assert data["schema_version"] == 1
        assert len(data["batches"]) == 1
