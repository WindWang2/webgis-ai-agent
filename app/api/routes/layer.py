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
from collections import OrderedDict
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response

from app.core.auth import require_owned_session, verify_session_owner
from app.lib.geojson_serializer import serialize_geojson
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
    request: Request = None,
):
    """通过引用 ID 或别名获取会话缓存中的大数据对象（如分析产生的 GeoJSON）。"""
    if not ref_id or len(ref_id) > 128 or any(c.isspace() for c in ref_id):
        raise HTTPException(status_code=400, detail="非法 ref_id")

    res = await session_data_manager.get_ref_data(session_id, ref_id, owner_token=owner_token)
    if not res.success:
        status_code = 403 if res.error_type == "PermissionDenied" else 404
        raise HTTPException(status_code=status_code, detail=res.error or "数据不可用")
    # #590：数据面响应侧序列化（多 MB GeoJSON）不能由默认 JSONResponse 在
    # 事件循环上整包编码 —— 与 #427/#499 同族，走分块 + to_thread 序列化
    #（C 编码器分批持有 GIL，事件循环间隙保持毫秒级），再以 bytes 响应返回。
    # P-7（#880）：compact 序列化（pretty 对 50k 要素层放大 ~1.8x）+ 客户端
    # 声明 gzip 时端点级压缩（dev/直连 uvicorn 无 nginx gzip 兜底）。
    body = await serialize_geojson(res.data, pretty=False)
    vary = {"Vary": "Accept-Encoding", "X-Content-Type-Options": "nosn"}
    if request is not None and "gzip" in (request.headers.get("accept-encoding") or ""):
        gz = await asyncio.to_thread(gzip.compress, body, 6)
        return Response(
            content=gz,
            media_type="application/json",
            headers={"Content-Encoding": "gzip", **vary},
        )
    return Response(content=body, media_type="application/json", headers=vary)


def _extract_fc(data) -> Optional[dict]:
    """Extract FeatureCollection from the three stored shapes (raw FC / {geojson: FC} / {type:..., geojson: FC})."""
    if isinstance(data, dict):
        nested = data.get("geojson")
        if isinstance(nested, dict) and nested.get("type") == "FeatureCollection":
            return nested
        if data.get("type") == "FeatureCollection":
            return data
    return None


def _find_feature_by_id(fc: dict, feature_id: str) -> Optional[dict]:
    """Lookup one feature by id property; checks feature.id and common properties keys."""
    fid_str = str(feature_id)
    for feature in fc.get("features", []):
        if not isinstance(feature, dict):
            continue
        # direct feature.id
        fid = feature.get("id")
        if fid is not None and str(fid) == fid_str:
            return feature
        props = feature.get("properties") or {}
        if isinstance(props, dict):
            for key in ("id", "OBJECTID", "fid", "osm_id", "@id", "featureId", "feature_id"):
                if key in props and props[key] is not None and str(props[key]) == fid_str:
                    return feature
    return None


@router.get("/layers/data/{ref_id}/feature/{feature_id}", tags=["图层数据"])
async def get_session_layer_feature(
    ref_id: str,
    feature_id: str,
    session_id: str = Query(..., min_length=8, max_length=128, description="会话 ID"),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _conv: Conversation = Depends(require_owned_session),
):
    """Return a single GeoJSON Feature by its id property (server-side lookup, wire carries one feature)."""
    if not ref_id or len(ref_id) > 128 or any(c.isspace() for c in ref_id):
        raise HTTPException(status_code=400, detail="非法 ref_id")
    if not feature_id or len(feature_id) > 256:
        raise HTTPException(status_code=400, detail="非法 feature_id")

    # P-2（#875）：MVT 瓦片路径的进程内空间索引常驻同一 ref 的 features
    # 列表 —— 命中时零 Redis 流量、零 json.loads，直接在已解析列表上扫描
    # （鉴权与会话归属同 MVT 路由：require_owned_session 已校验）。
    from app.services.mvt import spatial_index_cache
    index_entry = spatial_index_cache.get((session_id, ref_id))
    if index_entry is not None:
        feature = await asyncio.to_thread(
            _find_feature_by_id, {"features": index_entry.features}, feature_id
        )
        if not feature:
            raise HTTPException(status_code=404, detail="要素不存在")
        body = await serialize_geojson(feature, pretty=False)
        return Response(content=body, media_type="application/json")

    res = await session_data_manager.get_ref_data(session_id, ref_id, owner_token=owner_token)
    if not res.success:
        status_code = 403 if res.error_type == "PermissionDenied" else 404
        raise HTTPException(status_code=status_code, detail=res.error or "数据不可用")

    fc = _extract_fc(res.data)
    if not fc:
        raise HTTPException(status_code=404, detail="要素不存在")

    feature = await asyncio.to_thread(_find_feature_by_id, fc, feature_id)
    if not feature:
        raise HTTPException(status_code=404, detail="要素不存在")

    body = await serialize_geojson(feature, pretty=False)
    return Response(content=body, media_type="application/json")


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


