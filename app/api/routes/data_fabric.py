"""
Enterprise Geospatial Data Fabric REST Routes
"""
import logging
from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.auth import get_current_user_optional
from app.models.data_fabric import DataSourceModel, CatalogItemModel
from app.schemas.data_fabric_schema import (
    ConnectionProfile,
    QuerySpec,
)
from app.services.data_fabric.manager import data_fabric_manager
from app.services.data_fabric.security import DataFabricSecurity

logger = logging.getLogger(__name__)

router = APIRouter()


_ANONYMOUS_USER_IDS = {"anonymous", "anon"}


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
        return query.filter(DataSourceModel.org_id.is_(None))
    org_id = user.get("org_id")
    if org_id is not None:
        return query.filter(DataSourceModel.org_id == org_id)
    user_id = _real_user_id(user)
    if user_id is not None:
        # Authenticated user with no org: see their own + global sources.
        from sqlalchemy import or_
        return query.filter(
            or_(DataSourceModel.owner_id == user_id, DataSourceModel.org_id.is_(None))
        )
    return query.filter(DataSourceModel.org_id.is_(None))


def _require_tenant_owned(s: Optional[DataSourceModel], user: Optional[Dict[str, Any]]):
    """Authorize a single DataSource belongs to the caller's tenant (or 404).

    Returns 404 (not 403) to avoid leaking the existence of cross-tenant rows.
    """
    if s is None:
        raise HTTPException(status_code=404, detail="Data source not found")
    if user is None or _real_user_id(user) is None:
        if s.org_id is not None:
            raise HTTPException(status_code=404, detail="Data source not found")
        return s
    org_id = user.get("org_id")
    user_id = _real_user_id(user)
    if org_id is not None and s.org_id != org_id:
        if s.owner_id is None or (user_id is not None and s.owner_id != user_id):
            raise HTTPException(status_code=404, detail="Data source not found")
    return s


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


class CreateDataSourceRequest(BaseModel):
    name: str = Field(..., description="Data source display name")
    source_type: str = Field(..., description="Source adapter type (postgis, ogc_api, wfs, wms, wmts, arcgis)")
    endpoint_url: str = Field(..., description="Endpoint URL or database connection string")
    options: Dict[str, Any] = Field(default_factory=dict, description="Additional protocol options")


class MaterializeRequest(BaseModel):
    session_id: str = Field(..., description="Session identifier UUID")
    catalog_item_id: str = Field(..., description="Catalog item identifier")
    query_spec: Optional[QuerySpec] = Field(None, description="Optional query pushdown specification")


