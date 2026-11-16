"""Tests for the Assistant run dispatcher.

Covers:
  1. A dry run produces the run dir + four reproducibility artefact
     families (metadata.json, source_snapshot.json, prompt_snapshot/,
     logs.txt) without launching an executor.
  2. ``executor="claude"`` path builds the claude argv and delegates
     to RunRegistry.attach_workflow_subprocess.
  3. ``executor="notebooklm"`` path builds the NLM-CLI argv sequence
     (subprocess spawn mocked).
  4. stdout streamed lines appear in logs.txt and the SSE channel.
  5. Run records carry ``kind="workflow"`` and never share id
     namespaces with AKMS zsum runs.

The run dir pattern is
``<assistant_workspace>/assistant/outputs/<batch_id>/<workflow_id>/<run_id>``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from logic_loom_api.assistant.batches import SourceBatchService
from logic_loom_api.assistant.models import BatchSourceItem, SourceBatch
from logic_loom_api.assistant.runner import (
    EFFECTIVE_PROMPTS_FILENAME,
    LOGS_FILENAME,
    METADATA_FILENAME,
    PROMPT_SNAPSHOT_DIRNAME,
    SOURCE_SNAPSHOT_FILENAME,
    SUPPORTED_EXECUTORS,
    RunnerService,
    RunWorkflowRequest,
    build_claude_argv,
    build_notebooklm_argv,
    resolve_effective_prompts,
    run_dir_for,
    write_reproducibility_artefacts,
)
from logic_loom_api.config import Settings
from logic_loom_api.runs.registry import RunRegistry
from logic_loom_api.workflows import (
    WorkflowDefinition,
    list_bundled_workflows,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Return a Settings instance whose paths point at fresh tmp dirs."""
    return Settings(
        akms_workspace=tmp_path / "AKMS-Workspace",
        assistant_workspace=tmp_path / "Assistant-Workspace",
        assistant_workspace_colocate=False,
        cache_path=tmp_path / ".cache",
    )


@pytest.fixture
def registry(tmp_path: Path) -> RunRegistry:
    """Fresh per-test RunRegistry rooted at tmp_path."""
    return RunRegistry(cache_dir=tmp_path / ".cache", zsum_repo=None)


@pytest.fixture
def batches(settings: Settings) -> SourceBatchService:
    return SourceBatchService(settings)


@pytest.fixture
def sample_batch(batches: SourceBatchService) -> SourceBatch:
    """Seed a single Assistant batch with one source + a notebook_id."""
    batch = batches.create(
        title="run-dispatcher fixture batch",
        sources=[
            BatchSourceItem(
                id="src-1",
                kind="manual",
                title="Test source paper",
                doi="10.1234/test",
            )
        ],
        notebook_id="nlm-test-notebook-id",
    )
    return batch


