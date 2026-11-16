"""``/api/records/*`` routes — browse akms-records stores (read-only).

::

    GET /api/records/status                              always 200
    GET /api/records/stores/{store}/records              list + filter + facets
    GET /api/records/stores/{store}/records/{record_id}  one record + history + schema
    GET /api/records/stores/{store}/schemas              registered schemas
    GET /api/records/stores/{store}/graph                the record graph

Degrade-gracefully, like ``/api/mechdsl`` and ``/api/failure-memory``:
``/status`` is always 200 and reports whether the optional ``akms-records``
package is installed and which stores are configured; every per-store endpoint
returns 503 ``records_unavailable`` without the package and 404
``unknown_store`` for an id that is not in ``[[records_stores]]``. Nothing here
writes: see ``adapters/records.py``.
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from ..adapters import records as adapter
from ..adapters.records import RecordsAdapterError
from ..config import RecordsStore, Settings
from ..deps import get_settings
from ..errors import envelope

router = APIRouter()


def _call(settings: Settings, store_id: str, fn: Callable[[RecordsStore], Any]) -> Any:
    availability = adapter.records_available()
    if not availability.installed:
        return JSONResponse(
            envelope("records_unavailable", availability.reason or "akms-records is not installed."),
            status_code=503,
        )
    cfg = settings.records_store(store_id)
    if cfg is None:
        return JSONResponse(
            envelope(
                "unknown_store",
                f"No records store {store_id!r} is configured in [[records_stores]].",
            ),
            status_code=404,
        )
    try:
        return fn(cfg)
    except RecordsAdapterError as exc:
        return JSONResponse(envelope(exc.code, str(exc), {"code": exc.code}), status_code=exc.status)


@router.get("/status")
def records_status(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Package availability plus every configured store. Always 200."""
    availability = adapter.records_available()
    stores: list[dict[str, Any]]
    if availability.installed:
        stores = [adapter.describe_store(cfg) for cfg in settings.records_stores]
    else:
        stores = [
            {"id": cfg.id, "scope": cfg.scope, "root": None, "exists": False,
             "record_count": None, "schema_count": None, "replay_errors": 0, "error": None}
            for cfg in settings.records_stores
        ]
    return {
        "available": availability.installed,
        "package_installed": availability.installed,
        "version": availability.version,
        "reason": availability.reason,
        "experimental": True,
        "stores": stores,
    }


@router.get("/stores/{store_id}/records")
def records_list(
    store_id: str,
    schema: str | None = None,
    tag: str | None = None,
    lifecycle: str | None = None,
    review_state: str | None = None,
    q: str | None = None,
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    settings: Settings = Depends(get_settings),
) -> Any:
    return _call(
        settings,
        store_id,
        lambda cfg: adapter.list_records(
            cfg, schema=schema, tag=tag, lifecycle=lifecycle,
            review_state=review_state, q=q, limit=limit, offset=offset,
        ),
    )


@router.get("/stores/{store_id}/records/{record_id}")
def records_get(
    store_id: str, record_id: str, settings: Settings = Depends(get_settings)
) -> Any:
    return _call(settings, store_id, lambda cfg: adapter.get_record(cfg, record_id))


@router.get("/stores/{store_id}/schemas")
def records_schemas(store_id: str, settings: Settings = Depends(get_settings)) -> Any:
    return _call(settings, store_id, adapter.list_schemas)


@router.get("/stores/{store_id}/graph")
def records_graph(
    store_id: str,
    focus: str | None = None,
    depth: int = Query(2, ge=0, le=6),
    include_external: bool = True,
    schema: str | None = None,
    limit: int = Query(adapter.DEFAULT_GRAPH_LIMIT, ge=1, le=adapter.MAX_GRAPH_LIMIT),
    settings: Settings = Depends(get_settings),
) -> Any:
    return _call(
        settings,
        store_id,
        lambda cfg: adapter.record_graph(
            cfg, focus=focus, depth=depth, include_external=include_external,
            schema=schema, limit=limit,
        ),
    )
