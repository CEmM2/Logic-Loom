"""Run registry: launch + capture + broadcast for ``zsum`` subprocesses.

Lifecycle:
  - ``submit(stage, ...)`` returns a ``RunRecord`` and starts an asyncio task
    that drains stdout+stderr line by line, parses ``LogEvent``s, persists
    them to a per-run log file, pushes them onto a ring buffer (last N lines),
    and broadcasts to any active SSE subscribers.
  - ``stream(id)`` yields ``LogEvent``s in real time. Multiple subscribers
    work; cancel propagates only the request, not the run.
  - ``cancel(id)`` sends SIGTERM and (after 5 s) SIGKILL.
  - State is persisted to ``<cache>/runs/index.json`` so history survives
    restarts.

Log format follows zsum's stdlib ``logging`` setup:
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
A regex matcher splits that. Anything that doesn't match falls back to
INFO + raw line.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import re
import shlex
import signal
import uuid
from collections import deque
from pathlib import Path
from typing import AsyncIterator, Callable

from .models import LogEvent, RunRecord, RunStatus

logger = logging.getLogger(__name__)

LOG_LINE_RE = re.compile(
    r"^(?P<ts>[0-9:.-]+\s?[0-9:.]*)\s+\[(?P<level>[A-Z]+)\]\s+(?P<name>[\w.\-]+):\s+(?P<msg>.*)$"
)
RING_SIZE = 5_000
TAIL_PERSISTED = 100  # how many lines to keep in last_lines for restart history


def _parse_line(raw: str) -> LogEvent:
    m = LOG_LINE_RE.match(raw)
    now_iso = dt.datetime.now(dt.UTC).isoformat()
    if not m:
        return LogEvent(ts=now_iso, level="INFO", line=raw)
    return LogEvent(ts=now_iso, level=m.group("level"), line=raw)


def build_zsum_argv(
    stage: str,
    config_path: Path | None,
    extra_args: list[str] | None,
    *,
    python_cmd: str | None = None,
) -> list[str]:
    """Build the argv for invoking the zsum CLI.

    Mirrors the Obsidian plugin's invocation order — ``--config <abs>`` is
    a *global* option that must come BEFORE the subcommand, otherwise the
    CLI parses it as a stage-local arg and falls back to its own config
    search. (That fallback is what made user edits in the Pipeline → Config
    tab silently ignored: zsum picked up some other file.)

    ``python_cmd`` overrides the executable prefix. It is shell-split with
    :func:`shlex.split` so users can configure either a bare program
    (``"/usr/local/bin/zsum"``), a ``uv run zsum`` pair, or a ``-m`` form.
    Falls back to ``Settings.zsum_python_cmd``'s default factory so the
    in-process tests and stale callers stay working — the default form
    spawns through the sidecar's own Python interpreter, which works
    identically in dev and packaged builds.

    Pure helper so it's testable without spawning a subprocess.
    """
    if python_cmd is None:
        # Lazy import to avoid a config<->registry import cycle. The
        # default factory mirrors what Settings would produce.
        from ..config import _default_zsum_python_cmd

        python_cmd = _default_zsum_python_cmd()
    prefix = shlex.split(python_cmd)
    config_flag = ["--config", str(config_path)] if config_path else []
    return [*prefix, *config_flag, stage, *(extra_args or [])]


class _RunState:
    """Per-run runtime state owned by the registry."""

    def __init__(self, record: RunRecord):
        self.record = record
        self.ring: deque[LogEvent] = deque(maxlen=RING_SIZE)
        self.subscribers: set[asyncio.Queue[LogEvent | None]] = set()
        self.proc: asyncio.subprocess.Process | None = None
        self.task: asyncio.Task[None] | None = None
        self.done_event = asyncio.Event()
        # Set when cancel() is invoked. The driver checks this after the
        # subprocess exits and forces status='cancelled' regardless of
        # returncode — required on Windows where TerminateProcess returns
        # a positive exit code rather than the POSIX -SIGTERM convention.
        self.cancel_requested = False

    def fanout(self, event: LogEvent) -> None:
        self.ring.append(event)
        for q in list(self.subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def signal_done(self) -> None:
        self.done_event.set()
        for q in list(self.subscribers):
            try:
                q.put_nowait(None)  # sentinel
            except asyncio.QueueFull:
                pass


class RunRegistry:
    def __init__(self, cache_dir: Path, zsum_repo: Path | None):
        self.cache_dir = cache_dir
        self.runs_dir = cache_dir / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.runs_dir / "index.json"
        self.zsum_repo = zsum_repo
        self._runs: dict[str, _RunState] = {}
        self._load_index()

    # ── persistence ────────────────────────────────────────────────────────
    def _load_index(self) -> None:
        if not self.index_path.exists():
            return
        try:
            data = json.loads(self.index_path.read_text())
        except (OSError, json.JSONDecodeError):
            return
        for raw in data.get("runs", []):
            try:
                rec = RunRecord(**raw)
                # Hydrate as a frozen state — no live process. Mark stale runs.
                if rec.status in ("pending", "running"):
                    rec.status = "failed"
                    rec.last_lines.append("(run lost across restart)")
                self._runs[rec.id] = _RunState(rec)
            except Exception:
                logger.warning("skipping malformed run entry: %s", raw)

    def _persist_index(self) -> None:
        records = [s.record.model_dump() for s in self._runs.values()]
        tmp = self.index_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"runs": records[-200:]}, default=str))
        tmp.replace(self.index_path)

    # ── public API ─────────────────────────────────────────────────────────
    def list(self, *, limit: int = 50, kind: str | None = None) -> list[RunRecord]:
        """Return the last ``limit`` runs, optionally filtered by kind.

        The ``kind`` filter lets the run-history UI and the Runs page
        request only zsum or only workflow runs
        without scanning the full list themselves.
        """
        recs = [s.record for s in self._runs.values()]
        if kind is not None:
            recs = [r for r in recs if r.kind == kind]
        return recs[-limit:]

    def get(self, run_id: str) -> RunRecord | None:
        st = self._runs.get(run_id)
        return st.record if st else None

    def get_by_triplet(
        self, batch_id: str, workflow_id: str, run_id: str
    ) -> RunRecord | None:
        """Look up a workflow run by the (batch_id, workflow_id, run_id) triplet.

        Workflow runs are indexed by this triplet so the AKMS dispatch
        routes and the Runs page can address one specific run
        unambiguously. Returns ``None``
        if no run matches (including the case where the run_id matches but
        the batch_id or workflow_id doesn't — clients should treat that as
        a 404 rather than as a lookup error).
        """
        st = self._runs.get(run_id)
        if st is None:
            return None
        rec = st.record
        if rec.kind != "workflow":
            return None
        if rec.batch_id != batch_id or rec.workflow_id != workflow_id:
            return None
        return rec

    def list_workflow_runs(
        self, *, batch_id: str | None = None, workflow_id: str | None = None
    ) -> list[RunRecord]:
        """List workflow runs, optionally filtered by batch_id / workflow_id.

        Either filter can be omitted; both can be set to narrow to one
        (batch, workflow) pair.
        """
        out = [
            s.record for s in self._runs.values() if s.record.kind == "workflow"
        ]
        if batch_id is not None:
            out = [r for r in out if r.batch_id == batch_id]
        if workflow_id is not None:
            out = [r for r in out if r.workflow_id == workflow_id]
        return out

    def register_workflow_run(
        self,
        *,
        batch_id: str,
        workflow_id: str,
        run_id: str,
        log_path: str,
        args: list[str] | None = None,
        cwd: str | None = None,
    ) -> RunRecord:
        """Persist a workflow run record without launching a subprocess.

        The run-workflow handler creates the run record up front so the
        output store and the Runs page have something to address. The
        dispatch path later mutates ``status``/``pid``/``exit_code`` via
        :meth:`attach_workflow_subprocess`. The record is born in the
        ``"pending"`` state.

        ``stage`` is set to ``workflow_id`` so consumers that still read
        ``stage`` (legacy code paths) receive a meaningful value.
        """
        record = RunRecord(
            id=run_id,
            stage=workflow_id,
            kind="workflow",
            batch_id=batch_id,
            workflow_id=workflow_id,
            log_path=log_path,
            args=list(args or []),
            cwd=cwd,
        )
        if run_id in self._runs:
            raise ValueError(f"run id already present: {run_id!r}")
        self._runs[run_id] = _RunState(record)
        self._persist_index()
        return record

    async def submit(
        self,
        stage: str,
        *,
        config_overrides: dict[str, str] | None = None,
        cmd_override: list[str] | None = None,
        cwd_override: Path | None = None,
        extra_args: list[str] | None = None,
        config_path: Path | None = None,
        python_cmd: str | None = None,
    ) -> RunRecord:
        run_id = uuid.uuid4().hex[:16]
        log_path = self.runs_dir / f"{run_id}.log"
        cwd = cwd_override or self.zsum_repo
        if not cwd:
            raise RuntimeError("zsum_repo not configured; cannot launch zsum runs")
        args = cmd_override or build_zsum_argv(
            stage, config_path, extra_args, python_cmd=python_cmd
        )
        record = RunRecord(
            id=run_id,
            stage=stage,
            status="pending",
            log_path=str(log_path),
            args=args,
            cwd=str(cwd),
        )
        state = _RunState(record)
        self._runs[run_id] = state
        self._persist_index()
        state.task = asyncio.create_task(self._drive(state, args, cwd, config_overrides or {}))
        return record

    def cancel(self, run_id: str) -> bool:
        st = self._runs.get(run_id)
        if not st or not st.proc or st.record.status not in ("running", "pending"):
            return False
        st.cancel_requested = True
        try:
            st.proc.send_signal(signal.SIGTERM)
        except ProcessLookupError:
            return False
        return True

    # ── deletion ────────────────────────────────────────────────────────────
    _TERMINAL_STATUSES: tuple[str, ...] = ("succeeded", "failed", "cancelled")

    def delete(self, run_id: str) -> bool:
        """Delete a single terminal run record from the registry.

        Refuses to delete pending / running runs — caller must cancel
        first. Best-effort cleanup of the on-disk log file and run dir;
        filesystem errors are logged but do not block the in-memory
        removal because the registry is the source of truth for the UI.

        Returns ``True`` if a record was removed, ``False`` if the id
        is unknown. Raises :class:`ValueError` for non-terminal runs so
        the route layer can map to HTTP 409.
        """
        st = self._runs.get(run_id)
        if st is None:
            return False
        if st.record.status not in self._TERMINAL_STATUSES:
            raise ValueError(
                f"refusing to delete non-terminal run {run_id!r} "
                f"(status={st.record.status!r}); cancel first"
            )
        self._cleanup_artifacts(st.record)
        del self._runs[run_id]
        self._persist_index()
        return True

    def delete_terminal(self, statuses: tuple[str, ...] | None = None) -> int:
        """Bulk-delete all runs whose status is in ``statuses``.

        Defaults to every terminal status (succeeded + failed + cancelled).
        Returns the count of records removed. Non-terminal statuses in the
        filter are silently ignored — bulk-clear is for housekeeping, not
        cancellation.
        """
        allowed: set[str] = set(statuses or self._TERMINAL_STATUSES)
        allowed &= set(self._TERMINAL_STATUSES)
        if not allowed:
            return 0
        victims = [
            rid for rid, st in self._runs.items() if st.record.status in allowed
        ]
        for rid in victims:
            self._cleanup_artifacts(self._runs[rid].record)
            del self._runs[rid]
        if victims:
            self._persist_index()
        return len(victims)

    def _cleanup_artifacts(self, record: RunRecord) -> None:
        """Best-effort removal of the run's log file and run dir."""
        log_path = Path(record.log_path) if record.log_path else None
        if log_path is None:
            return
        try:
            if log_path.is_file():
                log_path.unlink()
            # Workflow runs store other artefacts alongside logs.txt under
            # the run dir; remove the whole directory if it looks like one.
            run_dir = log_path.parent
            if run_dir != self.runs_dir and run_dir.is_dir():
                import shutil as _shutil

                _shutil.rmtree(run_dir, ignore_errors=True)
        except OSError as exc:
            logger.warning(
                "cleanup failed for run %s log=%s: %s",
                record.id,
                log_path,
                exc,
            )

    async def stream(self, run_id: str) -> AsyncIterator[LogEvent]:
        st = self._runs.get(run_id)
        if not st:
            return
        # Replay ring buffer first so a fresh subscriber sees prior context.
        for evt in list(st.ring):
            yield evt
        if st.done_event.is_set():
            return
        q: asyncio.Queue[LogEvent | None] = asyncio.Queue(maxsize=RING_SIZE)
        st.subscribers.add(q)
        try:
            while True:
                evt = await q.get()
                if evt is None:
                    return
                yield evt
        finally:
            st.subscribers.discard(q)

    # ── workflow-run streaming ────────────────────────────────────────────
    async def stream_workflow_run(
        self,
        run_id: str,
        *,
        poll_interval: float = 0.1,
    ) -> AsyncIterator[LogEvent]:
        """Tail a workflow run's ``logs.txt`` and yield each appended line.

        Contract:
          * For workflow runs (kind == "workflow") the executor writes
            ``<run_dir>/logs.txt`` and the registry record's status
            transitions to a terminal value (succeeded/failed/cancelled)
            when the executor exits.
          * This stream tails the file by polling at ``poll_interval``
            seconds. Each new line becomes a :class:`LogEvent` via the
            same regex parser used for zsum runs.
          * Close-on-EOF: when the record reaches a terminal status AND
            the read loop has consumed every byte written, the generator
            returns and the SSE response closes cleanly.

        Falls through to :meth:`stream` for zsum runs (which have a live
        subprocess + ring buffer) — keeping the public surface uniform
        for the route layer.
        """
        st = self._runs.get(run_id)
        if not st:
            return
        rec = st.record
        if rec.kind != "workflow":
            async for evt in self.stream(run_id):
                yield evt
            return
        if not rec.log_path:
            return
        log_path = Path(rec.log_path)
        # File may not exist if the executor hasn't started yet; wait briefly
        # for the OutputStore.create_run_dir to have staked it out.
        for _ in range(50):  # up to ~5s
            if log_path.exists():
                break
            await asyncio.sleep(poll_interval)
        if not log_path.exists():
            return

        terminal: set[str] = {"succeeded", "failed", "cancelled"}
        buffer = ""
        with log_path.open("r", encoding="utf-8") as fh:
            while True:
                chunk = fh.read()
                if chunk:
                    buffer += chunk
                    # Emit each newline-terminated segment.
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        if line:
                            yield _parse_line(line)
                # Refresh record to test for terminal state.
                latest = self.get(run_id)
                if latest is not None and latest.status in terminal:
                    # Drain any remaining bytes (incl. trailing line without \n).
                    tail = fh.read()
                    if tail:
                        buffer += tail
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            if line:
                                yield _parse_line(line)
                    if buffer:
                        yield _parse_line(buffer)
                    return
                await asyncio.sleep(poll_interval)

    # ── workflow-run dispatch ──────────────────────────────────────────────
    def attach_workflow_subprocess(
        self,
        run_id: str,
        *,
        args: list[str],
        cwd: Path | str,
        log_path: Path | str | None = None,
        env: dict[str, str] | None = None,
        stdin: str | None = None,
        on_done: "Callable[[RunRecord], None] | None" = None,
    ) -> asyncio.Task[None]:
        """Attach a real subprocess to an already-registered workflow run.

        ``RunService.start_workflow`` calls
        :meth:`register_workflow_run` which mints the registry record in
        the ``pending`` state. The dispatch module then calls this entry point to
        launch the actual executor; the driver task:

          * appends each decoded stdout line to ``log_path`` (the same
            ``<run_dir>/logs.txt`` the SSE tail polls),
          * flips ``record.status`` ``pending → running → succeeded |
            failed | cancelled`` so :meth:`stream_workflow_run`'s close-on-
            EOF loop terminates cleanly (a run left non-terminal would keep
            the SSE stream open forever),
          * records ``args`` + ``pid`` + ``exit_code`` + ``ended_at`` on
            the record so the run-history endpoints see the live data.

        The returned :class:`asyncio.Task` is owned by the registry — the
        caller does not need to await it. Cancellation flows through
        :meth:`cancel` (SIGTERM → SIGKILL after 5s, same as zsum runs).

        ``on_done`` is invoked once with the final :class:`RunRecord`
        after the subprocess terminates (success, failure, or
        cancellation) and the terminal status is persisted. Exceptions
        in the callback are logged at WARNING and swallowed so an
        indexer hiccup never crashes the driver task. Use this hook to
        wire the research-memory index — see
        ``assistant.runner.RunnerService.start_run``.

        ``stdin``, when supplied, is written to the subprocess's stdin and
        the pipe is then closed (EOF) before stdout is streamed — for
        stdin-mode CLI templates whose command carries no ``{prompt}`` token
        (node-gen host-LLM bridge, issue #103). ``None`` (the default) leaves
        stdin inherited, exactly as before.

        Raises ``KeyError`` if ``run_id`` is unknown,
        :class:`RuntimeError` if the run is already attached.
        """
        st = self._runs.get(run_id)
        if st is None:
            raise KeyError(f"run not registered: {run_id!r}")
        if st.task is not None and not st.task.done():
            raise RuntimeError(
                f"run {run_id!r} already has a live subprocess task"
            )

        record = st.record
        record.args = list(args)
        record.cwd = str(cwd)
        if log_path is None:
            if not record.log_path:
                raise RuntimeError(
                    f"run {run_id!r} has no log_path and none supplied"
                )
            target_log = Path(record.log_path)
        else:
            target_log = Path(log_path)
            record.log_path = str(target_log)

        st.task = asyncio.create_task(
            self._drive_workflow(
                st,
                args,
                Path(cwd),
                target_log,
                env or {},
                stdin=stdin,
                on_done=on_done,
            )
        )
        return st.task

    async def _drive_workflow(
        self,
        state: _RunState,
        args: list[str],
        cwd: Path,
        log_path: Path,
        env_overrides: dict[str, str],
        *,
        stdin: str | None = None,
        on_done: "Callable[[RunRecord], None] | None" = None,
    ) -> None:
        """Workflow-run executor — appends to ``logs.txt`` and sets terminal status.

        Mirrors :meth:`_drive` for zsum but with three differences:

          * Writes in APPEND mode (``"a"``) rather than truncate — the
            ``OutputStore.create_run_dir`` may have staked out an
            empty ``logs.txt``; we preserve any existing content.
          * Persists each parsed log event to the ring buffer + subscriber
            fanout via :meth:`_RunState.fanout`, so any zsum-style stream
            consumers still work even though SSE primarily tails the file.
          * Always sets a terminal record status before returning,
            including on spawn failure — otherwise the SSE tail never closes.
        """
        record = state.record

        def _fire_on_done() -> None:
            """Run the post-completion hook; swallow exceptions.

            Indexer hiccups must never crash the driver task — the
            terminal status is already persisted, so the worst case is
            a stale search index until the next manual rebuild.
            """
            if on_done is None:
                return
            try:
                on_done(record)
            except Exception as exc:  # noqa: BLE001 — preserve driver liveness
                logger.warning(
                    "workflow run %s on_done hook failed: %s", record.id, exc
                )

        # Resolve cwd up front; Path.resolve handles Windows long-paths.
        resolved_cwd = Path(cwd).expanduser().resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, **(env_overrides or {})}
        for _k in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME"):
            env.pop(_k, None)

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                cwd=str(resolved_cwd),
                stdin=asyncio.subprocess.PIPE if stdin is not None else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
        except OSError as exc:
            # Spawn failed for ANY OS-level reason — missing binary (FileNotFoundError),
            # an unmaterialised / non-directory cwd (NotADirectoryError), a permission
            # error, or a rarer kernel exec error (ENOEXEC / E2BIG, etc.). All are
            # OSError subclasses; catching the base class means a doomed launch ALWAYS
            # flips to a terminal status instead of crashing the driver task and leaving
            # the run wedged non-terminal forever — the SSE tail would never close
            # (issue #103). Degrade gracefully: a failed launch never raises.
            record.status = "failed"
            record.exit_code = -1
            record.ended_at = dt.datetime.now(dt.UTC).isoformat()
            record.last_lines.append(f"(spawn failed: {exc})")
            try:
                with log_path.open("a", encoding="utf-8") as fh:
                    fh.write(f"(spawn failed: {exc})\n")
            except OSError:
                pass
            self._persist_index()
            state.signal_done()
            _fire_on_done()
            return

        state.proc = proc
        record.status = "running"
        record.pid = proc.pid
        self._persist_index()

        # Stdin-mode CLI template (no {prompt} token): feed the prompt to the
        # agent's stdin then close so it sees EOF before we stream stdout. A
        # broken pipe (agent exited early) is non-fatal — the wait/status logic
        # below records the real outcome.
        if stdin is not None and proc.stdin is not None:
            try:
                proc.stdin.write(stdin.encode("utf-8"))
                await proc.stdin.drain()
                proc.stdin.close()
            except (BrokenPipeError, ConnectionResetError, OSError) as exc:
                logger.debug(
                    "workflow run %s: stdin write skipped (%s)", record.id, exc
                )

        async def reader() -> None:
            assert proc.stdout
            with log_path.open("a", encoding="utf-8") as fh:
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    text = line.decode("utf-8", errors="replace").rstrip("\n")
                    fh.write(text + "\n")
                    fh.flush()
                    state.fanout(_parse_line(text))

        try:
            await reader()
            rc = await proc.wait()
        except asyncio.CancelledError:
            # If our task itself is cancelled, propagate after marking the
            # record cancelled. The subprocess will be reaped by the
            # cancel() path (SIGTERM/SIGKILL).
            record.status = "cancelled"
            record.ended_at = dt.datetime.now(dt.UTC).isoformat()
            self._persist_index()
            state.signal_done()
            _fire_on_done()
            raise

        record.exit_code = rc
        record.ended_at = dt.datetime.now(dt.UTC).isoformat()
        # cancel_requested wins over returncode-based inference. On POSIX,
        # SIGTERM yields rc < 0 (-15) and the `elif rc < 0` branch would
        # have caught it; on Windows, TerminateProcess returns a positive
        # exit code (1) so we'd otherwise fall through to 'failed'.
        if state.cancel_requested:
            record.status = "cancelled"
        elif rc == 0:
            record.status = "succeeded"
        elif rc < 0:
            record.status = "cancelled"
        else:
            record.status = "failed"
        record.last_lines = [evt.line for evt in list(state.ring)[-TAIL_PERSISTED:]]
        self._persist_index()
        state.signal_done()
        _fire_on_done()

    # ── driver ─────────────────────────────────────────────────────────────
    async def _drive(
        self,
        state: _RunState,
        args: list[str],
        cwd: Path,
        config_overrides: dict[str, str],
    ) -> None:
        record = state.record
        log_path = Path(record.log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, **config_overrides}
        # Unset venv vars so uv resolves the subprocess venv correctly.
        for _k in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME"):
            env.pop(_k, None)
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
        except FileNotFoundError as e:
            record.status = "failed"
            record.exit_code = -1
            record.ended_at = dt.datetime.now(dt.UTC).isoformat()
            record.last_lines.append(f"(spawn failed: {e})")
            self._persist_index()
            state.signal_done()
            return

        state.proc = proc
        record.status = "running"
        record.pid = proc.pid
        self._persist_index()

        async def reader() -> None:
            assert proc.stdout
            with log_path.open("w", encoding="utf-8") as fh:
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    text = line.decode("utf-8", errors="replace").rstrip("\n")
                    fh.write(text + "\n")
                    fh.flush()
                    state.fanout(_parse_line(text))

        await reader()
        rc = await proc.wait()

        record.exit_code = rc
        record.ended_at = dt.datetime.now(dt.UTC).isoformat()
        # cancel_requested wins over returncode-based inference (Windows
        # TerminateProcess returns positive exit codes, not -SIGTERM).
        if state.cancel_requested:
            record.status = "cancelled"
        elif rc == 0:
            record.status = "succeeded"
        elif rc < 0:  # signal-terminated (POSIX path)
            record.status = "cancelled"
        else:
            record.status = "failed"
        record.last_lines = [evt.line for evt in list(state.ring)[-TAIL_PERSISTED:]]
        self._persist_index()
        state.signal_done()


_GLOBAL_REGISTRY: RunRegistry | None = None


def init_registry(cache_dir: Path, zsum_repo: Path | None) -> RunRegistry:
    global _GLOBAL_REGISTRY
    _GLOBAL_REGISTRY = RunRegistry(cache_dir, zsum_repo)
    return _GLOBAL_REGISTRY


def get_registry() -> RunRegistry:
    if _GLOBAL_REGISTRY is None:
        raise RuntimeError("RunRegistry not initialized — call init_registry() in app lifespan")
    return _GLOBAL_REGISTRY