def _compute_descriptor_fallback(data) -> dict:
    """同步计算 descriptor 回退字段（pre-V3 ref / descriptor 键缺失时）。

    全要素扫描在 50k 特征量级可达数百 ms —— 与存储侧 compute_descriptor 的
    to_thread 对称（#590），必须在 worker 线程执行，不能占用事件循环。
    使用与 compute_descriptor 相同的轻量启发式预估字节数，绝不物化全量字符串。
    """
    points = _extract_points(data)
    # 与 compute_descriptor 完全对齐的 FC 抽取（裸 FC / {"geojson": FC} / {"type":..., "geojson": FC}）
    fc = data
    if isinstance(data, dict):
        nested = data.get("geojson")
        if isinstance(nested, dict):
            fc = nested
    features = fc.get("features", []) if isinstance(fc, dict) else []

    geom_types = set()
    for f in features:
        if isinstance(f, dict) and "geometry" in f and isinstance(f["geometry"], dict):
            geom_types.add(f["geometry"].get("type", "Unknown"))

    # Bbox via shared helper — also the patch target for #590 off-loop regression test.
    from app.schemas.ref_descriptor import estimate_bytes
    from app.tools._utils import _feature_collection_bbox

    # Keep a direct hook for the test's monkeypatch (the helper is the slow site).
    bbox = None
    try:
        # The test patches _feature_collection_bbox to sleep + record thread.
        maybe = _feature_collection_bbox(fc if isinstance(fc, dict) else {}, max_features=5000)
        if maybe is not None:
            bbox = maybe
    except Exception:
        bbox = None
    # GeometryCollection / beyond-5000 fallback: helper caps at 5000 and skips GC geometries.
    if bbox is None:
        from app.schemas.ref_descriptor import iter_leaf_coords as _iter

        coords: list[tuple[float, float]] = []
        for f in features:
            geom = f.get("geometry") if isinstance(f, dict) else None
            if not isinstance(geom, dict):
                continue
            gtype = geom.get("type")
            if gtype == "GeometryCollection":
                for sub in (geom.get("geometries") or []):
                    if isinstance(sub, dict):
                        for lon, lat in _iter(sub.get("coordinates")):
                            coords.append((lon, lat))
            else:
                for lon, lat in _iter(geom.get("coordinates")):
                    coords.append((lon, lat))
        if coords:
            lons = [c[0] for c in coords]
            lats = [c[1] for c in coords]
            bbox = [min(lons), min(lats), max(lons), max(lats)]
    if bbox is None and isinstance(fc, dict) and isinstance(fc.get("bbox"), list) and len(fc.get("bbox", [])) == 4:
        bbox = fc["bbox"]

    # 轻量预估：与 compute_descriptor 的 estimate_bytes 一致，永不物化全量字符串
    # 非 FC / 空 FC → 固定开销（与 compute_descriptor 分支一致）
    if isinstance(fc, dict) and fc.get("type") == "FeatureCollection" and isinstance(features, list) and len(features) > 0:
        estimated = estimate_bytes(len(features))
    else:
        estimated = estimate_bytes(0)

    # #668: attribute whitelist via shared helper (same as store-time descriptor)
    try:
        from app.schemas.ref_descriptor import collect_filterable_fields
        filterable_fields = collect_filterable_fields(features)
    except Exception:
        filterable_fields = None

    # 与 store 侧 compute_descriptor 同款有界字段 schema（旧 ref 回退路径
    # 与新路径的证据面保持一致）
    try:
        from app.schemas.ref_descriptor import collect_field_schema
        field_schema, field_schema_complete = collect_field_schema(features)
    except Exception:
        field_schema, field_schema_complete = None, True

    # B4: shared raster detection (same as compute_descriptor / is_raster_capable)
    try:
        from app.schemas.ref_descriptor import is_raster_capable
        raster_capable = is_raster_capable(data)
    except Exception:
        raster_capable = isinstance(data, dict) and ("file_path" in data or "path" in data)

    return {
        "points": points,
        "features": features,
        "geom_types": list(geom_types),
        "bbox": bbox,
        "raster_capable": raster_capable,
        "estimated_bytes": estimated,
        "filterable_fields": filterable_fields,
        "field_schema": field_schema,
        "field_schema_complete": field_schema_complete,
    }


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

    # V3: metadata-only auth + descriptor read — never hydrates the full payload.
    res = await session_data_manager.get_ref_descriptor_authorized(
        session_id, ref_id, owner_token=owner_token
    )
    if res.success:
        descriptor = res.data
        return {
            "ref_id": descriptor["ref_id"],
            "session_id": session_id,
            "feature_count": descriptor["feature_count"],
            "point_count": descriptor["point_count"],
            "geometry_types": descriptor["geometry_types"],
            "bbox": descriptor["bbox"],
            "mvt_capable": descriptor["mvt_capable"],
            "raster_capable": descriptor.get("raster_capable", False),
            "estimated_bytes": descriptor["estimated_bytes"],
            "filterable_fields": descriptor.get("filterable_fields"),
            "field_schema": descriptor.get("field_schema"),
            "field_schema_complete": descriptor.get("field_schema_complete", True),
        }
    if res.error_type == "PermissionDenied":
        raise HTTPException(status_code=403, detail=res.error or "数据不可用")
    # NotFound: pre-V3 ref without descriptor, or payload evicted — 走现有 fallback
    # （fallback 里 get_ref_data 会对 pre-V3 ref 现算 descriptor、对已 evicted ref
    #   返回 404，语义与现状一致）

    # Fallback: compute on-the-fly for refs without stored descriptor (pre-V3 refs)
    res = await session_data_manager.get_ref_data(session_id, ref_id, owner_token=owner_token)
    if not res.success or not res.data:
        status_code = 403 if res.error_type == "PermissionDenied" else 404
        raise HTTPException(status_code=status_code, detail=res.error or "数据不可用")

    # #590：回退计算整块移入 worker 线程（与 compute_descriptor 对称，永不物化大字符串）。
    computed = await asyncio.to_thread(_compute_descriptor_fallback, res.data)
    points = computed["points"]
    features = computed["features"]
    geom_types = computed["geom_types"]
    bbox = computed["bbox"]
    raster_capable = computed["raster_capable"]
    estimated_bytes = computed["estimated_bytes"]

    from app.schemas.ref_descriptor import is_mvt_capable

    return {
        "ref_id": ref_id,
        "session_id": session_id,
        "feature_count": len(features),
        "point_count": len(points),
        "geometry_types": list(geom_types),
        "bbox": bbox,
        "mvt_capable": is_mvt_capable(geom_types, len(features)),
        "raster_capable": raster_capable,
        "estimated_bytes": estimated_bytes,
        "filterable_fields": computed.get("filterable_fields"),
        "field_schema": computed.get("field_schema"),
        "field_schema_complete": computed.get("field_schema_complete", True),
    }


