"""G20 owner-scoped 数值栅格源解析（数据身份，不含样式）。"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.schemas.raster_spec import (
    RasterArtifactDescriptor,
    RasterExpressionError,
    inspect_raster_artifact,
    raster_content_fingerprint,
)

#: 数据修订解析缓存：(session, ref, path, mtime_ns, size) → (revision, expire)
_REVISION_CACHE: Dict[tuple, tuple[str, float]] = {}
_REVISION_TTL_S = 5.0
_REVISION_CACHE_MAX = 512


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


def _read_dataset_tags(path: str) -> Dict[str, str]:
    import rasterio

    with rasterio.open(path) as src:
        try:
            return {str(k).lower(): str(v) for k, v in (src.tags() or {}).items()}
        except Exception:  # noqa: BLE001
            return {}


def _read_band_meta(path: str, descriptor: RasterArtifactDescriptor) -> List[Dict[str, Any]]:
    """同步读 rasterio 头 + 每波段 tag（scale/offset/unit/角色）。

    波段角色**没有可靠元数据就只称第 N 波段**：只有显式 tag 声明且在封闭
    词表内才给出 role，绝不从 index/dtype 猜红/近红外/地表温度。
    """
    import rasterio

    known_roles = {"red", "green", "blue", "nir", "swir", "thermal", "elevation", "temperature"}
    out: List[Dict[str, Any]] = []
    with rasterio.open(path) as src:
        descriptions = list(getattr(src, "descriptions", None) or [])
        scales = list(getattr(src, "scales", None) or [])
        offsets = list(getattr(src, "offsets", None) or [])
        units_attr = list(getattr(src, "units", None) or [])
        for b in range(1, int(src.count) + 1):
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
                "scale_declared": bool(tags.get("scale") or (b <= len(scales) and float(scales[b - 1] or 1) != 1.0)),
                "offset_declared": bool(tags.get("offset") or (b <= len(offsets) and float(offsets[b - 1] or 0) != 0.0)),
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
        data = raw
        res_data = data
        res_error_type = None
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

    descriptor = await asyncio.to_thread(inspect_raster_artifact, safe_path)
    if descriptor is None or descriptor.band_count <= 0:
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

    # 复用既有 ref lifecycle 失效登记（(session, ref) → path 清 tile/stats 缓存）
    from app.services.raster_tile_service import register_raster_ref

    register_raster_ref(session_id, ref_id, safe_path)

    return RasterSource(
        session_id=session_id,
        ref_id=ref_id,
        path=safe_path,
        data_revision=revision,
        content_fingerprint=fingerprint,
        descriptor=descriptor,
        band_meta=band_meta,
        ref_revision=ref_revision,
        dataset_tags=dataset_tags,
    )
