"""Write-capable bridge to ``akms-failure-memory`` — operator mode ONLY.

This module is deliberately separate from ``adapters/failure_memory.py``, and
the separation is the whole security argument of the Failure Memory workspace:
the read-only adapter does not hold a write capability switched off — it does
not possess one at all. Every write in the feature lives here, behind the
operator-mode check the route layer makes first. There are no write flags
anywhere: the read-only module passes a literal ``False``, this module passes
a literal ``"write"`` — a literal has no name to rebind.

What this module may and may not do:

* **MUST** call the package's public API: ``record.add_lesson``,
  ``compiler.run_compiler``, ``refresh.refresh_project``. Nothing else writes.
* **MUST NOT** open the canonical registry for writing, append a dictionary to
  it, allocate a lesson id, serialise a lesson, compute a route, or emit a
  generated node — any of that would bypass the package's atomic writer,
  schema validation, id allocation and generated-artifact checks while looking
  exactly like a successful recording.
* **MAY** write exactly one thing: a proposal JSON file, in an OS temporary
  directory outside every configured repository, because ``add_lesson``'s public
  signature takes ``request_path`` (a path) and offers no in-memory form. That
  path is asserted at runtime to be outside the project root before it is used.

Atomicity note worth knowing before designing any UI on top of this:
``add_lesson`` is already a compound-but-atomic package operation. It allocates
the id, validates the candidate registry, atomically replaces it, and then
compiles in write mode — and if the compile raises it restores the previous
registry bytes before re-raising. There is therefore no "recorded but not
compiled" state for a caller to misreport, provided the caller reports success
only from a returned value and never from having started the call.
"""

from __future__ import annotations

import importlib
import json
import logging
import tempfile
from pathlib import Path
from typing import Any

from .failure_memory import FailureMemoryAdapterError, ResolvedProject, _wrap

logger = logging.getLogger(__name__)

_MODULE_NAME = "akms_failure_memory"


def _module(name: str) -> Any:
    try:
        return importlib.import_module(f"{_MODULE_NAME}.{name}")
    except ImportError as exc:  # pragma: no cover - guarded by the route layer
        raise FailureMemoryAdapterError(
            f"Package {_MODULE_NAME} is not installed.", code="failure_memory_absent"
        ) from exc


def record_lesson(
    project: ResolvedProject, proposal: dict[str, Any], *, global_vault: Path
) -> dict[str, Any]:
    """Record one lesson through the package's public recorder.

    ``proposal`` is handed to ``add_lesson`` unchanged. In particular Logic-Loom
    does NOT add an ``id``: ids are allocated by the package under the project
    lock, and the recorder rejects a request that carries one
    (``record_request``). The request model refuses the field too, so a caller
    cannot smuggle one through.

    Returns the package's own result — ``status``, the stored ``record`` with
    its allocated id, ``source_sha256``, and ``affected_outputs``. That last
    list will name EVERY generated node, not just the new one: each node embeds
    the whole-registry digest in its body, so one recording rewrites the whole
    generated tree. That is correct output, not corruption.
    """
    add_lesson = _module("record").add_lesson
    with tempfile.TemporaryDirectory(prefix="logic-loom-failure-memory-") as scratch:
        proposal_path = Path(scratch) / "proposal.json"
        # The single write this module performs, and it lands in an OS temp
        # directory. Asserted rather than assumed: a proposal written inside the
        # target repository would be an untracked artifact of a "read the form,
        # record it" flow, and the whole point here is that Logic-Loom adds no
        # bytes of its own to the project.
        if proposal_path.resolve().is_relative_to(project.root):
            raise FailureMemoryAdapterError(
                "Refusing to stage a proposal inside the target repository",
                code="path_escape",
            )
        proposal_path.write_text(
            json.dumps(proposal, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        try:
            return add_lesson(
                config_path=project.config_path,
                repository_root=project.root,
                global_vault=global_vault,
                request_path=proposal_path,
            )
        except Exception as exc:
            raise _wrap(exc) from exc


def compile_project(project: ResolvedProject, *, global_vault: Path) -> dict[str, Any]:
    """``run_compiler(mode="write")`` — the package publishes, Logic-Loom does not.

    The literal ``"write"`` here is the mirror of the read-only adapter's literal
    ``False``: the capability is expressed at the call site, never held in a
    rebindable module value.
    """
    run_compiler = _module("compiler").run_compiler
    try:
        return run_compiler(
            config_path=project.config_path,
            repository_root=project.root,
            global_vault=global_vault,
            mode="write",
        )
    except Exception as exc:
        raise _wrap(exc) from exc


def refresh_project(
    project: ResolvedProject,
    *,
    action: str,
    global_vault: Path,
    generated_at: str | None,
) -> dict[str, Any]:
    """Run one refresh stage through ``refresh.refresh_project``.

    ``generated_at`` is mandatory for the mirror and graph stages and is
    caller-owned by design — ``refresh._timestamp(None)`` raises
    ``generated_at_required`` rather than defaulting to the wall clock. The
    service derives it from registry content; see
    ``services.failure_memory.derive_generated_at`` for why a wall-clock value
    would make every subsequent freshness check read as stale.

    Expect ``repo2md_*`` failures on an ordinary install: every mutating refresh
    action runs ``refresh.preflight``, which demands a verifiable, pinned, clean,
    EDITABLE repo2md distribution at the project's pinned commit. An install
    from the pinned AKMS git commit alone cannot have one. That is a TOOLCHAIN
    shortfall, not stale knowledge, and the service classifies it as such.
    """
    refresh = _module("refresh").refresh_project
    try:
        return refresh(
            action=action,
            config_path=project.config_path,
            repository_root=project.root,
            global_vault=global_vault,
            generated_at=generated_at,
        )
    except Exception as exc:
        raise _wrap(exc) from exc


__all__ = ["compile_project", "record_lesson", "refresh_project"]
