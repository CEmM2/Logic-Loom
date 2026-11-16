"""Run + log-event data models.

:class:`RunRecord` is extended so the registry can persist
workflow runs alongside the existing zsum run history. The migration
contract:

* The new ``kind`` discriminator defaults to ``"zsum"`` on read. Existing
  index.json files written before the field existed do not carry it, and Pydantic's default
  value mechanism makes the absence transparent — old records round-trip
  unchanged.
* Workflow runs additionally carry ``batch_id`` and ``workflow_id`` so the
  registry can index by the ``(batch_id, workflow_id, run_id)`` triplet
  required by the AKMS dispatch routes and the run-history UI.
* Workflow runs may set ``stage = workflow_id`` for compatibility with the
  existing ``stage`` consumers (the zsum run history and the output store
  read it), or leave it
  blank — the registry derives ``stage`` from ``workflow_id`` if absent.

All other fields stay shape-compatible with the original zsum run schema so a
mid-run upgrade does not invalidate the index.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field

RunStatus = Literal["pending", "running", "succeeded", "failed", "cancelled"]

# Existing zsum records load with default kind="zsum"; new
# workflow runs persist kind="workflow".
RunKind = Literal["zsum", "workflow"]


class LogEvent(BaseModel):
    ts: str
    level: str
    line: str


class RunRecord(BaseModel):
    """Per-run record persisted to ``<cache>/runs/index.json``.

    Backwards compatibility:
      * ``kind`` defaults to ``"zsum"`` so older records on disk round-trip.
      * ``batch_id`` / ``workflow_id`` are ``None`` for legacy zsum records.
      * The Pydantic model uses ``extra="ignore"`` (the default) so older
        clients reading a newer record never raise on unknown fields.
    """

    id: str
    stage: str
    status: RunStatus = "pending"
    started_at: str = Field(default_factory=lambda: dt.datetime.now(dt.UTC).isoformat())
    ended_at: str | None = None
    exit_code: int | None = None
    pid: int | None = None
    log_path: str
    args: list[str] = Field(default_factory=list)
    cwd: str | None = None
    last_lines: list[str] = Field(default_factory=list)

    # ---- Workflow-run fields (backwards-compatible: each has a default) ----
    kind: RunKind = "zsum"
    batch_id: str | None = None
    workflow_id: str | None = None
