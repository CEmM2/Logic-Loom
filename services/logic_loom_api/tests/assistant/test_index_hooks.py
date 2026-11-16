"""Targeted tests for the research-index post-write/eviction hooks.

Covers the post-PR-#44 follow-ups:

  1. ``SourceBatchService.add_sources`` / ``remove_sources`` / ``delete``
     fire the appropriate on_change / on_delete hooks.
  2. ``RunnerService.start_run`` fires ``on_change`` for ``run_metadata``
     immediately (dry runs included) and for ``assistant_output`` via the
     subprocess-completion callback.
  3. The ``ingest_assistant_output`` / ``ingest_run_metadata`` adapters
     walk the run registry when one is initialized.

Tier: unit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from logic_loom_api.assistant.batches import SourceBatchService
from logic_loom_api.assistant.bootstrap import bootstrap_assistant_workspace
from logic_loom_api.assistant.models import BatchSourceItem, SourceBatch
from logic_loom_api.assistant.research_index.ingest import (
    ingest_assistant_output,
    ingest_run_metadata,
)
from logic_loom_api.assistant.research_index.schema import IndexableRecord
from logic_loom_api.assistant.runner import (
    METADATA_FILENAME,
    RunWorkflowRequest,
    RunnerService,
)
from logic_loom_api.config import Settings
from logic_loom_api.runs import registry as registry_mod


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeLifecycle:
    """Minimal stand-in for :class:`IndexLifecycle`.

    Captures every on_change / on_delete call so tests can assert the
    record-ids fired without spinning up a real FTS5 backend or
    threading.Timer debounce.
    """

    def __init__(self) -> None:
        self.changes: list[IndexableRecord] = []
        self.deletes: list[str] = []

    def on_change(self, record: IndexableRecord) -> None:
        self.changes.append(record)

    def on_delete(self, record_id: str) -> None:
        self.deletes.append(record_id)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Isolated AKMS + Assistant workspace under ``tmp_path``."""
    akms_ws = tmp_path / "akms-ws"
    assistant_ws = tmp_path / "assistant-ws"
    akms_ws.mkdir(parents=True, exist_ok=True)
    s = Settings(
        akms_workspace=akms_ws,
        assistant_workspace=assistant_ws,
    )
    bootstrap_assistant_workspace(s)
    return s


# ---------------------------------------------------------------------------
# SourceBatchService hooks
# ---------------------------------------------------------------------------


class TestBatchHooks:
    @pytest.mark.unit
    def test_remove_sources_fires_on_delete_for_evicted_zotero(
        self, settings: Settings
    ) -> None:
        """Removing a zotero source emits an on_delete with its record id."""
        fake = _FakeLifecycle()

        def on_changed(batch: SourceBatch) -> None:
            for item in batch.sources:
                if item.kind == "zotero":
                    fake.on_change(
                        IndexableRecord(
                            id=f"zotero:{batch.id}:{item.id}",
                            kind="zotero",
                            title=item.title,
                        )
                    )

        def on_deleted(batch_id: str, removed: list[BatchSourceItem]) -> None:
            for item in removed:
                if item.kind == "zotero":
                    fake.on_delete(f"zotero:{batch_id}:{item.id}")

        svc = SourceBatchService(
            settings, on_batch_changed=on_changed, on_sources_deleted=on_deleted
        )
        batch = svc.create(
            title="hooks",
            sources=[
                BatchSourceItem(id="s1", kind="zotero", title="Alpha"),
                BatchSourceItem(id="s2", kind="zotero", title="Beta"),
            ],
        )

        # Initial create fires on_change for both zotero items.
        assert {r.id for r in fake.changes} == {
            f"zotero:{batch.id}:s1",
            f"zotero:{batch.id}:s2",
        }
        fake.changes.clear()

        svc.remove_sources(batch.id, ids=["s1"])
        # s1 evicted; the remaining batch state re-upserts s2.
        assert fake.deletes == [f"zotero:{batch.id}:s1"]
        assert [r.id for r in fake.changes] == [f"zotero:{batch.id}:s2"]

    @pytest.mark.unit
    def test_delete_fires_on_delete_for_every_zotero_source(
        self, settings: Settings
    ) -> None:
        """Deleting a batch emits an on_delete for each zotero source."""
        fake = _FakeLifecycle()

        def on_deleted(batch_id: str, removed: list[BatchSourceItem]) -> None:
            for item in removed:
                if item.kind == "zotero":
                    fake.on_delete(f"zotero:{batch_id}:{item.id}")

        svc = SourceBatchService(settings, on_sources_deleted=on_deleted)
        batch = svc.create(
            title="doomed",
            sources=[
                BatchSourceItem(id="a", kind="zotero", title="A"),
                BatchSourceItem(id="b", kind="zotero", title="B"),
                BatchSourceItem(id="c", kind="discovered", title="C"),
            ],
        )

        svc.delete(batch.id)

        # Two zotero sources evicted; the discovered source is not indexed.
        assert sorted(fake.deletes) == [
            f"zotero:{batch.id}:a",
            f"zotero:{batch.id}:b",
        ]

    @pytest.mark.unit
    def test_eviction_hook_failure_does_not_block_delete(
        self, settings: Settings
    ) -> None:
        """An indexer hiccup must never block a batch write."""
        calls: list[str] = []

        def on_deleted(batch_id: str, removed: list[BatchSourceItem]) -> None:
            calls.append(batch_id)
            raise RuntimeError("indexer offline")

        svc = SourceBatchService(settings, on_sources_deleted=on_deleted)
        batch = svc.create(
            title="hiccup",
            sources=[BatchSourceItem(id="x", kind="zotero", title="X")],
        )
        # delete() must not raise — the hook fires and the save persists.
        svc.delete(batch.id)
        assert calls == [batch.id]
        assert svc.get(batch.id) is None


