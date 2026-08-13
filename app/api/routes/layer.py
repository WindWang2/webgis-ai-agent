"""
图层数据 API

- GET /layers/data/{ref_id}: Fetch-on-Demand 端点，Agent 执行链路的一部分。
- GET /layers/data/{ref_id}/tiles/{z}/{x}/{y}.mvt: 大 POI 矢量瓦片端点
  （Data Plane：替代超大 GeoJSON 全量下发的显示路径）。
- GET /layer-types: 图层类型枚举。
- 图层 CRUD 已移除 — Agent 通过工具链自动创建和管理图层（"Agent is Everything"）。
- 空间分析任务端点已移除 — Agent 通过 tool calling 驱动分析，不再走 REST CRUD。
"""

import asyncio
import gzip
import hashlib
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response

from app.core.auth import require_owned_session, verify_session_owner
from app.models.db_model import Conversation
from app.services.mvt import (
    RefDataUnavailableError,
    build_spatial_index_entry,
    encode_tile_from_index,
    single_flight,
    spatial_index_cache,
    tile_lru_cache,
)
from app.services.session_data import session_data_manager
from app.services.task_tracker import TaskTracker  # noqa: F401  (typing aid)
from app.tools._utils import async_db_session

logger = logging.getLogger(__name__)

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


async def _fetch_ref_data(session_id: str, ref_id: str, owner_token: Optional[str]) -> Any:
    """Fetch + authorize ref data (same semantics as the data endpoint)."""
    res = await session_data_manager.get_ref_data(session_id, ref_id, owner_token=owner_token)
    if not res.success:
        status_code = 403 if res.error_type == "PermissionDenied" else 404
        raise HTTPException(status_code=status_code, detail=res.error or "数据不可用")
    return res.data


def _encode_tile_cached(session_id: str, ref_id: str, z: int, x: int, y: int, data) -> bytes:
    """Sync tile pipeline: spatial-index lookup → encode → gzip → tile LRU store.

    Runs inside asyncio.to_thread. Raises RefDataUnavailableError when the
    index was evicted between the route's presence check and this build (the
    route then refetches the ref data once and retries). Encoding reuses the
    index's pre-built geometries (no per-tile GeoJSON→Shapely reconstruction).
    """
    key = (session_id, ref_id)
    entry = spatial_index_cache.get_or_build(key, lambda: build_spatial_index_entry(key, data))
    raw = encode_tile_from_index(entry, z, x, y)
    body = gzip.compress(raw)
    tile_lru_cache.put((session_id, ref_id, z, x, y), body)
    return body


