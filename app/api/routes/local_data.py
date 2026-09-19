"""本地地理数据只读资源接口（行政区 SHP + OSM 主题 GPKG）。

与工具层共用同一 service 实现（app.tools.local_admin / app.services.local_osm），
只读 GET；写侧（预处理）走 manage.py osm-ingest，不在 HTTP 面。

audit #836: 三个查询 handler 都做同步 SHP/GPKG 读取 + GeoJSON 序列化
（冷读中国行政区 SHP / pyogrio read_dataframe 可达数百 ms-秒级）——
与 upload/task/project/map 路由同款纪律，全部经 asyncio.to_thread 出离
事件循环，避免冻结全部并发 SSE/WS 流（#386 系列）。
"""
import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth import get_current_user
from app.services.local_osm import THEME_SPECS, catalog, query_osm_features
from app.tools.local_admin import LEVELS, query_admin_boundary, query_child_districts
from app.schemas.local_data_schema import (
    AdminBoundaryResponse,
    AdminChildrenResponse,
    OsmCatalogResponse,
    OsmFeaturesResponse,
)

router = APIRouter()


async def _local_data_budget(_user: dict = Depends(get_current_user)) -> None:
    """数据面 per-user 预算（与 layer.py _layer_data_budget 同纪律）。

    /local-data/* 在全局限流中豁免，但豁免必须换作用域限制：每个查询都是
    同步 SHP/GPKG 读取 + GeoJSON 序列化（数百 ms-秒级），认证用户高并发
    打满默认 executor 线程池会拖垮所有依赖 to_thread 的路径。每用户
    120 次/分钟；Redis 限流器缺席时 fail-open（与全局限流同语义）。
    """
    from app.core.rate_limiter import get_rate_limiter

    limiter = await get_rate_limiter()
    if not await limiter.is_allowed(
        f"local_data:{_user.get('user_id') or 'anon'}",
        max_requests=120,
        window_seconds=60,
    ):
        raise HTTPException(
            status_code=429,
            detail="Local data request budget exhausted; retry shortly",
        )


@router.get("/admin/{level}/boundary", response_model=AdminBoundaryResponse)
async def get_admin_boundary(
    level: str,
    name: Optional[str] = None,
    adcode: Optional[str] = None,
    to_wgs84: bool = False,
    simplified: bool = False,
    _user: dict = Depends(_local_data_budget),
) -> AdminBoundaryResponse:
    """GET 查询参数用朴素默认值（直接调用路由函数时 Query() 对象会泄漏为实参）。"""
    if level not in LEVELS:
        return {"error": f"不支持的级别: {level}（可选 {', '.join(LEVELS)}）"}
    return await asyncio.to_thread(
        query_admin_boundary,
        level, name=name, adcode=adcode, to_wgs84=to_wgs84, simplified=simplified,
    )


@router.get("/admin/children", response_model=AdminChildrenResponse)
async def get_admin_children(
    parent_name: str = Query(description="上级行政区名称，如'成都市'"),
    parent_level: str = Query(default="city", pattern="^(city|province)$"),
    to_wgs84: bool = False,
    simplified: bool = False,
    _user: dict = Depends(_local_data_budget),
) -> AdminChildrenResponse:
    return await asyncio.to_thread(
        query_child_districts,
        parent_name, parent_level, to_wgs84=to_wgs84, simplified=simplified,
    )


@router.get("/osm/catalog", response_model=OsmCatalogResponse)
async def get_osm_catalog(_user: dict = Depends(get_current_user)) -> OsmCatalogResponse:
    return OsmCatalogResponse(**catalog())


@router.get("/osm/features", response_model=OsmFeaturesResponse)
async def get_osm_features(
    theme: str = Query(description=f"主题: {', '.join(THEME_SPECS)}"),
    bbox: str = Query(description="WGS84 边界框 'minx,miny,maxx,maxy'"),
    name: Optional[str] = Query(default=None, description="名称包含匹配"),
    tag: Optional[str] = Query(default=None, description="标签过滤，如 'amenity=restaurant'"),
    limit: int = Query(default=200, ge=1, le=2000),
    _user: dict = Depends(_local_data_budget),
) -> OsmFeaturesResponse:
    bbox_list = [v.strip() for v in bbox.split(",")]
    return OsmFeaturesResponse(**await asyncio.to_thread(
        query_osm_features,
        theme, bbox_list, name_like=name, tag=tag, limit=limit,
    ))
