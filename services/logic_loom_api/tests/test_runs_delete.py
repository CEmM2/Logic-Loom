"""Registry tests for ``RunRegistry.delete`` and ``delete_terminal``.

Backs the "Clear failed / finished runs" Runs page affordance:

* ``delete(run_id)`` must remove terminal records, refuse non-terminal
  ones with ValueError, and tolerate unknown ids by returning False.
* ``delete_terminal(statuses)`` must remove only records whose status is
  in the (intersected with the terminal whitelist) status set, return the
  count, and persist the new index.

Log-file cleanup is best-effort — the in-memory removal is the source of
truth for the UI, so an OS-level cleanup failure must not raise.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from logic_loom_api.runs.registry import RunRegistry


def _seed_index(tmp_path: Path, records: list[dict]) -> Path:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True)
    (runs_dir / "index.json").write_text(json.dumps({"runs": records}))
    return runs_dir


def _legacy_run(
    *, id_: str, status: str, log_name: str | None = None
) -> dict:
    log_path = ""
    if log_name:
        # Caller writes the actual file under tmp before calling delete().
        log_path = log_name
    return {
        "id": id_,
        "stage": "paper-card",
        "status": status,
        "started_at": "2026-04-01T00:00:00+00:00",
        "ended_at": "2026-04-01T00:01:00+00:00",
        "exit_code": 0,
        "pid": 12345,
        "log_path": log_path,
        "args": [],
        "cwd": None,
        "last_lines": [],
    }


class TestDeleteRun:
    @pytest.mark.regression
    def test_delete_terminal_record_removes_in_memory_and_persists(self, tmp_path):
        _seed_index(
            tmp_path,
            [
                _legacy_run(id_="a", status="succeeded"),
                _legacy_run(id_="b", status="succeeded"),
            ],
        )
        reg = RunRegistry(tmp_path, zsum_repo=None)
        assert reg.delete("a") is True
        assert reg.get("a") is None
        # The index file was re-persisted; reloading must see only "b".
        reg2 = RunRegistry(tmp_path, zsum_repo=None)
        assert reg2.get("a") is None
        assert reg2.get("b") is not None

    @pytest.mark.regression
    def test_delete_unknown_run_returns_false(self, tmp_path):
        _seed_index(tmp_path, [])
        reg = RunRegistry(tmp_path, zsum_repo=None)
        assert reg.delete("never-existed") is False

    @pytest.mark.regression
    def test_delete_refuses_non_terminal(self, tmp_path):
        # The constructor force-flips pending/running on load to "failed"
        # to handle restart-stranded runs, so we don't get a clean way to
        # hold a non-terminal record from the index. Instead seed via the
        # API surface by mutating the record after a load.
        _seed_index(tmp_path, [_legacy_run(id_="x", status="succeeded")])
        reg = RunRegistry(tmp_path, zsum_repo=None)
        # Mutate to running so the delete-guard fires.
        reg._runs["x"].record.status = "running"
        with pytest.raises(ValueError, match="non-terminal"):
            reg.delete("x")

    @pytest.mark.regression
    def test_delete_terminal_filters_by_status(self, tmp_path):
        _seed_index(
            tmp_path,
            [
                _legacy_run(id_="ok", status="succeeded"),
                _legacy_run(id_="bad", status="failed"),
                _legacy_run(id_="ko", status="cancelled"),
            ],
        )
        reg = RunRegistry(tmp_path, zsum_repo=None)
        # Clear only failures + cancelled, leaving succeeded.
        removed = reg.delete_terminal(statuses=("failed", "cancelled"))
        assert removed == 2
        assert reg.get("ok") is not None
        assert reg.get("bad") is None
        assert reg.get("ko") is None

    @pytest.mark.regression
    def test_delete_terminal_default_clears_all_terminal(self, tmp_path):
        _seed_index(
            tmp_path,
            [
                _legacy_run(id_="a", status="succeeded"),
                _legacy_run(id_="b", status="failed"),
                _legacy_run(id_="c", status="cancelled"),
            ],
        )
        reg = RunRegistry(tmp_path, zsum_repo=None)
        removed = reg.delete_terminal()
        assert removed == 3
        assert reg.list() == []

    @pytest.mark.regression
    def test_delete_cleans_up_log_file(self, tmp_path):
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir(parents=True)
        log_path = runs_dir / "stray.log"
        log_path.write_text("hello", encoding="utf-8")
        (runs_dir / "index.json").write_text(
            json.dumps(
                {
                    "runs": [
                        _legacy_run(
                            id_="a", status="succeeded", log_name=str(log_path)
                        )
                    ]
                }
            )
        )
        reg = RunRegistry(tmp_path, zsum_repo=None)
        assert reg.delete("a") is True
        assert not log_path.exists()