# ---------------------------------------------------------------------------
# RunnerService hooks
# ---------------------------------------------------------------------------


class _FakeRegistry:
    """Bare-minimum registry stand-in for the runner unit tests.

    Captures ``attach_workflow_subprocess`` so we can verify the
    runner passes an ``on_done`` callback and exercise the
    completion-path indexing without spawning a subprocess.
    """

    def __init__(self) -> None:
        self.records: dict[str, Any] = {}
        self.attach_calls: list[dict[str, Any]] = []

    def register_workflow_run(self, **kwargs: Any) -> Any:
        from logic_loom_api.runs.models import RunRecord

        rec = RunRecord(
            id=kwargs["run_id"],
            stage=kwargs["workflow_id"],
            kind="workflow",
            batch_id=kwargs["batch_id"],
            workflow_id=kwargs["workflow_id"],
            log_path=kwargs["log_path"],
            cwd=kwargs.get("cwd"),
        )
        self.records[rec.id] = rec
        return rec

    def attach_workflow_subprocess(
        self,
        run_id: str,
        *,
        args: list[str],
        cwd: Path | str,
        log_path: Path | str | None = None,
        env: dict[str, str] | None = None,
        on_done: Any = None,
    ) -> None:
        self.attach_calls.append(
            {"run_id": run_id, "args": list(args), "on_done": on_done}
        )
        return None

    def list(self, *, limit: int = 50, kind: str | None = None) -> list[Any]:
        recs = list(self.records.values())
        if kind is not None:
            recs = [r for r in recs if r.kind == kind]
        return recs[-limit:]


