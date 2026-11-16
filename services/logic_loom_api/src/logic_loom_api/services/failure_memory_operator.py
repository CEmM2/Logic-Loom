"""Operator-mode service: write diagnostics, generated_at derivation, job wiring.

Sits between ``routes/failure_memory.py`` and
``adapters/failure_memory_operator.py``. It owns three things the adapter
deliberately does not: when a write is possible at all
(:func:`build_write_diagnostics`, answered before an operator fills in a form),
what ``generated_at`` should be (content-derived, never wall clock), and how a
job reports what happened — including saying out loud that a full-tree rewrite
after one recorded lesson is correct. It performs no writes of its own and
imports nothing that can write except the operator adapter.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..adapters import failure_memory as adapter
from ..adapters import failure_memory_operator as writer
from ..adapters.failure_memory import FailureMemoryAdapterError, ResolvedProject
from ..config import Settings
from ..models.failure_memory import (
    RecordRequest,
    RefreshRequest,
    ToolchainRequirement,
    WriteDiagnostics,
)
from . import failure_memory_currency as currency
from .failure_memory import derive_generated_at
from .failure_memory_jobs import Job, get_job_registry

logger = logging.getLogger(__name__)

#: Package error codes that mean "this install lacks a pinned tool", as opposed
#: to "the knowledge moved". Every mutating refresh action runs
#: ``refresh.preflight``. With ``[toolchain].mirror_provider = "repo2md"`` (the
#: default for configs written before AKMS 0.4) that demands a verifiable,
#: pinned, clean, EDITABLE repo2md distribution at the project's pinned commit.
#: With ``"builtin"`` it only checks the exporter shipped in akms
#: (``python -m akms.repo_wiki``); a failure there still carries a
#: ``repo2md_contract`` code, so the prefix covers both providers.
#:
#: The distinction is the whole point. ``unavailable`` means FIX THE TOOLCHAIN;
#: ``stale`` means THE KNOWLEDGE MOVED. A gate that reports a permanent toolchain
#: shortfall as staleness is indistinguishable from one that is simply broken,
#: and an operator learns to ignore it. The WWW stream classifies the identical
#: condition the same way, so the two surfaces cannot disagree about it.
TOOLCHAIN_UNAVAILABLE_PREFIXES = ("repo2md_",)
TOOLCHAIN_UNAVAILABLE_CODES = frozenset({"akms_contract"})

def is_toolchain_unavailable(code: str) -> bool:
    return code.startswith(TOOLCHAIN_UNAVAILABLE_PREFIXES) or code in TOOLCHAIN_UNAVAILABLE_CODES


def _mirror_provider(project: ResolvedProject) -> str:
    """``[toolchain].mirror_provider``, as akms-failure-memory reads it.

    0.4 exposes ``ProjectConfig.mirror_provider``; the toolchain-table fallback
    keeps the same default (``repo2md``) for a config without the key.
    """
    value = getattr(project.config, "mirror_provider", None)
    if not isinstance(value, str):
        value = str(project.config.toolchain.get("mirror_provider", "repo2md"))
    return "builtin" if value == "builtin" else "repo2md"


def _builtin_remediation() -> list[str]:
    return [
        "The builtin exporter runs as `python -m akms.repo_wiki` with the sidecar's own "
        "interpreter. Reinstall the failure-memory extra so akms and akms-failure-memory "
        "are the same 0.4.x release: uv sync --all-packages --extra failure-memory.",
        "No repo2md checkout is needed for this project; the repo2md_* toolchain keys "
        "are ignored while mirror_provider = \"builtin\".",
        "Until then: record and compile still work, and staleness is still detected — "
        "only republishing the canonical graph is blocked.",
    ]


def _registry_digest(project: ResolvedProject) -> str | None:
    """``routes.json``'s ``source_hash`` — the digest the route index was built from.

    Read from the compiled index rather than recomputed, so Logic-Loom derives
    from the same recorded value a consumer of the index would.
    """
    routes = adapter.route_index(project)
    recorded = (routes or {}).get("source_hash")
    return str(recorded) if recorded else None


def build_write_diagnostics(
    settings: Settings, project: ResolvedProject
) -> WriteDiagnostics:
    """Answer "can this Logic-Loom write here, and if not, what must be fixed?"."""
    operator = settings.failure_memory_operator()
    global_vault = settings.effective_akms_global_vault().expanduser()
    vault_exists = global_vault.is_dir()
    lock_path = project.path("lock")
    # Operational fact, reported as such — never the never-written verdict,
    # which comes only from the currency owner below.
    lock_parent = adapter.lock_parent_exists(project)
    history = currency.write_history(project)

    blockers: list[str] = []
    if not operator:
        blockers.append(
            "failure_memory_mode is not 'operator'; Logic-Loom is read-only and cannot write."
        )
    if not vault_exists:
        blockers.append(
            f"The configured read-only global vault does not exist: {global_vault}. "
            "The recorder and compiler need it for node-id collision checking."
        )

    digest = None
    derived = None
    try:
        digest = _registry_digest(project)
        derived = derive_generated_at(digest) if digest else None
    except FailureMemoryAdapterError as exc:
        logger.info("generated_at derivation unavailable: %s", exc)

    requirements: list[ToolchainRequirement] = []
    classification = "available"
    reason = "Toolchain preflight passed; refresh actions can run."
    remediation: list[str] = []
    mirror_provider = _mirror_provider(project)
    builtin = mirror_provider == "builtin"
    required_commit = "" if builtin else str(project.config.toolchain.get("repo2md_commit", ""))
    required_version = "" if builtin else str(project.config.toolchain.get("repo2md_version", ""))
    command = (
        []
        if builtin
        else [str(part) for part in (project.config.toolchain.get("repo2md_command") or [])]
    )
    try:
        identity = adapter.toolchain_preflight(project)
    except FailureMemoryAdapterError as exc:
        satisfied = False
        requirements.append(
            ToolchainRequirement(
                name=exc.code,
                satisfied=False,
                detail=str(exc),
            )
        )
        classification = "unavailable"
        if is_toolchain_unavailable(exc.code) and builtin:
            reason = (
                "Refresh is UNAVAILABLE, not stale. This project uses the builtin mirror "
                "exporter shipped in akms (mirror_provider = \"builtin\"), and the sidecar "
                "could not run it with the export contract refresh needs. This means FIX "
                "THE TOOLCHAIN (the installed akms); it does NOT mean the recorded knowledge "
                "has moved. Staleness DETECTION is unaffected and still works. "
                f"Preflight reported: {exc}"
            )
        elif is_toolchain_unavailable(exc.code):
            reason = (
                "Refresh is UNAVAILABLE, not stale. The mutating refresh actions require a "
                "repo2md checkout, which is a documented DEPLOYMENT PREREQUISITE — neither "
                "Logic-Loom nor WhichWhereWhatter vendors one, because failure memory is an "
                "opt-in extra. This means FIX THE TOOLCHAIN; it does NOT mean the recorded "
                "knowledge has moved. Staleness DETECTION is unaffected and still works: it "
                "reads the registry digest and the compiled route-index hash, neither of "
                f"which involves repo2md. Preflight reported: {exc}"
            )
        else:
            reason = f"Toolchain preflight failed ({exc.code}): {exc}"
        blockers.append(reason)
        remediation = _builtin_remediation() if builtin else [
            f"Install repo2md from its pinned checkout at commit {required_commit} "
            f"(version {required_version}) as an EDITABLE distribution — preflight ties the "
            "executable to a verifiable source checkout, so a plain wheel will not satisfy it.",
            f"Make {' '.join(command) or 'the repo2md command'} resolvable on the sidecar's PATH.",
            "Keep that checkout clean; the project pins repo2md_dirty_policy = "
            f"{project.config.toolchain.get('repo2md_dirty_policy', 'require-clean')!r}.",
            "Set AKMS_REPO2MD_ROOT to that checkout if it cannot be inferred from the "
            "editable-install metadata.",
            "Until then: record and compile still work, and staleness is still detected — "
            "only republishing the canonical graph is blocked.",
        ]
    else:
        satisfied = True
        for key in ("akms", "mirror", "repo2md"):
            value = identity.get(key)
            if value is not None:
                requirements.append(
                    ToolchainRequirement(
                        name=key, satisfied=True, detail=str(value)
                    )
                )

    return WriteDiagnostics(
        project_id=project.id,
        repository_id=project.repository_id,
        mode=settings.failure_memory_mode,
        operator=operator,
        package_installed=adapter.failure_memory_available(),
        # `record` and `compile` do NOT run the toolchain preflight; only refresh
        # does. Reporting them as blocked by a repo2md shortfall would be wrong
        # and would stop an operator recording a lesson they can perfectly well
        # record.
        can_record=operator and vault_exists,
        can_compile=operator and vault_exists,
        can_refresh=operator and vault_exists and satisfied,
        never_written=history.never_written,
        global_vault=str(global_vault),
        global_vault_exists=vault_exists,
        lock_path=str(lock_path),
        lock_parent_exists=lock_parent,
        generated_at_source=digest or "",
        derived_generated_at=derived,
        refresh_classification=classification,  # type: ignore[arg-type]
        refresh_reason=reason,
        mirror_provider=mirror_provider,  # type: ignore[arg-type]
        repo2md_required_commit=required_commit,
        repo2md_required_version=required_version,
        repo2md_command=command,
        refresh_remediation=remediation,
        # Never conditional. Detecting that the registry moved reads the registry
        # digest and the route-index hash; repo2md is nowhere in that path. An
        # unavailable refresh must never be readable as an inability to notice.
        detection_still_works=True,
        toolchain=requirements,
        blockers=blockers,
    )


# ── job submission ──────────────────────────────────────────────────────────


def _full_tree_note(result: dict[str, Any]) -> str:
    affected = result.get("affected_outputs") or result.get("written") or []
    if len(affected) <= 1:
        return ""
    return (
        f" {len(affected)} generated file(s) were rewritten — every generated node embeds "
        "the whole-registry digest, so one recorded lesson rewrites the entire generated "
        "tree. A full-tree diff here is correct output, not corruption."
    )


def submit_record(
    settings: Settings, project: ResolvedProject, payload: RecordRequest
) -> Job:
    """Queue one recording. Success is reported only from the package's return."""
    global_vault = settings.effective_akms_global_vault().expanduser()
    proposal = payload.to_proposal()

    def work() -> dict[str, Any]:
        return writer.record_lesson(project, proposal, global_vault=global_vault)

    def summarize(result: dict[str, Any]) -> str:
        record = result.get("record") or {}
        return (
            f"Recorded {record.get('id', '?')} for {payload.location.file}; the package "
            f"allocated the id and compiled the generated tree.{_full_tree_note(result)}"
        )

    return get_job_registry(settings).submit(
        project_id=project.id,
        action="record",
        detail=payload.location.file,
        work=work,
        summarize=summarize,
    )


