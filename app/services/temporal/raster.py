"""
Temporal Raster Engine.
Performs windowed raster time series analysis (selecting time slices, zonal statistics over AOI,
raster difference, and raster trend analysis) without loading full rasters into memory.
"""

import math
import os
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

import numpy as np

from app.services.temporal.models import TemporalRasterResult
from app.services.temporal.profiler import parse_value_to_instant

logger = logging.getLogger(__name__)


class TemporalRasterEngine:
    """
    Executes windowed raster time series operations.
    """

    def select_time_slice(
        self,
        raster_series: List[Dict[str, Any]],
        start_time: Optional[Union[str, datetime]] = None,
        end_time: Optional[Union[str, datetime]] = None,
        time_field: str = "timestamp",
    ) -> List[Dict[str, Any]]:
        """
        Filters a list of raster metadata objects by time range.
        """
        if not raster_series:
            return []

        start_epoch = None
        if start_time is not None:
            if isinstance(start_time, str):
                start_time = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=timezone.utc)
            start_epoch = start_time.timestamp()

        end_epoch = None
        if end_time is not None:
            if isinstance(end_time, str):
                end_time = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
            if end_time.tzinfo is None:
                end_time = end_time.replace(tzinfo=timezone.utc)
            end_epoch = end_time.timestamp()

        selected = []
        for item in raster_series:
            t_val = item.get(time_field) or item.get("time") or item.get("datetime") or item.get("date")
            if t_val is None:
                selected.append(item)
                continue

            inst = parse_value_to_instant(t_val, field_name_hint=time_field)
            if inst is None:
                selected.append(item)
                continue

            t_epoch = inst[0].epoch_seconds

            match = True
            if start_epoch is not None and t_epoch < start_epoch:
                match = False
            if end_epoch is not None and t_epoch > end_epoch:
                match = False

            if match:
                selected.append(item)

        return selected

    def temporal_raster_statistics(
        self,
        raster_series: List[Dict[str, Any]],
        metrics: Optional[List[str]] = None,
        aoi_geometry: Optional[Dict[str, Any]] = None,
        raster_path_field: str = "path",
    ) -> Dict[str, Any]:
        """
        Calculates windowed zonal statistics for raster series over an AOI.
        """
        metrics = metrics or ["mean", "min", "max", "std"]
        series_stats = []

        for item in raster_series:
            rpath = item.get(raster_path_field) or item.get("raster_path") or item.get("file")
            t_val = item.get("timestamp") or item.get("time") or item.get("datetime")

            stats_res: Dict[str, Any] = {}

            if rpath and isinstance(rpath, str) and os.path.exists(rpath):
                # Use windowed rasterstats zonal_stats
                try:
                    from app.lib.geo_analysis.raster_ops import zonal_statistics
                    polys = aoi_geometry or {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]]}}
                    z_res = zonal_statistics(polys, rpath, stats=metrics)
                    if z_res and len(z_res) > 0:
                        stats_res = z_res[0]
                except Exception as e:
                    logger.warning(f"Error computing zonal stats on {rpath}: {e}")

            # Fallback or mock data embedded in item for testing / in-memory raster series
            if not stats_res and "data" in item:
                arr = np.array(item["data"], dtype=float)
                if arr.size > 0:
                    stats_res = {
                        "mean": float(np.nanmean(arr)),
                        "min": float(np.nanmin(arr)),
                        "max": float(np.nanmax(arr)),
                        "std": float(np.nanstd(arr)),
                    }

            series_stats.append({
                "timestamp": str(t_val) if t_val else None,
                "path": rpath,
                "statistics": stats_res,
            })

        return {
            "total_rasters": len(raster_series),
            "series_statistics": series_stats,
        }

    def raster_difference(
        self,
        raster_t1: Union[str, Dict[str, Any]],
        raster_t2: Union[str, Dict[str, Any]],
        aoi_geometry: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Performs pixel/zonal raster difference (T2 - T1).

        The comparison window is derived from the AOI (or, without an AOI, the
        overlapping bounds of the two rasters) and covers that window in FULL —
        statistics are accumulated block-by-block (bounded memory, #448), never
        by truncating large windows. The two rasters must be CRS/transform
        aligned — otherwise the subtraction would silently compare
        misregistered pixels (previously it read the top-left megapixel of
        src1 only, ignoring both the AOI and src2's grid).
        """
        p1 = raster_t1 if isinstance(raster_t1, str) else raster_t1.get("path", "")
        p2 = raster_t2 if isinstance(raster_t2, str) else raster_t2.get("path", "")

        d1 = raster_t1.get("data") if isinstance(raster_t1, dict) else None
        d2 = raster_t2.get("data") if isinstance(raster_t2, dict) else None

        if d1 is not None and d2 is not None:
            arr1 = np.array(d1, dtype=float)
            arr2 = np.array(d2, dtype=float)
            diff = arr2 - arr1
            return {
                "mean_difference": float(np.nanmean(diff)),
                "min_difference": float(np.nanmin(diff)),
                "max_difference": float(np.nanmax(diff)),
                "std_difference": float(np.nanstd(diff)),
                "pixel_count": int(diff.size),
            }

        if p1 and p2 and os.path.exists(p1) and os.path.exists(p2):
            try:
                import rasterio
                with rasterio.open(p1) as src1, rasterio.open(p2) as src2:
                    self._validate_alignment(src1, src2)
                    window = self._difference_window(src1, src2, aoi_geometry)
                    if window is None or window.width <= 0 or window.height <= 0:
                        # AOI / overlap does not intersect either raster.
                        return self._empty_difference()
                    result = self._streamed_difference_stats(src1, src2, window)
                    result["analyzed_window"] = {
                        "col_off": int(window.col_off),
                        "row_off": int(window.row_off),
                        "width": int(window.width),
                        "height": int(window.height),
                    }
                    return result
            except ValueError:
                raise
            except Exception as e:
                logger.warning(f"Error computing raster difference: {e}")

        return self._empty_difference()

    # Per-side block size for difference reads (#448): the FULL AOI∩overlap
    # window is processed block-by-block so peak memory stays
    # O(_DIFF_BLOCK_SIZE²) even for windows far larger than 1024×1024 —
    # no silent truncation of large windows.
    _DIFF_BLOCK_SIZE = 1024

    @classmethod
    def _streamed_difference_stats(cls, src1, src2, window) -> Dict[str, Any]:
        """NaN-aware difference statistics over the full window, accumulated
        over fixed-size rasterio sub-windows (bounded memory, #448).

        Moments are combined with Chan's parallel algorithm so the streamed
        mean/std match a brute-force full-window np.nanmean/np.nanstd; an
        entirely-NaN window yields NaN stats (same as the previous
        full-array behavior) without per-block RuntimeWarnings.
        """
        from rasterio.windows import Window

        bs = cls._DIFF_BLOCK_SIZE
        row0, col0 = int(window.row_off), int(window.col_off)
        width, height = int(window.width), int(window.height)

        total = 0
        n = 0
        mean = 0.0
        m2 = 0.0  # Σ (x − running_mean)²
        mn: Optional[float] = None
        mx: Optional[float] = None

        for r in range(row0, row0 + height, bs):
            for c in range(col0, col0 + width, bs):
                w = Window(c, r, min(bs, col0 + width - c), min(bs, row0 + height - r))
                b1 = src1.read(1, window=w).astype(float)
                b2 = src2.read(1, window=w).astype(float)
                diff = b2 - b1
                total += diff.size
                valid = diff[~np.isnan(diff)]
                if valid.size == 0:
                    continue
                b_n = int(valid.size)
                b_mean = float(valid.mean())
                b_m2 = float(((valid - b_mean) ** 2).sum())
                # Chan et al. parallel combine of (n, mean, m2) with this block.
                delta = b_mean - mean
                combined = n + b_n
                mean += delta * (b_n / combined)
                m2 += b_m2 + delta * delta * (n * b_n / combined)
                n = combined
                b_mn = float(valid.min())
                b_mx = float(valid.max())
                mn = b_mn if mn is None or b_mn < mn else mn
                mx = b_mx if mx is None or b_mx > mx else mx

        if n == 0:
            nan = float("nan")
            return {
                "mean_difference": nan,
                "min_difference": nan,
                "max_difference": nan,
                "std_difference": nan,
                "pixel_count": total,
            }
        variance = max(m2 / n, 0.0)
        return {
            "mean_difference": float(mean),
            "min_difference": float(mn),
            "max_difference": float(mx),
            "std_difference": float(math.sqrt(variance)),
            "pixel_count": total,
        }

    @staticmethod
    def _empty_difference() -> Dict[str, Any]:
        return {
            "mean_difference": 0.0,
            "min_difference": 0.0,
            "max_difference": 0.0,
            "std_difference": 0.0,
            "pixel_count": 0,
        }

    @staticmethod
    def _validate_alignment(src1, src2) -> None:
        """Rejects CRS/transform-mismatched raster pairs with a clear error."""
        if (src1.crs is None) != (src2.crs is None) or (
            src1.crs is not None and str(src1.crs) != str(src2.crs)
        ):
            raise ValueError(
                f"Raster CRS mismatch in difference: {src1.crs} vs {src2.crs}. "
                "Refusing to subtract misaligned rasters."
            )
        t1, t2 = src1.transform, src2.transform
        if not np.allclose([t1.a, t1.b, t1.c, t1.d, t1.e, t1.f],
                           [t2.a, t2.b, t2.c, t2.d, t2.e, t2.f],
                           rtol=1e-6, atol=1e-9):
            raise ValueError(
                f"Raster transform mismatch in difference: {tuple(t1)} vs {tuple(t2)}. "
                "Refusing to subtract rasters with different pixel grids."
            )

    @staticmethod
    def _aoi_bounds_in_crs(src, aoi_geometry: Optional[Dict[str, Any]]) -> Optional[tuple]:
        """Returns AOI bounds in the raster's CRS (or None when not usable)."""
        if not aoi_geometry:
            return None
        try:
            from rasterio.features import bounds as geom_bounds
            geom = aoi_geometry.get("geometry") if aoi_geometry.get("type") == "Feature" else aoi_geometry
            if geom.get("type") == "FeatureCollection":
                boxes = [geom_bounds(f) for f in geom.get("features", []) if f.get("geometry")]
                if not boxes:
                    return None
                aoi_bounds = (
                    min(b[0] for b in boxes), min(b[1] for b in boxes),
                    max(b[2] for b in boxes), max(b[3] for b in boxes),
                )
            else:
                aoi_bounds = geom_bounds(geom)
        except Exception:
            return None
        if src.crs is not None:
            aoi_crs = (
                aoi_geometry.get("crs", {}).get("properties", {}).get("name", "EPSG:4326")
                if isinstance(aoi_geometry, dict) else "EPSG:4326"
            )
            if str(src.crs) != aoi_crs:
                try:
                    from rasterio.warp import transform_bounds
                    aoi_bounds = transform_bounds(aoi_crs, str(src.crs), *aoi_bounds, densify_pts=21)
                except Exception:
                    return None
        return aoi_bounds

    def _difference_window(self, src1, src2, aoi_geometry) -> Any:
        """Pixel window covering AOI ∩ raster bounds (both rasters, aligned).

        Without an AOI, falls back to the overlap of the two rasters' bounds.
        Both rasters share one window because alignment is validated first.
        """
        from rasterio.windows import Window

        if aoi_geometry:
            bounds = self._aoi_bounds_in_crs(src1, aoi_geometry)
        else:
            b1, b2 = src1.bounds, src2.bounds
            bounds = (
                max(b1[0], b2[0]), max(b1[1], b2[1]),
                min(b1[2], b2[2]), min(b1[3], b2[3]),
            )
        if bounds is None:
            return None
        minx, miny, maxx, maxy = bounds
        if maxx <= minx or maxy <= miny:
            return None

        inv = ~src1.transform
        corners = [
            inv * (minx, miny), inv * (minx, maxy),
            inv * (maxx, miny), inv * (maxx, maxy),
        ]
        col_min = min(c[0] for c in corners)
        row_min = min(c[1] for c in corners)
        col_max = max(c[0] for c in corners)
        row_max = max(c[1] for c in corners)

        # Clip to the raster extent.
        col_min = max(0.0, col_min)
        row_min = max(0.0, row_min)
        col_max = min(float(src1.width), col_max)
        row_max = min(float(src1.height), row_max)
        if col_max <= col_min or row_max <= row_min:
            return None

        # #448: the FULL window — no size cap. Memory is bounded instead by
        # block-looped reads in _streamed_difference_stats; truncating here
        # silently reported statistics for a top-left megapixel crop of large
        # AOIs.
        width = int(math.ceil(col_max - col_min))
        height = int(math.ceil(row_max - row_min))
        return Window(int(math.floor(col_min)), int(math.floor(row_min)), width, height)

    def raster_trend_over_aoi(
        self,
        raster_series: List[Dict[str, Any]],
        aoi_geometry: Optional[Dict[str, Any]] = None,
        raster_path_field: str = "path",
        stats_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Computes trend line (slope, intercept, direction) of AOI zonal mean across raster time series.

        ``stats_info`` may carry a precomputed ``temporal_raster_statistics``
        result (as produced by ``execute_raster_analysis``) so the zonal
        statistics pass is not re-run for the trend step — previously every
        raster was opened and statistically summarized twice per request.
        """
        if stats_info is None:
            stats_info = self.temporal_raster_statistics(
                raster_series=raster_series,
                metrics=["mean"],
                aoi_geometry=aoi_geometry,
                raster_path_field=raster_path_field,
            )

        timestamps = []
        means = []
        skipped: List[Dict[str, Any]] = []

        for idx, entry in enumerate(stats_info["series_statistics"]):
            # #458: a missing/failed raster yields empty statistics — it must
            # be SKIPPED and reported, not contribute a fabricated mean 0.0
            # that drags the trend toward a confident "stable" verdict.
            m_val = (entry.get("statistics") or {}).get("mean")
            if m_val is None:
                skipped.append({
                    "index": idx,
                    "timestamp": entry.get("timestamp"),
                    "path": entry.get("path"),
                    "reason": "missing_or_failed_statistics",
                })
                continue
            try:
                m_val = float(m_val)
            except (TypeError, ValueError):
                skipped.append({
                    "index": idx,
                    "timestamp": entry.get("timestamp"),
                    "path": entry.get("path"),
                    "reason": "non_numeric_mean",
                })
                continue
            if not math.isfinite(m_val):
                skipped.append({
                    "index": idx,
                    "timestamp": entry.get("timestamp"),
                    "path": entry.get("path"),
                    "reason": "non_finite_mean",
                })
                continue
            t_val = entry.get("timestamp") or f"t_{idx}"
            timestamps.append(str(t_val))
            means.append(m_val)

        from app.services.temporal.trend import TemporalTrendEngine
        trend_eng = TemporalTrendEngine()
        trend_res = trend_eng.analyze_trend(data=means, metric_name="raster_mean")

        result = {
            "timestamps": timestamps,
            "means": means,
            "slope": trend_res.slope,
            "intercept": trend_res.intercept,
            "r_squared": trend_res.r_squared,
            "direction": trend_res.direction,
        }
        if skipped:
            result["skipped_slices"] = skipped
        return result

    # #454: the temporal_raster tool's `operation` argument selects the
    # analysis branches. "all" (the previous unconditional pipeline) stays the
    # default so existing callers keep their result shape.
    _RASTER_OPERATIONS = ("all", "difference", "mean", "trend")

    def execute_raster_analysis(
        self,
        raster_series: List[Dict[str, Any]],
        start_time: Optional[Union[str, datetime]] = None,
        end_time: Optional[Union[str, datetime]] = None,
        aoi_geometry: Optional[Dict[str, Any]] = None,
        operation: str = "all",
    ) -> TemporalRasterResult:
        """
        High-level raster analysis pipeline.

        ``operation`` selects the branches (#454): "all" (default) runs the
        full statistics + trend + difference pipeline; "difference", "trend"
        and "mean" run only the requested analysis (statistics are always
        computed — the trend branch reuses them).
        """
        op = (operation or "all").strip().lower()
        if op not in self._RASTER_OPERATIONS:
            raise ValueError(
                f"Unsupported temporal raster operation '{operation}'. "
                f"Available operations: {list(self._RASTER_OPERATIONS)}"
            )

        selected_slices = self.select_time_slice(raster_series, start_time, end_time)
        # Single statistics pass; the trend step reuses these results instead of
        # re-opening every raster and recomputing the zonal statistics.
        stats = self.temporal_raster_statistics(selected_slices, aoi_geometry=aoi_geometry)

        trend = None
        if op in ("all", "trend"):
            trend = self.raster_trend_over_aoi(
                selected_slices, aoi_geometry=aoi_geometry, stats_info=stats
            )

        diff = None
        if op in ("all", "difference") and len(selected_slices) >= 2:
            diff = self.raster_difference(selected_slices[0], selected_slices[-1], aoi_geometry)

        return TemporalRasterResult(
            selected_slices=selected_slices,
            raster_statistics=stats,
            raster_difference=diff,
            raster_trend=trend,
        )
