"""G20 owner-scoped 数值栅格源解析（数据身份，不含样式）。"""
from __future__ import annotations

import asyncio
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.schemas.raster_spec import (
    RasterArtifactDescriptor,
    RasterBandInfo,
    RasterExpressionError,
    raster_content_fingerprint,
)
from app.services.raster_authoring.budgets import MAX_BAND_COUNT_METADATA

#: 数据修订解析缓存：(session, ref, path, mtime_ns, size) → (revision, expire)
_REVISION_CACHE: Dict[tuple, tuple[str, float]] = {}
_REVISION_TTL_S = 5.0
_REVISION_CACHE_MAX = 512

#: 已解析源缓存：瓦片风暴里每个瓦片都要解析一次源身份；旧 layer.py 路径
#: 有 5s 的 (session,ref)→safe_path 短 TTL 缓存，G20 路径同样按 (文件时间戳,
#: 大小) 缓存描述子/波段元数据，避免每瓦片重跑全幅统计读。
_SOURCE_CACHE: Dict[tuple, tuple["RasterSource", float]] = {}
_SOURCE_TTL_S = 5.0
_SOURCE_CACHE_MAX = 128

#: 描述子统计只读前 N 个波段、最长边 ≤1024；超出部分如实披露截断，
#: 绝不为 224 波段高光谱一次性分配 GB 级数组。
_DESCRIPTOR_MAX_BANDS = 16
_DESCRIPTOR_MAX_SIDE = 1024


def _cache_put(cache: Dict[tuple, tuple[str, float]], key: tuple, value: str) -> None:
    if len(cache) >= _REVISION_CACHE_MAX:
        cache.clear()
    cache[key] = (value, time.monotonic() + _REVISION_TTL_S)


def _cache_get(cache: Dict[tuple, tuple[str, float]], key: tuple) -> Optional[str]:
    hit = cache.get(key)
    if hit and time.monotonic() <= hit[1]:
        return hit[0]
    cache.pop(key, None)
    return None


@dataclass(frozen=True)
class RasterSource:
    """一次解析出的、可复核的源身份。

    ``data_revision`` 与 ``content_fingerprint`` 是两种不同用途的指纹：
    前者绑定 MapSpec 的数据版本，后者是工件平面的内容身份（analysis-reuse
    键），本任务绝不合并成「万能指纹」。
    """

    session_id: str
    ref_id: str
    path: str
    data_revision: str
    content_fingerprint: Optional[str]
    descriptor: RasterArtifactDescriptor
    band_meta: List[Dict[str, Any]] = field(default_factory=list)
    ref_revision: Optional[int] = None
    dataset_tags: Dict[str, str] = field(default_factory=dict)
    bands_truncated: bool = False

    @property
    def band_count(self) -> int:
        return int(self.descriptor.band_count or len(self.band_meta))

    @property
    def crs(self) -> Optional[str]:
        return self.descriptor.crs

    def band(self, index: int) -> Dict[str, Any]:
        for item in self.band_meta:
            if int(item.get("index")) == int(index):
                return item
        return {"index": index, "label": f"band {index}"}


@contextmanager
def _open(path: str):
    """与 raster_tile_service 同款：GDAL env 覆盖整个 open+read 生命周期。"""
    from app.lib.geo_raster.env import open_raster, rasterio_env

    with rasterio_env(), open_raster(path) as src:
        yield src


