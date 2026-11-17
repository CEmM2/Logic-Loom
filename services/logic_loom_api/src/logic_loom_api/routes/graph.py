"""Read-only AKMS graph routes backed by LogicLoom-owned cache."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from ..adapters.graph_adapter import (
    GraphAdapterError,
    GraphSourceIssuesError,
    build_cached_graph,
    cluster_slice,
    ego_slice,
    filter_edges,
    filter_nodes,
    graph_readiness,
    load_cached_graph,
    read_graph_issue_log,
    read_graph_status,
    scan_graph_sources,
    set_graph_issue_ignored,
    slice_to_payload,
)
from ..config import Settings
from ..deps import get_settings
from ..errors import envelope, http_error

router = APIRouter()
logger = logging.getLogger(__name__)


class GraphNode(BaseModel):
    id: str
    title: str | None = None
    domain: str | None = None
    subdomain: str | None = None
    status: str | None = None
    tags: list[str] = Field(default_factory=list)
    confidence: float | None = None
    source: str | None = None
    node_origin: str | None = None
    file_path: str | None = None
    # Provenance / tier fields stamped by graph_adapter onto
    # established (Tier-A) nodes. These are additive with defaults, so existing
    # consumers are unaffected, but WITHOUT declaring them the strict BaseModel
    # would silently drop them on serialization — established nodes would render
    # as Draft/Generated in the graph inspector. _graph_node_payload always emits
    # these keys, so declaring them here forwards them through /nodes /ego /cluster.
    is_established: bool = False
    tier: str | None = None
    verified_by: str | None = None
    verified_at: str | None = None
    version: str | None = None
    source_ref: str | None = None


class GraphEdge(BaseModel):
    source: str
    target: str
    type: str | None = None
    weight: float | None = None
    note: str | None = None
    edge_origin: str | None = None


class GraphMeta(BaseModel):
    generated_at: str | None = None
    node_count: int = 0
    edge_count: int = 0
    repo_id: str | None = None
    global_vault: str | None = None
    cache_path: str
    scope: str = "full"
    selected_node: str | None = None
    depth: int | None = None
    direction: str | None = None
    tag: str | None = None
    domain: str | None = None


class GraphSlice(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    meta: GraphMeta


class GraphStatus(BaseModel):
    cache_exists: bool
    cache_path: str
    # Optional AKMS checkout fields: null when no akms_repo is configured.
    repo_graph_path: str | None = None
    generated_at: str | None = None
    node_count: int = 0
    edge_count: int = 0
    repo_id: str | None = None
    akms_repo: str | None = None
    akms_global_vault: str
    graph_package_installed: bool
    repo_exists: bool
    global_vault_exists: bool
    graph_available: bool


class GraphRefreshProgress(BaseModel):
    stage: str
    message: str
    current: int
    total: int
    percent: int


class GraphSourceIssue(BaseModel):
    path: str
    relative_path: str
    source_kind: str
    reason: str
    error_type: str
    ignored: bool = False


class GraphIssueLog(BaseModel):
    generated_at: str | None = None
    log_path: str
    issue_count: int = 0
    ignored_issue_count: int = 0
    total_issue_count: int = 0
    total_markdown_files: int = 0
    valid_markdown_files: int = 0
    issues: list[GraphSourceIssue] = Field(default_factory=list)


class GraphRefreshRequest(BaseModel):
    skip_invalid_files: bool = False


class GraphIssueIgnoreRequest(BaseModel):
    path: str
    ignored: bool = True


def _review_required_message(report_payload: dict) -> str:
    """Name the files and the ways forward, so the 409 is actionable on its own."""
    issues = [i for i in report_payload.get("issues", []) if not i.get("ignored")]
    names = [str(i.get("relative_path") or i.get("path")) for i in issues[:5]]
    more = len(issues) - len(names)
    listed = ", ".join(names) + (f" and {more} more" if more > 0 else "")
    count = report_payload.get("issue_count", len(issues))
    return (
        f"{count} graph source file(s) can't be read as AKMS nodes"
        + (f": {listed}" if listed else "")
        + ". Fix their frontmatter, ignore files that aren't nodes "
        "(POST /api/graph/issues/ignore), or retry with skip_invalid_files=true "
        "to build without them. The full list is in detail.issues and in "
        f"{report_payload.get('log_path')}."
    )


def _review_required_response(report_payload: dict) -> JSONResponse:
    return JSONResponse(
        envelope(
            "review_required",
            _review_required_message(report_payload),
            report_payload,
        ),
        status_code=409,
    )


@router.get("/issues", response_model=GraphIssueLog)
def graph_issue_log(settings: Settings = Depends(get_settings)) -> GraphIssueLog:
    return GraphIssueLog(**read_graph_issue_log(settings))


@router.post("/issues/scan", response_model=GraphIssueLog)
def graph_issue_scan(settings: Settings = Depends(get_settings)) -> GraphIssueLog:
    try:
        report = scan_graph_sources(settings)
    except GraphAdapterError as e:
        raise http_error(412, "precondition_failed", str(e))
    return GraphIssueLog(**report.to_payload())


@router.post("/issues/ignore", response_model=GraphIssueLog)
def graph_issue_ignore(
    payload: GraphIssueIgnoreRequest,
    settings: Settings = Depends(get_settings),
) -> GraphIssueLog:
    try:
        report = set_graph_issue_ignored(settings, payload.path, ignored=payload.ignored)
    except GraphAdapterError as e:
        raise http_error(412, "precondition_failed", str(e))
    return GraphIssueLog(**report.to_payload())


@router.get("/status", response_model=GraphStatus)
def graph_status(settings: Settings = Depends(get_settings)) -> GraphStatus:
    status = read_graph_status(settings)
    readiness = graph_readiness(settings)
    return GraphStatus(**status, **readiness)


@router.post("/refresh", response_model=GraphStatus)
def graph_refresh(
    payload: GraphRefreshRequest | None = None,
    settings: Settings = Depends(get_settings),
):
    try:
        build_cached_graph(settings, skip_invalid_files=bool(payload and payload.skip_invalid_files))
    except GraphSourceIssuesError as e:
        return _review_required_response(e.report.to_payload())
    except GraphAdapterError as e:
        raise http_error(412, "precondition_failed", str(e))
    status = read_graph_status(settings)
    readiness = graph_readiness(settings)
    return GraphStatus(**status, **readiness)


@router.post("/refresh/stream")
async def graph_refresh_stream(
    payload: GraphRefreshRequest | None = None,
    settings: Settings = Depends(get_settings),
) -> EventSourceResponse:
    """Rebuild the graph cache, streaming progress as Server-Sent Events.

    A POST, because it writes the cache (it was a GET so EventSource could
    call it). Events: ``progress``, then one of ``done``, ``review-required``
    or ``failed``.
    """
    skip_invalid_files = bool(payload and payload.skip_invalid_files)
    queue: asyncio.Queue[dict[str, str] | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def emit(event: str, data: str) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, {"event": event, "data": data})

    def emit_progress(progress) -> None:
        emit("progress", GraphRefreshProgress(**progress.to_payload()).model_dump_json())

    def worker() -> None:
        try:
            build_cached_graph(
                settings,
                progress_cb=emit_progress,
                skip_invalid_files=skip_invalid_files,
            )
            status = read_graph_status(settings)
            readiness = graph_readiness(settings)
            emit("done", GraphStatus(**status, **readiness).model_dump_json())
        except GraphSourceIssuesError as e:
            emit("review-required", GraphIssueLog(**e.report.to_payload()).model_dump_json())
        except GraphAdapterError as e:
            emit("failed", json.dumps({"code": "precondition_failed", "message": str(e)}))
        except Exception as e:  # pragma: no cover - defensive guardrail
            logger.exception("graph refresh stream failed")
            emit("failed", json.dumps({"code": "internal_error", "message": str(e)}))
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    asyncio.create_task(asyncio.to_thread(worker))

    async def event_gen():
        while True:
            item = await queue.get()
            if item is None:
                break
            yield item

    return EventSourceResponse(event_gen())


@router.get("/nodes", response_model=list[GraphNode])
def graph_nodes(
    query: str | None = Query(default=None),
    domain: str | None = Query(default=None),
    status: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    origin: str | None = Query(default=None),
    settings: Settings = Depends(get_settings),
) -> list[GraphNode]:
    graph = _load_graph_or_raise(settings, auto_build=False)
    return [GraphNode(**node) for node in filter_nodes(graph.graph, query=query, domain=domain, status=status, tag=tag, origin=origin)]


@router.get("/edges", response_model=list[GraphEdge])
def graph_edges(
    source: str | None = Query(default=None),
    target: str | None = Query(default=None),
    edge_type: str | None = Query(default=None, alias="type"),
    settings: Settings = Depends(get_settings),
) -> list[GraphEdge]:
    graph = _load_graph_or_raise(settings, auto_build=False)
    return [GraphEdge(**edge) for edge in filter_edges(graph.graph, source=source, target=target, edge_type=edge_type)]


@router.get("/ego", response_model=GraphSlice)
def graph_ego(
    node: str = Query(..., min_length=1),
    depth: int = Query(default=2, ge=0, le=6),
    direction: Literal["in", "out", "both"] = Query(default="both"),
    settings: Settings = Depends(get_settings),
) -> GraphSlice:
    graph = _load_graph_or_raise(settings, auto_build=False)
    try:
        node_ids = ego_slice(graph.graph, node, depth=depth, direction=direction)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown node: {node}")
    nodes, edges = slice_to_payload(graph.graph, node_ids)
    return GraphSlice(
        nodes=[GraphNode(**item) for item in nodes],
        edges=[GraphEdge(**item) for item in edges],
        meta=_slice_meta(graph, scope="ego", selected_node=node, depth=depth, direction=direction),
    )


@router.get("/cluster", response_model=GraphSlice)
def graph_cluster(
    tag: str | None = Query(default=None),
    domain: str | None = Query(default=None),
    settings: Settings = Depends(get_settings),
) -> GraphSlice:
    graph = _load_graph_or_raise(settings, auto_build=False)
    try:
        node_ids = cluster_slice(graph.graph, tag=tag, domain=domain)
    except ValueError as e:
        raise http_error(400, "bad_request", str(e))
    nodes, edges = slice_to_payload(graph.graph, node_ids)
    return GraphSlice(
        nodes=[GraphNode(**item) for item in nodes],
        edges=[GraphEdge(**item) for item in edges],
        meta=_slice_meta(graph, scope="cluster", tag=tag, domain=domain),
    )


def _load_graph_or_raise(settings: Settings, *, auto_build: bool):
    try:
        return load_cached_graph(settings, auto_build=auto_build)
    except GraphAdapterError as e:
        raise http_error(412, "precondition_failed", str(e))


def _slice_meta(graph, *, scope: str, selected_node: str | None = None, depth: int | None = None, direction: str | None = None, tag: str | None = None, domain: str | None = None) -> GraphMeta:
    meta = graph.meta
    return GraphMeta(
        generated_at=meta.get("generated_at"),
        node_count=int(meta.get("node_count") or 0),
        edge_count=int(meta.get("edge_count") or 0),
        repo_id=meta.get("repo_id"),
        global_vault=meta.get("global_vault"),
        cache_path=str(graph.cache_path),
        scope=scope,
        selected_node=selected_node,
        depth=depth,
        direction=direction,
        tag=tag,
        domain=domain,
    )