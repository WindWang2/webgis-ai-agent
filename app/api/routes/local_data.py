"""本地地理数据只读资源接口（行政区 SHP + OSM 主题 GPKG）。

与工具层共用同一 service 实现（app.tools.local_admin / app.services.local_osm），
只读 GET；写侧（预处理）走 manage.py osm-ingest，不在 HTTP 面。
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.core.auth import get_current_user
from app.services.local_osm import THEME_SPECS, catalog, query_osm_features
from app.tools.local_admin import LEVELS, query_admin_boundary, query_child_districts

router = APIRouter()


@router.get("/admin/{level}/boundary")
async def get_admin_boundary(
    level: str,
    name: Optional[str] = None,
    adcode: Optional[str] = None,
    to_wgs84: bool = False,
    simplified: bool = False,
    _user: dict = Depends(get_current_user),
):
    """GET 查询参数用朴素默认值（直接调用路由函数时 Query() 对象会泄漏为实参）。"""
    if level not in LEVELS:
        return {"error": f"不支持的级别: {level}（可选 {', '.join(LEVELS)}）"}
    return query_admin_boundary(
        level, name=name, adcode=adcode, to_wgs84=to_wgs84, simplified=simplified
    )


@router.get("/admin/children")
async def get_admin_children(
    parent_name: str = Query(description="上级行政区名称，如'成都市'"),
    parent_level: str = Query(default="city", pattern="^(city|province)$"),
    to_wgs84: bool = False,
    simplified: bool = False,
    _user: dict = Depends(get_current_user),
):
    return query_child_districts(
        parent_name, parent_level, to_wgs84=to_wgs84, simplified=simplified
    )


@router.get("/osm/catalog")
async def get_osm_catalog(_user: dict = Depends(get_current_user)):
    return catalog()


@router.get("/osm/features")
async def get_osm_features(
    theme: str = Query(description=f"主题: {', '.join(THEME_SPECS)}"),
    bbox: str = Query(description="WGS84 边界框 'minx,miny,maxx,maxy'"),
    name: Optional[str] = Query(default=None, description="名称包含匹配"),
    tag: Optional[str] = Query(default=None, description="标签过滤，如 'amenity=restaurant'"),
    limit: int = Query(default=200, ge=1, le=2000),
    _user: dict = Depends(get_current_user),
):
    bbox_list = [v.strip() for v in bbox.split(",")]
    return query_osm_features(theme, bbox_list, name_like=name, tag=tag, limit=limit)
