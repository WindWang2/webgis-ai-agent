"""
Enterprise Geospatial Data Fabric REST Routes
"""
import asyncio
import contextlib
import logging
import threading
from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Header, Body, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.auth import get_current_user, get_current_user_optional
from app.models.data_fabric import DataSourceModel, CatalogItemModel
from app.schemas.data_fabric_schema import (  # noqa: F401 - 模块属性保持
    CatalogDescriptorResponse,
    CatalogExplainResponse,
    CatalogFeaturesPageResponse,
    CatalogItemResponse,
    CatalogListResponse,
    CatalogPreviewResponse,
    CatalogQueryResponse,
    CreateDataSourceRequest,
    MaterializeRequest,
    MaterializeResponse,
    QuerySpec,
    SourceCreateResponse,
    SourceDeleteResponse,
    SourceDetailResponse,
    SourceListResponse,
    SourceProbeResponse,
    SourceSyncResponse,
)
from app.services.data_fabric.manager import data_fabric_manager
from app.services.data_fabric.tile_service import catalog_tile_service
from app.services.data_fabric.errors import (
    DataFabricError,
    InvalidQueryError,
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
    # Authenticated user with no org claim (tokens carry org_id only when the
    # user belongs to an org):
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
    # Authenticated, no org claim: own row or truly global.
    if s.owner_id == user_id or (s.org_id is None and s.owner_id is None):
        return s
    raise HTTPException(status_code=404, detail="Data source not found")


def _require_source_manage(
    s: DataSourceModel, user: Optional[Dict[str, Any]], *, destructive: bool
) -> None:
    """security F-10：可见 ≠ 可管理。在 ``_require_tenant_owned`` 之后调用。

    - 本人创建的源：可删除/探查/同步；
    - admin：可管理租户内全部源（含全局源）；
    - 全局源（org/owner 皆空）：仅 admin；
    - 同 org 他人源：editor 可探查/同步（非破坏性），删除仅 owner/admin。
    """
    user_id = _real_user_id(user)
    role = (user or {}).get("role") or "viewer"
    if user_id is not None and s.owner_id == user_id:
        return
    if role == "admin":
        return
    is_global = s.org_id is None and s.owner_id is None
    if not is_global and not destructive and role == "editor":
        return
    raise HTTPException(
        status_code=403, detail="Insufficient privileges for this data source"
    )


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


async def _run_async_manager_cancellable(fn):
    """``_run_async_manager`` 的真取消版（W12 断线取消）。

    客户端断开时 Starlette 取消 handler task；await 侧的取消经
    ``wrap_future`` 传播为对 ``run_coroutine_threadsafe`` future 的
    ``cancel()`` —— df-manager-loop 上的协程在下一个 await 点收到
    CancelledError（远程 fetch 的 to_thread await 被取消；阻塞线程本身
    受 adapter timeout 有界）。旧实现里后台协程会白跑到 600s 超时或
    查询自然结束 —— 任务树不随断连收敛。
    """
    loop = _get_manager_loop()
    db = await asyncio.to_thread(SessionLocal)
    try:
        fut = asyncio.run_coroutine_threadsafe(fn(db), loop)
        try:
            return await asyncio.wrap_future(fut)
        finally:
            if not fut.done():
                fut.cancel()
    finally:
        # shield：handler task 的取消不该在「关 session」这一步二次打断 ——
        # 二次取消会让 session 泄漏（close 永不执行）。
        with contextlib.suppress(Exception):
            await asyncio.shield(asyncio.to_thread(db.close))


def _df_tile_response(gz_body: bytes, fingerprint: str, if_none_match: Optional[str]) -> Response:
    """gzip MVT 响应 + fingerprint 参与 ETag + 304 支持（对齐 layer.py 契约）。

    ETag 推导统一走 ``tile_identity.compute_tile_etag``（sha256(gz+fingerprint)
    截断 16 hex —— 版本语义（数据指纹）必须进摘要输入）。
    """
    from app.services.data_fabric.tile_identity import compute_tile_etag

    etag = compute_tile_etag(gz_body, fingerprint)
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


@router.post("/data-fabric/sources", tags=["Data Fabric / 数据织网"], response_model=SourceCreateResponse)
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


@router.get("/data-fabric/sources", tags=["Data Fabric / 数据织网"], response_model=SourceListResponse)
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


@router.get("/data-fabric/sources/{source_id}", tags=["Data Fabric / 数据织网"], response_model=SourceDetailResponse)
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


@router.delete("/data-fabric/sources/{source_id}", tags=["Data Fabric / 数据织网"], response_model=SourceDeleteResponse)
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
        _require_source_manage(s, user, destructive=True)
        session.delete(s)
        session.commit()

    await _run_sync_orm(_delete)
    return {"success": True, "message": f"Data source '{source_id}' deleted successfully"}


@router.post("/data-fabric/sources/{source_id}/probe", tags=["Data Fabric / 数据织网"], response_model=SourceProbeResponse)
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
        _require_source_manage(s, user, destructive=False)

        from app.services.data_fabric.manager import _profile_from_model

        profile = _profile_from_model(s)

        health_res = data_fabric_manager.probe_profile(profile)
        s.status = health_res.status
        session.commit()
        return health_res.model_dump()

    return await _run_sync_orm(_probe)


@router.post("/data-fabric/sources/{source_id}/sync", tags=["Data Fabric / 数据织网"], response_model=SourceSyncResponse)
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
        _require_source_manage(s, user, destructive=False)

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


@router.get("/data-fabric/catalog", tags=["Data Fabric / 数据织网"], response_model=CatalogListResponse)
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


@router.get("/data-fabric/catalog/{item_id}", tags=["Data Fabric / 数据织网"], response_model=CatalogItemResponse)
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


@router.get("/data-fabric/catalog/{item_id}/descriptor", tags=["Data Fabric / 数据织网"], response_model=CatalogDescriptorResponse)
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


@router.get("/data-fabric/catalog/{item_id}/preview", tags=["Data Fabric / 数据织网"], response_model=CatalogPreviewResponse)
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


@router.post("/data-fabric/catalog/{item_id}/explain", tags=["Data Fabric / 数据织网"], response_model=CatalogExplainResponse)
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
    """目录数据集的 server-side MVT 瓦片（ADR-0094 §8 / W12 tile_service）。

    - 权限：与 /query 一致（auth + tenant 归属门）——且鉴权必须在缓存
      查找之前（API-01，跨租户字节泄漏面），该顺序在 route 固定。
    - 协议适配 only：缓存/治理解析/构建收敛全部下沉
      ``app.services.data_fabric.tile_service``（此前 route 内直接构建
      PostGISAdapter 绕过 governed resolution）。PostGIS 走 ST_AsMVT；
      其余矢量源纯 Python 回退（bbox 有界查询 + 本地编码）。
    - revision-aware：缓存键含 catalog fingerprint（数据版本）；sync 变更
      fingerprint 自然切换新键，旧键 LRU 逐出。
    - 空瓦片（无相交要素）返回 204；命中返回 gzip + ETag（If-None-Match 304）。
    """
    if not (0 <= z <= 22) or x < 0 or y < 0 or x >= (1 << z) or y >= (1 << z):
        raise HTTPException(status_code=400, detail="非法瓦片坐标")

    async def _serve_tile(session: Session):
        # API-01：鉴权必须在缓存查找**之前** —— 否则跨租户调用者用已知
        # item_id 命中缓存即可拿到字节流。
        _authorize_catalog_item(session, item_id, user)
        item = session.query(CatalogItemModel).filter(CatalogItemModel.id == item_id).first()
        if not item:
            raise ValueError(f"Catalog item '{item_id}' not found")
        ds_model = item.data_source or (
            session.query(DataSourceModel).filter(DataSourceModel.id == item.source_id).first()
        )
        return await catalog_tile_service.serve_catalog_tile(item, ds_model, z, x, y)

    try:
        result = await _run_async_manager(_serve_tile)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except UnsupportedSourceError as e:
        raise HTTPException(status_code=422, detail=str(e) or "该数据集类型不支持瓦片服务")
    except DataFabricError as e:
        # #766 同族：tile 构建失败 ≠ 空瓦片 —— typed 502，全文仅在服务端日志。
        logger.error(f"MVT tile build failed for '{item_id}' {z}/{x}/{y}: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail="瓦片生成失败")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"MVT tile build failed for '{item_id}' {z}/{x}/{y}: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail="瓦片生成失败")

    if result.gz is None:
        return Response(status_code=204)

    resp = _df_tile_response(result.gz, result.fingerprint, if_none_match)
    # 观测：缓存状态与构建路径（hit/miss/bypass × cache/server_mvt/python_fallback）。
    resp.headers["X-Tile-Cache"] = result.cache_state
    resp.headers["X-Tile-Source"] = result.source
    return resp


@router.post("/data-fabric/catalog/{item_id}/query", tags=["Data Fabric / 数据织网"], response_model=CatalogQueryResponse)
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

        q_res = await _run_async_manager_cancellable(_query)
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


@router.get("/data-fabric/catalog/{item_id}/features", tags=["Data Fabric / 数据织网"], response_model=CatalogFeaturesPageResponse)
async def get_catalog_item_features_page(
    item_id: str,
    user: Dict[str, Any] = Depends(get_current_user),
    limit: int = Query(200, ge=1, le=1000, description="页大小（要素数）"),
    cursor: Optional[str] = Query(None, max_length=512, description="不透明 keyset 游标"),
    bbox: Optional[str] = Query(None, max_length=128, description="窗口 'w,s,e,n'（经纬度）"),
    fields: Optional[str] = Query(None, max_length=1024, description="字段投影 CSV"),
    order_by: Optional[str] = Query(None, max_length=256, description="keyset 排序键，如 'name ASC'"),
):
    """目录条目小批 features 分页浏览（W12 数据平面 vNext）。

    ADR-0047 浏览半边（目录侧）：keyset cursor 翻页（稳定序 = 排序键），
    Agent/前端浏览属性与小批 feature 不再需要一次整包 FeatureCollection。
    cursor 语义沿用 V2 pushdown 管线（CursorPage → adapter keyset）；源不
    支持 keyset 时 ``next_cursor=None, has_more=False`` 诚实降级。响应携带
    ``fingerprint`` 供客户端检测翻页中途数据改版。

    键集失效（非 uniform 排序键等）→ 400 typed，绝不静默错页。
    """
    from app.services.feature_pages import FeaturePageError, parse_bbox_param, parse_fields_param

    try:
        window_bbox = list(parse_bbox_param(bbox)) if bbox else None
        field_list = parse_fields_param(fields)
    except FeaturePageError as e:
        raise HTTPException(status_code=400, detail=str(e))

    async def _page(session: Session):
        _authorize_catalog_item(session, item_id, user)
        item = session.query(CatalogItemModel).filter(CatalogItemModel.id == item_id).first()
        if not item:
            raise ValueError(f"Catalog item '{item_id}' not found")
        spec = QuerySpec(
            limit=limit,
            bbox=window_bbox,
            fields=field_list,
            order_by=order_by,
            page_kind="cursor",
            **({"cursor": cursor} if cursor else {}),
        )
        result = await data_fabric_manager.query_catalog_item_async(session, item.id, spec)
        return item, result

    try:
        item, result = await _run_async_manager_cancellable(_page)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except InvalidQueryError as e:
        # 键集失效（如 cursor + 非 uniform 排序键）→ 可操作的 400，绝不静默错页。
        raise HTTPException(status_code=400, detail={"error": "invalid_query", "message": str(e)})
    except ResultTooLargeError as e:
        return JSONResponse(status_code=413, content={"success": False, **e.to_dict()})
    except DataFabricError as e:
        return JSONResponse(status_code=502, content={"success": False, **e.to_dict()})
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Catalog features page failed for '{item_id}': {e}", exc_info=True)
        raise HTTPException(status_code=400, detail="目录项分页查询失败")

    features = result.features or []
    return CatalogFeaturesPageResponse(
        dataset_id=item.id,
        features=features,
        returned_count=result.returned_count or len(features),
        next_cursor=result.next_cursor,
        has_more=bool(result.has_more),
        fingerprint=getattr(item, "fingerprint", None),
        total_matching=result.total_matching,
        truncated=result.truncated,
    )


@router.get("/data-fabric/catalog/{item_id}/features/stream", tags=["Data Fabric / 数据织网"])
async def stream_catalog_item_features_http(
    item_id: str,
    request: Request,
    user: Dict[str, Any] = Depends(get_current_user),
    page_size: int = Query(500, ge=1, le=2000, description="adapter 内部翻页批大小"),
    limit: int = Query(50_000, ge=1, le=200_000, description="NDJSON 总行数帽（有界响应）"),
    bbox: Optional[str] = Query(None, max_length=128, description="窗口 'w,s,e,n'（经纬度）"),
    fields: Optional[str] = Query(None, max_length=1024, description="字段投影 CSV"),
):
    """NDJSON 流式取数（W12：流控/取消的生产接线）。

    ``manager.stream_catalog_item_features`` 此前零 REST 消费方 —— 本端点
    是它的第一个生产面：每行一个 GeoJSON Feature，内存上界 = 泵有界队列
    （64 条）而非数据集总量；``limit`` 封顶响应行数。终结语义诚实：正常
    结束才有 ``{"_eof": true, "count": N}`` 尾行，客户端断开/取消静默
    终止（无 _eof = truncated）。

    取消：客户端断开 → Starlette 取消生成器 → finally 里 cancel_token
    取消 → 泵线程停 + 上游迭代器 close（协作取消，绝不悬挂/泄漏）。
    鉴权在流开始前完成（_prep 在 manager-loop 上跑完 tenant 门 + adapter
    治理解析）。
    """
    from app.extensions_platform import fabric_bridge
    from app.lib.cancellation import CancellationToken, OperationCancelled
    from app.services.feature_pages import FeaturePageError, parse_bbox_param, parse_fields_param

    try:
        window_bbox = list(parse_bbox_param(bbox)) if bbox else None
        field_list = parse_fields_param(fields)
    except FeaturePageError as e:
        raise HTTPException(status_code=400, detail=str(e))

    async def _prep(session: Session):
        _authorize_catalog_item(session, item_id, user)
        return data_fabric_manager.resolve_catalog_stream(session, item_id)

    try:
        adapter, dataset_name = await _run_async_manager(_prep)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except UnsupportedSourceError as e:
        # 与目录瓦片路由同语义：源类型不支持 → 422（非 502；失败 ≠ 不支持）。
        raise HTTPException(status_code=422, detail=str(e) or "该数据集类型不支持流式取数")
    except DataFabricError as e:
        return JSONResponse(status_code=502, content={"success": False, **e.to_dict()})
    except HTTPException:
        raise

    spec = QuerySpec(limit=limit, bbox=window_bbox, fields=field_list)
    cancel_token = CancellationToken(job_id=f"df-stream:{item_id}")

    async def _ndjson():
        import json as _json

        count = 0
        limit_hit = False
        pump = fabric_bridge.stream_features_from_adapter(
            adapter, dataset_name, spec,
            cancel_token=cancel_token, page_size=page_size,
        )
        try:
            # 显式 aclose（W13 断线取消链）：`async for` 在 GeneratorExit
            # （客户端断开 → Starlette 取消本生成器）时不会自动关闭内层
            # async generator —— 不显式 aclose，泵的收尾会被拖到 GC。
            async for feature in pump:
                count += 1
                yield _json.dumps(feature, ensure_ascii=False, separators=(",", ":"), default=str) + "\n"
                if count >= limit:
                    limit_hit = True
                    break
            # 终结诚实：limit 截断必须显式标注 —— 拿满 N 行 ≠ 数据集读完。
            yield _json.dumps(
                {"_eof": True, "count": count, "limit_reached": limit_hit},
                separators=(",", ":"),
            ) + "\n"
        except OperationCancelled:
            # 客户端断开驱动的协作取消：静默终止（无 _eof 尾行 = truncated）。
            pass
        except DataFabricError as e:
            # 源中途失败：无 _eof 已可判 truncated，但客户端无法区分「触帽
            # 截断」与「源炸了」—— 显式 _error 尾行区分两种不完整。
            d = e.to_dict()
            yield _json.dumps(
                {"_error": d.get("error_type", "DataFabricError"), "error": d.get("error"), "count": count},
                ensure_ascii=False, separators=(",", ":"),
            ) + "\n"
        finally:
            cancel_token.cancel("stream closed")
            with contextlib.suppress(Exception):
                await pump.aclose()

    return StreamingResponse(
        _ndjson(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/data-fabric/materialize", tags=["Data Fabric / 数据织网"], response_model=MaterializeResponse)
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