def _inspect_bounded(path: str) -> tuple[RasterArtifactDescriptor, bool]:
    """有界描述子：最多读前 16 个波段、最长边 ≤1024 的降采样样本。

    ``inspect_raster_artifact`` 会按 ``src.count`` 全波段解码；高光谱影像
    会一次分配 GB 级数组。本函数把统计读限制在展示所需的前 N 个波段，
    但 ``band_count`` 仍报告真实波段数，截断事实由调用方披露。
    """
    import numpy as np
    from rasterio.enums import Resampling

    with _open(path) as src:
        count = int(src.count)
        indexes = list(range(1, min(count, _DESCRIPTOR_MAX_BANDS) + 1))
        scale = min(1.0, _DESCRIPTOR_MAX_SIDE / float(max(src.width, src.height)))
        out_h = max(1, int(round(src.height * scale)))
        out_w = max(1, int(round(src.width * scale)))
        data = src.read(indexes=indexes, out_shape=(len(indexes), out_h, out_w),
                        masked=True, resampling=Resampling.average)
        arr = np.ma.getdata(data)
        mask = np.ma.getmaskarray(data)
        bands: List[RasterBandInfo] = []
        for i, band in enumerate(indexes):
            valid = np.isfinite(arr[i]) & ~mask[i]
            vmin = float(arr[i][valid].min()) if valid.any() else None
            vmax = float(arr[i][valid].max()) if valid.any() else None
            bands.append(RasterBandInfo(
                index=band, dtype=str(src.dtypes[band - 1]),
                vmin=vmin, vmax=vmax,
                description=(src.descriptions[band - 1] or "") if src.descriptions else "",
            ))
        return RasterArtifactDescriptor(
            file_path=path, width=int(src.width), height=int(src.height),
            crs=str(src.crs) if src.crs else None,
            bounds=list(src.bounds) if src.bounds else None,
            dtype=str(src.dtypes[0]) if src.dtypes else "",
            nodata=(float(src.nodata) if src.nodata is not None else None),
            band_count=count, bands=bands,
            has_overviews=bool(src.overviews(1)),
            transform=[float(src.transform.a), float(src.transform.b),
                       float(src.transform.c), float(src.transform.d),
                       float(src.transform.e), float(src.transform.f)],
            resolution_x=abs(float(src.transform.a)),
            resolution_y=abs(float(src.transform.e)),
            driver=str(src.driver),
            block_size=([int(src.block_shapes[0][1]), int(src.block_shapes[0][0])]
                        if getattr(src, "block_shapes", None) else None),
        ), count > len(indexes)


def _read_dataset_tags(path: str) -> Dict[str, str]:
    with _open(path) as src:
        try:
            return {str(k).lower(): str(v) for k, v in (src.tags() or {}).items()}
        except Exception:  # noqa: BLE001
            return {}


def _numeric_flag(values: List[Any], band: int, neutral: float) -> bool:
    """坏 scale/offset tag 不能让整份栅格不可用（按未知/中性处理）。"""
    if band > len(values):
        return False
    try:
        return float(values[band - 1]) != neutral
    except (TypeError, ValueError):
        return False