class TestRunnerHooks:
    @pytest.mark.unit
    def test_dry_run_indexes_run_metadata_immediately(
        self, settings: Settings
    ) -> None:
        """``run_metadata`` becomes searchable as soon as the run is registered."""
        lifecycle = _FakeLifecycle()
        registry = _FakeRegistry()
        batches = SourceBatchService(settings)
        batches.create(title="r1", sources=[])

        runner = RunnerService(
            settings,
            batches=batches,
            registry=registry,
            index_lifecycle=lifecycle,
        )
        batch_id = batches.list_batches()[0].id

        result = runner.start_run(
            RunWorkflowRequest(
                batch_id=batch_id,
                workflow_id="assistant.literature_survey",
                dry_run=True,
            )
        )

        # The run-metadata record fired exactly once.
        meta_changes = [r for r in lifecycle.changes if r.kind == "run_metadata"]
        assert len(meta_changes) == 1
        rec = meta_changes[0]
        assert rec.batch_id == batch_id
        assert rec.run_id == result["run"]["id"]
        assert rec.workflow_id == "assistant.literature_survey"
        assert "literature_survey" in rec.body  # raw metadata.json content

    @pytest.mark.unit
    def test_real_run_passes_on_done_callback_that_indexes_output(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        """The runner's ``on_done`` callback fires ``assistant_output`` indexing."""
        lifecycle = _FakeLifecycle()
        registry = _FakeRegistry()
        batches = SourceBatchService(settings)
        batches.create(title="r2", sources=[])

        runner = RunnerService(
            settings,
            batches=batches,
            registry=registry,
            index_lifecycle=lifecycle,
        )
        batch_id = batches.list_batches()[0].id

        result = runner.start_run(
            RunWorkflowRequest(
                batch_id=batch_id,
                workflow_id="assistant.literature_survey",
                dry_run=False,
            )
        )

        # The runner registered an attach with a callable on_done.
        assert len(registry.attach_calls) == 1
        on_done = registry.attach_calls[0]["on_done"]
        assert callable(on_done)

        # Simulate the executor writing a primary .md, then fire the callback.
        run_dir = Path(result["run_dir"])
        (run_dir / "output.md").write_text("# Survey\n\nKey finding.")
        finished = registry.records[result["run"]["id"]]
        finished.status = "succeeded"

        # Clear the run_metadata changes that fired at start_run so we can
        # assert specifically on the completion-path emissions.
        lifecycle.changes.clear()
        on_done(finished)

        output_changes = [
            r for r in lifecycle.changes if r.kind == "assistant_output"
        ]
        assert len(output_changes) == 1
        out = output_changes[0]
        assert out.batch_id == batch_id
        assert out.workflow_id == "assistant.literature_survey"
        assert "Key finding" in out.body

    @pytest.mark.unit
    def test_runner_hook_failure_does_not_block_start(
        self, settings: Settings
    ) -> None:
        """An indexer hiccup must not stop the run from registering."""

        class _ExplodingLifecycle:
            def on_change(self, _record: Any) -> None:
                raise RuntimeError("indexer down")

            def on_delete(self, _record_id: str) -> None:  # pragma: no cover
                raise RuntimeError("indexer down")

        registry = _FakeRegistry()
        batches = SourceBatchService(settings)
        batches.create(title="r3", sources=[])

        runner = RunnerService(
            settings,
            batches=batches,
            registry=registry,
            index_lifecycle=_ExplodingLifecycle(),
        )
        batch_id = batches.list_batches()[0].id

        # The exception is swallowed at the runner boundary.
        result = runner.start_run(
            RunWorkflowRequest(
                batch_id=batch_id,
                workflow_id="assistant.literature_survey",
                dry_run=True,
            )
        )
        assert result["dry_run"] is True
        assert result["run"]["id"] in registry.records


# ---------------------------------------------------------------------------
# Adapter walks the registry when available
# ---------------------------------------------------------------------------


class TestAdapterRegistryWalk:
    @pytest.mark.unit
    def test_assistant_output_adapter_walks_registry_first(
        self,
        settings: Settings,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When a registry has Assistant runs, the adapter uses them.

        Exercises the production code path on top of the existing
        filesystem-walk unit test (in test_index_adapters.py).
        """
        # Build a registry rooted under tmp_path so it doesn't disturb
        # other tests' state.
        cache_dir = tmp_path / "registry-cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        # Register the singleton with monkeypatch BEFORE init_registry installs
        # the tmp registry, so teardown restores the pre-test value. Patching
        # it afterwards would record the tmp registry as the "original" and
        # leak it into every later test.
        monkeypatch.setattr(registry_mod, "_GLOBAL_REGISTRY", None)
        registry = registry_mod.init_registry(cache_dir, None)
        # Materialise a run dir somewhere outside the AKMS workspace
        # (canonical Assistant layout: <assistant_ws>/assistant/outputs/...).
        run_dir = (
            settings.assistant_workspace_path()
            / "assistant"
            / "outputs"
            / "b1"
            / "assistant.literature_survey"
            / "run_2026-05-17_2000_demo"
        )
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / METADATA_FILENAME).write_text(
            '{"workflow_id": "assistant.literature_survey"}'
        )
        (run_dir / "output.md").write_text("# Findings\n\nKey insight")
        log_path = run_dir / "logs.txt"
        log_path.write_text("")

        registry.register_workflow_run(
            batch_id="b1",
            workflow_id="assistant.literature_survey",
            run_id="run_2026-05-17_2000_demo",
            log_path=str(log_path),
            cwd=str(run_dir),
        )

        output_records = list(ingest_assistant_output(settings))
        assert len(output_records) == 1
        assert output_records[0].batch_id == "b1"
        assert "Findings" in output_records[0].body

        meta_records = list(ingest_run_metadata(settings))
        assert len(meta_records) == 1
        assert meta_records[0].batch_id == "b1"
