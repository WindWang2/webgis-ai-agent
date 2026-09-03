"""
Enterprise Geospatial Data Fabric REST Routes
"""
import asyncio
import logging
import threading
from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Header, Body
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.auth import get_current_user, get_current_user_optional
from app.models.data_fabric import DataSourceModel, CatalogItemModel
from app.schemas.data_fabric_schema import (
    ConnectionProfile,
    QuerySpec,
)
from app.services.data_fabric.manager import data_fabric_manager
from app.services.data_fabric.errors import (
    DataFabricError,
    ResultTooLargeError,
    UnsupportedSourceError,
)
from app.services.data_fabric.security import DataFabricSecurity
from app.services.data_fabric.registry import resolve_adapter_spec

logger = logging.getLogger(__name__)

router = APIRouter()


_ANONYMOUS_USER_IDS = {"anonymous", "anon"}


def _is_demo_source_type(source_type: Optional[str]) -> bool:
    """#767: True iff the source_type resolves to the explicit demo/sample
    adapter (``generic``/``mock``/``sample``). Unknown types are not demo."""
    if not source_type:
        return False
    try:
        return bool(resolve_adapter_spec(source_type).is_demo)
    except DataFabricError:
        return False


def _real_user_id(user: Optional[Dict[str, Any]]) -> Optional[str]:
    """Extract a real user id from the auth dependency result.

    ``get_current_user_optional`` returns ``{"user_id": "anonymous"}`` for
    unauthenticated requests, NOT None — treating "anonymous" as a real owner
    id breaks FK constraints (no users row with id='anonymous') and silently
    scopes rows to a fake owner. Normalize sentinel ids to None here.
    """
    if not user:
        return None
    uid = user.get("user_id") or user.get("id") or user.get("sub")
    if uid is None or str(uid) in _ANONYMOUS_USER_IDS:
        return None
    return str(uid)


def _tenant_filter(query, user: Optional[Dict[str, Any]]):
    """Apply org_id tenant scoping to a DataSourceModel query (SEC-03/DATA-02).

    Before this fix, every Data Fabric route queried DataSourceModel / CatalogItemModel
    with no tenant scoping — any caller could enumerate, read, delete, or query every
    data source across all orgs. Anonymous callers (no user) now see only org-less /
    owner-less rows (legacy/global sources); authenticated callers are scoped to their
    org. Private endpoints remain allow-listed server-side only (SEC-01).
    """
    if user is None or _real_user_id(user) is None:
        # Anonymous callers may only see truly global (un-owned) sources.
        return query.filter(
            DataSourceModel.org_id.is_(None),
            DataSourceModel.owner_id.is_(None),
        )
    org_id = user.get("org_id")
    user_id = _real_user_id(user)
    if org_id is not None:
        return query.filter(
            or_(
                DataSourceModel.org_id == org_id,
                DataSourceModel.owner_id == user_id,
            )
        )
    # Authenticated user with no org claim (JWT never carries org_id today):
    # own sources + truly global ones. org_id IS NULL used to include every
    # other user's owner_id-scoped source.
    return query.filter(
        or_(
            DataSourceModel.owner_id == user_id,
            and_(
                DataSourceModel.org_id.is_(None),
                DataSourceModel.owner_id.is_(None),
            ),
        )
    )


def _require_tenant_owned(s: Optional[DataSourceModel], user: Optional[Dict[str, Any]]):
    """Authorize a single DataSource belongs to the caller's tenant (or 404).

    Returns 404 (not 403) to avoid leaking the existence of cross-tenant rows.
    """
    if s is None:
        raise HTTPException(status_code=404, detail="Data source not found")
    user_id = _real_user_id(user)
    org_id = user.get("org_id") if user else None
    if user_id is None:
        if s.org_id is not None or s.owner_id is not None:
            raise HTTPException(status_code=404, detail="Data source not found")
        return s
    if org_id is not None:
        if s.org_id == org_id or s.owner_id == user_id:
            return s
        raise HTTPException(status_code=404, detail="Data source not found")
    # Authenticated, no org: own row or truly global. The previous
    # `if org_id is not None` gate never ran because JWT has no org_id.
    if s.owner_id == user_id or (s.org_id is None and s.owner_id is None):
        return s
    raise HTTPException(status_code=404, detail="Data source not found")