from app.services.raster_tile_service import render_raster_tile


@router.get("/layers/data/{ref_id}/raster-tiles/{z}/{x}/{y}.png", tags=["图层数据"])
async def get_raster_tile(
    ref_id: str,
    z: int,
    x: int,
    y: int,
    session_id: str = Query(..., min_length=8, max_length=128, description="会话 ID"),
    cmap: Optional[str] = Query(None, description="单波段着色（matplotlib 合法名，如 viridis；缺省灰度）"),
    bands: Optional[str] = Query(None, description="波段组合（1-based CSV，如 '1' 或 '3,2,1'；缺省前 3 波段）"),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _conv: Conversation = Depends(require_owned_session),
    if_none_match: Optional[str] = Header(None, alias="If-None-Match"),
):
    """以 Web Mercator XYZ PNG 瓦片形式返回栅格图层数据（Data Plane 路径）。

    P-5（#878）：与 MVT 路由同构的性能路径 —— PNG LRU 命中零 ref-store IO；
    single-flight 去重并发同瓦片；(session, ref) → safe_path 短 TTL 缓存
    免去每瓦片的 Redis 拉取 + 路径校验；响应带 ETag 支持 304。
    """
    if not ref_id or len(ref_id) > 128 or any(c.isspace() for c in ref_id):
        raise HTTPException(status_code=400, detail="非法 ref_id")
    if not (0 <= z <= 20) or x < 0 or y < 0 or x >= (1 << z) or y >= (1 << z):
        raise HTTPException(status_code=400, detail="非法瓦片坐标")

    from app.services.mvt import tile_lru_cache

    # C5：样式进缓存键（换样式 = 新缓存条目，不重算遥感产物）
    band_tuple = None
    if bands:
        try:
            band_tuple = tuple(int(b) for b in bands.split(",") if b.strip())[:3]
        except ValueError:
            raise HTTPException(status_code=400, detail="bands 必须是 1-based 波段 CSV，如 '1' 或 '3,2,1'")
        if not band_tuple or any(b < 1 for b in band_tuple):
            raise HTTPException(status_code=400, detail="bands 必须是 1-based 波段 CSV，如 '1' 或 '3,2,1'")

    cache_key = (session_id, ref_id, z, x, y, cmap or "", band_tuple or ())
    cached_png = tile_lru_cache.get(cache_key)
    if cached_png is not None:
        return _png_tile_response(cached_png, if_none_match)

    async def _compute_png() -> bytes:
        safe_path = await _resolve_raster_tile_path(session_id, ref_id, owner_token)
        png = await asyncio.to_thread(render_raster_tile, safe_path, z, x, y, 256, cmap, band_tuple)
        tile_lru_cache.put(cache_key, png)
        return png

    png_bytes = await single_flight.run(cache_key, _compute_png)
    return _png_tile_response(png_bytes, if_none_match)


