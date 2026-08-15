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
        overlapping bounds of the two rasters) instead of a fixed top-left
        1024×1024 crop, and the two rasters must be CRS/transform aligned —
        otherwise the subtraction would silently compare misregistered pixels
        (previously it read the top-left megapixel of src1 only, ignoring both
        the AOI and src2's grid).
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
                    b1 = src1.read(1, window=window).astype(float)
                    b2 = src2.read(1, window=window).astype(float)
                    diff = b2 - b1
                    return {
                        "mean_difference": float(np.nanmean(diff)),
                        "min_difference": float(np.nanmin(diff)),
                        "max_difference": float(np.nanmax(diff)),
                        "std_difference": float(np.nanstd(diff)),
                        "pixel_count": int(diff.size),
                    }
            except ValueError:
                raise
            except Exception as e:
                logger.warning(f"Error computing raster difference: {e}")

        return self._empty_difference()

    @staticmethod
    def _empty_difference() -> Dict[str, Any]:
        return {
            "mean_difference": 0.0,
            "min_difference": 0.0,
            "max_difference": 0.0,
            "std_difference": 0.0,
            "pixel_count": 0,
        }

    # Max window dimension (per side) used to bound memory for a single
    # difference read. The window is still anchored at the AOI/overlap origin
    # (unlike the old fixed top-left crop).
    _DIFF_MAX_WINDOW = 1024

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

        width = min(int(math.ceil(col_max - col_min)), self._DIFF_MAX_WINDOW)
        height = min(int(math.ceil(row_max - row_min)), self._DIFF_MAX_WINDOW)
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

        for idx, entry in enumerate(stats_info["series_statistics"]):
            m_val = entry["statistics"].get("mean", 0.0)
            t_val = entry.get("timestamp") or f"t_{idx}"
            timestamps.append(str(t_val))
            means.append(float(m_val))

        from app.services.temporal.trend import TemporalTrendEngine
        trend_eng = TemporalTrendEngine()
        trend_res = trend_eng.analyze_trend(data=means, metric_name="raster_mean")

        return {
            "timestamps": timestamps,
            "means": means,
            "slope": trend_res.slope,
            "intercept": trend_res.intercept,
            "r_squared": trend_res.r_squared,
            "direction": trend_res.direction,
        }

    def execute_raster_analysis(
        self,
        raster_series: List[Dict[str, Any]],
        start_time: Optional[Union[str, datetime]] = None,
        end_time: Optional[Union[str, datetime]] = None,
        aoi_geometry: Optional[Dict[str, Any]] = None,
    ) -> TemporalRasterResult:
        """
        High-level raster analysis pipeline.
        """
        selected_slices = self.select_time_slice(raster_series, start_time, end_time)
        # Single statistics pass; the trend step reuses these results instead of
        # re-opening every raster and recomputing the zonal statistics.
        stats = self.temporal_raster_statistics(selected_slices, aoi_geometry=aoi_geometry)
        trend = self.raster_trend_over_aoi(
            selected_slices, aoi_geometry=aoi_geometry, stats_info=stats
        )

        diff = None
        if len(selected_slices) >= 2:
            diff = self.raster_difference(selected_slices[0], selected_slices[-1], aoi_geometry)

        return TemporalRasterResult(
            selected_slices=selected_slices,
            raster_statistics=stats,
            raster_difference=diff,
            raster_trend=trend,
        )