def _authorize_catalog_item(db: Session, item_id: str, user: Optional[Dict[str, Any]]):
    """Authorize catalog-item access for the caller's tenant (or 404).

    Cross-tenant access to preview/query/materialize previously had NO guard —
    any caller could read or materialize any catalog item by id. We resolve the
    item's DataSource and apply the same tenant check as the source routes.
    Returns 404 (not 403) to avoid leaking cross-tenant row existence.
    """
    item = db.query(CatalogItemModel).filter(CatalogItemModel.id == item_id).first()
    if item is None:
        raise HTTPException(status_code=404, detail="Catalog item not found")
    src = db.query(DataSourceModel).filter(DataSourceModel.id == item.source_id).first()
    _require_tenant_owned(src, user)
    return item


def _require_existing_session_owner(
    db: Session,
    session_id: str,
    user: Optional[Dict[str, Any]],
    owner_token: Optional[str],
) -> None:
    """Block materialize into someone else's existing Conversation.

    A session_id with no Conversation row is allowed (same as the first chat
    turn creating store keys). If the row exists, the caller must match
    user_id or X-Session-Token — otherwise this is a write-IDOR.
    """
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    from app.models.db_model import Conversation

    from app.core.auth import authorize_session_write

    conv = db.query(Conversation).filter(Conversation.id == session_id).first()
    if not authorize_session_write(conv, _real_user_id(user), owner_token):
        raise HTTPException(status_code=404, detail="Session not found")


# ── #565: sync SQLAlchemy off the event loop ──────────────────────────────
# These routes are async yet previously ran sync Session ORM directly on the
# event loop — under DB latency / pool contention a sync pool acquire can
# stall the loop up to pool_timeout=30s, freezing every concurrent SSE/WS
# stream (the same failure mode #386/#421/#425 eliminated elsewhere). The
# sync Session is not thread-safe, so each offload creates its own
# SessionLocal() INSIDE the worker thread (the _run_workflow_engine pattern);
# the injected request-scoped session never crosses threads. Tenant-guard
# semantics (SEC-03/DATA-02 in _require_tenant_owned / _authorize_catalog_item)
# are unchanged — only the execution thread moves.


def _run_sync_orm(fn):
    """Run ``fn(session)`` — a route's sync ORM work — in a worker thread
    with its own SessionLocal(). Returns the plain data the closure builds."""
    def _worker():
        with SessionLocal() as thread_db:
            return fn(thread_db)

    return asyncio.to_thread(_worker)


# C3 修复（ADR-0094 §10 / 审计）：此前每个请求 ``asyncio.run`` 新建事件循环，
# 与 loop-绑定的 RedisSessionDataManager 单例相互竞争（并发时 _ensure_connected
# 无锁 aclose 他人正在 await 的客户端 → 普通并发负载下物化误报 redis-unavailable）。
# 现在全部 manager 协程跑在一个常驻 worker loop 上：Redis 客户端绑定一次即稳定。
_MANAGER_LOOP: Optional[asyncio.AbstractEventLoop] = None
_MANAGER_LOOP_LOCK = threading.Lock()


def _get_manager_loop() -> asyncio.AbstractEventLoop:
    global _MANAGER_LOOP
    with _MANAGER_LOOP_LOCK:
        if _MANAGER_LOOP is None or _MANAGER_LOOP.is_closed():
            loop = asyncio.new_event_loop()
            t = threading.Thread(target=loop.run_forever, daemon=True, name="df-manager-loop")
            t.start()
            _MANAGER_LOOP = loop
        return _MANAGER_LOOP


def _run_async_manager(fn):
    """Run an async data_fabric manager call (``fn(session) -> coroutine``) on
    the shared manager event loop with its own SessionLocal().

    Manager 方法在 session 上做同步 SQLAlchemy I/O，不能跑应用主循环；协程
    经 run_coroutine_threadsafe 提交到常驻 df-manager-loop（会话对象自 worker
    线程顺序移交，无并发访问）。
    """
    loop = _get_manager_loop()

    def _worker():
        with SessionLocal() as thread_db:
            fut = asyncio.run_coroutine_threadsafe(fn(thread_db), loop)
            return fut.result(timeout=600)

    return asyncio.to_thread(_worker)


class CreateDataSourceRequest(BaseModel):
    name: str = Field(..., description="Data source display name")
    source_type: str = Field(..., description="Source adapter type (postgis, ogc_api, wfs, wms, wmts, arcgis)")
    endpoint_url: str = Field(..., description="Endpoint URL or database connection string")
    options: Dict[str, Any] = Field(default_factory=dict, description="Additional protocol options")


