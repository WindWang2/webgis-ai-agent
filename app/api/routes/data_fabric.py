"""
Enterprise Geospatial Data Fabric REST Routes
"""
import logging
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Header
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.data_fabric import DataSourceModel, CatalogItemModel
from app.schemas.data_fabric_schema import (
    ConnectionProfile,
    DatasetDescriptor,
    QuerySpec,
    QueryResult,
    DataFabricHealth,
)
from app.services.data_fabric.manager import data_fabric_manager
from app.services.data_fabric.security import DataFabricSecurity

logger = logging.getLogger(__name__)

router = APIRouter()


class CreateDataSourceRequest(BaseModel):
    name: str = Field(..., description="Data source display name")
    source_type: str = Field(..., description="Source adapter type (postgis, ogc_api, wfs, wms, wmts, arcgis)")
    endpoint_url: str = Field(..., description="Endpoint URL or database connection string")
    options: Dict[str, Any] = Field(default_factory=dict, description="Additional protocol options")
    allow_private: bool = Field(False, description="Allow private subnets/loopback connection (SSRF exception)")


class MaterializeRequest(BaseModel):
    session_id: str = Field(..., description="Session identifier UUID")
    catalog_item_id: str = Field(..., description="Catalog item identifier")
    query_spec: Optional[QuerySpec] = Field(None, description="Optional query pushdown specification")


@router.post("/data-fabric/sources", tags=["Data Fabric / 数据织网"])
async def create_data_source(
    req: CreateDataSourceRequest,
    db: Session = Depends(get_db),
):
    """注册新的地理空间数据源连接配置"""
    try:
        source = data_fabric_manager.create_data_source(
            db=db,
            name=req.name,
            source_type=req.source_type,
            endpoint_url=req.endpoint_url,
            profile_options=req.options,
            allow_private=req.allow_private,
        )
        return {
            "success": True,
            "data_source": {
                "id": source.id,
                "name": source.name,
                "source_type": source.source_type,
                "endpoint_url": source.endpoint_url,
                "status": source.status,
                "capabilities": source.capabilities_json,
                "connection_profile": source.connection_profile,
            },
        }
    except Exception as e:
        logger.error(f"Failed to create data source: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/data-fabric/sources", tags=["Data Fabric / 数据织网"])
async def list_data_sources(
    source_type: Optional[str] = Query(None, description="Filter by source type"),
    db: Session = Depends(get_db),
):
    """获取所有已注册的地理空间数据源列表"""
    query = db.query(DataSourceModel)
    if source_type:
        query = query.filter(DataSourceModel.source_type == source_type)

    sources = query.order_by(DataSourceModel.created_at.desc()).all()
    return {
        "sources": [
            {
                "id": s.id,
                "name": s.name,
                "source_type": s.source_type,
                "endpoint_url": s.endpoint_url,
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
):
    """获取指定数据源详情"""
    s = db.query(DataSourceModel).filter(DataSourceModel.id == source_id).first()
    if not s:
        raise HTTPException(status_code=404, detail=f"Data source '{source_id}' not found")

    return {
        "id": s.id,
        "name": s.name,
        "source_type": s.source_type,
        "endpoint_url": s.endpoint_url,
        "status": s.status,
        "capabilities": s.capabilities_json,
        "connection_profile": DataFabricSecurity.sanitize_profile_dict(s.connection_profile or {}),
        "last_health_check": s.last_health_check.isoformat() if s.last_health_check else None,
    }


@router.delete("/data-fabric/sources/{source_id}", tags=["Data Fabric / 数据织网"])
async def delete_data_source(
    source_id: str,
    db: Session = Depends(get_db),
):
    """删除指定数据源及其关联目录项"""
    s = db.query(DataSourceModel).filter(DataSourceModel.id == source_id).first()
    if not s:
        raise HTTPException(status_code=404, detail=f"Data source '{source_id}' not found")

    db.delete(s)
    db.commit()
    return {"success": True, "message": f"Data source '{source_id}' deleted successfully"}


@router.post("/data-fabric/sources/{source_id}/probe", tags=["Data Fabric / 数据织网"])
async def probe_data_source(
    source_id: str,
    db: Session = Depends(get_db),
):
    """探查数据源健康状况与连通性"""
    s = db.query(DataSourceModel).filter(DataSourceModel.id == source_id).first()
    if not s:
        raise HTTPException(status_code=404, detail=f"Data source '{source_id}' not found")

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
):
    """主动刷新/同步数据源图层元数据至 Spatial Catalog"""
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
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """检索 Spatial Catalog 空间元数据索引目录"""
    query = db.query(CatalogItemModel)

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

    total = query.count()
    items = query.order_by(CatalogItemModel.updated_at.desc()).offset(offset).limit(limit).all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {
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
            for item in items
        ],
    }


@router.get("/data-fabric/catalog/{item_id}", tags=["Data Fabric / 数据织网"])
async def get_catalog_item(
    item_id: str,
    db: Session = Depends(get_db),
):
    """获取指定 Spatial Catalog 项元数据"""
    item = db.query(CatalogItemModel).filter(CatalogItemModel.id == item_id).first()
    if not item:
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
):
    """获取完整的 DatasetDescriptor 契约元数据"""
    item = db.query(CatalogItemModel).filter(CatalogItemModel.id == item_id).first()
    if not item:
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
):
    """获取 Spatial Catalog 项的有界样例数据预览"""
    try:
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
):
    """执行下推（Pushdown）选择性查询"""
    try:
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
):
    """按需实例化（Materialize）数据至会话 SessionStore 并产生 ref_id 游标"""
    try:
        res = await data_fabric_manager.materialize_catalog_item(
            db=db,
            session_id=req.session_id,
            item_id=req.catalog_item_id,
            query_spec=req.query_spec,
            owner_token=owner_token,
        )
        return {"success": True, **res}
    except Exception as e:
        logger.error(f"Materialization failed for catalog item '{req.catalog_item_id}': {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))
