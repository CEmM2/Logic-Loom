"""AKMS node-generation dispatch module — real tests.

Drives ``dispatch_node_generation_to_claude`` and
``RunRegistry.attach_workflow_subprocess`` against stub binaries so the
suite never reaches out to a real Claude CLI. The acceptance criteria
are:

  Calling the dispatcher against a stub Claude binary produces
         lines on the stream and exits cleanly.
  ``cwd`` is set to the per-run claude-runtime/ directory
         (the runtime materializer's contract).
"""

from __future__ import annotations

import asyncio
import os
import stat
import sys
import textwrap
from pathlib import Path

import pytest

from logic_loom_api.akms_dispatch import (
    AkmsDispatchError,
    build_dispatch_argv,
    dispatch_node_generation_to_claude,
)
from logic_loom_api.runs.models import RunRecord
from logic_loom_api.runs.registry import RunRegistry


def _make_stub_claude(tmp_path: Path, stdout_lines: list[str], rc: int = 0) -> Path:
    """Drop a small executable script that mimics the claude CLI.

    POSIX: `.py` with a shebang + the executable bit. Windows: a `.bat`
    that wraps the .py via the current Python interpreter — shebangs
    are not honoured by `CreateProcess` and `.py` is not in `PATHEXT`
    by default, so calling the script directly fails with
    `[WinError 193] %1 is not a valid Win32 application`.
    """
    py_stub = tmp_path / "stub_claude.py"
    py_body = textwrap.dedent(
        f"""\
        import os, sys
        # Print PWD so the test can verify cwd was honoured.
        sys.stdout.write("CWD:" + os.getcwd() + "\\n")
        for line in {stdout_lines!r}:
            sys.stdout.write(line + "\\n")
            sys.stdout.flush()
        sys.exit({rc})
        """
    )
    if os.name == "nt":
        py_stub.write_text(py_body, encoding="utf-8")
        bat = tmp_path / "stub_claude.bat"
        bat.write_text(
            f'@echo off\r\n"{sys.executable}" "{py_stub}" %*\r\n',
            encoding="utf-8",
        )
        return bat
    py_stub.write_text(f"#!{sys.executable}\n{py_body}", encoding="utf-8")
    py_stub.chmod(py_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return py_stub


class TestNodeGenerationDispatch:
    """Dispatch module + RunRegistry subprocess attachment."""

    @pytest.mark.asyncio
    async def test_dispatch_against_stub_binary(self, tmp_path):
        """Dispatcher streams lines and exits cleanly against a stub.

        The stub writes the cwd marker plus two payload lines and exits
        with rc=0. The generator must yield every line and complete
        without raising.
        """
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        plan = tmp_path / "plan.json"
        plan.write_text("{}", encoding="utf-8")
        out_dir = tmp_path / "out"
        stub = _make_stub_claude(tmp_path, ["line one", "line two"])

        lines: list[str] = []
        async for chunk in dispatch_node_generation_to_claude(
            nlm_id="nbk-1",
            node_selection="all",
            plan_path=plan,
            out_dir=out_dir,
            claude_binary=str(stub),
            cwd=runtime,
        ):
            lines.append(chunk)

        # Stub emits 3 lines: CWD marker + "line one" + "line two".
        assert any(line.startswith("CWD:") for line in lines)
        assert "line one" in lines
        assert "line two" in lines

    @pytest.mark.asyncio
    async def test_dispatch_sets_cwd_to_resolved_runtime_path(self, tmp_path):
        """cwd is the resolved per-run runtime path."""
        # Use a symlink to verify Path.resolve unwraps it (Windows long-path
        # contract surrogate).
        real = tmp_path / "real_runtime"
        real.mkdir()
        link = tmp_path / "link_runtime"
        try:
            link.symlink_to(real)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks not supported on this OS")

        plan = tmp_path / "plan.json"
        plan.write_text("{}", encoding="utf-8")
        out_dir = tmp_path / "out"
        stub = _make_stub_claude(tmp_path, [])

        lines: list[str] = []
        async for chunk in dispatch_node_generation_to_claude(
            nlm_id="nbk-1",
            node_selection="all",
            plan_path=plan,
            out_dir=out_dir,
            claude_binary=str(stub),
            cwd=link,
        ):
            lines.append(chunk)

        # First line is "CWD:<resolved>". The dispatcher resolves the
        # symlink before passing to create_subprocess_exec.
        cwd_line = next(line for line in lines if line.startswith("CWD:"))
        cwd_seen = cwd_line[len("CWD:"):]
        assert Path(cwd_seen).resolve() == real.resolve()

    def test_build_dispatch_argv_includes_prompt_inputs(self, tmp_path):
        """The argv prompt carries NLM_ID, NODE_SELECTION, PLAN_PATH, OUT_DIR."""
        argv = build_dispatch_argv(
            claude_binary="claude",
            nlm_id="nbk-42",
            node_selection="theme:a",
            plan_path=tmp_path / "plan.json",
            out_dir=tmp_path / "out",
        )
        assert argv[0] == "claude"
        assert "--print" in argv
        assert "--output-format" in argv
        assert "json" in argv
        prompt = argv[-1]
        assert "NLM_ID: nbk-42" in prompt
        assert "NODE_SELECTION: theme:a" in prompt
        assert "PLAN_PATH:" in prompt
        assert "OUT_DIR:" in prompt
        assert "node-gen skill" in prompt

    @pytest.mark.asyncio
    async def test_registry_attach_workflow_subprocess_sets_terminal_status(
        self, tmp_path
    ):
        """Driver writes to logs.txt AND flips status terminal on exit.

        SSE close-on-EOF depends on a terminal status (succeeded/failed/cancelled).
        """
        cache = tmp_path / "cache"
        cache.mkdir()
        reg = RunRegistry(cache, zsum_repo=None)
        run_id = "run_2026-05-13_0000_p91"
        log_path = tmp_path / "logs.txt"
        log_path.touch()
        reg.register_workflow_run(
            batch_id="b",
            workflow_id="akms.node_extraction",
            run_id=run_id,
            log_path=str(log_path),
            cwd=str(tmp_path),
        )

        stub = _make_stub_claude(tmp_path, ["hello from stub"])
        task = reg.attach_workflow_subprocess(
            run_id,
            args=[str(stub)],
            cwd=tmp_path,
            log_path=log_path,
        )
        await task

        rec = reg.get(run_id)
        assert rec is not None
        assert rec.status == "succeeded", f"got {rec.status}"
        assert rec.exit_code == 0
        # logs.txt content was appended.
        content = log_path.read_text(encoding="utf-8")
        assert "hello from stub" in content
        # Args + pid recorded.
        assert rec.args[0] == str(stub)
        assert rec.pid is not None

    @pytest.mark.asyncio
    async def test_registry_attach_workflow_subprocess_failed_status_on_nonzero_rc(
        self, tmp_path
    ):
        """Non-zero exit code flips status to 'failed'."""
        cache = tmp_path / "cache"
        cache.mkdir()
        reg = RunRegistry(cache, zsum_repo=None)
        run_id = "run_2026-05-13_0000_p91-fail"
        log_path = tmp_path / "logs.txt"
        log_path.touch()
        reg.register_workflow_run(
            batch_id="b",
            workflow_id="akms.node_extraction",
            run_id=run_id,
            log_path=str(log_path),
            cwd=str(tmp_path),
        )

        stub = _make_stub_claude(tmp_path, ["uh oh"], rc=3)
        task = reg.attach_workflow_subprocess(
            run_id, args=[str(stub)], cwd=tmp_path, log_path=log_path
        )
        await task

        rec = reg.get(run_id)
        assert rec.status == "failed"
        assert rec.exit_code == 3

    @pytest.mark.asyncio
    async def test_registry_attach_workflow_subprocess_spawn_failure(
        self, tmp_path
    ):
        """Missing binary still flips status to terminal ('failed')."""
        cache = tmp_path / "cache"
        cache.mkdir()
        reg = RunRegistry(cache, zsum_repo=None)
        run_id = "run_2026-05-13_0000_p91-missing"
        log_path = tmp_path / "logs.txt"
        log_path.touch()
        reg.register_workflow_run(
            batch_id="b",
            workflow_id="akms.node_extraction",
            run_id=run_id,
            log_path=str(log_path),
            cwd=str(tmp_path),
        )
        task = reg.attach_workflow_subprocess(
            run_id,
            args=[str(tmp_path / "does-not-exist")],
            cwd=tmp_path,
            log_path=log_path,
        )
        await task
        rec = reg.get(run_id)
        assert rec.status == "failed"
        # Log file mentions the spawn failure for human inspection.
        content = log_path.read_text(encoding="utf-8")
        assert "spawn failed" in content
