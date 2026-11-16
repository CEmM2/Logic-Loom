"""Run output streaming (SSE).

Covers:
A streaming client receives appended lines in near-real-time.
Stream disconnects cleanly on terminal state.

Tier: integration.

Note on the test strategy: ``starlette.testclient.TestClient`` blocks on
SSE responses (no chunked iteration). ``httpx.AsyncClient.stream`` could
drive the route layer, but the load-bearing behaviour is the registry's
:meth:`stream_workflow_run` generator. We therefore exercise the generator
directly (both behaviours above) and add a thin
sanity check that the route is wired up (the route just delegates to
``EventSourceResponse(gen())``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from logic_loom_api.batches import BatchStore, EditableBatch
from logic_loom_api.runs.output_store import OutputStore
from logic_loom_api.runs.registry import RunRegistry


@pytest.fixture()
def isolated_workspace(tmp_path, monkeypatch):
    ws = tmp_path / "akms-workspace"
    ws.mkdir()
    monkeypatch.setenv("LOGIC_LOOM_AKMS_WORKSPACE", str(ws))
    monkeypatch.setenv("LOGIC_LOOM_CACHE_PATH", str(tmp_path / "cache"))
    monkeypatch.setenv("LOGIC_LOOM_CONFIG", str(tmp_path / "missing.toml"))
    return ws, tmp_path / "cache"


class TestRunOutputStreaming:
    """Workflow-run output streaming."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_stream_delivers_appended_lines(self, isolated_workspace):
        """SSE GET /api/runs/{id}/stream tails logs.txt and yields lines.

        Drives :meth:`RunRegistry.stream_workflow_run` directly: stake out
        the run dir via OutputStore, register the workflow run, append
        lines from a background task, and confirm the stream surfaces each
        appended line as a LogEvent.
        """
        ws, cache = isolated_workspace
        reg = RunRegistry(cache_dir=cache, zsum_repo=None)
        run_id, run_dir = OutputStore.create_run_dir(
            ws, batch_id="b", workflow_id="akms.node_extraction", run_label="t"
        )
        rec = reg.register_workflow_run(
            batch_id="b",
            workflow_id="akms.node_extraction",
            run_id=run_id,
            log_path=str(run_dir / "logs.txt"),
        )
        log_path = Path(rec.log_path)

        async def writer():
            for i in range(3):
                await asyncio.sleep(0.05)
                with log_path.open("a", encoding="utf-8") as fh:
                    fh.write(f"line-{i}\n")
                    fh.flush()
            await asyncio.sleep(0.05)
            # Flip the record to a terminal state so the stream closes.
            rec.status = "succeeded"

        writer_task = asyncio.create_task(writer())
        try:
            collected = []
            async for evt in reg.stream_workflow_run(run_id, poll_interval=0.02):
                collected.append(evt.line)
                if len(collected) >= 3:
                    # If terminal hasn't been set yet, wait for the writer.
                    await writer_task
            # Every appended line surfaces in the stream.
            assert "line-0" in collected
            assert "line-1" in collected
            assert "line-2" in collected
        finally:
            if not writer_task.done():
                writer_task.cancel()
                try:
                    await writer_task
                except asyncio.CancelledError:
                    pass

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_stream_closes_on_terminal_state(self, isolated_workspace):
        """When the run record reaches a terminal status, the stream
        emits remaining buffered output and the generator returns."""
        ws, cache = isolated_workspace
        reg = RunRegistry(cache_dir=cache, zsum_repo=None)
        run_id, run_dir = OutputStore.create_run_dir(
            ws, batch_id="b", workflow_id="akms.node_extraction", run_label="closed"
        )
        rec = reg.register_workflow_run(
            batch_id="b",
            workflow_id="akms.node_extraction",
            run_id=run_id,
            log_path=str(run_dir / "logs.txt"),
        )
        log_path = Path(rec.log_path)
        # Write one buffered line, then flip terminal before the stream starts.
        with log_path.open("w", encoding="utf-8") as fh:
            fh.write("tail-line\n")
        rec.status = "succeeded"

        # The stream must drain and return promptly — wrap in wait_for so a
        # hang manifests as a test failure rather than a hanging test.
        async def collect():
            out = []
            async for evt in reg.stream_workflow_run(
                run_id, poll_interval=0.02
            ):
                out.append(evt.line)
            return out

        result = await asyncio.wait_for(collect(), timeout=5.0)
        # Buffered line delivered + generator returned cleanly.
        assert any("tail-line" in line for line in result)

    @pytest.mark.integration
    def test_stream_route_404_for_unknown_run(self, isolated_workspace):
        """Route smoke check: unknown run_id returns 404.

        Uses TestClient — 404 is emitted before the stream starts, so
        there's no SSE iteration issue here.
        """
        from logic_loom_api.main import api

        store = BatchStore()
        store.add(EditableBatch(id="batch-1", title="t"))
        store.save(isolated_workspace[0])

        with TestClient(api) as client:
            resp = client.get("/api/runs/does-not-exist/stream")
            assert resp.status_code == 404