def _tile_response(body: bytes, if_none_match: Optional[str]) -> Response:
    """MVT response with ETag (sha256 of gzip bytes) and 304 support."""
    etag = '"%s"' % hashlib.sha256(body).hexdigest()[:16]
    if if_none_match:
        candidate = if_none_match.strip()
        if candidate == "*" or candidate.strip('"') == etag.strip('"'):
            return Response(status_code=304, headers={"ETag": etag})
    return Response(
        content=body,
        media_type="application/vnd.mapbox-vector-tile",
        headers={
            "Content-Encoding": "gzip",
            "Cache-Control": "private, max-age=300",  # ref 数据会话内不可变
            "ETag": etag,
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/layers/data/{ref_id}/tiles/{z}/{x}/{y}.mvt", tags=["图层数据"])
async def get_mvt_tile(
    ref_id: str,
    z: int,
    x: int,
    y: int,
    session_id: str = Query(..., min_length=8, max_length=128, description="会话 ID"),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _conv: Conversation = Depends(require_owned_session),
    if_none_match: Optional[str] = Header(None, alias="If-None-Match"),
):
    """以 MVT 瓦片形式返回会话引用数据（Data Plane 显示路径，全几何类型）。

    权限语义与 /layers/data/{ref_id} 完全一致（require_owned_session +
    owner_token）。性能路径：tile LRU 命中直接返回；未命中走 single-flight
    去重后，在 asyncio.to_thread 中完成索引查询 + 编码 + gzip。空瓦片返回
    合法的空 MVT message（无 layer）。响应 gzip 压缩并携带 ETag，
    支持 If-None-Match 条件请求（304）。
    """
    if not ref_id or len(ref_id) > 128 or any(c.isspace() for c in ref_id):
        raise HTTPException(status_code=400, detail="非法 ref_id")
    if not (0 <= z <= 20) or x < 0 or y < 0 or x >= (1 << z) or y >= (1 << z):
        raise HTTPException(status_code=400, detail="非法瓦片坐标")

    cache_key = (session_id, ref_id, z, x, y)

    # 1) tile LRU cache first — 命中时不做任何 ref-store 工作。
    #    缓存以 session_id 为 key 隔离，且 require_owned_session 已校验会话归属。
    cached = tile_lru_cache.get(cache_key)
    if cached is not None:
        return _tile_response(cached, if_none_match)

    # 2) single-flight：同一 (session, ref, z, x, y) 的并发请求共享一次计算。
    async def _compute() -> bytes:
        # ref 数据仅在空间索引尚未构建时拉取（含 owner_token 鉴权）。
        # 索引构建一次后即按 (session_id, ref_id) 常驻 LRU，不再重复读大 JSON。
        data = None
        if spatial_index_cache.get((session_id, ref_id)) is None:
            data = await _fetch_ref_data(session_id, ref_id, owner_token)
        try:
            return await asyncio.to_thread(_encode_tile_cached, session_id, ref_id, z, x, y, data)
        except RefDataUnavailableError:
            # 索引在检查后被 LRU 逐出的竞态：重新拉取一次并重试
            data = await _fetch_ref_data(session_id, ref_id, owner_token)
            return await asyncio.to_thread(_encode_tile_cached, session_id, ref_id, z, x, y, data)

    body = await single_flight.run(cache_key, _compute)
    return _tile_response(body, if_none_match)


@router.get("/layers/descriptor/{ref_id}", tags=["图层数据"])
async def get_layer_descriptor(
    ref_id: str,
    session_id: str = Query(..., min_length=8, max_length=128, description="会话 ID"),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _conv: Conversation = Depends(require_owned_session),
):
    """返回轻量级图层元数据描述符 (Count, Bounds, Geometry Types, MVT Capability, Est Bytes).
    
    V3 Performance: reads pre-computed descriptor from storage (computed once at
    ref creation), eliminating 100k-feature scans on every descriptor request.
    Falls back to on-the-fly compute for refs stored before V3.
    """
    if not ref_id or len(ref_id) > 128 or any(c.isspace() for c in ref_id):
        raise HTTPException(status_code=400, detail="非法 ref_id")

    # V3: Try pre-computed descriptor first
    descriptor = await session_data_manager.get_ref_descriptor(session_id, ref_id)
    if descriptor:
        # Auth check: verify ownership via the existing get_ref_data path
        # (descriptor itself doesn't contain sensitive data, but access control must match)
        res = await session_data_manager.get_ref_data(session_id, ref_id, owner_token=owner_token)
        if not res.success:
            status_code = 403 if res.error_type == "PermissionDenied" else 404
            raise HTTPException(status_code=status_code, detail=res.error or "数据不可用")
        # Descriptor found and access granted
        return {
            "ref_id": descriptor["ref_id"],
            "session_id": session_id,
            "feature_count": descriptor["feature_count"],
            "point_count": descriptor["point_count"],
            "geometry_types": descriptor["geometry_types"],
            "bbox": descriptor["bbox"],
            "mvt_capable": descriptor["mvt_capable"],
            "raster_capable": False,  # descriptor doesn't store raster flag yet
            "estimated_bytes": descriptor["estimated_bytes"],
        }
    
    # Fallback: compute on-the-fly for refs without stored descriptor (pre-V3 refs)
    res = await session_data_manager.get_ref_data(session_id, ref_id, owner_token=owner_token)
    if not res.success or not res.data:
        status_code = 403 if res.error_type == "PermissionDenied" else 404
        raise HTTPException(status_code=status_code, detail=res.error or "数据不可用")

    data = res.data
    points = _extract_points(data)
    fc = data if isinstance(data, dict) and data.get("type") == "FeatureCollection" else (data.get("geojson") if isinstance(data, dict) else {})
    features = fc.get("features", []) if isinstance(fc, dict) else []

    geom_types = set()
    for f in features:
        if isinstance(f, dict) and "geometry" in f and isinstance(f["geometry"], dict):
            geom_types.add(f["geometry"].get("type", "Unknown"))

    from app.tools._utils import _feature_collection_bbox
    bbox = _feature_collection_bbox(fc if isinstance(fc, dict) else {"type": "FeatureCollection", "features": []})

    return {
        "ref_id": ref_id,
        "session_id": session_id,
        "feature_count": len(features),
        "point_count": len(points),
        "geometry_types": list(geom_types),
        "bbox": bbox,
        "mvt_capable": len(points) > 0,
        "raster_capable": isinstance(data, dict) and ("file_path" in data or "path" in data),
        "estimated_bytes": len(str(data)),
    }


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
    if not isinstance(raster_path, str) or not raster_path:
        raise HTTPException(status_code=400, detail="栅格数据缺少有效路径")

    # SEC-08 (deep-audit round 2): raster_path comes from session ref data that
    # a user can populate via materialize_dataset / skill results. Validate it
    # resolves inside the allowed data roots before rasterio.open — previously a
    # ref could point at any GeoTIFF the process can read (another session's
    # upload, cached artifacts), crossing the per-session isolation boundary.
    from app.utils.path import validate_data_path
    from app.core.config import settings

    try:
        safe_path = validate_data_path(raster_path, settings.DATA_DIR)
    except ValueError as e:
        logger.warning(f"[layer] raster tile path rejected: {e}")
        raise HTTPException(status_code=400, detail="非法栅格路径")

    png_bytes = await asyncio.to_thread(render_raster_tile, safe_path, z, x, y)
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