@router.post("/data-fabric/sources", tags=["Data Fabric / 数据织网"])
async def create_data_source(
    req: CreateDataSourceRequest,
    db: Session = Depends(get_db),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    """注册新的地理空间数据源连接配置"""
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
        source = data_fabric_manager.create_data_source(
            db=db,
            name=req.name,
            source_type=req.source_type,
            endpoint_url=req.endpoint_url,
            profile_options=req.options,
            allow_private=False,
            org_id=org_id,
            owner_id=user_id,
        )
        return {
            "success": True,
            "data_source": {
                "id": source.id,
                "name": source.name,
                "source_type": source.source_type,
                "endpoint_url": DataFabricSecurity.redact_url(source.endpoint_url),
                "status": source.status,
                "capabilities": source.capabilities_json,
                # SEC-07: the stored profile now carries the REAL credentials
                # (needed for later probe/sync/query) — always sanitize on
                # egress, including this create response.
                "connection_profile": DataFabricSecurity.sanitize_profile_dict(source.connection_profile or {}),
            },
        }
    except Exception as e:
        logger.error(f"Failed to create data source: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/data-fabric/sources", tags=["Data Fabric / 数据织网"])
async def list_data_sources(
    source_type: Optional[str] = Query(None, description="Filter by source type"),
    db: Session = Depends(get_db),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    """获取所有已注册的地理空间数据源列表"""
    query = db.query(DataSourceModel)
    query = _tenant_filter(query, user)
    if source_type:
        query = query.filter(DataSourceModel.source_type == source_type)

    sources = query.order_by(DataSourceModel.created_at.desc()).all()
    return {
        "sources": [
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
            for s in sources
        ]
    }


@router.get("/data-fabric/sources/{source_id}", tags=["Data Fabric / 数据织网"])
async def get_data_source(
    source_id: str,
    db: Session = Depends(get_db),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    """获取指定数据源详情"""
    s = db.query(DataSourceModel).filter(DataSourceModel.id == source_id).first()
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


@router.delete("/data-fabric/sources/{source_id}", tags=["Data Fabric / 数据织网"])
async def delete_data_source(
    source_id: str,
    db: Session = Depends(get_db),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    """删除指定数据源及其关联目录项"""
    s = db.query(DataSourceModel).filter(DataSourceModel.id == source_id).first()
    _require_tenant_owned(s, user)

    db.delete(s)
    db.commit()
    return {"success": True, "message": f"Data source '{source_id}' deleted successfully"}


@router.post("/data-fabric/sources/{source_id}/probe", tags=["Data Fabric / 数据织网"])
async def probe_data_source(
    source_id: str,
    db: Session = Depends(get_db),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    """探查数据源健康状况与连通性"""
    s = db.query(DataSourceModel).filter(DataSourceModel.id == source_id).first()
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
    db.commit()

    return health_res.model_dump()


@router.post("/data-fabric/sources/{source_id}/sync", tags=["Data Fabric / 数据织网"])
async def sync_data_source_catalog(
    source_id: str,
    db: Session = Depends(get_db),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    """主动刷新/同步数据源图层元数据至 Spatial Catalog"""
    s = db.query(DataSourceModel).filter(DataSourceModel.id == source_id).first()
    _require_tenant_owned(s, user)
    try:
        items = data_fabric_manager.sync_catalog(db, source_id)
        return {
            "success": True,
            "synced_count": len(items),
            "items": [{"id": item.id, "name": item.name, "title": item.title} for item in items],
        }
    except Exception as e:
        logger.error(f"Catalog sync failed for source '{source_id}': {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/data-fabric/catalog", tags=["Data Fabric / 数据织网"])
async def list_spatial_catalog(
    q: Optional[str] = Query(None, description="Search keyword query"),
    source_id: Optional[str] = Query(None, description="Filter by source ID"),
    geometry_type: Optional[str] = Query(None, description="Filter by geometry type"),
    feature_type: Optional[str] = Query(None, description="Filter by feature type (vector/raster)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    summary: bool = Query(
        True,
        description="If true (default), strip the heavy `descriptor` / `meta_profile` "
        "JSON from the list payload; pass ?summary=false to receive the full row "
        "(backward compat).",
    ),
    db: Session = Depends(get_db),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    """检索 Spatial Catalog 空间元数据索引目录

    Default response is a slim summary (no `descriptor`, no `meta_profile`):
    the dedicated ``GET /data-fabric/catalog/{id}/descriptor`` returns the
    full payload on demand. Pass ``?summary=false`` to opt into the legacy
    shape with both fields populated.
    """
    from app.models.data_fabric import DataSourceModel as _DS
    from sqlalchemy.orm import defer

    query = db.query(CatalogItemModel)

    # Tenancy guard: catalog items belong to a source, which is org-scoped
    # (DATA-P0-SEC). Unauthenticated callers used to enumerate every org's
    # descriptors; the join through DataSource.org_id now applies the same
    # tenant filter the sources endpoint already uses.
    if user and user.get("org_id"):
        query = query.join(_DS, _DS.id == CatalogItemModel.source_id).filter(
            _DS.org_id == user["org_id"]
        )

    if source_id:
        query = query.filter(CatalogItemModel.source_id == source_id)
    if geometry_type:
        query = query.filter(CatalogItemModel.geometry_type.ilike(f"%{geometry_type}%"))
    if feature_type:
        query = query.filter(CatalogItemModel.feature_type == feature_type)
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

    total = query.count()
    items = query.order_by(CatalogItemModel.updated_at.desc()).offset(offset).limit(limit).all()

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
            "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        }
        if not summary:
            base["meta_profile"] = item.meta_profile_json
            base["descriptor"] = item.descriptor_json
        return base

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_row(item) for item in items],
    }


@router.get("/data-fabric/catalog/{item_id}", tags=["Data Fabric / 数据织网"])
async def get_catalog_item(
    item_id: str,
    db: Session = Depends(get_db),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    """获取指定 Spatial Catalog 项元数据"""
    item = db.query(CatalogItemModel).filter(CatalogItemModel.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail=f"Catalog item '{item_id}' not found")
    # Tenancy: org-scoped via the parent data source. (DATA-P0-SEC)
    if user and user.get("org_id"):
        src = db.query(DataSourceModel).filter(DataSourceModel.id == item.source_id).first()
        if src and src.org_id and src.org_id != user["org_id"]:
            raise HTTPException(status_code=404, detail=f"Catalog item '{item_id}' not found")

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


@router.get("/data-fabric/catalog/{item_id}/descriptor", tags=["Data Fabric / 数据织网"])
async def get_catalog_item_descriptor(
    item_id: str,
    db: Session = Depends(get_db),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    """获取完整的 DatasetDescriptor 契约元数据"""
    item = db.query(CatalogItemModel).filter(CatalogItemModel.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail=f"Catalog item '{item_id}' not found")
    if user and user.get("org_id"):
        src = db.query(DataSourceModel).filter(DataSourceModel.id == item.source_id).first()
        if src and src.org_id and src.org_id != user["org_id"]:
            raise HTTPException(status_code=404, detail=f"Catalog item '{item_id}' not found")

    return item.descriptor_json or {
        "id": item.name,
        "title": item.title,
        "description": item.description,
        "geometry_type": item.geometry_type,
        "srs": item.crs,
        "bbox": item.bbox_json,
    }


@router.get("/data-fabric/catalog/{item_id}/preview", tags=["Data Fabric / 数据织网"])
async def preview_catalog_item(
    item_id: str,
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    """获取 Spatial Catalog 项的有界样例数据预览"""
    try:
        _authorize_catalog_item(db, item_id, user)
        q_spec = QuerySpec(limit=limit)
        q_res = data_fabric_manager.query_catalog_item(db, item_id, q_spec)
        return {
            "dataset_id": item_id,
            "features": q_res.features,
            "total_count": q_res.total_count,
            "schema_info": q_res.schema_info,
            "metadata": q_res.metadata,
        }
    except Exception as e:
        logger.error(f"Catalog item preview failed for '{item_id}': {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/data-fabric/catalog/{item_id}/query", tags=["Data Fabric / 数据织网"])
async def query_catalog_item(
    item_id: str,
    query_spec: QuerySpec,
    db: Session = Depends(get_db),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    """执行下推（Pushdown）选择性查询"""
    try:
        _authorize_catalog_item(db, item_id, user)
        q_res = data_fabric_manager.query_catalog_item(db, item_id, query_spec)
        return q_res.model_dump()
    except Exception as e:
        logger.error(f"Catalog item query failed for '{item_id}': {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/data-fabric/materialize", tags=["Data Fabric / 数据织网"])
async def materialize_catalog_item(
    req: MaterializeRequest,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    db: Session = Depends(get_db),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    """按需实例化（Materialize）数据至会话 SessionStore 并产生 ref_id 游标"""
    try:
        _authorize_catalog_item(db, req.catalog_item_id, user)
        res = await data_fabric_manager.materialize_catalog_item(
            db=db,
            session_id=req.session_id,
            item_id=req.catalog_item_id,
            query_spec=req.query_spec,
            owner_token=owner_token,
        )
        # The manager now carries its own truth flag. A store-unavailable or
        # audit-commit failure is a transient infra problem (503), not a bad
        # request; the structured body lets the agent/tool retry or degrade.
        if not res.get("success"):
            return JSONResponse(status_code=503, content=res)
        return res
    except Exception as e:
        logger.error(f"Materialization failed for catalog item '{req.catalog_item_id}': {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))
