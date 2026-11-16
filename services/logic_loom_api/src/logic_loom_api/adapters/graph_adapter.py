"""Helpers for compiling and loading AKMS graphs into LogicLoom-owned cache."""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
import shutil
import tomllib
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Literal

import frontmatter  # type: ignore[import-not-found,import-untyped]

from ..config import Settings

logger = logging.getLogger(__name__)

Direction = Literal["in", "out", "both"]
GraphSourceKind = Literal[
    "global-vault", "local-nodes", "code-mirror", "established-library"
]

# The shipped established node library lives under
# ``logic_loom_api/resources/akms_nodes/established/``. It is a GITIGNORED build
# artifact produced by ``scripts/sync_akms_nodes.py`` — absent on a fresh
# checkout and in dev sidecars built without the sync step — so every consumer
# MUST tolerate it not existing (degrade to an empty source). See
# :func:`established_library_root`.


class GraphAdapterError(RuntimeError):
    """Raised when AKMS graph support is unavailable or misconfigured."""


class GraphSourceIssuesError(GraphAdapterError):
    """Raised when graph source files need user review before generation."""

    def __init__(self, report: "GraphIssueLog") -> None:
        super().__init__(
            f"Graph source review required: {report.issue_count} file(s) need attention before generation."
        )
        self.report = report


@dataclass(slots=True)
class CachedGraph:
    graph: Any
    meta: dict[str, Any]
    cache_path: Path
    built_now: bool = False


@dataclass(slots=True)
class GraphBuildProgress:
    stage: str
    message: str
    current: int
    total: int

    @property
    def percent(self) -> int:
        if self.total <= 0:
            return 0
        return int(round((self.current / self.total) * 100))

    def to_payload(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "message": self.message,
            "current": self.current,
            "total": self.total,
            "percent": self.percent,
        }


@dataclass(slots=True)
class GraphSourceIssue:
    path: Path
    relative_path: str
    source_kind: GraphSourceKind
    reason: str
    error_type: str
    ignored: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "relative_path": self.relative_path,
            "source_kind": self.source_kind,
            "reason": self.reason,
            "error_type": self.error_type,
            "ignored": self.ignored,
        }


@dataclass(slots=True)
class GraphIssueLog:
    generated_at: str | None
    log_path: Path
    total_markdown_files: int
    valid_markdown_files: int
    issues: list[GraphSourceIssue]
    valid_files_by_source: dict[GraphSourceKind, list[Path]] = field(
        default_factory=lambda: {
            "global-vault": [],
            "local-nodes": [],
            "code-mirror": [],
            "established-library": [],
        },
        repr=False,
    )

    @property
    def issue_count(self) -> int:
        return sum(1 for issue in self.issues if not issue.ignored)

    @property
    def ignored_issue_count(self) -> int:
        return sum(1 for issue in self.issues if issue.ignored)

    @property
    def total_issue_count(self) -> int:
        return len(self.issues)

    def to_payload(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "log_path": str(self.log_path),
            "issue_count": self.issue_count,
            "ignored_issue_count": self.ignored_issue_count,
            "total_issue_count": self.total_issue_count,
            "total_markdown_files": self.total_markdown_files,
            "valid_markdown_files": self.valid_markdown_files,
            "issues": [issue.to_payload() for issue in self.issues],
        }


GraphProgressCallback = Callable[[GraphBuildProgress], None]


def graph_package_installed() -> bool:
    try:
        importlib.import_module("akms")
        return True
    except ImportError:
        return False


def graph_readiness(settings: Settings) -> dict[str, Any]:
    """Graph availability. The AKMS checkout is optional: it only contributes
    its ``knowledge/local-nodes`` and ``knowledge/code-mirror`` trees, so the
    graph is available with the package and a source vault alone."""
    repo = settings.akms_repo_dir()
    global_vault = settings.effective_akms_global_vault().expanduser().resolve()
    installed = graph_package_installed()
    return {
        "graph_package_installed": installed,
        "repo_exists": repo is not None,
        "global_vault_exists": global_vault.is_dir(),
        "graph_available": installed and global_vault.is_dir(),
        "akms_repo": str(repo.resolve()) if repo is not None else None,
        "akms_global_vault": str(global_vault),
    }


def repo_graph_path(settings: Settings) -> Path | None:
    """The checkout's own ``knowledge/graph/graph.json`` (None without one)."""
    repo = settings.akms_repo_dir()
    if repo is None:
        return None
    return repo / "knowledge" / "graph" / "graph.json"


def graph_cache_path(settings: Settings) -> Path:
    return settings.graph_cache_path().expanduser()