@pytest.fixture
def runner(
    settings: Settings,
    batches: SourceBatchService,
    registry: RunRegistry,
) -> RunnerService:
    return RunnerService(settings, batches=batches, registry=registry)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAssistantRunDispatcher:
    """Integration tests for the Assistant run dispatcher."""

    @pytest.mark.integration
    def test_dry_run_writes_four_reproducibility_artefacts(
        self,
        runner: RunnerService,
        sample_batch: SourceBatch,
        settings: Settings,
    ) -> None:
        """Verifies: dry run writes all four artefact families
        without spawning an executor.
        """
        result = runner.start_run(
            RunWorkflowRequest(
                batch_id=sample_batch.id,
                workflow_id="assistant.literature_survey",
                dry_run=True,
            )
        )

        assert result["dry_run"] is True
        run_dir = Path(result["run_dir"])
        assert run_dir.is_dir()

        # The four artefact families:
        assert (run_dir / METADATA_FILENAME).is_file()
        assert (run_dir / SOURCE_SNAPSHOT_FILENAME).is_file()
        assert (run_dir / PROMPT_SNAPSHOT_DIRNAME).is_dir()
        assert (
            run_dir / PROMPT_SNAPSHOT_DIRNAME / EFFECTIVE_PROMPTS_FILENAME
        ).is_file()
        assert (run_dir / LOGS_FILENAME).is_file()

        # ``argv`` absent on dry runs (no spawn).
        assert "argv" not in result

    @pytest.mark.integration
    def test_executor_claude_builds_argv_and_attaches_subprocess(
        self,
        runner: RunnerService,
        sample_batch: SourceBatch,
        registry: RunRegistry,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verifies: ``executor="claude"`` path constructs the
        claude argv and hands it to RunRegistry.attach_workflow_subprocess.
        """
        attach_calls: list[dict] = []

        def fake_attach(run_id, *, args, cwd, log_path, env=None, on_done=None):
            attach_calls.append(
                {"run_id": run_id, "args": list(args), "cwd": str(cwd)}
            )
            return None  # we don't await it in the test

        monkeypatch.setattr(registry, "attach_workflow_subprocess", fake_attach)

        result = runner.start_run(
            RunWorkflowRequest(
                batch_id=sample_batch.id,
                workflow_id="assistant.literature_survey",
                dry_run=False,
            )
        )

        assert result["dry_run"] is False
        assert len(attach_calls) == 1
        argv = attach_calls[0]["args"]
        assert argv[0] == "claude"
        assert "--workflow" in argv
        assert "assistant.literature_survey" in argv
        assert "--out-dir" in argv

    @pytest.mark.integration
    def test_executor_notebooklm_builds_nlm_cli_command(
        self,
        runner: RunnerService,
        sample_batch: SourceBatch,
        registry: RunRegistry,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verifies: ``executor="notebooklm"`` path constructs the
        NLM-CLI argv sequence using the batch's notebook_id.
        """
        attach_calls: list[dict] = []

        def fake_attach(run_id, *, args, cwd, log_path, env=None, on_done=None):
            attach_calls.append({"args": list(args)})
            return None

        monkeypatch.setattr(registry, "attach_workflow_subprocess", fake_attach)

        # Build a synthetic workflow with executor="notebooklm".
        wf = WorkflowDefinition(
            id="assistant.nlm_demo",
            name="NLM demo",
            category="Analysis",
            executor="notebooklm",
            source_mode="notebooklm",
            output_format="markdown",
            prompt_slots={"main": "assistant.literature_survey"},
        )
        # Stub the lookup so start_run finds the synthetic workflow.
        monkeypatch.setattr(
            "logic_loom_api.assistant.runner._bundled_workflow",
            lambda wid: wf if wid == wf.id else None,
        )

        runner.start_run(
            RunWorkflowRequest(
                batch_id=sample_batch.id,
                workflow_id=wf.id,
                dry_run=False,
            )
        )

        assert len(attach_calls) == 1
        argv = attach_calls[0]["args"]
        assert argv[0] == "nlm"
        assert argv[1] == "run"
        assert "--notebook" in argv
        # The batch's notebook_id flows through.
        assert sample_batch.notebook_id in argv

    @pytest.mark.integration
    def test_run_record_carries_kind_workflow(
        self,
        runner: RunnerService,
        sample_batch: SourceBatch,
        registry: RunRegistry,
    ) -> None:
        """Verifies: registry record persists ``kind="workflow"``.

        Tests the discriminator pinning that protects against AKMS-side
        and Assistant-side runs sharing an id namespace.
        """
        result = runner.start_run(
            RunWorkflowRequest(
                batch_id=sample_batch.id,
                workflow_id="assistant.paper_introduction",
                dry_run=True,
            )
        )
        run_id = result["run"]["id"]

        rec = registry.get(run_id)
        assert rec is not None
        assert rec.kind == "workflow"
        assert rec.batch_id == sample_batch.id
        assert rec.workflow_id == "assistant.paper_introduction"

        # list(kind="workflow") finds it; list(kind="zsum") does not.
        workflow_runs = registry.list(kind="workflow")
        assert any(r.id == run_id for r in workflow_runs)
        zsum_runs = registry.list(kind="zsum")
        assert all(r.id != run_id for r in zsum_runs)

    @pytest.mark.integration
    def test_stdout_lines_stream_into_logs_txt(
        self,
        runner: RunnerService,
        sample_batch: SourceBatch,
        registry: RunRegistry,
    ) -> None:
        """Verifies: the run-dir logs.txt is the same path the SSE
        endpoint tails.

        The actual line-streaming is exercised by the registry's
        existing _drive_workflow tests; this test pins the contract
        that the run dispatcher writes its log_path to the artefact
        location.
        """
        result = runner.start_run(
            RunWorkflowRequest(
                batch_id=sample_batch.id,
                workflow_id="assistant.related_work",
                dry_run=True,
            )
        )
        run_id = result["run"]["id"]
        rec = registry.get(run_id)
        assert rec is not None
        # Registry log_path matches the artefact path.
        assert Path(rec.log_path) == Path(result["run_dir"]) / LOGS_FILENAME
        # File exists (created empty for dry runs).
        assert Path(rec.log_path).is_file()

    @pytest.mark.integration
    def test_out_dir_pattern_uses_batch_workflow_run(
        self,
        runner: RunnerService,
        sample_batch: SourceBatch,
        settings: Settings,
    ) -> None:
        """Verifies: the run dir is laid down at
        ``<assistant_workspace>/assistant/outputs/<batch_id>/<workflow_id>/<run_id>``.
        """
        result = runner.start_run(
            RunWorkflowRequest(
                batch_id=sample_batch.id,
                workflow_id="assistant.proposal_ideas",
                dry_run=True,
            )
        )
        run_dir = Path(result["run_dir"])
        # Expected path structure: <ws>/assistant/outputs/<batch>/<wf>/<run>
        relative = run_dir.relative_to(settings.assistant_workspace_path())
        parts = relative.parts
        assert parts[0] == "assistant"
        assert parts[1] == "outputs"
        assert parts[2] == sample_batch.id
        assert parts[3] == "assistant.proposal_ideas"
        # run_id is hex length 16 (from _new_run_id).
        assert len(parts[4]) == 16

    @pytest.mark.integration
    def test_prompt_snapshot_writes_one_file_per_slot(
        self,
        runner: RunnerService,
        sample_batch: SourceBatch,
    ) -> None:
        """Verifies: ``prompt_snapshot/`` contains both the
        effective_prompts.json audit file and one ``<slot>.md`` per
        resolved slot.
        """
        result = runner.start_run(
            RunWorkflowRequest(
                batch_id=sample_batch.id,
                workflow_id="assistant.method_comparison",
                dry_run=True,
            )
        )
        run_dir = Path(result["run_dir"])
        snap_dir = run_dir / PROMPT_SNAPSHOT_DIRNAME
        # The audit file:
        audit = json.loads((snap_dir / EFFECTIVE_PROMPTS_FILENAME).read_text())
        slot_keys = set(audit["slots"].keys())
        # method_comparison has one slot ("main") per its bundled
        # workflow definition.
        assert slot_keys == {"main"}
        # And one file per slot, suffixed .md for assistant.* slot.
        per_slot = list(snap_dir.glob("main*"))
        assert any(p.suffix == ".md" for p in per_slot)

    @pytest.mark.integration
    def test_prompt_override_takes_precedence_over_default(
        self,
        runner: RunnerService,
        sample_batch: SourceBatch,
    ) -> None:
        """Verifies: ``prompt_overrides`` short-circuits the bundled
        default resolution per the resolution order in
        ``resolve_effective_prompts``.
        """
        custom = "OVERRIDDEN PROMPT BODY"
        result = runner.start_run(
            RunWorkflowRequest(
                batch_id=sample_batch.id,
                workflow_id="assistant.annotated_bibliography",
                prompt_overrides={"main": custom},
                dry_run=True,
            )
        )
        run_dir = Path(result["run_dir"])
        snap_dir = run_dir / PROMPT_SNAPSHOT_DIRNAME
        audit = json.loads((snap_dir / EFFECTIVE_PROMPTS_FILENAME).read_text())
        assert audit["slots"]["main"]["variant_id"] == "override"
        # The per-slot file contains the override text.
        per_slot = next(snap_dir.glob("main.*"))
        assert per_slot.read_text(encoding="utf-8") == custom

    @pytest.mark.unit
    def test_supported_executors_pinned(self) -> None:
        """Verifies the closed set of supported executors."""
        assert SUPPORTED_EXECUTORS == frozenset({"claude", "notebooklm"})

    @pytest.mark.unit
    def test_unknown_workflow_raises_value_error(
        self,
        runner: RunnerService,
        sample_batch: SourceBatch,
    ) -> None:
        """Verifies: unknown workflow ids surface as ``ValueError``."""
        with pytest.raises(ValueError, match="workflow not found"):
            runner.start_run(
                RunWorkflowRequest(
                    batch_id=sample_batch.id,
                    workflow_id="unknown.workflow",
                    dry_run=True,
                )
            )

    @pytest.mark.unit
    def test_unknown_batch_raises_value_error(
        self,
        runner: RunnerService,
    ) -> None:
        """Verifies: unknown batch ids surface as ``ValueError``."""
        with pytest.raises(ValueError, match="batch not found"):
            runner.start_run(
                RunWorkflowRequest(
                    batch_id="no-such-batch",
                    workflow_id="assistant.literature_survey",
                    dry_run=True,
                )
            )

    @pytest.mark.unit
    def test_metadata_json_carries_workflow_metadata(
        self,
        runner: RunnerService,
        sample_batch: SourceBatch,
    ) -> None:
        """Verifies: ``metadata.json`` carries workflow + run identifying
        fields, sufficient for the Outputs API and the research index to
        ingest.
        """
        result = runner.start_run(
            RunWorkflowRequest(
                batch_id=sample_batch.id,
                workflow_id="assistant.literature_survey",
                run_label="my-test-label",
                dry_run=True,
            )
        )
        run_dir = Path(result["run_dir"])
        meta = json.loads((run_dir / METADATA_FILENAME).read_text())
        assert meta["batch_id"] == sample_batch.id
        assert meta["workflow_id"] == "assistant.literature_survey"
        assert meta["workflow_category"] == "Literature Review"
        assert meta["executor"] == "claude"
        assert meta["run_label"] == "my-test-label"
        assert meta["kind"] == "workflow"
        assert meta["dry_run"] is True
