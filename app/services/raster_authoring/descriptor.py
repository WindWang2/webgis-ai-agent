"""G20 元数据、有界直方图与统计口径（真实文件读取，零遥感算法重算）。"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.schemas.raster_spec import (
    MAX_RASTER_STATS_SIDE,
    RasterExpressionError,
)
from app.services.raster_authoring.budgets import (
    MAX_DESCRIPTOR_BANDS,
    bounded_buckets,
    truncation_fact,
)
from app.services.raster_authoring.source import RasterSource

#: (path, indexes, data_revision) → (stats, sample_count, expire)
_STATS_CACHE: Dict[tuple, tuple[Tuple[Tuple[float, float], ...], int, float]] = {}
_STATS_LOCK = threading.Lock()
_STATS_TTL_S = 300.0
_STATS_CACHE_MAX = 256


@dataclass(frozen=True)
class BandStats:
    per_band: Tuple[Tuple[float, float], ...]
    sample_count: int
    method: str = "minmax_decimated"
    approximate: bool = True
    side: int = MAX_RASTER_STATS_SIDE

    def as_dict(self) -> Dict[str, Any]:
        return {
            "method": self.method,
            "sample_count": self.sample_count,
            "approximate": self.approximate,
            "side": self.side,
            "per_band": [list(p) for p in self.per_band],
        }


def _compute(path: str, indexes: Sequence[int]) -> Tuple[Tuple[Tuple[float, float], ...], int]:
    import rasterio
    from rasterio.enums import Resampling

    with rasterio.open(path) as src:
        scale = min(1.0, MAX_RASTER_STATS_SIDE / float(max(src.width, src.height)))
        out_h = max(1, int(round(src.height * scale)))
        out_w = max(1, int(round(src.width * scale)))
        data = src.read(
            indexes=list(indexes),
            out_shape=(len(indexes), out_h, out_w),
            masked=True,
            resampling=Resampling.average,
        )
        arr = np.ma.getdata(data)
        mask = np.ma.getmaskarray(data)
        out: List[Tuple[float, float]] = []
        for b in range(len(indexes)):
            valid = np.isfinite(arr[b]) & ~mask[b]
            if valid.any():
                out.append((float(arr[b][valid].min()), float(arr[b][valid].max())))
            else:
                # 全 nodata：等端点（不等于「有极值」），由渲染/图例明确披露
                out.append((0.0, 0.0))
        return tuple(out), int(out_h * out_w * len(indexes))


def band_stats(source: RasterSource, indexes: Sequence[int]) -> BandStats:
    """每个 (数据版本, 波段子集) 只解析一次全局范围；相邻瓦片/导出共用。"""
    key = (source.path, tuple(int(i) for i in indexes), source.data_revision)
    with _STATS_LOCK:
        hit = _STATS_CACHE.get(key)
        if hit and time.monotonic() <= hit[2]:
            return BandStats(hit[0], hit[1])
    per_band, sample_count = _compute(source.path, indexes)
    with _STATS_LOCK:
        if len(_STATS_CACHE) >= _STATS_CACHE_MAX:
            _STATS_CACHE.clear()
        _STATS_CACHE[key] = (per_band, sample_count, time.monotonic() + _STATS_TTL_S)
    return BandStats(per_band, sample_count)


def histogram(
    source: RasterSource,
    index: int,
    buckets: int,
    *,
    bounds: Optional[Tuple[float, float]] = None,
) -> Dict[str, Any]:
    """有界直方图（同一 2048 边降采样样本；近似显式披露）。"""
    import rasterio
    from rasterio.enums import Resampling

    n = bounded_buckets(buckets)
    with rasterio.open(source.path) as src:
        scale = min(1.0, MAX_RASTER_STATS_SIDE / float(max(src.width, src.height)))
        out_h = max(1, int(round(src.height * scale)))
        out_w = max(1, int(round(src.width * scale)))
        window = src.read(
            indexes=int(index), out_shape=(out_h, out_w),
            masked=True, resampling=Resampling.average,
        )
        arr = np.ma.getdata(window)
        mask = np.ma.getmaskarray(window)
    finite = np.isfinite(arr) & ~mask
    values = arr[finite]
    if values.size == 0:
        return {
            "band": int(index), "buckets": n, "counts": [0] * n,
            "min": None, "max": None, "valid_samples": 0,
            "total_samples": int(arr.size), "approximate": True,
            "method": "histogram_decimated_2048", "all_nodata": True,
        }
    lo, hi = (float(bounds[0]), float(bounds[1])) if bounds else (float(values.min()), float(values.max()))
    if hi <= lo:
        counts = [int(values.size)] + [0] * (n - 1)
    else:
        idx = np.clip(((values - lo) / (hi - lo) * n).astype(int), 0, n - 1)
        counts = np.bincount(idx.ravel(), minlength=n)[:n].tolist()
    return {
        "band": int(index), "buckets": n, "counts": [int(c) for c in counts],
        "min": lo, "max": hi, "valid_samples": int(values.size),
        "total_samples": int(arr.size), "approximate": True,
        "method": "histogram_decimated_2048", "all_nodata": False,
        "sample_truncation": truncation_fact(
            requested=int(max(out_h, out_w)), used=int(min(MAX_RASTER_STATS_SIDE, max(out_h, out_w))),
            what="histogram_side",
        ),
    }


def _epsg4326_bounds(source: RasterSource) -> Optional[List[float]]:
    bounds = source.descriptor.bounds
    if not bounds or len(bounds) != 4 or not source.crs:
        return None
    try:
        from rasterio.warp import transform_bounds

        return [float(v) for v in transform_bounds(source.crs, "EPSG:4326", *bounds)]
    except Exception:  # noqa: BLE001 - 无法变换时如实返回 None
        return None


def describe(source: RasterSource, *, histogram_bands: Optional[List[int]] = None,
             buckets: Optional[int] = None) -> Dict[str, Any]:
    """工作台/工具的唯一描述面（有界；Pi 投影另在 service 做字段裁剪）。"""
    desc = source.descriptor
    band_list = list(source.band_meta)
    histogram_bands = histogram_bands or [1]
    if len(histogram_bands) > MAX_DESCRIPTOR_BANDS:
        raise RasterExpressionError(
            "BUDGET_EXCEEDED",
            f"at most {MAX_DESCRIPTOR_BANDS} histogram bands",
            field="histogram_bands", status_code=413,
        )
    stats_indexes = list(range(1, min(source.band_count, 3) + 1))
    stats = band_stats(source, stats_indexes) if stats_indexes else None
    histograms = [
        histogram(source, int(b), buckets or 32) for b in histogram_bands
        if 1 <= int(b) <= source.band_count
    ]
    disclosures: List[str] = []
    if not source.crs:
        disclosures.append("no_crs: coordinate queries cannot be georeferenced")
    if any(not b.get("unit") for b in band_list):
        disclosures.append("unit_unknown: band units are not declared in the source")
    if source.band_count == 2:
        disclosures.append("two_band_default: single-band display; explicit two-band RGB is unsupported")
    if all(b.get("vmin") is None for b in band_list):
        disclosures.append("all_nodata: no finite valid samples in the decimated read")

    from app.services.raster_authoring.contract import default_expression_request

    return {
        "success": True,
        "ref_id": source.ref_id,
        "session_id": source.session_id,
        "data_revision": source.data_revision,
        "content_fingerprint": source.content_fingerprint,
        "crs": source.crs,
        "crs_present": bool(source.crs),
        "bounds_source_crs": list(desc.bounds) if desc.bounds else None,
        "bounds_epsg4326": _epsg4326_bounds(source),
        "width": desc.width,
        "height": desc.height,
        "transform": list(desc.transform) if desc.transform else None,
        "resolution_x": desc.resolution_x,
        "resolution_y": desc.resolution_y,
        "driver": desc.driver,
        "nodata": desc.nodata,
        "has_overviews": desc.has_overviews,
        "band_count": source.band_count,
        "bands": band_list,
        "statistics": stats.as_dict() if stats else None,
        "histograms": histograms,
        "capabilities": {
            "numeric_bands": True,
            "tiles": bool(source.crs),
            "probe": True,
            "classified_semantics_requires_declaration": True,
        },
        "defaults": default_expression_request(source),
        "disclosures": disclosures,
    }