class MaterializeRequest(BaseModel):
    session_id: str = Field(..., description="Session identifier UUID")
    catalog_item_id: str = Field(..., description="Catalog item identifier")
    query_spec: Optional[QuerySpec] = Field(None, description="Optional query pushdown specification")


# ── PostGIS server-side MVT tile cache（Wave I，ADR-0094）─────────────────────
# 有界 LRU：键 (item_id, z, x, y)，值为 (gzip_bytes, fingerprint)。fingerprint
# 参与响应 ETag；catalog 版本变化后旧条目自然失效（键重建 + LRU 逐出）。
class _DfTileCache:
    def __init__(self, max_entries: int = 2048):
        from collections import OrderedDict

        self._cache: "OrderedDict[tuple, tuple]" = OrderedDict()
        self._lock = threading.Lock()
        self._max = max_entries

    def get(self, key):
        with self._lock:
            v = self._cache.get(key)
            if v is not None:
                self._cache.move_to_end(key)
            return v

    def put(self, key, value) -> None:
        with self._lock:
            self._cache[key] = value
            self._cache.move_to_end(key)
            while len(self._cache) > self._max:
                self._cache.popitem(last=False)

    def invalidate_item(self, item_id: str) -> None:
        with self._lock:
            for k in [k for k in self._cache if k[0] == item_id]:
                del self._cache[k]


_DF_TILE_CACHE = _DfTileCache()


def _df_tile_response(gz_body: bytes, fingerprint: str, if_none_match: Optional[str]) -> Response:
    """gzip MVT 响应 + fingerprint 参与 ETag + 304 支持（对齐 layer.py 契约）。"""
    import hashlib as _hashlib

    etag = '"%s"' % _hashlib.sha256(gz_body + fingerprint.encode()).hexdigest()[:16]
    headers = {
        "Content-Encoding": "gzip",
        "Cache-Control": "private, max-age=60",
        "ETag": etag,
        "X-Content-Type-Options": "nosniff",
        "X-Dataset-Fingerprint": fingerprint[:16],
    }
    if if_none_match:
        candidate = if_none_match.strip()
        if candidate == "*" or candidate.strip('"') == etag.strip('"'):
            return Response(status_code=304, headers=headers)
    return Response(
        content=gz_body,
        media_type="application/vnd.mapbox-vector-tile",
        headers=headers,
    )


