"""Read-only bridge to the optional ``akms-failure-memory`` package.

Logic-Loom is a **consumer** of ``akms-failure-memory``, never a second
implementation of it. This adapter therefore does four things and nothing more:

1. **Detect** whether the optional package is importable, and emit the frozen
   consumer-contract degradation envelope when it is not.
2. **Resolve** an EXPLICITLY configured project id to its validated
   :class:`akms_failure_memory.config.ProjectConfig` plus repository root.
3. **Read** project-owned data (registry, compiled route index, generated node
   bodies, published graph metadata) through the package's own validators where
   one exists.
4. **Delegate** status, compiler diagnostics and provider resolution to the
   package's public API.

Hard boundaries — all four are asserted mechanically by
``tests/test_failure_memory_readonly_audit.py``:

* **Public API only.** The only ``akms_failure_memory`` names used are
  ``config.load_project_config`` / ``config.ProjectConfig``,
  ``compiler.run_compiler`` / ``compiler.validate_registry_bytes``,
  ``provider.resolve_provider``, ``refresh.status`` and
  ``errors.FailureMemoryError`` — every one of them exported in its module's
  ``__all__``. No private AKMS provider helper is imported, and ``repo2md`` is
  never imported at all.
* **No recorder, no compiler, no registry writer.** Logic-Loom never opens
  ``lessons.json`` for writing, never appends a dictionary to it, and never
  re-derives routes, node ids or fingerprints. Doing so would bypass the atomic
  writer, schema validation, ID allocation and generated-artifact checks the
  package exists to own.
* **No mutation, full stop.** This module contains no filesystem write of any
  kind. ``resolve_provider`` is called with a LITERAL ``False`` for
  ``write_artifacts`` (a literal has no name to rebind — see
  :func:`resolve_preview`), and the compiler is only ever run in its
  non-writing ``validate``/``check`` modes.
* **No filesystem discovery.** A project exists for Logic-Loom only because it
  is listed in ``[[failure_memory_projects]]``. There is no walk, no glob and
  no "look for a ``.failure-memory`` directory near here" fallback.

The one file the package itself creates during a read is its project lock
(``paths.lock``), taken by ``resolve_provider`` and ``run_compiler`` so a
concurrent recorder cannot tear the inputs out from under a read. It is created
with ``O_EXCL`` and removed on release, and is a configured disposable runtime
path — never tracked repository content. See
``test_readonly_endpoints_do_not_mutate_the_repository`` for the demonstration.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ..config import FailureMemoryProject, Settings

if TYPE_CHECKING:  # pragma: no cover - typing only
    from akms_failure_memory.config import ProjectConfig

logger = logging.getLogger(__name__)

#: Import (module) name used for ``find_spec`` / ``import_module``.
_MODULE_NAME = "akms_failure_memory"
#: Distribution name on PyPI / in ``pyproject.toml`` (``importlib.metadata``
#: keys on the *distribution* name, which uses a hyphen, not the import name).
_DISTRIBUTION_NAME = "akms-failure-memory"

#: The consumer-side degradation envelope frozen by the shared conformance pack
#: (``consumer_contract/v1``, ``expected/errors.json#absent_package_degradation``,
#: pack digest ``90ad2801481723e224e7215fffb3a7b1cd661e615bcbaa9c5a62565a40c94eb7``).
#:
#: It exists because ``schemas/provider-result.v1.json`` pins ``status`` to the
#: constant ``"ok"`` — the provider result schema has no legal representation of
#: a failed or degraded resolution, so "the package is not installed" has to be
#: expressed in a separate consumer-owned shape. Logic-Loom emits it VERBATIM
#: rather than wrapping it in the ``{"error": {...}}`` envelope used elsewhere in
#: this service, because the bytes are contract, not house style. Pinned by
#: ``test_absent_package_envelope_matches_the_frozen_contract``.
DEGRADATION_ENVELOPE: dict[str, Any] = {
    "schema_version": "failure-memory-consumer-degradation/v1",
    "status": "unavailable",
    "degraded": True,
    "enforcement": "disabled",
    "reason": {
        "code": "failure_memory_absent",
        "message": "akms_failure_memory is not installed; failure-memory context is unavailable.",
    },
    "records": [],
    "required_node_ids": [],
}


class FailureMemoryAdapterError(RuntimeError):
    """Raised when a configured failure-memory project cannot be read.

    ``code`` mirrors the package's own :class:`FailureMemoryError` codes when the
    failure came from there, so the route layer can forward a stable code into
    the standard Logic-Loom error envelope instead of inventing one per call
    site.
    """

    def __init__(self, message: str, *, code: str = "failure_memory_error") -> None:
        super().__init__(message)
        self.code = code


# ── availability ────────────────────────────────────────────────────────────


def failure_memory_available() -> bool:
    """True when the optional ``akms_failure_memory`` package is importable.

    Uses :func:`importlib.util.find_spec`, which performs no import side
    effects — the same probe the frozen contract names as the detection
    mechanism (``expected/errors.json#absent_package_degradation.detection``).
    """
    try:
        return importlib.util.find_spec(_MODULE_NAME) is not None
    except ModuleNotFoundError:
        # find_spec raises when a *parent* package is missing; treat as absent.
        return False


def package_version() -> str | None:
    try:
        return importlib.metadata.version(_DISTRIBUTION_NAME)
    except importlib.metadata.PackageNotFoundError:
        return None


def degradation_envelope() -> dict[str, Any]:
    """A fresh deep copy of :data:`DEGRADATION_ENVELOPE` (never the shared dict)."""
    return json.loads(json.dumps(DEGRADATION_ENVELOPE))


def _module(name: str) -> Any:
    """Import one ``akms_failure_memory`` submodule, or raise a typed error."""
    try:
        return importlib.import_module(f"{_MODULE_NAME}.{name}")
    except ImportError as exc:  # pragma: no cover - guarded by callers
        raise FailureMemoryAdapterError(
            f"Package {_MODULE_NAME} is not installed.", code="failure_memory_absent"
        ) from exc


def _failure_memory_error() -> type[Exception]:
    return _module("errors").FailureMemoryError


# ── project resolution ──────────────────────────────────────────────────────


@dataclass(slots=True, frozen=True)
class ResolvedProject:
    """One configured project, loaded and validated.

    ``repository_id`` comes from the project's own validated configuration and
    is the ONE authority on repository identity (the package's architecture doc
    is explicit that directory basenames and overlay values never override it).
    Logic-Loom stamps it onto every overlay node and edge so that ``src/config.py``
    in one repository can never merge with ``src/config.py`` in another.
    """

    id: str
    config_path: Path
    root: Path
    config: "ProjectConfig"

    @property
    def repository_id(self) -> str:
        return self.config.repository_id

    @property
    def node_namespace(self) -> str:
        return self.config.node_namespace

    def path(self, key: str) -> Path:
        """Resolve one configured path key under this project's root.

        Delegates to :meth:`ProjectConfig.resolve`, which raises ``path_escape``
        for any configured path that resolves outside the repository root — the
        containment check is the package's, not a Logic-Loom re-implementation.
        """
        try:
            return self.config.resolve(self.root, key)
        except Exception as exc:  # FailureMemoryError
            raise _wrap(exc) from exc


def _wrap(exc: Exception) -> FailureMemoryAdapterError:
    code = getattr(exc, "code", None) or "failure_memory_error"
    return FailureMemoryAdapterError(str(exc), code=str(code))


def load_project(settings: Settings, project_id: str) -> ResolvedProject:
    """Load one EXPLICITLY configured project by id.

    Raises ``unknown_project`` when the id is not in
    ``[[failure_memory_projects]]``. There is deliberately no fallback that goes
    looking for it on disk: an unlisted project is invisible to Logic-Loom.
    """
    entry = settings.failure_memory_project(project_id)
    if entry is None:
        raise FailureMemoryAdapterError(
            f"No failure-memory project is configured with id {project_id!r}.",
            code="unknown_project",
        )
    return load_configured_project(entry)


def load_configured_project(entry: FailureMemoryProject) -> ResolvedProject:
    """Load and validate one configured entry, resolving its repository root."""
    load_project_config = _module("config").load_project_config
    config_path = entry.config_path()
    if not config_path.is_file():
        raise FailureMemoryAdapterError(
            f"Configured failure-memory config does not exist: {config_path}",
            code="config_missing",
        )
    try:
        config = load_project_config(config_path)
    except Exception as exc:  # FailureMemoryError
        raise _wrap(exc) from exc
    root = entry.resolved_root(config.paths.get("registry"))
    if not root.is_dir():
        raise FailureMemoryAdapterError(
            f"Repository root for project {entry.id!r} does not exist: {root}",
            code="root_missing",
        )
    resolved_config = config_path.resolve()
    if not resolved_config.is_relative_to(root.resolve()):
        # The project config must live inside the repository whose paths it
        # anchors. Without this, one entry could point `config` at repo A and
        # `root` at repo B and read A's policy against B's files.
        raise FailureMemoryAdapterError(
            f"Project {entry.id!r} config {resolved_config} is not inside its root {root}.",
            code="path_escape",
        )
    return ResolvedProject(
        id=entry.id, config_path=config_path, root=root.resolve(), config=config
    )


# ── reads ───────────────────────────────────────────────────────────────────


def artifact_status(project: ResolvedProject) -> dict[str, Any]:
    """Package-reported existence + digest of every configured artifact.

    Straight delegation to ``akms_failure_memory.refresh.status`` (exported in
    that module's ``__all__``), which stats and hashes the configured paths and
    takes no lock and writes nothing.
    """
    status = _module("refresh").status
    try:
        return status(project.config, project.root)
    except Exception as exc:
        raise _wrap(exc) from exc


def lock_parent_exists(project: ResolvedProject) -> bool:
    """Whether the project's configured lock parent directory exists.

    Read paths never create it (``create_parent_directories=False``; they
    raise ``lock_parent_missing`` instead). An absent parent means "no write
    operation has ever run here" — an operational fact, not a fault, and
    never evidence about content.
    """
    try:
        return project.path("lock").parent.is_dir()
    except FailureMemoryAdapterError:
        return False


def artifact_presence(project: ResolvedProject) -> dict[str, bool]:
    """Which of the project's canonical and generated artifacts actually exist.

    This — not the presence of a disposable scratch directory — is what tells
    you whether a project has ever been written to: an ordinary fresh clone
    has the committed registry and route index but no ``runtime/`` tree.
    """
    presence: dict[str, bool] = {}
    for key in ("registry", "routes", "graph"):
        try:
            presence[key] = project.path(key).is_file()
        except FailureMemoryAdapterError:
            presence[key] = False
    try:
        generated = project.path("generated_nodes")
        presence["generated_nodes"] = generated.is_dir() and any(
            generated.glob(f"{project.node_namespace}-*.md")
        )
    except FailureMemoryAdapterError:
        presence["generated_nodes"] = False
    return presence


# NOTE: the never-written determination deliberately does NOT live here. It
# lives in ``services.failure_memory_currency.write_history`` — the single
# source of truth every consumer calls. ``artifact_presence`` above is the raw
# evidence that function reads.


def toolchain_preflight(project: ResolvedProject) -> dict[str, Any]:
    """``refresh.preflight`` — identity verification, explicitly non-mutating.

    Public (``refresh.__all__``) and documented as verifying "without mutation";
    it reads digests and runs read-only ``git`` queries in the pinned repo2md
    checkout. It lives in the READ-ONLY adapter on purpose: an operator needs to
    know whether refresh would work BEFORE switching this Logic-Loom into
    operator mode, and asking that question must not require the write-capable
    module.
    """
    preflight = _module("refresh").preflight
    try:
        return preflight(config=project.config, repository_root=project.root)
    except Exception as exc:
        raise _wrap(exc) from exc


def registry_lessons(project: ResolvedProject) -> tuple[dict[str, Any], ...]:
    """Validated canonical lessons, via the package's own registry validator.

    Uses ``compiler.validate_registry_bytes`` rather than a bare ``json.load``
    so Logic-Loom applies the SAME closed-key-set, id-pattern, date, related-id
    and duplicate rules the recorder does — a registry Logic-Loom is willing to
    display is a registry the package considers valid. The function's docstring
    is explicit that it validates "without mutating".
    """
    validate_registry_bytes = _module("compiler").validate_registry_bytes
    registry = project.path("registry")
    if not registry.is_file():
        raise FailureMemoryAdapterError(
            f"Registry does not exist: {registry}", code="registry_missing"
        )
    try:
        return validate_registry_bytes(registry.read_bytes(), registry, project.config)
    except Exception as exc:
        raise _wrap(exc) from exc


def route_index(project: ResolvedProject) -> dict[str, Any] | None:
    """The COMMITTED compiled route index, or None when it has not been compiled.

    Deliberately the committed bytes rather than a fresh in-memory compile: this
    is the index ``resolve_provider`` actually reads, so the coverage the UI
    shows is the coverage the provider will use. Drift between it and the
    registry is what ``POST /check`` reports.
    """
    routes = project.path("routes")
    if not routes.is_file():
        return None
    try:
        data = json.loads(routes.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FailureMemoryAdapterError(
            f"Cannot read route index {routes}: {exc}", code="routes_unreadable"
        ) from exc
    if not isinstance(data, dict):
        raise FailureMemoryAdapterError(
            f"Route index {routes} is not an object", code="routes_unreadable"
        )
    return data


def generated_node_text(project: ResolvedProject, node_id: str) -> str | None:
    """Body of one compiled lesson node, or None when it is not on disk.

    ``node_id`` is validated against the project's own namespace before it is
    used as a filename, so a crafted id cannot escape the generated-nodes
    directory. Note the compiled node body is reached through ``content_ref``,
    never through a provider record's ``source_ref`` — that field is the AKMS v2
    provenance enum (its value is the literal string ``"human"``), not a path.
    """
    if not node_id.startswith(f"{project.node_namespace}-") or "/" in node_id or "\\" in node_id:
        raise FailureMemoryAdapterError(
            f"Node id {node_id!r} is not in project namespace {project.node_namespace!r}",
            code="unknown_node",
        )
    candidate = project.path("generated_nodes") / f"{node_id}.md"
    resolved = candidate.resolve()
    if not resolved.is_relative_to(project.root):
        raise FailureMemoryAdapterError(
            "Generated node path escapes the repository root", code="path_escape"
        )
    if not resolved.is_file():
        return None
    return resolved.read_text(encoding="utf-8")


def graph_metadata(project: ResolvedProject) -> dict[str, Any] | None:
    """``generated_at`` / ``repo_id`` / counts from the published project graph.

    Only the metadata header is surfaced. The graph is a disposable generated
    artifact in both consumer configs, so its absence is a normal state, not an
    error — it just means "refresh has not run here yet".
    """
    graph = project.path("graph")
    if not graph.is_file():
        return None
    try:
        data = json.loads(graph.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FailureMemoryAdapterError(
            f"Cannot read graph {graph}: {exc}", code="graph_unreadable"
        ) from exc
    if not isinstance(data, dict):
        return None
    # AKMS publishes the graph in networkx node-link form: the metadata header
    # lives under "graph", nodes under "nodes", and edges under "links" (older
    # payloads used "edges"). Reading the top level directly would silently
    # report None for every field and make a correctly published graph look
    # unidentified.
    metadata = data.get("graph")
    metadata = metadata if isinstance(metadata, dict) else {}
    nodes = data.get("nodes")
    edges = data.get("links") if isinstance(data.get("links"), list) else data.get("edges")
    return {
        "generated_at": metadata.get("generated_at"),
        "repo_id": metadata.get("repo_id"),
        "global_vault": metadata.get("global_vault"),
        "node_count": metadata.get("node_count")
        if isinstance(metadata.get("node_count"), int)
        else (len(nodes) if isinstance(nodes, list) else None),
        "edge_count": metadata.get("edge_count")
        if isinstance(metadata.get("edge_count"), int)
        else (len(edges) if isinstance(edges, list) else None),
    }


# ── delegated operations (all non-writing package modes) ────────────────────


def resolve_preview(
    project: ResolvedProject, request: dict[str, Any]
) -> dict[str, Any]:
    """Run one provider request as a PREVIEW.

    ``write_artifacts`` is the LITERAL ``False`` below — deliberately not a
    module constant, default argument, settings field or parameter. A literal
    has no name to rebind, so no process state can put this call on the
    writing path; keep it a literal. Operator publication lives in the
    separate ``adapters/failure_memory_operator.py``, whose call site passes a
    literal ``"write"`` behind the operator-guarded routes, so this module
    structurally lacks the capability rather than holding it switched off.

    The package computes the identical result either way — same ``records``,
    same fingerprints. The frozen conformance pack pins exactly this: the only
    fields that differ from a writing invocation are the three ``artifacts``
    paths (null here) and the two absolute resolution paths (absent here).
    """
    resolve_provider = _module("provider").resolve_provider
    try:
        result = resolve_provider(
            config_path=project.config_path,
            repository_root=project.root,
            request_source=request,
            write_artifacts=False,
        )
    except Exception as exc:
        raise _wrap(exc) from exc
    _reject_published_artifacts(result)
    return result


def _reject_published_artifacts(result: Mapping[str, Any]) -> None:
    """Fail loudly if a preview ever comes back having published something.

    Detection, not prevention — by the time this runs the write would already
    have happened. It exists because a silent read-only violation is far worse
    than a noisy one: an operator who is told the surface cannot write, and is
    never contradicted, has no reason to look. If this ever fires, the invariant
    is broken somewhere below this adapter and the answer must not be served.
    """
    artifacts = result.get("artifacts") or {}
    published = {
        key: value for key, value in artifacts.items() if value not in (None, "")
    }
    if published:
        raise FailureMemoryAdapterError(
            "Read-only preview published provider artifacts: "
            + ", ".join(f"{key}={value!r}" for key, value in sorted(published.items())),
            code="readonly_violation",
        )


@contextmanager
def bounded_project_lock(project: ResolvedProject, *, timeout_seconds: float):
    """Hold the package's own project lock with a SHORT, caller-chosen bound.

    ``run_compiler`` acquires the project lock with the project's configured
    ``toolchain.timeout_seconds`` — 120 seconds in the shipped configuration —
    and exposes no override. A read endpoint that consults compiler
    diagnostics must not inherit that: under contention it would block the
    read for the full write timeout. ``ProjectLock`` is public
    (``locks.__all__``) and re-entrant per thread at the pinned commit, so
    holding it here with a small timeout makes the compiler's own inner
    acquisition on the same thread immediate, while contention surfaces as a
    typed ``lock_contention`` within *timeout_seconds* instead of two minutes.

    ``create_parent_directories=False`` always: this is a read-side
    acquisition and must never create structure in the target repository —
    an absent parent raises the same typed ``lock_parent_missing`` every
    other read path raises.
    """
    ProjectLock = _module("locks").ProjectLock
    try:
        lock = ProjectLock(
            project.path("lock"),
            timeout_seconds=timeout_seconds,
            create_parent_directories=False,
        )
        lock.acquire()
    except FailureMemoryAdapterError:
        raise
    except Exception as exc:  # FailureMemoryError
        raise _wrap(exc) from exc
    try:
        yield
    finally:
        lock.release()


def publication_problems(project: ResolvedProject) -> list[str]:
    """``provider.validate_publication`` — published-graph consistency.

    The SINGLE definition of "do the published graph and the published
    generated node files agree, under this project's identity?" lives in the
    package (public, ``provider.__all__``), precisely so consumers do not
    mirror the predicate and drift. Lock-free, deterministic, reads only the
    two published artifacts. Returns the package's own problem strings; empty
    means consistent.
    """
    validate_publication = _module("provider").validate_publication
    try:
        return [
            str(problem)
            for problem in validate_publication(
                config_path=project.config_path, repository_root=project.root
            )
        ]
    except Exception as exc:
        raise _wrap(exc) from exc


def validate_registry(project: ResolvedProject) -> dict[str, Any]:
    """``run_compiler(mode="validate")`` — validates, publishes nothing.

    ``validate`` returns before the compiler touches any output path at all
    (it does not even need a global vault), so it is the cheapest honest answer
    to "is this registry well-formed?".
    """
    run_compiler = _module("compiler").run_compiler
    try:
        return run_compiler(
            config_path=project.config_path,
            repository_root=project.root,
            mode="validate",
        )
    except Exception as exc:
        raise _wrap(exc) from exc


def check_generated(project: ResolvedProject, *, global_vault: Path) -> dict[str, Any]:
    """``run_compiler(mode="check")`` — compares committed bytes, writes nothing.

    ``check`` recompiles in memory and diffs the result against the committed
    generated nodes and route index, reporting ``added`` / ``changed`` /
    ``stale``. Publication happens only under ``mode="write"``, which this
    module never uses.

    Expect a WHOLE-TREE diff whenever the registry has moved: every generated
    node embeds the whole-registry ``source_sha256`` in its body, so recording
    one lesson rewrites every node file. ``changed == 12`` after adding one
    lesson to a twelve-lesson registry is the normal, correct result, not a
    sign that something went wrong.

    ``global_vault`` is passed explicitly and is READ-ONLY: the compiler only
    globs it for node-id collisions.
    """
    run_compiler = _module("compiler").run_compiler
    try:
        return run_compiler(
            config_path=project.config_path,
            repository_root=project.root,
            global_vault=global_vault,
            mode="check",
        )
    except Exception as exc:
        raise _wrap(exc) from exc


__all__ = [
    "DEGRADATION_ENVELOPE",
    "bounded_project_lock",
    "FailureMemoryAdapterError",
    "ResolvedProject",
    "artifact_status",
    "check_generated",
    "degradation_envelope",
    "failure_memory_available",
    "generated_node_text",
    "graph_metadata",
    "load_configured_project",
    "artifact_presence",
    "load_project",
    "lock_parent_exists",
    "package_version",
    "publication_problems",
    "registry_lessons",
    "resolve_preview",
    "route_index",
    "toolchain_preflight",
    "validate_registry",
]