def _read_band_meta(path: str, descriptor: RasterArtifactDescriptor) -> List[Dict[str, Any]]:
    """同步读 rasterio 头 + 每波段 tag（scale/offset/unit/角色）。

    波段角色**没有可靠元数据就只称第 N 波段**：只有显式 tag 声明且在封闭
    词表内才给出 role，绝不从 index/dtype 猜红/近红外/地表温度。
    """
    known_roles = {"red", "green", "blue", "nir", "swir", "thermal", "elevation", "temperature"}
    out: List[Dict[str, Any]] = []
    with _open(path) as src:
        descriptions = list(getattr(src, "descriptions", None) or [])
        scales = list(getattr(src, "scales", None) or [])
        offsets = list(getattr(src, "offsets", None) or [])
        units_attr = list(getattr(src, "units", None) or [])
        # tag 读取按头信息计，极宽的波段表也要有界；
        # band_count 仍报告真实值，缺少 tag 的波段由 band() 回退为「第 N 波段」。
        for b in range(1, min(int(src.count), MAX_BAND_COUNT_METADATA) + 1):
            tags = {}
            try:
                tags = {str(k).lower(): v for k, v in (src.tags(b) or {}).items()}
            except Exception:  # noqa: BLE001 - 驱动不支持 tag 时按未知
                tags = {}
            description = descriptions[b - 1] if b <= len(descriptions) else ""
            raw_role = str(tags.get("band_role") or tags.get("role") or "").strip().lower()
            role = raw_role if raw_role in known_roles else None
            unit = (
                tags.get("units") or tags.get("unit")
                or (units_attr[b - 1] if b <= len(units_attr) else None)
            )
            scale = tags.get("scale", scales[b - 1] if b <= len(scales) else 1.0)
            offset = tags.get("offset", offsets[b - 1] if b <= len(offsets) else 0.0)
            try:
                scale_f = float(scale)
                offset_f = float(offset)
            except (TypeError, ValueError):
                scale_f, offset_f = 1.0, 0.0
            info = next(
                (x for x in descriptor.bands if int(x.index) == b), None
            )
            out.append({
                "index": b,
                "label": f"band {b}",
                "description": str(description or ""),
                "dtype": str(src.dtypes[b - 1]) if b <= len(src.dtypes) else (info.dtype if info else ""),
                "unit": str(unit) if unit else None,
                "scale": scale_f,
                "offset": offset_f,
                "scale_declared": bool(tags.get("scale")) or _numeric_flag(scales, b, 1.0),
                "offset_declared": bool(tags.get("offset")) or _numeric_flag(offsets, b, 0.0),
                "role": role,
                "vmin": info.vmin if info else None,
                "vmax": info.vmax if info else None,
            })
    return out


def _resolve_data_revision(
    session_id: str, ref_id: str, path: str, payload: Dict[str, Any]
) -> tuple[str, Optional[str], Optional[int]]:
    """数据修订 = ref 已声明的 content_revision 优先，否则用内容指纹（有界）。"""
    declared = payload.get("content_revision")
    if declared is None:
        declared = payload.get("revision")
    try:
        ref_revision = int(declared) if declared is not None else None
    except (TypeError, ValueError):
        ref_revision = None
    try:
        stat = os.stat(path)
        fingerprint_key = (path, stat.st_mtime_ns, stat.st_size)
    except OSError:
        fingerprint_key = (path, 0, 0)
    cache_key = (session_id, ref_id) + fingerprint_key
    cached = _cache_get(_REVISION_CACHE, cache_key)
    fingerprint = cached
    if fingerprint is None:
        fingerprint = raster_content_fingerprint(path)
        if fingerprint:
            _cache_put(_REVISION_CACHE, cache_key, fingerprint)
    fingerprint_short = fingerprint[:16] if fingerprint else None
    if ref_revision is not None:
        return f"r{ref_revision}:{fingerprint_short or 'unknown'}", fingerprint_short, ref_revision
    return f"fp:{fingerprint_short or 'unknown'}", fingerprint_short, None


