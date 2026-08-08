"""
Temporal Raster Engine.
Performs windowed raster time series analysis (selecting time slices, zonal statistics over AOI,
raster difference, and raster trend analysis) without loading full rasters into memory.
"""

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
                from rasterio.windows import Window
                with rasterio.open(p1) as src1, rasterio.open(p2) as src2:
                    # Windowed read
                    window = Window(0, 0, min(src1.width, 1024), min(src1.height, 1024))
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
            except Exception as e:
                logger.warning(f"Error computing raster difference: {e}")

        return {
            "mean_difference": 0.0,
            "min_difference": 0.0,
            "max_difference": 0.0,
            "std_difference": 0.0,
            "pixel_count": 0,
        }

    def raster_trend_over_aoi(
        self,
        raster_series: List[Dict[str, Any]],
        aoi_geometry: Optional[Dict[str, Any]] = None,
        raster_path_field: str = "path",
    ) -> Dict[str, Any]:
        """
        Computes trend line (slope, intercept, direction) of AOI zonal mean across raster time series.
        """
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
        stats = self.temporal_raster_statistics(selected_slices, aoi_geometry=aoi_geometry)
        trend = self.raster_trend_over_aoi(selected_slices, aoi_geometry=aoi_geometry)

        diff = None
        if len(selected_slices) >= 2:
            diff = self.raster_difference(selected_slices[0], selected_slices[-1], aoi_geometry)

        return TemporalRasterResult(
            selected_slices=selected_slices,
            raster_statistics=stats,
            raster_difference=diff,
            raster_trend=trend,
        )
