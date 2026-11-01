"""OGC API - Coverages 受控 subset 读取（GOAL-02 / M3）。

链路：三态资格门（describe 声明）→ subset 协商（轴名来自服务 domainset，
bbox 只映射到可识别的经纬轴）→ 预算三重门（请求字节帽/像元帽/响应字节
帽）→ COVJSON/GeoTIFF 封闭格式解析 → 本地 COG（write_cog）→
``ref:fabric-cog/`` + fingerprint + provenance（raster_materialization）。

诚实边界（reason code 封闭）：

- ``capability_not_declared``：服务未声明 coverage 端点/格式/网格 —— 拒绝；
- ``budget_exceeded``：无 subset 的整幅网格超像元帽 —— 拒绝并给出服务
  声明的网格规模（agent 据此缩小 subset），绝不静默降采样；
- ``axis_not_identifiable``：bbox 无法映射到声明轴（轴名/值域不可判）——
  要求显式 subsets；
- ``compressed_bomb`` / ``oversize_response``：响应字节帽（bounded_get）。

CRS 语义：collection ``extent.spatial`` 按 OGC API 约定为 CRS84（默认）；
声明 ``crs`` 字段时以其为准；二者皆缺 → crs=unknown，物化被拒（不猜投影）。
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from app.services.data_fabric.errors import DataFabricError

logger = logging.getLogger(__name__)

#: 封闭 reason code（M3；测试断言依据）。
RC_CAPABILITY_NOT_DECLARED = "capability_not_declared"
RC_BUDGET_EXCEEDED = "budget_exceeded"
RC_AXIS_NOT_IDENTIFIABLE = "axis_not_identifiable"
RC_OVERSIZE_RESPONSE = "oversize_response"
RC_UNREACHABLE = "unreachable"

#: 默认预算（M8 可收紧）：像元帽 = 全网格 nx*ny*bands 上限；字节帽 = 单响应。
DEFAULT_MAX_CELLS = 4_000_000
DEFAULT_MAX_RESPONSE_BYTES = 128 * 1024 * 1024

_COVJSON_FORMAT = "application/prs.coverage+json"
_GEOTIFF_FORMATS = (
    "image/tiff; application=geotiff; profile=cloud-optimized",
    "image/tiff; application=geotiff",
    "image/tiff",
)

# 经纬轴识别的封闭名表（小写比较；轴名来自服务 domainset 声明）。
# 刻意不含 "e"/"n"（UTM Easting/Northing 的常用轴名）—— 投影坐标轴与
# 地理轴不可凭名混判（R1 P0-1：EPSG:326xx 网格曾会被贴上 CRS84 地理参考）。
_LON_AXIS_NAMES = {"x", "lon", "long", "longitude", "crs84-x", "crs84-long"}
_LAT_AXIS_NAMES = {"y", "lat", "latitude", "crs84-y", "crs84-lat"}

#: 地理 CRS 的封闭识别表（COVJSON referencing system id / URI 归一后比对）。
_GEOGRAPHIC_CRS_URIS = frozenset({
    "http://www.opengis.net/def/crs/ogc/1.3/crs84",
    "https://www.opengis.net/def/crs/ogc/1.3/crs84",
    "http://www.opengis.net/def/crs/ogc/0/crs84",
    "urn:ogc:def:crs:ogc:1.3:crs84",
    "http://www.opengis.net/def/crs/epsg/0/4326",
    "https://www.opengis.net/def/crs/epsg/0/4326",
    "urn:ogc:def:crs:epsg::4326",
    "epsg:4326",
    "ogc:crs84",
})


class CoverageReadError(DataFabricError):
    """受控读取失败（reason code 封闭；details 携带协商事实）。"""

    def __init__(self, message: str, *, reason_code: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.reason_code = reason_code
        self.details = details or {}


def _axis_kind(name: str) -> Optional[str]:
    key = str(name).strip().lower()
    if key in _LON_AXIS_NAMES:
        return "lon"
    if key in _LAT_AXIS_NAMES:
        return "lat"
    return None


def _identify_spatial_axes(axes: Dict[str, Dict[str, Any]]) -> Optional[Tuple[str, str]]:
    """声明轴 → (lon_axis, lat_axis)；不可判 → None（诚实拒绝 bbox）。"""
    lon_axis = lat_axis = None
    for name in axes:
        kind = _axis_kind(name)
        if kind == "lon" and lon_axis is None:
            lon_axis = name
        elif kind == "lat" and lat_axis is None:
            lat_axis = name
    if lon_axis and lat_axis:
        return lon_axis, lat_axis
    return None


async def read_coverage_subset(
    adapter: Any,
    dataset_id: str,
    *,
    session_id: str,
    bbox: Optional[List[float]] = None,
    subsets: Optional[Dict[str, Tuple[float, float]]] = None,
    bands: Optional[List[str]] = None,
    max_cells: int = DEFAULT_MAX_CELLS,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    title: Optional[str] = None,
    cancel_token: Any = None,
) -> Dict[str, Any]:
    """subset → bounded raster → COG ref（budget/fingerprint/provenance）。

    ``adapter``：OGCCoveragesAdapter（或测试替身，需 describe/会话面）。
    ``subsets``：{axis_name: (min, max)} —— 轴名必须来自 descriptor.axes。
    ``bbox``：仅当声明轴可识别为经/纬轴时接受（否则 typed 拒绝要求显式
    subsets —— 不猜轴序）。
    """
    desc = await asyncio.to_thread(adapter.describe, dataset_id)
    meta = desc.metadata
    cap = meta.get("capability") or {}
    if not meta.get("subset_read_ready"):
        reasons = ",".join(r.get("code", "?") for r in cap.get("reasons", []))
        raise CoverageReadError(
            f"coverage '{dataset_id}' is not subset-read-ready "
            f"(state={cap.get('state')}, reasons=[{reasons}])",
            reason_code=RC_CAPABILITY_NOT_DECLARED,
            details={"dataset_id": dataset_id, "capability": cap},
        )

    axes: Dict[str, Dict[str, Any]] = meta.get("axes") or {}
    bands_declared: List[Dict[str, Any]] = meta.get("bands") or []
    formats_supported: List[str] = meta.get("formats_supported") or []

    # ── subset 协商（轴名以服务声明为准） ─────────────────────────
    subset_params: Dict[str, Tuple[float, float]] = {}
    if subsets:
        for axis_name, window in subsets.items():
            if axis_name not in axes:
                raise CoverageReadError(
                    f"subset axis '{axis_name}' is not declared by the service "
                    f"(declared axes: {sorted(axes)})",
                    reason_code=RC_AXIS_NOT_IDENTIFIABLE,
                    details={"axis": axis_name, "declared_axes": sorted(axes)},
                )
            subset_params[axis_name] = (float(window[0]), float(window[1]))
    if bbox is not None:
        spatial = _identify_spatial_axes(axes)
        if spatial is None:
            raise CoverageReadError(
                "bbox cannot be mapped to declared axes (axis names not "
                "identifiable as lon/lat); pass explicit subsets keyed by "
                f"declared axis names: {sorted(axes)}",
                reason_code=RC_AXIS_NOT_IDENTIFIABLE,
                details={"declared_axes": sorted(axes)},
            )
        lon_axis, lat_axis = spatial
        subset_params[lon_axis] = (float(bbox[0]), float(bbox[2]))
        subset_params[lat_axis] = (float(bbox[1]), float(bbox[3]))

    # ── 像元预算（请求前，按服务声明的网格规模 + subset 收缩比） ──
    counts = {
        name: spec.get("count")
        for name, spec in axes.items()
        if isinstance(spec.get("count"), int)
    }
    if len(counts) >= 2:
        total = 1
        for name, cnt in counts.items():
            if name in subset_params:
                spec = axes[name]
                lo, hi = subset_params[name]
                first, last = spec.get("first"), spec.get("last")
                if (
                    isinstance(first, (int, float))
                    and isinstance(last, (int, float))
                    and last > first
                ):
                    frac = min(1.0, max(0.0, (hi - lo) / (last - first)))
                    total *= max(1, int(cnt * frac))
                else:
                    total *= cnt
            else:
                total *= cnt
        total *= max(1, len(bands) if bands else len(bands_declared) or 1)
        if total > max_cells:
            raise CoverageReadError(
                f"estimated grid {total} cells exceeds budget {max_cells} — "
                "narrow the subset (subsets/bbox) or raise max_cells explicitly",
                reason_code=RC_BUDGET_EXCEEDED,
                details={
                    "estimated_cells": total,
                    "max_cells": max_cells,
                    "declared_grid": counts,
                    "subset_params": {k: list(v) for k, v in subset_params.items()},
                },
            )

    # ── 输出格式（服务声明；COVJSON 优先——结构化可校验） ─────────
    if _COVJSON_FORMAT in formats_supported:
        chosen_format = _COVJSON_FORMAT
    else:
        tiff = next((f for f in formats_supported if f in _GEOTIFF_FORMATS), None)
        if tiff is None:
            raise CoverageReadError(
                "no supported output format declared by the service",
                reason_code=RC_CAPABILITY_NOT_DECLARED,
                details={"formats_declared": meta.get("formats_declared")},
            )
        chosen_format = tiff

    # ── 请求（bounded_get：Content-Length 预检 + 流式帽） ──────────
    base = adapter.url.rstrip("/")
    coverage_url = f"{base}/collections/{dataset_id}/coverage"
    params: List[Tuple[str, str]] = []
    if chosen_format == _COVJSON_FORMAT:
        params.append(("f", "json"))
    for axis_name, (lo, hi) in subset_params.items():
        params.append(("subset", f"{axis_name}({lo}:{hi})"))

    from app.services.data_fabric.security import bounded_get

    def _fetch() -> bytes:
        # requests 的重复 query key 需逐一追加（params list-of-tuples）。
        import requests as _requests

        from app.services.data_fabric.errors import SourceBadResponseError

        try:
            return bounded_get(
                adapter.session, coverage_url, params=params,
                timeout=60, max_bytes=max_response_bytes,
            )
        except SourceBadResponseError as e:
            # 分流（R1 P1-1）：字节帽族（details 携带 cap 事实）→ oversize；
            # 其余（HTTP 状态/网络）→ unreachable —— reason 语义与事实一致。
            details = getattr(e, "details", None) or {}
            if "cap" in details or "content_length" in details:
                raise CoverageReadError(
                    f"coverage response exceeds byte cap: {e}",
                    reason_code=RC_OVERSIZE_RESPONSE,
                    details={"dataset_id": dataset_id, "cap": max_response_bytes},
                ) from e
            raise CoverageReadError(
                f"coverage fetch failed: {e}",
                reason_code=RC_UNREACHABLE,
                details={"dataset_id": dataset_id},
            ) from e
        except _requests.HTTPError as e:
            # bounded_get 的 raise_for_status 路径（非 2xx 状态）。
            raise CoverageReadError(
                f"coverage fetch failed: {e}",
                reason_code=RC_UNREACHABLE,
                details={"dataset_id": dataset_id},
            ) from e

    body = await asyncio.to_thread(_fetch)
    if cancel_token is not None and cancel_token.cancelled:
        from app.lib.cancellation import OperationCancelled

        raise OperationCancelled(cancel_token.reason or "cancelled after fetch")

    # ── 解析 → 数组 + 仿射（COVJSON 封闭子集） ────────────────────
    provenance: Dict[str, Any] = {
        "source_url": _redact(coverage_url),
        "source_type": "ogc_api_coverages",
        "dataset_id": dataset_id,
        "format": chosen_format,
        "subset_params": {k: list(v) for k, v in subset_params.items()},
        "response_bytes": len(body),
    }
    if chosen_format == _COVJSON_FORMAT:
        arr, transform, crs, nodata, band_names, axes_used, bands_missing = (
            await asyncio.to_thread(
                _parse_covjson,
                body, bands=bands, bands_declared=bands_declared,
            )
        )
    else:
        arr, transform, crs, nodata, band_names, axes_used = await asyncio.to_thread(
            _parse_geotiff, body, bands=bands,
        )
        bands_missing = []

    provenance.update({
        "band_names": band_names,
        # R1 P2：请求了但服务未提供的波段如实披露（不静默丢弃）。
        "bands_missing": bands_missing,
        "axes_used": axes_used,
        "grid_shape": list(arr.shape),
        "nodata": nodata,
        "crs": crs,
        "declared_grid": counts,
    })
    if crs is None:
        raise CoverageReadError(
            "coverage CRS could not be determined from service declarations — "
            "refusing to materialize without a declared CRS",
            reason_code=RC_CAPABILITY_NOT_DECLARED,
            details={"dataset_id": dataset_id},
        )

    # ── COG 落地 + 发布（raster_materialization 纪律） ────────────
    with tempfile.TemporaryDirectory(prefix="cov-read-") as td:
        staging = Path(td)
        raw_tif = staging / "coverage_raw.tif"
        await asyncio.to_thread(_write_gtiff, arr, transform, crs, nodata, raw_tif)
        from app.lib.geo_raster.cog import write_cog

        cog_path = staging / "coverage.cog.tif"
        await asyncio.to_thread(write_cog, str(raw_tif), str(cog_path))

        from app.services.data_fabric.raster_materialization import publish_cog_artifact

        result = await publish_cog_artifact(
            session_id=session_id,
            source_path=cog_path,
            title=title or f"coverage:{dataset_id}",
            producer_tool="read_coverage_subset",
            provenance=provenance,
            redacted_source_url=provenance["source_url"],
            cancel_token=cancel_token,
        )

    result.update({
        "dataset_id": dataset_id,
        "band_names": band_names,
        "grid_shape": list(arr.shape),
        "crs": crs,
        "nodata": nodata,
        "budget": {"max_cells": max_cells, "response_bytes": len(body)},
    })
    return result


def _redact(url: str) -> str:
    from app.services.data_fabric.security import DataFabricSecurity

    return DataFabricSecurity.redact_url(url)


def _resolve_covjson_crs(doc: Dict[str, Any]) -> Optional[str]:
    """COVJSON domain.referencing → 地理 CRS（封闭识别表；不可判 → None）。

    COVJSON 的 referencing 块声明轴与 CRS 的绑定；仅识别 CRS84/EPSG:4326
    地理 CRS（本封闭子集），其余（投影 CRS/未知 URI）返回 None —— 调用方
    typed 拒绝物化（R1 P0-1：绝不把不可判 CRS 伪造成 CRS84）。
    """
    referencing = (doc.get("domain") or {}).get("referencing") or []
    if not isinstance(referencing, list):
        return None
    for ref in referencing:
        if not isinstance(ref, dict):
            continue
        system = ref.get("system") or {}
        sid = str(system.get("id") or "").strip().lower()
        if sid and sid in _GEOGRAPHIC_CRS_URIS:
            return "OGC:CRS84"
    return None


def _parse_covjson(
    body: bytes,
    *,
    bands: Optional[List[str]],
    bands_declared: List[Dict[str, Any]],
) -> Tuple[np.ndarray, Any, Any, Optional[float], List[str], Dict[str, Any], List[str]]:
    """COVJSON 封闭子集解析：Grid domain（values 轴）+ NdArray ranges。"""
    import json as _json

    from rasterio.transform import Affine

    try:
        doc = _json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, _json.JSONDecodeError) as e:
        raise CoverageReadError(
            f"COVJSON parse failed: {e}", reason_code=RC_CAPABILITY_NOT_DECLARED,
        ) from e
    domain = doc.get("domain") or {}
    axes_doc = (domain.get("axes") or {})
    if not isinstance(axes_doc, dict) or len(axes_doc) < 2:
        raise CoverageReadError(
            "COVJSON domain has fewer than 2 axes",
            reason_code=RC_CAPABILITY_NOT_DECLARED,
        )
    # 轴顺序：先 lon 后 lat（值域判别 + 名称识别；不猜其他投影轴）。
    axis_items = list(axes_doc.items())
    lon_axis = next(
        (i for i, (n, _) in enumerate(axis_items) if _axis_kind(n) == "lon"), None
    )
    lat_axis = next(
        (i for i, (n, _) in enumerate(axis_items) if _axis_kind(n) == "lat"), None
    )
    if lon_axis is None or lat_axis is None:
        raise CoverageReadError(
            f"COVJSON axes not identifiable as lon/lat: {list(axes_doc)}",
            reason_code=RC_AXIS_NOT_IDENTIFIABLE,
            details={"axes": list(axes_doc)},
        )
    lon_vals = np.asarray([float(v) for v in axis_items[lon_axis][1].get("values", [])], dtype="float64")
    lat_vals = np.asarray([float(v) for v in axis_items[lat_axis][1].get("values", [])], dtype="float64")
    if lon_vals.size < 2 or lat_vals.size < 2:
        raise CoverageReadError(
            "COVJSON axes need >= 2 values to derive a transform",
            reason_code=RC_CAPABILITY_NOT_DECLARED,
        )

    ranges = doc.get("ranges") or {}
    band_names = [b["name"] for b in bands_declared if b.get("name")] or list(ranges.keys())
    requested = list(bands) if bands else None
    selected = [b for b in band_names if (not bands or b in bands) and b in ranges]
    bands_missing = [b for b in (requested or []) if b not in selected]
    if not selected:
        raise CoverageReadError(
            f"no matching ranges (requested={bands}, available={list(ranges)})",
            reason_code=RC_CAPABILITY_NOT_DECLARED,
        )
    nodata = None
    for b in bands_declared:
        if b.get("name") in selected and b.get("nodata") is not None:
            nodata = float(b["nodata"])
            break
    layers = []
    for name in selected:
        rng = ranges[name]
        axis_names = rng.get("axisNames") or []
        shape = rng.get("shape") or []
        values = rng.get("values")
        if not isinstance(values, list) or not shape:
            raise CoverageReadError(
                f"range '{name}' lacks NdArray values/shape (TiledNdArray unsupported)",
                reason_code=RC_CAPABILITY_NOT_DECLARED,
            )
        arr1 = np.asarray(values, dtype="float64")
        # 轴序按 axisNames 归一化为 [lat, lon]（GDAL north-up 语义）。
        if axis_names:
            kind_map = [_axis_kind_axis(str(a), axis_items) for a in axis_names]
            if kind_map == ["lat", "lon"]:
                arr2 = arr1.reshape(shape)
            elif kind_map == ["lon", "lat"]:
                arr2 = arr1.reshape(shape).T
            else:
                raise CoverageReadError(
                    f"range axis order not identifiable: {axis_names}",
                    reason_code=RC_AXIS_NOT_IDENTIFIABLE,
                )
        else:
            arr2 = arr1.reshape(shape)
            if arr2.shape != (lat_vals.size, lon_vals.size) and arr2.shape == (lon_vals.size, lat_vals.size):
                arr2 = arr2.T
        if arr2.shape != (lat_vals.size, lon_vals.size):
            raise CoverageReadError(
                f"range shape {arr2.shape} != grid ({lat_vals.size}, {lon_vals.size})",
                reason_code=RC_CAPABILITY_NOT_DECLARED,
            )
        layers.append(arr2)
    stacked = np.stack(layers) if len(layers) > 1 else layers[0][np.newaxis, ...]

    # GDAL north-up：lat 轴需降序。升序 → 翻转（真实坐标语义不变）。
    if lat_vals[1] > lat_vals[0]:
        lat_order = lat_vals[::-1]
        stacked = stacked[:, ::-1, :]
    else:
        lat_order = lat_vals
    lon_order = lon_vals if lon_vals[1] > lon_vals[0] else lon_vals[::-1]
    if lon_vals[1] < lon_vals[0]:
        stacked = stacked[:, :, ::-1]
    xres = (lon_order[-1] - lon_order[0]) / (lon_order.size - 1)
    # north-up：lat_order 降序（顶在前），步长自然为负。
    yres = (lat_order[-1] - lat_order[0]) / (lat_order.size - 1)
    transform = Affine.translation(lon_order[0] - xres / 2, lat_order[0] - yres / 2) * Affine.scale(xres, yres)
    axes_used = {
        "lon": {"count": int(lon_order.size), "first": float(lon_order[0]), "last": float(lon_order[-1])},
        "lat": {"count": int(lat_order.size), "first": float(lat_order[0]), "last": float(lat_order[-1])},
    }
    # CRS 来自响应自身的 referencing 声明（P0-1：不可判 → None，绝不默认）。
    crs = _resolve_covjson_crs(doc)
    return (
        stacked.astype("float32"), transform, crs, nodata, selected, axes_used,
        bands_missing,
    )


def _axis_kind_axis(key: str, axis_items: List[Tuple[str, Any]]) -> Optional[str]:
    return _axis_kind(key)


def _parse_geotiff(
    body: bytes, *, bands: Optional[List[str]]
) -> Tuple[np.ndarray, Any, Any, Optional[float], List[str], Dict[str, Any]]:
    """GeoTIFF 输出：字节 → rasterio 内存读（transform/CRS/nodata 全来自文件）。"""
    from rasterio.io import MemoryFile

    with MemoryFile(body) as mem:
        with mem.open() as ds:
            indexes = list(range(1, ds.count + 1))
            arr = ds.read(indexes)
            transform = ds.transform
            crs = ds.crs.to_string() if ds.crs else None
            nodata = ds.nodata
            band_names = [ds.descriptions[i - 1] or f"band{i}" for i in indexes]
            shape = {"bands": ds.count, "height": ds.height, "width": ds.width}
    return arr, transform, crs, nodata, band_names, shape


def _write_gtiff(
    arr: np.ndarray, transform: Any, crs: Any, nodata: Optional[float], path: Path
) -> None:
    import rasterio

    count, height, width = arr.shape
    profile = {
        "driver": "GTiff",
        "width": width,
        "height": height,
        "count": count,
        "dtype": "float32",
        "transform": transform,
        "crs": crs,
        "nodata": nodata,
        "tiled": True,
        "blockxsize": min(512, max(16, width)),
        "blockysize": min(512, max(16, height)),
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(arr.astype("float32"))


__all__ = [
    "CoverageReadError",
    "DEFAULT_MAX_CELLS",
    "read_coverage_subset",
    "RC_AXIS_NOT_IDENTIFIABLE",
    "RC_BUDGET_EXCEEDED",
    "RC_CAPABILITY_NOT_DECLARED",
    "RC_OVERSIZE_RESPONSE",
]
