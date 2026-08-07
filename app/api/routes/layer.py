"""
图层数据 API

- GET /layers/data/{ref_id}: Fetch-on-Demand 端点，Agent 执行链路的一部分。
- GET /layers/data/{ref_id}/tiles/{z}/{x}/{y}.mvt: 大 POI 矢量瓦片端点
  （Data Plane：替代超大 GeoJSON 全量下发的显示路径）。
- GET /layer-types: 图层类型枚举。
- 图层 CRUD 已移除 — Agent 通过工具链自动创建和管理图层（"Agent is Everything"）。
- 空间分析任务端点已移除 — Agent 通过 tool calling 驱动分析，不再走 REST CRUD。
"""

import gzip
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response

from app.core.auth import require_owned_session, verify_session_owner
from app.models.db_model import Conversation
from app.services.mvt import encode_point_tile
from app.services.session_data import session_data_manager
from app.services.task_tracker import TaskTracker  # noqa: F401  (typing aid)
from app.tools._utils import async_db_session

router = APIRouter()


async def _verify_session_owner(
    session_id: str,
    user_id,
    owner_token: Optional[str] = None,
) -> None:
    """Back-compat wrapper delegating to verify_session_owner."""
    async with async_db_session() as db:
        await verify_session_owner(db, session_id, user_id=user_id, owner_token=owner_token)


@router.get("/layers/data/{ref_id}", tags=["图层数据"])
async def get_session_layer_data(
    ref_id: str,
    session_id: str = Query(..., min_length=8, max_length=128, description="会话 ID"),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _conv: Conversation = Depends(require_owned_session),
):
    """通过引用 ID 或别名获取会话缓存中的大数据对象（如分析产生的 GeoJSON）。"""
    if not ref_id or len(ref_id) > 128 or any(c.isspace() for c in ref_id):
        raise HTTPException(status_code=400, detail="非法 ref_id")

    res = await session_data_manager.get_ref_data(session_id, ref_id, owner_token=owner_token)
    if not res.success:
        status_code = 403 if res.error_type == "PermissionDenied" else 404
        raise HTTPException(status_code=status_code, detail=res.error or "数据不可用")
    return res.data


def _extract_points(data) -> list[tuple[tuple[float, float], dict]]:
    """从会话引用数据中提取 Point 要素 [(lon, lat), properties]。

    兼容三种形状：裸 FeatureCollection、{"geojson": FC}、{"type":"poi_query", "geojson": FC}。
    非 Point 要素被跳过（MVT 编码器目前仅支持 Point）。
    """
    fc = data
    if isinstance(data, dict):
        nested = data.get("geojson")
        if isinstance(nested, dict):
            fc = nested
    if not isinstance(fc, dict) or fc.get("type") != "FeatureCollection":
        return []
    points = []
    for feature in fc.get("features", []):
        geometry = feature.get("geometry")
        if not geometry or geometry.get("type") != "Point":
            continue
        coords = geometry.get("coordinates")
        if isinstance(coords, (list, tuple)) and len(coords) >= 2 and coords[0] is not None and coords[1] is not None:
            points.append(((coords[0], coords[1]), feature.get("properties") or {}))
    return points


@router.get("/layers/data/{ref_id}/tiles/{z}/{x}/{y}.mvt", tags=["图层数据"])
async def get_mvt_tile(
    ref_id: str,
    z: int,
    x: int,
    y: int,
    session_id: str = Query(..., min_length=8, max_length=128, description="会话 ID"),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _conv: Conversation = Depends(require_owned_session),
):
    """以 MVT 瓦片形式返回会话引用数据中的 Point 要素（Data Plane 显示路径）。

    权限语义与 /layers/data/{ref_id} 完全一致（require_owned_session +
    owner_token）。空瓦片返回合法的空 MVT message（无 layer）。响应 gzip 压缩。
    """
    if not ref_id or len(ref_id) > 128 or any(c.isspace() for c in ref_id):
        raise HTTPException(status_code=400, detail="非法 ref_id")
    if not (0 <= z <= 20) or x < 0 or y < 0 or x >= (1 << z) or y >= (1 << z):
        raise HTTPException(status_code=400, detail="非法瓦片坐标")

    res = await session_data_manager.get_ref_data(session_id, ref_id, owner_token=owner_token)
    if not res.success:
        status_code = 403 if res.error_type == "PermissionDenied" else 404
        raise HTTPException(status_code=status_code, detail=res.error or "数据不可用")

    points = _extract_points(res.data)
    tile = encode_point_tile(points, z, x, y)
    body = gzip.compress(tile)
    return Response(
        content=body,
        media_type="application/x-protobuf",
        headers={
            "Content-Encoding": "gzip",
            "Cache-Control": "private, max-age=300",  # ref 数据会话内不可变
            "X-Content-Type-Options": "nosniff",
        },
    )


from app.services.raster_tile_service import render_raster_tile


@router.get("/layers/data/{ref_id}/raster-tiles/{z}/{x}/{y}.png", tags=["图层数据"])
async def get_raster_tile(
    ref_id: str,
    z: int,
    x: int,
    y: int,
    session_id: str = Query(..., min_length=8, max_length=128, description="会话 ID"),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _conv: Conversation = Depends(require_owned_session),
):
    """以 Web Mercator XYZ PNG 瓦片形式返回栅格图层数据（Data Plane 路径）。"""
    if not ref_id or len(ref_id) > 128 or any(c.isspace() for c in ref_id):
        raise HTTPException(status_code=400, detail="非法 ref_id")
    if not (0 <= z <= 20) or x < 0 or y < 0 or x >= (1 << z) or y >= (1 << z):
        raise HTTPException(status_code=400, detail="非法瓦片坐标")

    res = await session_data_manager.get_ref_data(session_id, ref_id, owner_token=owner_token)
    if not res.success or not res.data:
        status_code = 403 if res.error_type == "PermissionDenied" else 404
        raise HTTPException(status_code=status_code, detail=res.error or "栅格数据不可用")

    raster_path = res.data.get("file_path") or res.data.get("path") if isinstance(res.data, dict) else str(res.data)
    png_bytes = render_raster_tile(raster_path, z, x, y)
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/layer-types", tags=["元数据"])
def get_layer_types():
    """获取支持的图层类型列表"""
    return {
        "layer_types": [
            {"type": "vector", "description": "矢量图层", "formats": ["shapefile", "geojson", "gpx", "kml"]},
            {"type": "raster", "description": "栅格图层", "formats": ["tiff", "jpg", "png", "dem"]},
            {"type": "tile", "description": "瓦片图层", "formats": ["xyz", "wmts", "tms"]}
        ],
        "analysis_types": [
            {"type": "buffer", "description": "缓冲区分析"},
            {"type": "clip", "description": "裁剪分析"},
            {"type": "intersect", "description": "相交分析"},
            {"type": "dissolve", "description": "融合分析"},
            {"type": "union", "description": "联合分析"},
            {"type": "spatial_join", "description": "空间连接"}
        ]
    }