# P-5（#878）：(session, ref) → 已校验 safe_path 的短 TTL 缓存。
# 栅格 ref 的 payload 只是 file_path dict，但每次读取协议昂贵（metadata +
# payload GET + WATCH/MULTI）；瓦片风暴时一屏 20-40 瓦片全部重复付费。
# 失效：TTL 5s + tile_lru_cache.invalidate_ref 联动清 PNG（ref 失效时路径
# 缓存最迟 5s 后自然过期）。
_RASTER_PATH_TTL_S = 5.0
_raster_path_cache: "OrderedDict[tuple, tuple[str, float]]" = OrderedDict()
_RASTER_PATH_CACHE_MAX = 256


async def _resolve_raster_tile_path(session_id: str, ref_id: str, owner_token: Optional[str]) -> str:
    """Resolve + validate a raster ref's file path with a short process cache."""
    import time as _time

    key = (session_id, ref_id)
    hit = _raster_path_cache.get(key)
    if hit is not None:
        path, expire_at = hit
        if _time.monotonic() <= expire_at:
            _raster_path_cache.move_to_end(key)
            return path
        _raster_path_cache.pop(key, None)

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

    _raster_path_cache[key] = (safe_path, _time.monotonic() + _RASTER_PATH_TTL_S)
    _raster_path_cache.move_to_end(key)
    while len(_raster_path_cache) > _RASTER_PATH_CACHE_MAX:
        _raster_path_cache.popitem(last=False)
    return safe_path


def _png_tile_response(png_bytes: bytes, if_none_match: Optional[str]) -> Response:
    """PNG 瓦片响应：ETag（sha256 前 16 位）+ If-None-Match 304（对齐 MVT）。"""
    etag = '"%s"' % hashlib.sha256(png_bytes).hexdigest()[:16]
    if if_none_match:
        candidate = if_none_match.strip()
        if candidate == "*" or candidate.strip('"') == etag.strip('"'):
            return Response(status_code=304, headers={"ETag": etag})
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={
            "Cache-Control": "private, max-age=300",
            "ETag": etag,
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