async def resolve_source(
    session_id: str,
    ref_id: str,
    owner_token: Optional[str] = None,
    *,
    trusted_dispatch: bool = False,
) -> RasterSource:
    """owner 校验 → scope 内路径 → 描述子 → 数据修订（全部可复核）。

    ``trusted_dispatch=True`` **只**由 ``@tool`` 门面使用：Pi 的 session_id 由
    ToolRegistry 从已授权请求上下文注入（模型不能指定别的 session），但
    dispatcher 不注入 owner_token。此时走 session store 的服务端内部读，
    仍是 per-session 隔离（``get(session_id, ref_id)`` 取不到别的会话数据），
    且后续还有 ``resolve_scoped_raster_path`` 的 owner-scope 路径校验。
    HTTP 路由恒用 owner_token 路径（``require_owned_session`` + get_ref_data）。
    """
    if not ref_id or len(ref_id) > 128 or any(c.isspace() for c in ref_id):
        raise RasterExpressionError(
            "SOURCE_UNAVAILABLE", "invalid raster ref_id", field="ref_id", status_code=404,
        )
    from app.services.session_data import session_data_manager

    if trusted_dispatch:
        raw = await session_data_manager.get(session_id, ref_id)
        if raw is None:
            raise RasterExpressionError(
                "SOURCE_UNAVAILABLE",
                "raster ref is not available for this session owner",
                field="ref_id", status_code=404,
            )
        if isinstance(raw, str):
            import json

            try:
                raw = json.loads(raw)
            except ValueError:
                pass
        res_data = raw
    else:
        res = await session_data_manager.get_ref_data(
            session_id, ref_id, owner_token=owner_token
        )
        if not res.success or not res.data:
            raise RasterExpressionError(
                "SOURCE_UNAVAILABLE", "raster ref is not available for this session owner",
                field="ref_id",
                status_code=404 if res.error_type != "PermissionDenied" else 403,
                correction_hint="Select a registered raster ref owned by this session.",
            )
        res_data = res.data
    payload = res_data if isinstance(res_data, dict) else {}
    raw_path = payload.get("file_path") or payload.get("path")
    if isinstance(res_data, str):
        raw_path = res_data
    if not isinstance(raw_path, str) or not raw_path:
        raise RasterExpressionError(
            "SOURCE_NOT_NUMERIC_RASTER",
            "the ref does not carry a raster file path",
            field="ref_id",
            correction_hint="This looks like a baked PNG layer; numeric band controls are unavailable.",
        )

    from app.services.scoped_raster_paths import resolve_scoped_raster_path

    try:
        safe_path = await asyncio.to_thread(
            resolve_scoped_raster_path, raw_path, session_id=session_id
        )
    except Exception as exc:  # noqa: BLE001 - 不把服务器路径回显给调用方
        raise RasterExpressionError(
            "SOURCE_PATH_REJECTED",
            "raster path is outside the authorized owner scope",
            field="ref_id", status_code=400,
        ) from exc

    # 复用既有 ref lifecycle 失效登记（(session, ref) → path 清 tile/stats 缓存）
    from app.services.raster_tile_service import register_raster_ref

    register_raster_ref(session_id, ref_id, safe_path)

    try:
        stat = os.stat(safe_path)
        cache_key = (session_id, ref_id, safe_path, stat.st_mtime_ns, stat.st_size)
    except OSError:
        cache_key = (session_id, ref_id, safe_path, 0, 0)
    cached = _SOURCE_CACHE.get(cache_key)
    if cached and time.monotonic() <= cached[1]:
        return cached[0]

    descriptor, truncated = await asyncio.to_thread(_inspect_bounded, safe_path)
    if descriptor.band_count <= 0:
        raise RasterExpressionError(
            "SOURCE_NOT_NUMERIC_RASTER",
            "ref is not a readable numeric raster",
            field="ref_id", status_code=422,
            correction_hint="Register a GeoTIFF/COG raster; baked PNG layers have no numeric bands.",
        )
    try:
        band_meta = await asyncio.to_thread(_read_band_meta, safe_path, descriptor)
        dataset_tags = await asyncio.to_thread(_read_dataset_tags, safe_path)
    except Exception as exc:  # noqa: BLE001
        raise RasterExpressionError(
            "SOURCE_UNAVAILABLE", "raster band metadata could not be read",
            field="ref_id", status_code=404,
        ) from exc

    revision, fingerprint, ref_revision = await asyncio.to_thread(
        _resolve_data_revision, session_id, ref_id, safe_path, payload
    )
    source = RasterSource(
        session_id=session_id,
        ref_id=ref_id,
        path=safe_path,
        data_revision=revision,
        content_fingerprint=fingerprint,
        descriptor=descriptor,
        band_meta=band_meta,
        ref_revision=ref_revision,
        dataset_tags=dataset_tags,
        bands_truncated=truncated or descriptor.band_count > len(band_meta),
    )
    if len(_SOURCE_CACHE) >= _SOURCE_CACHE_MAX:
        _SOURCE_CACHE.clear()
    _SOURCE_CACHE[cache_key] = (source, time.monotonic() + _SOURCE_TTL_S)
    return source