def submit_compile(settings: Settings, project: ResolvedProject) -> Job:
    global_vault = settings.effective_akms_global_vault().expanduser()

    def work() -> dict[str, Any]:
        return writer.compile_project(project, global_vault=global_vault)

    def summarize(result: dict[str, Any]) -> str:
        counts = result.get("counts") or {}
        return (
            f"Compiler {result.get('status')}: {counts.get('added', 0)} added, "
            f"{counts.get('changed', 0)} changed, {counts.get('stale', 0)} stale, "
            f"{counts.get('warnings', 0)} warning(s).{_full_tree_note(result)}"
        )

    return get_job_registry(settings).submit(
        project_id=project.id, action="compile", detail="write", work=work, summarize=summarize
    )


def submit_refresh(
    settings: Settings, project: ResolvedProject, payload: RefreshRequest
) -> Job:
    """Queue one refresh stage.

    ``generated_at`` is resolved HERE, on the request thread, so a derivation
    failure is a 4xx the operator sees immediately rather than a job that fails
    a second later for a reason unrelated to the refresh itself.
    """
    global_vault = settings.effective_akms_global_vault().expanduser()
    generated_at = payload.generated_at
    if generated_at is None and payload.action in {"lessons", "mirror", "graph", "all"}:
        digest = _registry_digest(project)
        if digest is None:
            raise FailureMemoryAdapterError(
                "Cannot derive generated_at: this project has no compiled route index yet, "
                "so there is no recorded registry digest to derive from. Compile first, or "
                "pass an explicit timezone-aware generated_at.",
                code="generated_at_underivable",
            )
        generated_at = derive_generated_at(digest)

    action = payload.action

    def work() -> dict[str, Any]:
        return writer.refresh_project(
            project, action=action, global_vault=global_vault, generated_at=generated_at
        )

    def summarize(result: dict[str, Any]) -> str:
        stages = ", ".join(sorted((result.get("stages") or {}).keys())) or action
        return (
            f"Refresh '{action}' completed ({stages})."
            + (f" generated_at={generated_at}." if generated_at else "")
            + _full_tree_note((result.get("stages") or {}).get("lessons") or {})
        )

    return get_job_registry(settings).submit(
        project_id=project.id,
        action="refresh",
        detail=action,
        work=work,
        summarize=summarize,
    )


__all__ = [
    "build_write_diagnostics",
    "is_toolchain_unavailable",
    "submit_compile",
    "submit_record",
    "submit_refresh",
]