@router.post("/data-fabric/sources", tags=["Data Fabric / 数据织网"])
async def create_data_source(
    req: CreateDataSourceRequest,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """注册新的地理空间数据源连接配置

    Requires authentication: an anonymous caller previously created a tenant-
    GLOBAL source (org_id NULL, owner_id NULL) that then appeared in every
    anonymous user's list and was probe/sync-able by anyone. State-changing +
    outbound-request-triggering endpoints must not be unauthenticated.

    Event-loop safety (#425, sibling of #386): the manager call drives a sync
    remote probe AND an automatic full catalog sync (requests with 5-15s
    timeouts). It runs in a worker thread via asyncio.to_thread with a
    thread-local SessionLocal() (#565: the injected sync Session is not
    thread-safe and must never cross threads).
    """
    try:
        # SSRF is always enforced at registration (ADR-0050 §5 P0). A previous
        # `allow_private` request field let any caller disable all private/loopback/
        # metadata blocking — a privilege escalation. Private endpoints must be
        # allow-listed server-side, never via the public request body.
        org_id = user.get("org_id") if user else None
        # "anonymous" is the sentinel returned by get_current_user_optional for
        # unauthenticated requests — never persist it as an owner_id (no users
        # row with that id → FK violation on Postgres).
        user_id = _real_user_id(user)

        def _create(session: Session) -> dict:
            source = data_fabric_manager.create_data_source(
                db=session,
                name=req.name,
                source_type=req.source_type,
                endpoint_url=req.endpoint_url,
                profile_options=req.options,
                allow_private=False,
                org_id=org_id,
                owner_id=user_id,
            )
            # Serialize INSIDE the worker: create_data_source commits internally
            # (and the auto catalog sync commits again), and SessionLocal has
            # expire_on_commit=True — reading source.* after the worker session
            # closes raises DetachedInstanceError, which the catch-all below
            # misreports as 400 on success (#565 review).
            return {
                "id": source.id,
                "name": source.name,
                "source_type": source.source_type,
                # #767: label demo/sample sources so synthetic data is never
                # mistaken for a real remote fetch.
                "is_demo": _is_demo_source_type(source.source_type),
                "endpoint_url": DataFabricSecurity.redact_url(source.endpoint_url),
                "status": source.status,
                "capabilities": source.capabilities_json,
                # SEC-07: the stored profile now carries the REAL credentials
                # (needed for later probe/sync/query) — always sanitize on
                # egress, including this create response.
                "connection_profile": DataFabricSecurity.sanitize_profile_dict(source.connection_profile or {}),
            }

        data_source = await _run_sync_orm(_create)
        return {"success": True, "data_source": data_source}
    except UnsupportedSourceError as e:
        # #767: unregistered/unsupported source types (csv, geojson, ...) are
        # rejected with an actionable 4xx BEFORE any probe or DB write — never
        # persisted as an "unreachable" row with a success response.
        return JSONResponse(status_code=400, content={"success": False, **e.to_dict()})
    except DataFabricError as e:
        return JSONResponse(status_code=400, content={"success": False, **e.to_dict()})
    except Exception as e:
        logger.error(f"Failed to create data source: {e}", exc_info=True)
        # 不回显原始异常（可能含连接串/内网地址）；全文仅在服务端日志。
        raise HTTPException(status_code=400, detail="数据源创建失败")


@router.get("/data-fabric/sources", tags=["Data Fabric / 数据织网"])
async def list_data_sources(
    source_type: Optional[str] = Query(None, description="Filter by source type"),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    """获取所有已注册的地理空间数据源列表"""
    def _load(session: Session) -> list[dict]:
        query = session.query(DataSourceModel)
        query = _tenant_filter(query, user)
        if source_type:
            query = query.filter(DataSourceModel.source_type == source_type)
        rows = query.order_by(DataSourceModel.created_at.desc()).all()
        # Serialize INSIDE the worker: rows are ORM instances bound to the
        # worker session; never read their attributes after it closes
        # (#565 review — same rule as create/sync/get).
        return [
            {
                "id": s.id,
                "name": s.name,
                "source_type": s.source_type,
                "endpoint_url": DataFabricSecurity.redact_url(s.endpoint_url),
                "status": s.status,
                "capabilities": s.capabilities_json,
                "connection_profile": DataFabricSecurity.sanitize_profile_dict(s.connection_profile or {}),
                "last_health_check": s.last_health_check.isoformat() if s.last_health_check else None,
            }
            for s in rows
        ]

    sources = await _run_sync_orm(_load)
    return {"sources": sources}


@router.get("/data-fabric/sources/{source_id}", tags=["Data Fabric / 数据织网"])
async def get_data_source(
    source_id: str,
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    """获取指定数据源详情"""
    def _load(session: Session):
        s = session.query(DataSourceModel).filter(DataSourceModel.id == source_id).first()
        _require_tenant_owned(s, user)
        return {
            "id": s.id,
            "name": s.name,
            "source_type": s.source_type,
            "endpoint_url": DataFabricSecurity.redact_url(s.endpoint_url),
            "status": s.status,
            "capabilities": s.capabilities_json,
            "connection_profile": DataFabricSecurity.sanitize_profile_dict(s.connection_profile or {}),
            "last_health_check": s.last_health_check.isoformat() if s.last_health_check else None,
        }

    return await _run_sync_orm(_load)


@router.delete("/data-fabric/sources/{source_id}", tags=["Data Fabric / 数据织网"])
async def delete_data_source(
    source_id: str,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """删除指定数据源及其关联目录项

    Requires authentication: DELETE is destructive and cascade-removes catalog
    items. The anonymous branch of _require_tenant_owned still matched legacy
    GLOBAL sources (org_id/owner_id both NULL), making them deletable by any
    unauthenticated caller.
    """
    def _delete(session: Session) -> None:
        s = session.query(DataSourceModel).filter(DataSourceModel.id == source_id).first()
        _require_tenant_owned(s, user)
        session.delete(s)
        session.commit()

    await _run_sync_orm(_delete)
    return {"success": True, "message": f"Data source '{source_id}' deleted successfully"}


@router.post("/data-fabric/sources/{source_id}/probe", tags=["Data Fabric / 数据织网"])
async def probe_data_source(
    source_id: str,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """探查数据源健康状况与连通性

    Requires authentication: probe triggers a server-side outbound HTTP request
    to the source endpoint — anonymous callers must not be able to initiate
    arbitrary outbound requests.

    Event-loop safety (#425/#565): the sync adapter probe (requests, 5s
    timeout) AND the DB read/commit now all run in one worker thread with its
    own session — nothing synchronous stays on the event loop.
    """
    def _probe(session: Session) -> dict:
        s = session.query(DataSourceModel).filter(DataSourceModel.id == source_id).first()
        _require_tenant_owned(s, user)

        profile = ConnectionProfile(
            id=s.id,
            name=s.name,
            source_type=s.source_type,
            url=s.endpoint_url,
            options=s.connection_profile.get("options", {}),
            allow_private=s.connection_profile.get("allow_private", False),
        )

        health_res = data_fabric_manager.probe_profile(profile)
        s.status = health_res.status
        session.commit()
        return health_res.model_dump()

    return await _run_sync_orm(_probe)


@router.post("/data-fabric/sources/{source_id}/sync", tags=["Data Fabric / 数据织网"])
async def sync_data_source_catalog(
    source_id: str,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """主动刷新/同步数据源图层元数据至 Spatial Catalog

    Requires authentication: sync triggers outbound requests against the source
    endpoint; anonymous callers must not initiate them.

    Event-loop safety (#425/#565): a full catalog sync blocks for its entire
    duration (list_datasets at a 10s timeout plus a bounded describe pool
    whose shutdown waits on the calling thread — minutes at thousands of
    datasets). The whole manager call runs in a worker thread with its own
    thread-local session; the ownership gate runs in a separate worker so its
    404 propagates outside the try/except below (unchanged semantics).
    """
    def _authorize(session: Session) -> None:
        s = session.query(DataSourceModel).filter(DataSourceModel.id == source_id).first()
        _require_tenant_owned(s, user)

    await _run_sync_orm(_authorize)
    try:
        # Serialize INSIDE the worker: sync_catalog commits before returning,
        # and SessionLocal has expire_on_commit=True — reading item.* after the
        # worker session closes raises DetachedInstanceError (#565 review)。
        # V2 (ADR-0094 §9)：sync 返回结构化增量 diff（added/updated/unchanged/
        # removed/warnings），条目序列化仍在此完成。
        def _sync_and_serialize(session: Session):
            result = data_fabric_manager.sync_catalog(session, source_id)
            if isinstance(result, dict):
                rows = result.get("items", [])
                diff = {
                    "added": result.get("added", 0),
                    "updated": result.get("updated", 0),
                    "unchanged": result.get("unchanged", 0),
                    "removed": result.get("removed", 0),
                }
                warnings = result.get("warnings", [])
            else:  # 兼容 mock/legacy list 返回
                rows = result
                diff = {}
                warnings = []
            items = [
                {"id": item.id, "name": item.name, "title": item.title}
                for item in rows
            ]
            return {"items": items, "diff": diff, "warnings": warnings}

        outcome = await _run_sync_orm(_sync_and_serialize)
        return {
            "success": True,
            "synced_count": len(outcome["items"]),
            "items": outcome["items"],
            "diff": outcome["diff"],
            "warnings": outcome["warnings"],
        }
    except Exception as e:
        logger.error(f"Catalog sync failed for source '{source_id}': {e}", exc_info=True)
        # 不回显原始异常；全文仅在服务端日志。
        raise HTTPException(status_code=400, detail="数据源目录同步失败")


@router.get("/data-fabric/catalog", tags=["Data Fabric / 数据织网"])
async def list_spatial_catalog(
    q: Optional[str] = Query(None, description="Search keyword query"),
    source_id: Optional[str] = Query(None, description="Filter by source ID"),
    geometry_type: Optional[str] = Query(None, description="Filter by geometry type"),
    feature_type: Optional[str] = Query(None, description="Filter by feature type (vector/raster)"),
    availability: Optional[str] = Query(
        None, description="Filter by availability: 'available' | 'unavailable' (ADR-0094 §9)"
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    summary: bool = Query(
        True,
        description="If true (default), strip the heavy `descriptor` / `meta_profile` "
        "JSON from the list payload; pass ?summary=false to receive the full row "
        "(backward compat).",
    ),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    """检索 Spatial Catalog 空间元数据索引目录

    Default response is a slim summary (no `descriptor`, no `meta_profile`):
    the dedicated ``GET /data-fabric/catalog/{id}/descriptor`` returns the
    full payload on demand. Pass ``?summary=false`` to opt into the legacy
    shape with both fields populated.
    """
    def _load(session: Session) -> dict:
        from app.models.data_fabric import DataSourceModel as _DS
        from sqlalchemy.orm import defer

        query = session.query(CatalogItemModel).join(
            _DS, _DS.id == CatalogItemModel.source_id
        )
        # Same tenant/owner filter as GET /data-fabric/sources. JWT has no org_id,
        # so the old `if user.get("org_id")` join never ran and dumped the catalog.
        query = _tenant_filter(query, user)

        if source_id:
            query = query.filter(CatalogItemModel.source_id == source_id)
        if geometry_type:
            query = query.filter(CatalogItemModel.geometry_type.ilike(f"%{geometry_type}%"))
        if feature_type:
            query = query.filter(CatalogItemModel.feature_type == feature_type)
        if availability:
            # ADR-0094 §9：服务端 availability 过滤（unavailable = 数据集已从
            # 源消失但保留元数据供 stale 检索）。
            query = query.filter(CatalogItemModel.availability == availability)
        if q:
            kw = f"%{q}%"
            query = query.filter(
                (CatalogItemModel.name.ilike(kw)) |
                (CatalogItemModel.title.ilike(kw)) |
                (CatalogItemModel.description.ilike(kw))
            )

        # Defer the heavy JSON columns at the ORM level so the list query
        # doesn't hydrate them; the summary path then never needs to load them.
        if summary:
            query = query.options(
                defer(CatalogItemModel.descriptor_json),
                defer(CatalogItemModel.meta_profile_json),
            )

        def _row(item):
            base = {
                "id": item.id,
                "source_id": item.source_id,
                "name": item.name,
                "title": item.title,
                "description": item.description,
                "geometry_type": item.geometry_type,
                "feature_type": item.feature_type,
                "crs": item.crs,
                "bbox": item.bbox_json,
                "availability": getattr(item, "availability", "available"),
                "updated_at": item.updated_at.isoformat() if item.updated_at else None,
            }
            if not summary:
                base["meta_profile"] = item.meta_profile_json
                base["descriptor"] = item.descriptor_json
            return base

        total = query.count()
        items = query.order_by(CatalogItemModel.updated_at.desc()).offset(offset).limit(limit).all()
        # Serialize inside the worker: with summary=false the deferred JSON
        # columns are only loadable while the session is open.
        return {
            "total": total,
            "rows": [_row(item) for item in items],
        }

    data = await _run_sync_orm(_load)

    return {
        "total": data["total"],
        "limit": limit,
        "offset": offset,
        "items": data["rows"],
    }


@router.get("/data-fabric/catalog/{item_id}", tags=["Data Fabric / 数据织网"])
async def get_catalog_item(
    item_id: str,
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    """获取指定 Spatial Catalog 项元数据"""
    def _get(session: Session) -> dict:
        item = _authorize_catalog_item(session, item_id, user)
        return {
            "id": item.id,
            "source_id": item.source_id,
            "name": item.name,
            "title": item.title,
            "description": item.description,
            "geometry_type": item.geometry_type,
            "feature_type": item.feature_type,
            "crs": item.crs,
            "bbox": item.bbox_json,
            "meta_profile": item.meta_profile_json,
            "descriptor": item.descriptor_json,
            "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        }

    return await _run_sync_orm(_get)


@router.get("/data-fabric/catalog/{item_id}/descriptor", tags=["Data Fabric / 数据织网"])
async def get_catalog_item_descriptor(
    item_id: str,
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    """获取完整的 DatasetDescriptor 契约元数据"""
    def _get(session: Session) -> dict:
        item = _authorize_catalog_item(session, item_id, user)
        return item.descriptor_json or {
            "id": item.name,
            "title": item.title,
            "description": item.description,
            "geometry_type": item.geometry_type,
            "srs": item.crs,
            "bbox": item.bbox_json,
        }

    return await _run_sync_orm(_get)


@router.get("/data-fabric/catalog/{item_id}/preview", tags=["Data Fabric / 数据织网"])
async def preview_catalog_item(
    item_id: str,
    limit: int = Query(10, ge=1, le=100),
    user: Dict[str, Any] = Depends(get_current_user),
):
    """获取 Spatial Catalog 项的有界样例数据预览

    Requires authentication: preview triggers a server-side remote fetch against
    the source endpoint, so anonymous callers must not be able to initiate it.

    Event-loop safety (#425/#565): the whole manager call (ownership gate +
    DB lookups + blocking adapter fetch) runs in a worker thread's own event
    loop with a thread-local session. Oversized remote results surface as an
    actionable 413, not an unbounded payload.
    """
    try:
        async def _preview(session: Session):
            _authorize_catalog_item(session, item_id, user)
            q_spec = QuerySpec(limit=limit)
            return await data_fabric_manager.query_catalog_item_async(session, item_id, q_spec)

        q_res = await _run_async_manager(_preview)
        return {
            "dataset_id": item_id,
            "features": q_res.features,
            "total_count": q_res.total_count,
            "schema_info": q_res.schema_info,
            "metadata": q_res.metadata,
        }
    except HTTPException:
        raise
    except ResultTooLargeError as e:
        # Oversized remote result — actionable 413 with the shrink hint.
        return JSONResponse(status_code=413, content={"success": False, **e.to_dict()})
    except DataFabricError as e:
        # #766: the remote fetch failed (unreachable / bad response / breaker
        # open) — typed 502, never an empty-but-200 "successful" payload.
        return JSONResponse(status_code=502, content={"success": False, **e.to_dict()})
    except Exception as e:
        logger.error(f"Catalog item preview failed for '{item_id}': {e}", exc_info=True)
        # 不回显原始异常；全文仅在服务端日志。
        raise HTTPException(status_code=400, detail="目录项预览失败")


@router.post("/data-fabric/catalog/{item_id}/explain", tags=["Data Fabric / 数据织网"])
async def explain_catalog_item(
    item_id: str,
    body: Optional[Dict[str, Any]] = Body(None),
    user: Dict[str, Any] = Depends(get_current_user),
):
    """Explain query plan (dry-run, ADR-0094 §13).

    返回 pushdown 划分 / 估算 / pagination 策略 / result mode / warnings /
    capability 矩阵 —— 不执行查询，不泄漏 secret/连接 URI。
    """
    from app.services.data_fabric.manager import DataFabricManager
    from app.schemas.data_fabric_schema import QuerySpec

    query_spec = None
    if body and isinstance(body.get("query_spec"), dict):
        try:
            query_spec = QuerySpec(**body["query_spec"])
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"invalid query_spec: {e}")

    async def _explain(session: Session):
        _authorize_catalog_item(session, item_id, user)
        return DataFabricManager.explain_catalog_item(session, item_id, query_spec)

    try:
        outcome = await _run_async_manager(_explain)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Explain failed for item '{item_id}': {e}", exc_info=True)
        raise HTTPException(status_code=400, detail="查询计划生成失败")
    if outcome.get("status") == "error":
        raise HTTPException(status_code=422, detail=outcome)
    return outcome


@router.get("/data-fabric/catalog/{item_id}/tiles/{z}/{x}/{y}.pbf", tags=["Data Fabric / 数据织网"])
async def get_catalog_mvt_tile(
    item_id: str,
    z: int,
    x: int,
    y: int,
    user: Dict[str, Any] = Depends(get_current_user),
    if_none_match: Optional[str] = Header(None, alias="If-None-Match"),
):
    """PostGIS 数据集的 server-side MVT 瓦片（ADR-0094 §8 / ST_AsMVT）。

    - 权限：与 /query 一致（auth + tenant 归属门）。
    - revision-aware：缓存键包含 catalog fingerprint（dataset version）；
      catalog sync 检测到版本变化自然切换到新键（旧键 LRU 逐出），
      不破坏现有 tile cache contract。
    - bounded：ST_AsMVT LIMIT 上限 + statement_timeout（Wave D serve_mvt_tile）。
    - 空瓦片（无相交要素）返回 204；命中返回 gzip + ETag（If-None-Match 304）。
    """
    import gzip as _gzip

    if not (0 <= z <= 22) or x < 0 or y < 0 or x >= (1 << z) or y >= (1 << z):
        raise HTTPException(status_code=400, detail="非法瓦片坐标")

    cache_key = (item_id, z, x, y)
    cached = _DF_TILE_CACHE.get(cache_key)
    if cached is not None:
        return _df_tile_response(cached[0], cached[1], if_none_match)

    async def _build(session: Session):

        _authorize_catalog_item(session, item_id, user)
        item = session.query(CatalogItemModel).filter(CatalogItemModel.id == item_id).first()
        if not item:
            raise ValueError(f"Catalog item '{item_id}' not found")
        ds_model = item.data_source
        if not ds_model or ds_model.source_type not in ("postgis", "postgres", "postgresql"):
            raise HTTPException(
                status_code=422,
                detail="server-side tiles 仅支持 PostGIS 数据源（该数据集类型不支持）",
            )
        conn_profile = ConnectionProfile(
            id=ds_model.id,
            name=ds_model.name,
            source_type=ds_model.source_type,
            url=ds_model.endpoint_url,
            options=ds_model.connection_profile.get("options", {}),
            allow_private=ds_model.connection_profile.get("allow_private", False),
        )
        from app.services.data_fabric.adapters.postgis_adapter import PostGISAdapter

        adapter = PostGISAdapter(conn_profile)
        tile = await asyncio.to_thread(
            adapter.serve_mvt_tile, item.name, z, x, y, timeout_s=30.0
        )
        fingerprint = item.fingerprint or "-"
        if tile is None:
            return None, fingerprint
        gz = _gzip.compress(tile, 6, mtime=0)  # 确定性 gzip（ETag 稳定）
        return gz, fingerprint

    try:
        gz, fingerprint = await _run_async_manager(_build)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"MVT tile build failed for '{item_id}' {z}/{x}/{y}: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail="瓦片生成失败")

    if gz is None:
        return Response(status_code=204)

    _DF_TILE_CACHE.put(cache_key, (gz, fingerprint))
    return _df_tile_response(gz, fingerprint, if_none_match)


@router.post("/data-fabric/catalog/{item_id}/query", tags=["Data Fabric / 数据织网"])
async def query_catalog_item(
    item_id: str,
    query_spec: QuerySpec,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """执行下推（Pushdown）选择性查询

    Requires authentication: query runs a remote fetch against the source
    endpoint, so anonymous callers must not be able to initiate it.

    Event-loop safety (#425/#565): the whole manager call (ownership gate +
    DB lookups + blocking adapter fetch) runs in a worker thread's own event
    loop with a thread-local session. Oversized remote results surface as an
    actionable 413, not an unbounded payload.
    """
    try:
        async def _query(session: Session):
            _authorize_catalog_item(session, item_id, user)
            return await data_fabric_manager.query_catalog_item_async(session, item_id, query_spec)

        q_res = await _run_async_manager(_query)
        return q_res.model_dump()
    except HTTPException:
        raise
    except ResultTooLargeError as e:
        # Oversized remote result — actionable 413 with the shrink hint.
        return JSONResponse(status_code=413, content={"success": False, **e.to_dict()})
    except DataFabricError as e:
        # #766: fetch failure ≠ empty dataset — typed 502 with the error code.
        return JSONResponse(status_code=502, content={"success": False, **e.to_dict()})
    except Exception as e:
        logger.error(f"Catalog item query failed for '{item_id}': {e}", exc_info=True)
        # 不回显原始异常；全文仅在服务端日志。
        raise HTTPException(status_code=400, detail="目录项查询失败")


@router.post("/data-fabric/materialize", tags=["Data Fabric / 数据织网"])
async def materialize_catalog_item(
    req: MaterializeRequest,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    user: Dict[str, Any] = Depends(get_current_user),
):
    """按需实例化（Materialize）数据至会话 SessionStore 并产生 ref_id 游标

    Requires authentication: materialize runs a remote fetch and writes session
    store refs, so anonymous callers must not be able to initiate it.
    """
    try:
        async def _materialize(session: Session):
            _authorize_catalog_item(session, req.catalog_item_id, user)
            _require_existing_session_owner(session, req.session_id, user, owner_token)
            return await data_fabric_manager.materialize_catalog_item(
                db=session,
                session_id=req.session_id,
                item_id=req.catalog_item_id,
                query_spec=req.query_spec,
                owner_token=owner_token,
            )

        res = await _run_async_manager(_materialize)
        # The manager now carries its own truth flag. A store-unavailable or
        # audit-commit failure is a transient infra problem (503), not a bad
        # request; the structured body lets the agent/tool retry or degrade.
        if not res.get("success"):
            return JSONResponse(status_code=503, content=res)
        return res
    except HTTPException:
        raise
    except ResultTooLargeError as e:
        # Oversized remote result — actionable 413 with the shrink hint.
        return JSONResponse(status_code=413, content={"success": False, **e.to_dict()})
    except DataFabricError as e:
        # #766: typed remote-fetch failure on the materialize path.
        return JSONResponse(status_code=502, content={"success": False, **e.to_dict()})
    except Exception as e:
        logger.error(f"Materialization failed for catalog item '{req.catalog_item_id}': {e}", exc_info=True)
        # 不回显原始异常（可能含连接串/内网地址）；全文仅在服务端日志。
        raise HTTPException(status_code=400, detail="目录项实例化失败")
