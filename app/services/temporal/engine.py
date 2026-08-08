"""
Unified Temporal GIS Runtime Engine Orchestrator.
Provides a single entry point seam for profiling, filtering, aggregating, change analysis,
trend analysis, spatiotemporal clustering, and windowed raster series processing.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from app.services.temporal.aggregation import TemporalAggregationEngine
from app.services.temporal.change import TemporalChangeEngine
from app.services.temporal.filter import TemporalFilterEngine
from app.services.temporal.models import (
    SpatiotemporalHotspotResult,
    TemporalAggregation,
    TemporalChangeResult,
    TemporalDatasetProfile,
    TemporalFilter,
    TemporalOperator,
    TemporalRasterResult,
    TemporalTrendResult,
    TemporalUnit,
)
from app.services.temporal.profiler import TemporalProfiler
from app.services.temporal.raster import TemporalRasterEngine
from app.services.temporal.spatiotemporal import SpatiotemporalClusterEngine
from app.services.temporal.trend import TemporalTrendEngine

logger = logging.getLogger(__name__)


class TemporalEngine:
    """
    Unified Orchestrator Seam for Temporal GIS Runtime Engine.
    """

    def __init__(self) -> None:
        self.profiler = TemporalProfiler()
        self.filter_engine = TemporalFilterEngine()
        self.aggregation_engine = TemporalAggregationEngine()
        self.change_engine = TemporalChangeEngine()
        self.trend_engine = TemporalTrendEngine()
        self.spatiotemporal_engine = SpatiotemporalClusterEngine()
        self.raster_engine = TemporalRasterEngine()

    def profile(
        self,
        features_or_records: Any,
        primary_field: Optional[str] = None,
        secondary_field: Optional[str] = None,
    ) -> TemporalDatasetProfile:
        """Profiles dataset temporal bounds, fields, granularity, timezone, and gaps."""
        return self.profiler.profile_dataset(
            features_or_records=features_or_records,
            primary_field=primary_field,
            secondary_field=secondary_field,
        )

    def filter(
        self,
        features_or_records: Any,
        time_field: Optional[str] = None,
        operator: Optional[Union[TemporalOperator, str]] = None,
        instant: Optional[Any] = None,
        start: Optional[Any] = None,
        end: Optional[Any] = None,
        relative_window: Optional[str] = None,
        ref_time: Optional[datetime] = None,
        filter_spec: Optional[TemporalFilter] = None,
    ) -> Any:
        """Filters dataset along temporal dimension."""
        return self.filter_engine.filter_dataset(
            features_or_records=features_or_records,
            time_field=time_field,
            operator=operator,
            instant=instant,
            start=start,
            end=end,
            relative_window=relative_window,
            ref_time=ref_time,
            filter_spec=filter_spec,
        )

    def aggregate(
        self,
        features_or_records: Any,
        time_field: Optional[str] = None,
        group_by_unit: Union[TemporalUnit, str] = TemporalUnit.DAY,
        metrics: Optional[List[Any]] = None,
        target_fields: Optional[List[str]] = None,
        agg_spec: Optional[TemporalAggregation] = None,
    ) -> List[Dict[str, Any]]:
        """Groups dataset by time unit and calculates statistical metrics."""
        return self.aggregation_engine.aggregate(
            features_or_records=features_or_records,
            time_field=time_field,
            group_by_unit=group_by_unit,
            metrics=metrics,
            target_fields=target_fields,
            agg_spec=agg_spec,
        )

    def compare_change(
        self,
        snapshots: List[Dict[str, Any]],
        snapshot_names_or_times: Optional[List[str]] = None,
        numeric_fields: Optional[List[str]] = None,
        id_field: str = "id",
        time_field: Optional[str] = None,
    ) -> TemporalChangeResult:
        """Compares multi-snapshot temporal changes for counts, attribute deltas, and geometry."""
        return self.change_engine.compare_snapshots(
            snapshots=snapshots,
            snapshot_names_or_times=snapshot_names_or_times,
            numeric_fields=numeric_fields,
            id_field=id_field,
            time_field=time_field,
        )

    def analyze_trend(
        self,
        data: Union[List[float], List[Dict[str, Any]]],
        metric_name: str = "value",
        time_field: Optional[str] = None,
        moving_avg_window: int = 3,
        z_threshold: float = 2.0,
    ) -> TemporalTrendResult:
        """Analyzes temporal trend using moving averages, Sen's slope, and anomaly detection."""
        return self.trend_engine.analyze_trend(
            data=data,
            metric_name=metric_name,
            time_field=time_field,
            moving_avg_window=moving_avg_window,
            z_threshold=z_threshold,
        )

    def cluster_spatiotemporal(
        self,
        geojson: Dict[str, Any],
        eps1_spatial_meters: float = 1000.0,
        eps2_temporal_seconds: float = 3600.0,
        min_samples: int = 5,
        timestamp_field: str = "timestamp",
    ) -> SpatiotemporalHotspotResult:
        """Executes space-time clustering (ST-DBSCAN)."""
        return self.spatiotemporal_engine.cluster(
            geojson=geojson,
            eps1_spatial_meters=eps1_spatial_meters,
            eps2_temporal_seconds=eps2_temporal_seconds,
            min_samples=min_samples,
            timestamp_field=timestamp_field,
        )

    def analyze_raster_series(
        self,
        raster_series: List[Dict[str, Any]],
        start_time: Optional[Union[str, datetime]] = None,
        end_time: Optional[Union[str, datetime]] = None,
        aoi_geometry: Optional[Dict[str, Any]] = None,
    ) -> TemporalRasterResult:
        """Executes windowed raster time series analysis."""
        return self.raster_engine.execute_raster_analysis(
            raster_series=raster_series,
            start_time=start_time,
            end_time=end_time,
            aoi_geometry=aoi_geometry,
        )

    # --- High-level Async Tool/Harness Seam Interfaces ---

    async def profile_dataset(
        self,
        dataset: Any,
        session_id: str = "",
    ) -> TemporalDatasetProfile:
        """High level temporal profile wrapper."""
        def _sync_run():
            features = dataset.get("features", []) if isinstance(dataset, dict) else dataset
            return self.profile(features_or_records=features)
        return await asyncio.to_thread(_sync_run)

    async def execute_filter(
        self,
        dataset: Any,
        temporal_field: Optional[str] = None,
        t_filter: Optional[TemporalFilter] = None,
        session_id: str = "",
    ) -> Any:
        """High level temporal filter wrapper."""
        def _sync_run():
            from app.services.temporal.models import TemporalAnalysisResult
            features = dataset.get("features", []) if isinstance(dataset, dict) else dataset

            # Derive operator and parameters from filter spec
            op, inst, st, et, rw = None, None, None, None, None
            if t_filter:
                st = t_filter.start_time
                et = t_filter.end_time
                rw = t_filter.relative_window
                if t_filter.filter_type == "range" or (st or et):
                    op = "BETWEEN"
                elif t_filter.filter_type == "relative_window" or rw:
                    op = "RELATIVE_WINDOW"

            filtered_feats = self.filter(
                features_or_records=features,
                time_field=temporal_field,
                operator=op,
                instant=inst,
                start=st,
                end=et,
                relative_window=rw,
                filter_spec=t_filter,
            )

            result_geojson = {
                "type": "FeatureCollection",
                "features": filtered_feats if isinstance(filtered_feats, list) else []
            }

            return TemporalAnalysisResult(
                analysis_type="temporal_filter",
                status="success",
                result_geojson=result_geojson,
            )

        return await asyncio.to_thread(_sync_run)

    async def execute_aggregate(
        self,
        dataset: Any,
        temporal_field: Optional[str] = None,
        agg_spec: Optional[TemporalAggregation] = None,
        session_id: str = "",
    ) -> Any:
        """High level temporal aggregate wrapper."""
        def _sync_run():
            from app.services.temporal.models import TemporalAnalysisResult
            features = dataset.get("features", []) if isinstance(dataset, dict) else dataset
            unit = agg_spec.interval_unit if agg_spec else "day"
            metrics = [agg_spec.aggregation_func] if agg_spec else ["count"]
            targets = agg_spec.metric_fields if agg_spec else []

            aggregated_records = self.aggregate(
                features_or_records=features,
                time_field=temporal_field,
                group_by_unit=unit,
                metrics=metrics,
                target_fields=targets,
                agg_spec=agg_spec,
            )

            return TemporalAnalysisResult(
                analysis_type="temporal_aggregate",
                status="success",
                change_summary={
                    "aggregated_buckets": len(aggregated_records),
                    "records": aggregated_records,
                },
            )

        return await asyncio.to_thread(_sync_run)

    async def execute_change(
        self,
        dataset_t1: Any,
        dataset_t2: Any,
        metric_fields: Optional[List[str]] = None,
        session_id: str = "",
    ) -> Any:
        """High level temporal change wrapper."""
        def _sync_run():
            from app.services.temporal.models import TemporalAnalysisResult
            feats_t1 = dataset_t1.get("features", []) if isinstance(dataset_t1, dict) else dataset_t1
            feats_t2 = dataset_t2.get("features", []) if isinstance(dataset_t2, dict) else dataset_t2

            change_res = self.change_engine.compare_snapshots(
                snapshots=[feats_t1, feats_t2],
                numeric_fields=metric_fields,
            )

            return TemporalAnalysisResult(
                analysis_type="temporal_change",
                status="success",
                change_summary=change_res.model_dump(),
            )

        return await asyncio.to_thread(_sync_run)

    async def execute_trend(
        self,
        dataset: Any,
        value_field: str = "",
        temporal_field: Optional[str] = None,
        session_id: str = "",
    ) -> Any:
        """High level temporal trend wrapper."""
        def _sync_run():
            from app.services.temporal.models import TemporalAnalysisResult
            features = dataset.get("features", []) if isinstance(dataset, dict) else dataset

            trend_res = self.analyze_trend(
                data=features,
                metric_name=value_field or "value",
                time_field=temporal_field,
            )

            return TemporalAnalysisResult(
                analysis_type="temporal_trend",
                status="success",
                trend_metrics={
                    "slope": trend_res.slope,
                    "intercept": trend_res.intercept,
                    "r_squared": trend_res.r_squared,
                    "direction": getattr(trend_res, "direction", getattr(trend_res, "trend_direction", "stable")),
                },
            )

        return await asyncio.to_thread(_sync_run)

    async def execute_spatiotemporal_hotspot(
        self,
        dataset: Any,
        temporal_field: Optional[str] = None,
        eps_spatial_m: float = 1000.0,
        eps_temporal_days: float = 30.0,
        min_samples: int = 5,
        session_id: str = "",
    ) -> Any:
        """High level spatiotemporal hotspot wrapper."""
        def _sync_run():
            from app.services.temporal.models import TemporalAnalysisResult
            geojson = dataset if isinstance(dataset, dict) and "features" in dataset else {"type": "FeatureCollection", "features": dataset}

            hotspot_res = self.cluster_spatiotemporal(
                geojson=geojson,
                eps1_spatial_meters=eps_spatial_m,
                eps2_temporal_seconds=eps_temporal_days * 86400.0,
                min_samples=min_samples,
                timestamp_field=temporal_field or "timestamp",
            )

            return TemporalAnalysisResult(
                analysis_type="spatiotemporal_hotspot",
                status="success",
                hotspots=[hotspot_res],
            )

        return await asyncio.to_thread(_sync_run)

    async def execute_temporal_raster(
        self,
        raster_series: List[Any],
        aoi_geometry: Optional[Dict[str, Any]] = None,
        operation: str = "difference",
        session_id: str = "",
    ) -> Any:
        """High level temporal raster wrapper."""
        def _sync_run():
            from app.services.temporal.models import TemporalAnalysisResult
            raster_res = self.analyze_raster_series(
                raster_series=raster_series,
                aoi_geometry=aoi_geometry,
            )

            return TemporalAnalysisResult(
                analysis_type="temporal_raster",
                status="success",
                raster_series=[raster_res.model_dump()],
            )

        return await asyncio.to_thread(_sync_run)