def graph_source_key(settings: Settings) -> str:
    """Deterministic fingerprint of ALL graph source roots.

    Keys the graph cache on every source root the compile reads — the user
    vault, the repo ``local-nodes`` / ``code-mirror`` trees, AND the shipped
    established library. Each markdown file contributes its resolved path, size,
    and mtime-ns, so ANY change to the shipped library (add / edit / remove a
    node) changes the key and invalidates the cache. A missing established
    library (gitignored build artifact) simply contributes nothing — the key
    stays well-defined and stable, so its absence never wedges the cache.

    P4F-4 (F4): the established library's ``PROVENANCE.toml`` is ALSO a cache
    input — ``build_cached_graph`` stamps ``verified_by``/``verified_at``/
    ``version``/``source_ref`` from it into ``graph.json``. Without folding it in,
    a release that updates ONLY the manifest (e.g. a re-verification bump) leaves
    the key unchanged, so ``auto_build`` serves stale provenance until a manual
    rebuild. It contributes the same path/size/mtime-ns fingerprint as a node.
    """
    repo_root = _ensure_repo_root(settings)
    global_vault = settings.effective_akms_global_vault().expanduser().resolve()
    hasher = hashlib.sha256()

    def _fingerprint(source_kind: str, file_path: Path) -> None:
        try:
            stat = file_path.stat()
        except OSError:
            return
        hasher.update(source_kind.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(str(file_path.resolve()).encode("utf-8"))
        hasher.update(f"\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode("utf-8"))

    for source_kind, source_root, _kwargs in _graph_source_specs(repo_root, global_vault):
        if not source_root.exists():
            continue
        for md_path in sorted(source_root.glob("**/*.md")):
            _fingerprint(source_kind, md_path)
        # The established library also ships a PROVENANCE.toml manifest that is
        # stamped into the cache — fold it in so a manifest-only change is seen.
        if source_kind == "established-library":
            manifest = source_root / "PROVENANCE.toml"
            if manifest.is_file():
                _fingerprint("established-provenance", manifest)
    return hasher.hexdigest()


def graph_cache_is_stale(settings: Settings) -> bool:
    """True when the cache is missing or its recorded source key no longer matches.

    A shipped-library change (or any source change) flips the recorded
    ``source_key``, so the cache is stale and must be rebuilt to reflect it.
    """
    cache_path = graph_cache_path(settings)
    if not cache_path.is_file():
        return True
    meta = _read_graph_json_meta(cache_path)
    recorded = meta.get("source_key")
    if not isinstance(recorded, str):
        return True
    return recorded != graph_source_key(settings)


def graph_issue_log_path(settings: Settings) -> Path:
    return graph_cache_path(settings).parent / "source_issues.json"


def graph_issue_ignore_path(settings: Settings) -> Path:
    return graph_cache_path(settings).parent / "ignored_source_issues.json"


def read_graph_status(settings: Settings) -> dict[str, Any]:
    cache_path = graph_cache_path(settings)
    meta = _read_graph_json_meta(cache_path)
    return {
        "cache_exists": cache_path.is_file(),
        "cache_path": str(cache_path),
        "repo_graph_path": _opt_path_str(repo_graph_path(settings)),
        "generated_at": meta.get("generated_at"),
        "node_count": int(meta.get("node_count") or 0),
        "edge_count": int(meta.get("edge_count") or 0),
        "repo_id": meta.get("repo_id"),
        "global_vault": meta.get("global_vault") or str(settings.effective_akms_global_vault()),
    }


def read_graph_issue_log(settings: Settings) -> dict[str, Any]:
    log_path = graph_issue_log_path(settings)
    if not log_path.is_file():
        return {
            "generated_at": None,
            "log_path": str(log_path),
            "issue_count": 0,
            "ignored_issue_count": 0,
            "total_issue_count": 0,
            "total_markdown_files": 0,
            "valid_markdown_files": 0,
            "issues": [],
        }

    try:
        with log_path.open() as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("failed to read graph issue log %s: %s", log_path, e)
        return {
            "generated_at": None,
            "log_path": str(log_path),
            "issue_count": 0,
            "ignored_issue_count": 0,
            "total_issue_count": 0,
            "total_markdown_files": 0,
            "valid_markdown_files": 0,
            "issues": [],
        }

    return {
        "generated_at": data.get("generated_at"),
        "log_path": str(log_path),
        "issue_count": int(data.get("issue_count") or 0),
        "ignored_issue_count": int(data.get("ignored_issue_count") or 0),
        "total_issue_count": int(data.get("total_issue_count") or 0),
        "total_markdown_files": int(data.get("total_markdown_files") or 0),
        "valid_markdown_files": int(data.get("valid_markdown_files") or 0),
        "issues": list(data.get("issues") or []),
    }


def set_graph_issue_ignored(settings: Settings, path: str | Path, *, ignored: bool) -> GraphIssueLog:
    ignore_path = graph_issue_ignore_path(settings)
    ignored_paths = _read_graph_issue_ignored_paths(settings)
    normalized_path = _normalize_graph_issue_path(path)

    if ignored:
        ignored_paths.add(normalized_path)
    else:
        ignored_paths.discard(normalized_path)

    _write_graph_issue_ignored_paths(ignore_path, ignored_paths)
    return scan_graph_sources(settings)


def scan_graph_sources(
    settings: Settings,
    *,
    repo_root: Path | None = None,
    global_vault: Path | None = None,
) -> GraphIssueLog:
    _load_akms_graph_api()
    validators = _load_akms_schema_validators()

    repo_root = repo_root or _ensure_repo_root(settings)
    global_vault = global_vault or _ensure_global_vault(settings)
    log_path = graph_issue_log_path(settings)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    ignored_paths = _read_graph_issue_ignored_paths(settings)

    issues: list[GraphSourceIssue] = []
    valid_files_by_source: dict[GraphSourceKind, list[Path]] = {
        "global-vault": [],
        "local-nodes": [],
        "code-mirror": [],
        "established-library": [],
    }
    total_markdown_files = 0

    for source_kind, source_root, parser_kwargs in _graph_source_specs(repo_root, global_vault):
        if not source_root.exists():
            continue
        for md_path in sorted(source_root.glob("**/*.md")):
            total_markdown_files += 1
            try:
                metadata = dict(frontmatter.load(str(md_path)).metadata)
            except Exception as e:
                issues.append(_graph_source_issue(source_kind, source_root, md_path, e, ignored_paths))
                continue

            try:
                validators.parse_node_frontmatter_from_dict(
                    metadata,
                    path=str(md_path),
                    **parser_kwargs,
                )
            except Exception as e:
                issues.append(_graph_source_issue(source_kind, source_root, md_path, e, ignored_paths))
                continue

            valid_files_by_source[source_kind].append(md_path)

    report = GraphIssueLog(
        generated_at=datetime.now(UTC).isoformat(),
        log_path=log_path,
        total_markdown_files=total_markdown_files,
        valid_markdown_files=sum(len(paths) for paths in valid_files_by_source.values()),
        issues=issues,
        valid_files_by_source=valid_files_by_source,
    )
    _write_graph_issue_log(report)
    return report


def build_cached_graph(
    settings: Settings,
    *,
    progress_cb: GraphProgressCallback | None = None,
    skip_invalid_files: bool = False,
) -> CachedGraph:
    total_steps = 8

    _emit_graph_progress(
        progress_cb,
        stage="checking-dependencies",
        message="Checking AKMS graph dependencies…",
        current=1,
        total=total_steps,
    )
    api = _load_akms_graph_api()

    _emit_graph_progress(
        progress_cb,
        stage="resolving-paths",
        message="Resolving AKMS repo and graph source vault…",
        current=2,
        total=total_steps,
    )
    repo_root = _ensure_repo_root(settings)
    global_vault = _ensure_global_vault(settings)
    cache_path = graph_cache_path(settings)
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    _emit_graph_progress(
        progress_cb,
        stage="scanning-sources",
        message="Scanning graph source files for schema issues…",
        current=3,
        total=total_steps,
    )
    issue_report = scan_graph_sources(settings, repo_root=repo_root, global_vault=global_vault)
    if issue_report.issue_count and not skip_invalid_files:
        raise GraphSourceIssuesError(issue_report)

    staged_tempdir: TemporaryDirectory[str] | None = None
    build_repo_root = repo_root
    build_global_vault = global_vault

    # Stage whenever there are issues to filter OUT, or established-library
    # files to union IN. The AKMS compiler only reads global-vault + repo
    # knowledge/ roots, so the shipped library is merged by copying its valid
    # nodes into the staged global vault (read-only: originals are never
    # touched). When there are no issues, valid_files_by_source holds every
    # source file, so staging reproduces the real sources exactly.
    established_files = issue_report.valid_files_by_source.get("established-library", [])
    if issue_report.total_issue_count > 0 or established_files:
        _emit_graph_progress(
            progress_cb,
            stage="preparing-filtered-sources",
            message=_staging_message(issue_report),
            current=4,
            total=total_steps,
        )
        staged_tempdir, build_repo_root, build_global_vault = _stage_filtered_graph_inputs(
            issue_report,
            repo_root=repo_root,
            global_vault=global_vault,
            cache_path=cache_path,
        )
    else:
        _emit_graph_progress(
            progress_cb,
            stage="preparing-filtered-sources",
            message="No graph source issues detected; using the configured AKMS sources as-is…",
            current=4,
            total=total_steps,
        )

    try:
        _emit_graph_progress(
            progress_cb,
            stage="building-graph",
            message="Scanning vault and compiling the AKMS graph…",
            current=5,
            total=total_steps,
        )
        graph = api.build_graph(
            str(build_repo_root),
            global_vault=str(build_global_vault),
            output_path=str(cache_path),
        )
    finally:
        if staged_tempdir is not None:
            staged_tempdir.cleanup()

    if build_global_vault != global_vault:
        _rewrite_graph_cache_global_vault(cache_path, global_vault)

    _emit_graph_progress(
        progress_cb,
        stage="scanning-node-files",
        message="Scanning node markdown files for source paths…",
        current=6,
        total=total_steps,
    )
    file_paths = _scan_node_file_paths(repo_root, global_vault)

    _emit_graph_progress(
        progress_cb,
        stage="augmenting-cache",
        message=(
            "Writing node file paths into the cached graph…"
            if file_paths
            else "No node markdown paths found; skipping cache augmentation…"
        ),
        current=7,
        total=total_steps,
    )
    if file_paths:
        _augment_graph_cache_with_file_paths(cache_path, file_paths)

    # Stamp provenance/tier fields onto established-library nodes.
    # Runs AFTER file_path augmentation so the user-vault-wins guard in
    # _augment_graph_cache_with_provenance can read the correct file_path.
    _augment_graph_cache_with_provenance(cache_path, load_established_provenance())

    # Stamp the cache with a fingerprint of every source root
    # (including the shipped established library) so a later library change
    # makes ``graph_cache_is_stale`` return True and forces a rebuild.
    _write_graph_cache_source_key(cache_path, graph_source_key(settings))

    _emit_graph_progress(
        progress_cb,
        stage="loading-graph",
        message="Loading the generated graph for visualization…",
        current=8,
        total=total_steps,
    )
    loaded = api.load_graph(str(cache_path))
    meta = _read_graph_json_meta(cache_path)
    return CachedGraph(graph=loaded, meta=meta, cache_path=cache_path, built_now=True)


def load_cached_graph(settings: Settings, *, auto_build: bool = False) -> CachedGraph:
    _load_akms_graph_api()
    _ensure_repo_root(settings)
    _ensure_global_vault(settings)

    cache_path = graph_cache_path(settings)
    if not cache_path.is_file():
        if not auto_build:
            raise GraphAdapterError(f"Graph cache missing: {cache_path}")
        return build_cached_graph(settings)

    # When auto-building, a stale source key (e.g. a shipped-library
    # change) forces a rebuild so the update is reflected. Read routes pass
    # auto_build=False, so their "cache missing → 412" contract is unchanged.
    if auto_build and graph_cache_is_stale(settings):
        return build_cached_graph(settings)

    api = _load_akms_graph_api()
    graph = api.load_graph(str(cache_path))
    meta = _read_graph_json_meta(cache_path)
    return CachedGraph(graph=graph, meta=meta, cache_path=cache_path, built_now=False)


def filter_nodes(
    graph: Any,
    *,
    query: str | None = None,
    domain: str | None = None,
    status: str | None = None,
    tag: str | None = None,
    origin: str | None = None,
) -> list[dict[str, Any]]:
    query_norm = query.strip().lower() if query else None
    tag_norm = tag.strip().lower() if tag else None
    domain_norm = domain.strip().lower() if domain else None
    status_norm = status.strip().lower() if status else None
    origin_norm = origin.strip().lower() if origin else None

    out: list[dict[str, Any]] = []
    for node_id in sorted(graph.nodes):
        data = dict(graph.nodes[node_id])
        tags = _normalize_tags(data.get("tags"))
        haystack = " ".join(
            filter(
                None,
                [
                    node_id,
                    str(data.get("title", "")),
                    str(data.get("domain", "")),
                    str(data.get("subdomain", "")),
                    " ".join(tags),
                ],
            )
        ).lower()
        if query_norm and query_norm not in haystack:
            continue
        if domain_norm and str(data.get("domain", "")).lower() != domain_norm:
            continue
        if status_norm and str(data.get("status", "")).lower() != status_norm:
            continue
        if origin_norm and str(data.get("node_origin", "")).lower() != origin_norm:
            continue
        if tag_norm and tag_norm not in {t.lower() for t in tags}:
            continue
        out.append(_graph_node_payload(node_id, data))
    return out


def filter_edges(
    graph: Any,
    *,
    source: str | None = None,
    target: str | None = None,
    edge_type: str | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for u, v, data in sorted(graph.edges(data=True), key=lambda item: (item[0], item[1], str(item[2].get("type", "")))):
        if source and u != source:
            continue
        if target and v != target:
            continue
        if edge_type and str(data.get("type", "")) != edge_type:
            continue
        out.append(_graph_edge_payload(u, v, data))
    return out


def ego_slice(graph: Any, node_id: str, *, depth: int, direction: Direction) -> set[str]:
    if node_id not in graph:
        raise KeyError(node_id)
    if depth <= 0:
        return {node_id}
    if direction == "both":
        api = _load_networkx()
        return set(api.ego_graph(graph.to_undirected(), node_id, radius=depth).nodes)

    neighbors = graph.predecessors if direction == "in" else graph.successors
    seen = {node_id}
    q: deque[tuple[str, int]] = deque([(node_id, 0)])
    while q:
        current, dist = q.popleft()
        if dist >= depth:
            continue
        for nxt in neighbors(current):
            if nxt in seen:
                continue
            seen.add(nxt)
            q.append((nxt, dist + 1))
    return seen


def cluster_slice(
    graph: Any,
    *,
    tag: str | None = None,
    domain: str | None = None,
) -> set[str]:
    if not tag and not domain:
        raise ValueError("At least one of 'tag' or 'domain' is required")
    tag_norm = tag.strip().lower() if tag else None
    domain_norm = domain.strip().lower() if domain else None
    selected: set[str] = set()
    for node_id in graph.nodes:
        data = graph.nodes[node_id]
        tags = {t.lower() for t in _normalize_tags(data.get("tags"))}
        matches_tag = tag_norm is None or tag_norm in tags
        matches_domain = domain_norm is None or str(data.get("domain", "")).lower() == domain_norm
        if matches_tag and matches_domain:
            selected.add(node_id)
    return selected


def slice_to_payload(graph: Any, node_ids: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes = [_graph_node_payload(node_id, dict(graph.nodes[node_id])) for node_id in sorted(node_ids)]
    edges: list[dict[str, Any]] = []
    for u, v, data in sorted(graph.edges(data=True), key=lambda item: (item[0], item[1], str(item[2].get("type", "")))):
        if u in node_ids and v in node_ids:
            edges.append(_graph_edge_payload(u, v, data))
    return nodes, edges


def _load_akms_graph_api():
    try:
        module = importlib.import_module("akms.graph.build_graph")
    except ImportError as e:
        raise GraphAdapterError(
            "AKMS graph support is not installed. Install with: uv pip install -e logic-loom-api[akms]"
        ) from e
    return module


def _load_networkx():
    try:
        return importlib.import_module("networkx")
    except ImportError as e:
        raise GraphAdapterError("networkx is required for graph queries") from e


# Name of the empty stand-in repo root used when no AKMS checkout is
# configured; it lives under Logic-Loom's own graph cache directory.
_NO_REPO_STUB = "no-akms-repo"


def _ensure_repo_root(settings: Settings) -> Path:
    """Repo root handed to the AKMS graph compiler.

    The optional ``akms_repo`` checkout when present. Otherwise an empty,
    Logic-Loom-owned stub under ``<cache_path>/graph/no-akms-repo``: the
    checkout only contributes ``knowledge/local-nodes`` and
    ``knowledge/code-mirror`` (absent trees are skipped by every scanner) and
    the compiler always writes to the explicit cache ``output_path``, so the
    graph compiles from the source vault + established library alone. Nothing
    is written outside Logic-Loom's cache (the graph API stays read-only).
    """
    repo = settings.akms_repo_dir()
    if repo is not None:
        return repo.resolve()
    stub = graph_cache_path(settings).parent / _NO_REPO_STUB
    stub.mkdir(parents=True, exist_ok=True)
    return stub.resolve()


def _opt_path_str(value: Path | None) -> str | None:
    return str(value) if value is not None else None


def _ensure_global_vault(settings: Settings) -> Path:
    vault = settings.effective_akms_global_vault().expanduser().resolve()
    if not vault.is_dir():
        raise GraphAdapterError(f"akms_global_vault does not exist: {vault}")
    return vault


def _read_graph_json_meta(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with path.open() as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("failed to read graph cache metadata from %s: %s", path, e)
        return {}
    graph_meta = data.get("graph")
    return dict(graph_meta) if isinstance(graph_meta, dict) else {}


def established_library_root() -> Path | None:
    """Resolve the shipped established node library root, or ``None``.

    The library ships under ``logic_loom_api.resources.akms_nodes.established``
    (populated by ``scripts/sync_akms_nodes.py``). It is a GITIGNORED build
    artifact — absent on a fresh checkout and in dev sidecars built without the
    sync step — so callers MUST treat a
    ``None`` return (or a non-existent path) as "no established source" and
    degrade gracefully, never as an error. Read-only: this path is only ever
    read from, never written to.

    ``resources/`` is a namespace package (no ``__init__.py``), so
    ``resources.files()`` returns a ``MultiplexedPath`` that does not convert
    to a filesystem ``Path`` cleanly. We anchor on the ``logic_loom_api``
    package's own file location — which is always a concrete extracted path in
    both editable installs and force-included wheels — and join the resource
    subtree, mirroring how the bundled Claude assets are located.
    """
    try:
        anchor = resources.files("logic_loom_api")
    except (ModuleNotFoundError, FileNotFoundError, ImportError):
        return None
    try:
        root = Path(str(anchor)) / "resources" / "akms_nodes" / "established"
    except (TypeError, ValueError):
        return None
    return root if root.is_dir() else None


def load_established_provenance() -> dict[str, dict[str, str]]:
    """Load the PROVENANCE.toml manifest from the shipped established library.

    Returns a mapping of node-id → provenance dict (keys: ``verified_by``,
    ``verified_at``, ``version``, ``source_ref``).  Returns an empty dict when
    the manifest is absent or unreadable so callers degrade gracefully.

    Provenance is NOT in node frontmatter (``GlobalNodeFrontmatter`` is
    ``extra: forbid``) — it lives only in this sidecar manifest.
    """
    root = established_library_root()
    if root is None:
        return {}
    manifest = root / "PROVENANCE.toml"
    if not manifest.is_file():
        return {}
    try:
        with manifest.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        logger.warning("failed to read established provenance manifest %s: %s", manifest, e)
        return {}
    # Each top-level key is a node id; value is the provenance table.
    result: dict[str, dict[str, str]] = {}
    for node_id, entry in data.items():
        if isinstance(entry, dict):
            result[node_id] = {k: str(v) for k, v in entry.items()}
    return result


def _augment_graph_cache_with_provenance(
    cache_path: Path, provenance: dict[str, dict[str, str]]
) -> None:
    """Stamp provenance fields onto established-library nodes in the graph cache.

    Mirrors ``_augment_graph_cache_with_file_paths``.  For each node whose id
    appears in the PROVENANCE manifest, sets:
      - ``is_established: true``
      - ``tier: "A"``
      - ``verified_by``, ``verified_at``, ``version``, ``source_ref``

    A node that shadows an established id from the user vault will NOT have
    been stamped with ``file_path`` pointing at the established tree (the user's
    path wins in ``_scan_node_file_paths``), so we use ``file_path`` as a proxy
    to detect shadowing — if file_path is under the established library root, the
    node is established; if it points elsewhere (user vault), it is NOT marked.

    When no provenance is available (empty dict), the function is a no-op.
    """
    if not provenance:
        return
    established_root = established_library_root()
    # Resolve the root once so the shadow-guard comparison below is symlink-safe
    # (packaged wheels / macOS /private prefixes symlink the resources tree).
    established_root_resolved = (
        established_root.resolve() if established_root is not None else None
    )
    try:
        with cache_path.open() as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("failed to augment graph cache with provenance %s: %s", cache_path, e)
        return

    changed = False
    for node in data.get("nodes", []):
        node_id = node.get("id")
        if not isinstance(node_id, str):
            continue
        if node_id not in provenance:
            continue
        # Guard: if file_path is set and does NOT point under the established
        # library root, a user-vault node won the id clash — do not mark it.
        # Both sides are resolved so a symlinked resources tree (packaged wheel,
        # macOS /private prefix) does not spuriously fail the relative_to check
        # and leave a genuine established node unstamped.
        file_path = node.get("file_path")
        if file_path and established_root_resolved is not None:
            try:
                Path(file_path).resolve().relative_to(established_root_resolved)
            except ValueError:
                # file_path is outside the established root → user vault node.
                continue

        entry = provenance[node_id]
        node["is_established"] = True
        node["tier"] = "A"
        node["verified_by"] = entry.get("verified_by", "")
        node["verified_at"] = entry.get("verified_at", "")
        node["version"] = entry.get("version", "")
        node["source_ref"] = entry.get("source_ref", "")
        changed = True

    if not changed:
        return
    with cache_path.open("w") as f:
        json.dump(data, f, sort_keys=True, indent=2, default=str)


def _graph_source_specs(
    repo_root: Path,
    global_vault: Path,
) -> list[tuple[GraphSourceKind, Path, dict[str, Any]]]:
    specs: list[tuple[GraphSourceKind, Path, dict[str, Any]]] = [
        ("global-vault", global_vault, {}),
        ("local-nodes", repo_root / "knowledge" / "local-nodes", {"is_local": True}),
        ("code-mirror", repo_root / "knowledge" / "code-mirror", {"is_code_mirror": True}),
    ]
    # The shipped established library is an ADDITIONAL read-only source
    # unioned into the graph. Established nodes are valid GlobalNodeFrontmatter
    # (parser_kwargs {} — same shape as global-vault). Omit the spec entirely
    # when the library is absent so ``scan_graph_sources`` never sees it.
    established_root = established_library_root()
    if established_root is not None:
        specs.append(("established-library", established_root, {}))
    return specs


def _graph_source_issue(
    source_kind: GraphSourceKind,
    source_root: Path,
    md_path: Path,
    error: Exception,
    ignored_paths: set[str],
) -> GraphSourceIssue:
    try:
        relative_path = str(md_path.relative_to(source_root))
    except ValueError:
        relative_path = md_path.name

    reason = str(error).strip() or error.__class__.__name__
    normalized_path = _normalize_graph_issue_path(md_path)
    return GraphSourceIssue(
        path=Path(normalized_path),
        relative_path=relative_path,
        source_kind=source_kind,
        reason=reason,
        error_type=error.__class__.__name__,
        ignored=normalized_path in ignored_paths,
    )


def _write_graph_issue_log(report: GraphIssueLog) -> None:
    report.log_path.parent.mkdir(parents=True, exist_ok=True)
    with report.log_path.open("w") as f:
        json.dump(report.to_payload(), f, sort_keys=True, indent=2, default=str)


def _stage_filtered_graph_inputs(
    report: GraphIssueLog,
    *,
    repo_root: Path,
    global_vault: Path,
    cache_path: Path,
) -> tuple[TemporaryDirectory[str], Path, Path]:
    staged_tempdir = TemporaryDirectory(prefix="graph-build-", dir=str(cache_path.parent))
    staging_root = Path(staged_tempdir.name)
    staged_repo_root = staging_root / repo_root.name
    staged_global_vault = staging_root / "global-vault"

    staged_global_vault.mkdir(parents=True, exist_ok=True)
    (staged_repo_root / "knowledge" / "graph").mkdir(parents=True, exist_ok=True)
    _copy_filtered_markdown_files(
        report.valid_files_by_source["global-vault"],
        source_root=global_vault,
        target_root=staged_global_vault,
    )
    _copy_filtered_markdown_files(
        report.valid_files_by_source["local-nodes"],
        source_root=repo_root / "knowledge" / "local-nodes",
        target_root=staged_repo_root / "knowledge" / "local-nodes",
    )
    _copy_filtered_markdown_files(
        report.valid_files_by_source["code-mirror"],
        source_root=repo_root / "knowledge" / "code-mirror",
        target_root=staged_repo_root / "knowledge" / "code-mirror",
    )

    # Union the shipped established library into the staged global vault
    # (read-only — the originals under resources/akms_nodes/established/ are
    # never written to). Established nodes carry global frontmatter, so the
    # compiler treats them as global-origin. Skip any whose id already exists in
    # the user's vault so the user's copy wins (id-clash disambiguation).
    established_files = report.valid_files_by_source.get("established-library", [])
    if established_files:
        established_root = established_library_root()
        if established_root is not None:
            user_vault_ids = _node_ids_from_files(
                report.valid_files_by_source["global-vault"]
            )
            deduped = _dedupe_established_files(established_files, user_vault_ids)
            _copy_filtered_markdown_files(
                deduped,
                source_root=established_root,
                target_root=staged_global_vault / "_established",
            )

    local_state_path = repo_root / "knowledge" / "graph" / "local_state.yaml"
    if local_state_path.is_file():
        shutil.copy2(local_state_path, staged_repo_root / "knowledge" / "graph" / "local_state.yaml")

    logger.info(
        "prepared filtered graph staging repo at %s (using %d valid markdown file(s), "
        "skipping %d issue(s), unioning %d established-library file(s))",
        staged_repo_root,
        report.valid_markdown_files,
        report.total_issue_count,
        len(report.valid_files_by_source.get("established-library", [])),
    )
    return staged_tempdir, staged_repo_root, staged_global_vault


def _node_ids_from_files(files: list[Path]) -> set[str]:
    """Collect the ``id`` frontmatter value from each markdown file (best-effort)."""
    ids: set[str] = set()
    for md_path in files:
        try:
            metadata = dict(frontmatter.load(str(md_path)).metadata)
        except Exception:
            continue
        node_id = metadata.get("id")
        if isinstance(node_id, str):
            ids.add(node_id)
    return ids


def _dedupe_established_files(
    established_files: list[Path], user_vault_ids: set[str]
) -> list[Path]:
    """Drop established files whose node id already exists in the user vault.

    The user's vault node wins on an id clash (id-clash disambiguation). Established files with no readable id are staged as-is;
    the compiler validates them downstream.
    """
    if not user_vault_ids:
        return list(established_files)
    kept: list[Path] = []
    for md_path in established_files:
        try:
            metadata = dict(frontmatter.load(str(md_path)).metadata)
        except Exception:
            kept.append(md_path)
            continue
        node_id = metadata.get("id")
        if isinstance(node_id, str) and node_id in user_vault_ids:
            logger.info(
                "established-library node %r shadowed by user vault; skipping %s",
                node_id,
                md_path,
            )
            continue
        kept.append(md_path)
    return kept


def _staging_message(report: GraphIssueLog) -> str:
    established_count = len(report.valid_files_by_source.get("established-library", []))
    if report.total_issue_count > 0:
        return _filtered_sources_message(report)
    if established_count > 0:
        return (
            f"Unioning {established_count} established-library node file(s) into a "
            "staged graph source set…"
        )
    return "Staging a filtered graph source set…"


def _filtered_sources_message(report: GraphIssueLog) -> str:
    if report.issue_count > 0:
        return (
            f"Skipping {report.total_issue_count} invalid markdown file(s) "
            f"({report.ignored_issue_count} already ignored) and staging a filtered graph source set…"
            if report.ignored_issue_count > 0
            else f"Skipping {report.total_issue_count} invalid markdown file(s) and staging a filtered graph source set…"
        )
    return f"Using {report.ignored_issue_count} ignored markdown file(s) from the skip list and staging a filtered graph source set…"


def _copy_filtered_markdown_files(files: list[Path], *, source_root: Path, target_root: Path) -> None:
    target_root.mkdir(parents=True, exist_ok=True)
    for src_path in files:
        relative_path = src_path.relative_to(source_root)
        dest_path = target_root / relative_path
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_path, dest_path)


def _write_graph_cache_source_key(cache_path: Path, source_key: str) -> None:
    """Record the source fingerprint in the cache meta."""
    try:
        with cache_path.open() as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("failed to write graph cache source key for %s: %s", cache_path, e)
        return

    graph_meta = data.get("graph")
    if not isinstance(graph_meta, dict):
        return
    graph_meta["source_key"] = source_key

    with cache_path.open("w") as f:
        json.dump(data, f, sort_keys=True, indent=2, default=str)


def _rewrite_graph_cache_global_vault(cache_path: Path, global_vault: Path) -> None:
    try:
        with cache_path.open() as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("failed to rewrite graph cache metadata for %s: %s", cache_path, e)
        return

    graph_meta = data.get("graph")
    if not isinstance(graph_meta, dict):
        return
    graph_meta["global_vault"] = str(global_vault)

    with cache_path.open("w") as f:
        json.dump(data, f, sort_keys=True, indent=2, default=str)


def _load_akms_schema_validators():
    try:
        return importlib.import_module("akms.schema.validators")
    except ImportError as e:
        raise GraphAdapterError(
            "AKMS schema validators are not installed. Install with: uv pip install -e logic-loom-api[akms]"
        ) from e


def _normalize_graph_issue_path(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve(strict=False))


def _read_graph_issue_ignored_paths(settings: Settings) -> set[str]:
    ignore_path = graph_issue_ignore_path(settings)
    if not ignore_path.is_file():
        return set()

    try:
        with ignore_path.open() as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("failed to read graph ignored-issues file %s: %s", ignore_path, e)
        return set()

    raw_paths = data.get("paths") if isinstance(data, dict) else None
    if not isinstance(raw_paths, list):
        return set()
    return {_normalize_graph_issue_path(item) for item in raw_paths if isinstance(item, str)}


def _write_graph_issue_ignored_paths(ignore_path: Path, ignored_paths: set[str]) -> None:
    ignore_path.parent.mkdir(parents=True, exist_ok=True)
    with ignore_path.open("w") as f:
        json.dump(
            {
                "updated_at": datetime.now(UTC).isoformat(),
                "paths": sorted(ignored_paths),
            },
            f,
            sort_keys=True,
            indent=2,
            default=str,
        )


def _emit_graph_progress(
    progress_cb: GraphProgressCallback | None,
    *,
    stage: str,
    message: str,
    current: int,
    total: int,
) -> None:
    if progress_cb is None:
        return
    progress_cb(
        GraphBuildProgress(
            stage=stage,
            message=message,
            current=current,
            total=total,
        )
    )


def _augment_graph_cache_with_file_paths(cache_path: Path, file_paths: dict[str, str]) -> None:
    try:
        with cache_path.open() as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("failed to augment graph cache %s: %s", cache_path, e)
        return

    changed = False
    for node in data.get("nodes", []):
        node_id = node.get("id")
        if not isinstance(node_id, str):
            continue
        file_path = file_paths.get(node_id)
        if file_path and node.get("file_path") != file_path:
            node["file_path"] = file_path
            changed = True
    if not changed:
        return
    with cache_path.open("w") as f:
        json.dump(data, f, sort_keys=True, indent=2, default=str)


def _scan_node_file_paths(repo_root: Path, global_vault: Path) -> dict[str, str]:
    # User-vault directories are scanned first so a user node's file_path wins
    # over a shadowed established node (matching the staging dedupe order). The
    # established library is appended last and only fills ids not already
    # claimed — its real shipped path is surfaced for read-only "copy to vault".
    directories = [
        global_vault,
        repo_root / "knowledge" / "local-nodes",
        repo_root / "knowledge" / "code-mirror",
    ]
    established_root = established_library_root()
    if established_root is not None:
        directories.append(established_root)
    node_paths: dict[str, str] = {}
    for directory in directories:
        if not directory.exists():
            continue
        for md_path in sorted(directory.glob("**/*.md")):
            try:
                metadata = dict(frontmatter.load(str(md_path)).metadata)
            except Exception:
                continue
            node_id = metadata.get("id")
            if isinstance(node_id, str) and node_id not in node_paths:
                node_paths[node_id] = str(md_path.resolve())
    return node_paths


def _normalize_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value]
    return [str(value)]


def _graph_node_payload(node_id: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": node_id,
        "title": data.get("title"),
        "domain": data.get("domain"),
        "subdomain": data.get("subdomain"),
        "status": data.get("status"),
        "tags": _normalize_tags(data.get("tags")),
        "confidence": float(data["confidence"]) if data.get("confidence") is not None else None,
        "source": data.get("source"),
        "node_origin": data.get("node_origin"),
        "file_path": data.get("file_path"),
        # Provenance / tier fields — present only on established (Tier A) nodes.
        "is_established": data.get("is_established", False),
        "tier": data.get("tier"),
        "verified_by": data.get("verified_by"),
        "verified_at": data.get("verified_at"),
        "version": data.get("version"),
        "source_ref": data.get("source_ref"),
    }


def _graph_edge_payload(source: str, target: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": source,
        "target": target,
        "type": data.get("type"),
        "weight": float(data["weight"]) if data.get("weight") is not None else None,
        "note": data.get("note"),
        "edge_origin": data.get("edge_origin"),
    }