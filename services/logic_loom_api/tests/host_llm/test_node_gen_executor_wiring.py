"""Wiring of the resolved node-gen runtime into the LIVE executor (issue #103).

The host-LLM node-gen bridge was first built + validated (``resolve_node_gen_runtime``,
the ``require_node_gen_binary`` → 400 gate, template-aware argv) but stopped at the
adapter boundary: ``create_run`` only *validated* the chosen binary and then
registered the run with ``args=[]`` — the resolved provider had **no** end-to-end
effect. The wiring connects that boundary to the actual subprocess launch.

``POST /api/akms/node-generation/runs`` now threads the resolved provider's argv +
subprocess-scoped ``AKMS_LLM_*`` env + stdin into
:meth:`RunRegistry.attach_workflow_subprocess` (full dispatch — EVERY resolvable
provider is dispatched, not merely gated):

* ``codex-cli`` / a custom CLI → the provider's OWN template argv.
* ``akms`` API → the claude shape + ``AKMS_LLM_PROVIDER`` / ``AKMS_MODEL`` / key env.
* ``nlm`` → the ``nlm run --notebook … --workflow … --out-dir …`` shape.
* no choice → the claude fallback, no env (back-compat).

These are route-level integration tests: they drive the real ``create_run`` route
(async + threadpool + attach) with a stubbed ``attach_workflow_subprocess`` that
CAPTURES the launch ``args`` / ``env`` / ``stdin`` so the test asserts what the
registry receives for each provider choice, without spawning a real agent CLI.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from logic_loom_api.akms_claude_runtime import runtime_dir
from logic_loom_api.batches import BatchStore, EditableBatch
from logic_loom_api.routes.akms_dispatch import AKMS_NODE_EXTRACTION_WORKFLOW
from logic_loom_api.runs.registry import RunRegistry

#: Completion-key env vars that, if exported by CI, would flip akms/no-choice
#: resolution — scrubbed in every test so a CI-exported key cannot flip it.
_KEY_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "AKMS_LLM_API_BASE",
    "AKMS_LEARN_NLM_NOTEBOOK_ID",
    "NLM_NOTEBOOK_ID",
)


@pytest.fixture(autouse=True)
def _scrub_key_env(monkeypatch):
    for var in _KEY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _all_binaries_on_path(monkeypatch):
    """Make every agent binary resolve on PATH so CLI providers are 'available'
    and the gate never 400s on a missing binary — resolution stays deterministic
    regardless of what is actually installed on the test host."""
    monkeypatch.setattr(shutil, "which", lambda binary: "/usr/bin/" + binary)


@pytest.fixture()
def wired_client(tmp_path, monkeypatch):
    """TestClient over the real create_run route with a CAPTURING attach stub.

    Yields ``(client, captured)`` where ``captured`` is the list of attach-call
    kwargs (``run_id`` / ``args`` / ``cwd`` / ``log_path`` / ``env`` / ``stdin``)
    the route handed to the registry. The stub replaces the real
    ``attach_workflow_subprocess`` so no agent CLI is spawned — the assertion is
    purely on the launch command the wiring produced.
    """
    from fastapi import FastAPI
    from logic_loom_api.routes import akms_dispatch as akms_routes
    from logic_loom_api.routes import runs as runs_routes
    from logic_loom_api.runs.registry import init_registry

    ws = tmp_path / "akms-workspace"
    ws.mkdir()
    monkeypatch.setenv("LOGIC_LOOM_AKMS_WORKSPACE", str(ws))
    monkeypatch.setenv("LOGIC_LOOM_CACHE_PATH", str(tmp_path / "cache"))
    # Point config at a non-existent TOML so only env vars drive resolution.
    monkeypatch.setenv("LOGIC_LOOM_CONFIG", str(tmp_path / "missing.toml"))

    store = BatchStore()
    store.add(EditableBatch(id="batch-1", title="Test Batch"))
    store.save(ws)

    captured: list[dict] = []

    def _capture_attach(self, run_id, *, args, cwd, log_path, env=None, stdin=None, on_done=None):
        captured.append(
            {
                "run_id": run_id,
                "args": list(args),
                "cwd": str(cwd),
                "log_path": str(log_path),
                "env": dict(env or {}),
                "stdin": stdin,
            }
        )
        return None  # the route ignores the return; no real subprocess

    monkeypatch.setattr(RunRegistry, "attach_workflow_subprocess", _capture_attach)

    app = FastAPI()
    app.include_router(akms_routes.router, prefix="/api/akms/node-generation")
    app.include_router(runs_routes.router, prefix="/api/runs")
    cache = tmp_path / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    init_registry(cache, zsum_repo=None)

    with TestClient(app) as client:
        yield client, captured


def _create(client, **extra):
    # Default a notebook id so tests exercise dispatch/attach without calling
    # `nlm notebook create` (empty nlm_id now means create — nlm_batch_fix).
    body = {
        "batch_id": "batch-1",
        "run_label": "ng",
        "dry_run": False,
        "nlm_id": "nb-test",
        **extra,
    }
    return client.post("/api/akms/node-generation/runs", json=body)


class TestIssue103ExecutorWiring:
    """The resolved node-gen runtime reaches the live executor (#103)."""

    @pytest.mark.integration
    def test_codex_cli_choice_dispatches_codex_template_not_claude_flags(
        self, wired_client, monkeypatch, tmp_path
    ):
        """A codex-cli choice launches ``codex exec <prompt>`` — its OWN template,
        never claude's --print/--output-format flags (which codex rejects) — in a
        codex runtime workspace that carries the node-gen skill."""
        import logic_loom_api.akms_claude_runtime as rt

        # Redirect the runtime cache off the real user cache for this test.
        monkeypatch.setattr(rt, "cache_base_dir", lambda: tmp_path / "cache-base")
        monkeypatch.setenv("LOGIC_LOOM_NODEGEN_LLM_PROVIDER", "codex-cli")
        client, captured = wired_client

        resp = _create(client)
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "enqueued"

        assert len(captured) == 1, "executor must be attached exactly once"
        args = captured[0]["args"]
        assert args[0] == "codex"
        assert args[1] == "exec"
        assert "--print" not in args, args
        assert "--output-format" not in args, args
        # The node-gen instruction prompt is substituted into the {prompt} slot.
        assert any("Use the node-gen skill" in a for a in args), args
        # CLI providers authenticate via their own session — no AKMS_LLM_* env.
        assert captured[0]["env"] == {}
        assert captured[0]["stdin"] is None
        # codex runs in its OWN PER-RUN runtime workspace (under
        # codex_runtime_dir()/<run_id>, NOT the shared base, NOT claude's runtime_dir),
        # materialised with the node-gen skill under codex's .agents/skills/ + AGENTS.md.
        run_id = resp.json()["run_id"]
        cwd = Path(captured[0]["cwd"])
        assert cwd == rt.codex_runtime_dir() / run_id
        assert cwd != rt.codex_runtime_dir()  # per-run, not the shared base
        assert cwd.parent == rt.codex_runtime_dir()
        assert (cwd / ".agents" / "skills" / "node-gen" / "SKILL.md").is_file()
        assert (cwd / "AGENTS.md").is_file()

    @pytest.mark.integration
    def test_akms_api_choice_sets_akms_llm_env_on_claude_dispatch(
        self, wired_client, monkeypatch
    ):
        """An akms API choice keeps dispatching claude (it parses claude's JSON
        output) but layers the AKMS_LLM_* + key env onto the subprocess, scoped —
        never written into the parent os.environ."""
        monkeypatch.setenv("LOGIC_LOOM_NODEGEN_LLM_PROVIDER", "akms")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
        client, captured = wired_client

        resp = _create(client)
        assert resp.status_code == 200, resp.text

        assert len(captured) == 1
        args = captured[0]["args"]
        assert args[0] == "claude"
        assert "--output-format" in args and "json" in args
        env = captured[0]["env"]
        assert env["AKMS_LLM_PROVIDER"] == "openai"
        assert env["AKMS_MODEL"] == "gpt-4o"
        assert env["OPENAI_API_KEY"] == "sk-openai-test"
        # No process-global leak: the parent env is untouched by the wiring.
        assert "AKMS_LLM_PROVIDER" not in os.environ
        assert "AKMS_MODEL" not in os.environ

    @pytest.mark.integration
    def test_nlm_choice_dispatches_nlm_batch_module(self, wired_client, monkeypatch):
        """An nlm choice (CLI on PATH + a configured notebook id) launches the
        vendored ``akms_nodes_gen.nlm_batch`` module (NotebookLM-only synthesis,
        no agent LLM), grounded on the notebook, with the bundled per-node prompt."""
        monkeypatch.setenv("LOGIC_LOOM_NODEGEN_LLM_PROVIDER", "nlm")
        monkeypatch.setenv("NLM_NOTEBOOK_ID", "nbk-103")
        client, captured = wired_client

        resp = _create(client)
        assert resp.status_code == 200, resp.text
        out_dir = resp.json()["out_dir"]

        assert len(captured) == 1
        args = captured[0]["args"]
        # Dispatched via the python module, NOT the (non-existent) `nlm run`.
        assert args[:3] == [sys.executable, "-m", "akms_nodes_gen.nlm_batch"]
        assert "nlm" not in args[1:3]  # no bogus `nlm run`
        # Grounded on the resolved notebook, writing into the run's output dir.
        assert args[args.index("--notebook-id") + 1] == "nbk-103"
        assert args[args.index("--out-dir") + 1] == out_dir
        assert args[args.index("--output-format") + 1] == "yaml"
        # The bundled NotebookLM per-node prompt is supplied (--prompt-file required).
        assert args[args.index("--prompt-file") + 1].endswith("notebooklm_node_prompt.md")
        assert "--plan" in args
        # #103 E2E: partial-batch edges are allowed; no rapid auto-retry storm.
        assert "--allow-invented-edges" in args
        assert args[args.index("--max-retries") + 1] == "0"
        assert captured[0]["env"] == {}

    @pytest.mark.integration
    def test_no_choice_dispatches_claude_fallback_no_env(
        self, wired_client, monkeypatch
    ):
        """No provider configured → the claude fallback, unchanged, with no env
        manipulation (back-compat)."""
        monkeypatch.delenv("LOGIC_LOOM_NODEGEN_LLM_PROVIDER", raising=False)
        monkeypatch.delenv("LOGIC_LOOM_LLM_PROVIDER", raising=False)
        client, captured = wired_client

        resp = _create(client)
        assert resp.status_code == 200, resp.text

        assert len(captured) == 1
        args = captured[0]["args"]
        assert args[0] == "claude"
        assert "--print" in args
        assert captured[0]["env"] == {}
        assert captured[0]["stdin"] is None

    @pytest.mark.integration
    def test_attach_cwd_is_per_run_claude_runtime_workspace(
        self, wired_client, monkeypatch, tmp_path
    ):
        """The agent runs the bundled node-gen skill, so the launch cwd is a PER-RUN
        materialised claude-runtime workspace (runtime_dir()/<run_id>) —
        NOT the output dir, NOT the shared runtime_dir."""
        import logic_loom_api.akms_claude_runtime as rt

        monkeypatch.setattr(rt, "cache_base_dir", lambda: tmp_path / "cache-base")
        monkeypatch.delenv("LOGIC_LOOM_NODEGEN_LLM_PROVIDER", raising=False)
        client, captured = wired_client

        resp = _create(client)
        assert resp.status_code == 200, resp.text
        assert len(captured) == 1
        run_id = resp.json()["run_id"]
        cwd = Path(captured[0]["cwd"])
        assert cwd == rt.runtime_dir() / run_id
        assert cwd.parent == rt.runtime_dir()  # per-run subdir, not the shared base
        assert (cwd / ".claude").is_dir()  # the claude tree was materialised into it
        # The log target is the run's logs.txt (the SSE tail's file).
        assert captured[0]["log_path"].endswith("logs.txt")

    @pytest.mark.integration
    def test_two_runs_get_distinct_per_run_cwds(self, wired_client, monkeypatch, tmp_path):
        """Race regression: two node-gen runs must get DIFFERENT per-run cwds, so one
        run's force=True rmtree can never wipe the skill tree another run is executing
        from. Two codex runs → two distinct dirs, both under the codex runtime base."""
        import logic_loom_api.akms_claude_runtime as rt

        monkeypatch.setattr(rt, "cache_base_dir", lambda: tmp_path / "cache-base")
        monkeypatch.setenv("LOGIC_LOOM_NODEGEN_LLM_PROVIDER", "codex-cli")
        client, captured = wired_client

        # Distinct labels → distinct run_ids (make_run_id is label+minute based, so
        # same-label same-minute runs would collide on id — the registry rejects the
        # dup, so they never both launch; here we exercise two genuinely distinct runs).
        r1 = _create(client, run_label="ng-a")
        r2 = _create(client, run_label="ng-b")
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.json()["run_id"] != r2.json()["run_id"]
        assert len(captured) == 2
        cwd1, cwd2 = captured[0]["cwd"], captured[1]["cwd"]
        assert cwd1 != cwd2, "concurrent runs must not share a cwd"
        assert Path(cwd1).parent == rt.codex_runtime_dir()
        assert Path(cwd2).parent == rt.codex_runtime_dir()
        # Each per-run dir independently carries the skill (neither wiped the other).
        assert (Path(cwd1) / ".agents" / "skills" / "node-gen" / "SKILL.md").is_file()
        assert (Path(cwd2) / ".agents" / "skills" / "node-gen" / "SKILL.md").is_file()

    @pytest.mark.integration
    def test_dry_run_does_not_attach_a_subprocess(self, wired_client, monkeypatch):
        """A dry run returns a preview and is never registered — so no executor
        is launched (the #103 wiring is gated on an enqueued run)."""
        monkeypatch.setenv("LOGIC_LOOM_NODEGEN_LLM_PROVIDER", "codex-cli")
        client, captured = wired_client

        resp = _create(client, dry_run=True)
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "preview"
        assert captured == [], "dry run must NOT launch an executor"

    @pytest.mark.integration
    def test_chosen_but_missing_cli_still_400s_before_any_attach(
        self, wired_client, monkeypatch
    ):
        """The fail-fast missing-binary gate still fires under the wiring: a codex-cli
        choice with codex NOT on PATH → 400 and NO executor launched."""
        monkeypatch.setenv("LOGIC_LOOM_NODEGEN_LLM_PROVIDER", "codex-cli")
        # Only claude resolves; codex is missing → the gate raises.
        monkeypatch.setattr(
            shutil, "which", lambda binary: "/usr/bin/claude" if binary == "claude" else None
        )
        client, captured = wired_client

        resp = _create(client)
        assert resp.status_code == 400, resp.text
        assert "codex" in resp.text
        assert captured == [], "a gated run must never reach the executor"

    @pytest.mark.integration
    def test_custom_cli_without_prompt_token_pipes_prompt_on_stdin(
        self, wired_client, monkeypatch
    ):
        """A custom CLI whose command has no ``{prompt}`` token gets the node-gen
        prompt on stdin (mirroring host_llm's _run_cli_template convention), and
        the route threads that stdin into the executor."""
        monkeypatch.setenv("LOGIC_LOOM_NODEGEN_LLM_PROVIDER", "mytool")
        monkeypatch.setenv(
            "LOGIC_LOOM_LEARN_LLM_CUSTOM_CLIS", '{"mytool": "mytool --run"}'
        )
        client, captured = wired_client

        resp = _create(client)
        assert resp.status_code == 200, resp.text

        assert len(captured) == 1
        assert captured[0]["args"] == ["mytool", "--run"]
        assert "Use the node-gen skill" in (captured[0]["stdin"] or ""), captured[0]
        assert captured[0]["env"] == {}


@pytest.mark.asyncio
async def test_registry_attach_feeds_stdin_to_subprocess(tmp_path):
    """The new ``stdin`` param on attach_workflow_subprocess is written to the
    child's stdin then closed (EOF) before stdout streams — proven end-to-end
    against a stub that echoes whatever it reads on stdin."""
    import stat
    import sys
    import textwrap

    from logic_loom_api.runs.registry import RunRegistry

    cache = tmp_path / "cache"
    cache.mkdir()
    reg = RunRegistry(cache, zsum_repo=None)

    stub = tmp_path / "stdin_echo.py"
    body = textwrap.dedent(
        """\
        import sys
        data = sys.stdin.read()
        sys.stdout.write("STDIN:" + data + "\\n")
        """
    )
    if sys.platform.startswith("win"):
        stub.write_text(body, encoding="utf-8")
        stub_args = [sys.executable, str(stub)]
    else:
        stub.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        stub_args = [str(stub)]

    run_id = "run_issue103_stdin"
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
        args=stub_args,
        cwd=tmp_path,
        log_path=log_path,
        stdin="prompt-on-stdin",
    )
    await task

    rec = reg.get(run_id)
    assert rec.status == "succeeded", rec.status
    assert "STDIN:prompt-on-stdin" in log_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_drive_workflow_terminalizes_on_nonfilenotfound_oserror(tmp_path, monkeypatch):
    """Regression: a spawn-time OSError that is NOT FileNotFoundError (e.g. ENOEXEC —
    a bad interpreter / exec-format error, or an unusable env) must still flip the run
    to a terminal status. Before the guard was widened to ``except OSError`` it would
    escape, crash the driver task, and leave the run wedged non-terminal forever (the
    SSE tail never closing). Mutation note: narrowing the except back to
    FileNotFoundError makes this FAIL (status stays 'running')."""
    import asyncio as _asyncio

    from logic_loom_api.runs.registry import RunRegistry

    cache = tmp_path / "cache"
    cache.mkdir()
    reg = RunRegistry(cache, zsum_repo=None)

    run_id = "run_issue103_oserror"
    log_path = tmp_path / "logs.txt"
    log_path.touch()
    reg.register_workflow_run(
        batch_id="b",
        workflow_id="akms.node_extraction",
        run_id=run_id,
        log_path=str(log_path),
        cwd=str(tmp_path),
    )

    async def _raise_enoexec(*args, **kwargs):
        # 8 == errno.ENOEXEC ("Exec format error") — an OSError that is NOT
        # FileNotFoundError/NotADirectoryError/PermissionError.
        raise OSError(8, "Exec format error")

    monkeypatch.setattr(
        _asyncio, "create_subprocess_exec", _raise_enoexec
    )

    task = reg.attach_workflow_subprocess(
        run_id, args=["whatever"], cwd=tmp_path, log_path=log_path
    )
    await task

    rec = reg.get(run_id)
    assert rec.status == "failed", f"expected terminal 'failed', got {rec.status!r}"
    assert rec.exit_code == -1
    assert "spawn failed" in log_path.read_text(encoding="utf-8")


def test_materialize_codex_runtime_lays_down_node_gen_skill(tmp_path, monkeypatch):
    """The codex runtime carries the node-gen skill at codex's .agents/skills/
    location (+ AGENTS.md + the version sentinel), seeded from the SAME node-gen skill
    bundled for the claude runtime — so `codex exec` (cwd = this dir) can discover and
    run it, exactly as the live codex test verified."""
    import logic_loom_api.akms_claude_runtime as rt

    monkeypatch.setattr(rt, "cache_base_dir", lambda: tmp_path / "cb")

    target = rt.materialize_codex_runtime(force=True)

    assert target == rt.codex_runtime_dir()
    assert target != rt.runtime_dir()
    skill = target / ".agents" / "skills" / "node-gen" / "SKILL.md"
    assert skill.is_file(), "node-gen SKILL.md must be present under .agents/skills/"
    assert "node-gen" in skill.read_text(encoding="utf-8")
    assert (target / "AGENTS.md").is_file()
    assert (target / ".logic-loom-runtime-ok").read_text(encoding="utf-8").strip() == rt.RUNTIME_VERSION
