"""
Temporal GIS Runtime Tools for ToolRegistry.
Exposes temporal_profile, temporal_filter, temporal_aggregate,
temporal_change, temporal_trend, spatiotemporal_hotspot, and temporal_raster.
"""
import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, ToolExecutionPolicy, tool
from app.tools._utils import trim_features
from app.services.temporal.engine import TemporalEngine
from app.services.temporal.models import TemporalFilter, TemporalAggregation

logger = logging.getLogger(__name__)


def trim_temporal_result(payload: Any, max_features: int = 50) -> Any:
    """Apply Fetch-on-Demand trimming to a temporal analysis result.

    Temporal results embed filtered/aggregate FeatureCollections, per-feature
    change records, and clustered point features — inlining these into tool_result
    can exceed the 50k DB/LLM-context cap. We keep summary metrics the LLM reasons
    over and collapse the heavy geometry-bearing fields to descriptors.
    """
    if not isinstance(payload, dict):
        return payload

    out = dict(payload)

    # result_geojson: full filtered/aggregate FeatureCollection -> trimmed preview.
    rg = out.get("result_geojson")
    if isinstance(rg, dict):
        feature_count = len(rg.get("features", [])) if rg.get("type") == "FeatureCollection" else 0
        out["result_geojson"] = {
            "type": rg.get("type"),
            "feature_count": feature_count,
            "preview": trim_features(rg, max_features=max_features),
        }

    # Nested models that can carry their own result_geojson (e.g. TemporalAnalysisResult).
    profile = out.get("profile")
    if isinstance(profile, dict) and isinstance(profile.get("result_geojson"), dict):
        profile = dict(profile)
        nested_rg = profile["result_geojson"]
        profile["result_geojson"] = {
            "type": nested_rg.get("type"),
            "feature_count": len(nested_rg.get("features", []))
            if nested_rg.get("type") == "FeatureCollection" else 0,
            "preview": trim_features(nested_rg, max_features=max_features),
        }
        out["profile"] = profile

    # feature_changes / clustered features: cap the list length.
    for list_key in ("feature_changes",):
        items = out.get(list_key)
        if isinstance(items, list) and len(items) > max_features:
            out[list_key] = items[:max_features]
            out[f"_{list_key}_trimmed"] = {"original_count": len(items), "kept_count": max_features}

    # Hotspot clustered point features can be very large.
    hotspots = out.get("hotspots")
    if isinstance(hotspots, list):
        trimmed_hotspots = []
        for hs in hotspots:
            if not isinstance(hs, dict):
                trimmed_hotspots.append(hs)
                continue
            hs = dict(hs)
            feats = hs.get("features")
            if isinstance(feats, list) and len(feats) > max_features:
                hs["features"] = feats[:max_features]
                hs["_features_trimmed"] = {"original_count": len(feats), "kept_count": max_features}
            trimmed_hotspots.append(hs)
        out["hotspots"] = trimmed_hotspots

    try:
        import json
        if len(json.dumps(out)) > 40000:
            out["_payload_notice"] = "Payload truncated for context safety (>40,000 chars). Use layer endpoints for full GeoJSON access."
            def _truncate_features(obj: Any):
                if isinstance(obj, dict):
                    if "features" in obj and isinstance(obj["features"], list):
                        obj["features"] = []
                    if "feature_changes" in obj and isinstance(obj["feature_changes"], list):
                        obj["feature_changes"] = []
                    for v in obj.values():
                        _truncate_features(v)
                elif isinstance(obj, list):
                    for item in obj:
                        _truncate_features(item)
            _truncate_features(out)
    except Exception:
        pass

    return out


# --- Pydantic Args Models ---

class TemporalProfileArgs(BaseModel):
    dataset: Any = Field(..., description="Vector GeoJSON dataset or SessionStore ref ID")


class TemporalFilterArgs(BaseModel):
    dataset: Any = Field(..., description="Vector GeoJSON dataset or SessionStore ref ID")
    temporal_field: Optional[str] = Field(default=None, description="Name of temporal timestamp or date column")
    start_time: Optional[str] = Field(default=None, description="Start ISO-8601 string or timestamp")
    end_time: Optional[str] = Field(default=None, description="End ISO-8601 string or timestamp")
    relative_window: Optional[str] = Field(default=None, description="Relative window e.g. 'last_7_days', 'past_3_months'")


class TemporalAggregateArgs(BaseModel):
    dataset: Any = Field(..., description="Vector GeoJSON dataset or SessionStore ref ID")
    temporal_field: Optional[str] = Field(default=None, description="Temporal column name")
    interval_unit: str = Field(default="day", description="Interval unit: hour, day, week, month, year")
    aggregation_func: str = Field(default="count", description="Aggregation function: count, sum, mean, min, max")
    metric_fields: List[str] = Field(default_factory=list, description="Target metric column names")


class TemporalChangeArgs(BaseModel):
    dataset_t1: Any = Field(..., description="Dataset or layer at time snapshot T1")
    dataset_t2: Any = Field(..., description="Dataset or layer at time snapshot T2")
    metric_fields: List[str] = Field(default_factory=list, description="Target attribute columns to compare")


class TemporalTrendArgs(BaseModel):
    dataset: Any = Field(..., description="Time series dataset or SessionStore ref ID")
    temporal_field: Optional[str] = Field(default=None, description="Temporal column name")
    value_field: str = Field(..., description="Target value column to analyze trend")


class SpatiotemporalHotspotArgs(BaseModel):
    dataset: Any = Field(..., description="Spatio-temporal point dataset or ref ID")
    temporal_field: Optional[str] = Field(default=None, description="Temporal column name")
    eps_spatial_m: float = Field(default=1000.0, description="Spatial search radius in meters")
    eps_temporal_days: float = Field(default=30.0, description="Temporal search window in days")
    min_samples: int = Field(default=5, description="Minimum cluster size")


class TemporalRasterArgs(BaseModel):
    raster_series: List[Any] = Field(..., description="List of raster refs or COG URLs with timestamps")
    aoi_geometry: Optional[Dict[str, Any]] = Field(default=None, description="Optional Area of Interest polygon GeoJSON")
    operation: str = Field(
        default="all",
        description=(
            "Raster temporal operation: all (default: statistics + difference + trend), "
            "difference (T2-T1 pixel difference), mean (per-slice statistics), trend (slope over series means)"
        ),
    )


def register_temporal_tools(registry: ToolRegistry):
    """Register Temporal GIS Runtime tools into ToolRegistry."""
    engine = TemporalEngine()

    @tool(
        registry,
        name="temporal_profile",
        description="自动探测数据集的时间字段、时间类型、时间跨度范围、时间分辨率、缺失与数据质量指标。",
        tier=2,
        domains=["temporal"],
        args_model=TemporalProfileArgs,
        execution_policy=ToolExecutionPolicy.THREAD,
    )
    async def temporal_profile(
        dataset: Any,
        session_id: str = "",
    ) -> dict:
        try:
            profile = await engine.profile_dataset(dataset=dataset, session_id=session_id)
            return trim_temporal_result(profile.model_dump())
        except Exception as e:
            logger.error(f"[temporal_profile] Failed: {e}", exc_info=True)
            return {"type": "error", "message": f"时间数据集探查失败: {str(e)}"}

    @tool(
        registry,
        name="temporal_filter",
        description="基于时间点、时间段区间或相对时间窗口（如'最近 7 天'、'过去 3 个月'）对 GIS 数据进行精准筛选。",
        tier=2,
        domains=["temporal"],
        args_model=TemporalFilterArgs,
        execution_policy=ToolExecutionPolicy.THREAD,
    )
    async def temporal_filter(
        dataset: Any,
        temporal_field: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        relative_window: Optional[str] = None,
        session_id: str = "",
    ) -> dict:
        try:
            t_filter = TemporalFilter(
                filter_type="range" if (start_time or end_time) else ("relative_window" if relative_window else "range"),
                start_time=start_time,
                end_time=end_time,
                relative_window=relative_window,
            )
            res = await engine.execute_filter(
                dataset=dataset,
                temporal_field=temporal_field,
                t_filter=t_filter,
                session_id=session_id,
            )
            return trim_temporal_result(res.model_dump())
        except Exception as e:
            logger.error(f"[temporal_filter] Failed: {e}", exc_info=True)
            return {"type": "error", "message": f"时间维度筛选失败: {str(e)}"}

    @tool(
        registry,
        name="temporal_aggregate",
        description="按小时、天、周、月、年时间粒度对 GIS 数据进行时间重采样与统计汇总。",
        tier=2,
        domains=["temporal"],
        args_model=TemporalAggregateArgs,
        execution_policy=ToolExecutionPolicy.THREAD,
    )
    async def temporal_aggregate(
        dataset: Any,
        temporal_field: Optional[str] = None,
        interval_unit: str = "day",
        aggregation_func: str = "count",
        metric_fields: List[str] = None,
        session_id: str = "",
    ) -> dict:
        if metric_fields is None:
            metric_fields = []
        try:
            agg_spec = TemporalAggregation(
                interval_unit=interval_unit,
                aggregation_func=aggregation_func,
                metric_fields=metric_fields,
            )
            res = await engine.execute_aggregate(
                dataset=dataset,
                temporal_field=temporal_field,
                agg_spec=agg_spec,
                session_id=session_id,
            )
            return trim_temporal_result(res.model_dump())
        except Exception as e:
            logger.error(f"[temporal_aggregate] Failed: {e}", exc_info=True)
            return {"type": "error", "message": f"时间维度统计汇总失败: {str(e)}"}

    @tool(
        registry,
        name="temporal_change",
        description="多时刻对比分析：比较 T1 与 T2（或 T1...Tn）时间快照要素数量、属性变化与空间几何演变。",
        tier=2,
        domains=["temporal"],
        args_model=TemporalChangeArgs,
        execution_policy=ToolExecutionPolicy.THREAD,
    )
    async def temporal_change(
        dataset_t1: Any,
        dataset_t2: Any,
        metric_fields: List[str] = None,
        session_id: str = "",
    ) -> dict:
        if metric_fields is None:
            metric_fields = []
        try:
            res = await engine.execute_change(
                dataset_t1=dataset_t1,
                dataset_t2=dataset_t2,
                metric_fields=metric_fields,
                session_id=session_id,
            )
            return trim_temporal_result(res.model_dump())
        except Exception as e:
            logger.error(f"[temporal_change] Failed: {e}", exc_info=True)
            return {"type": "error", "message": f"多时刻变化对比失败: {str(e)}"}

    @tool(
        registry,
        name="temporal_trend",
        description="时间序列趋势分析：计算线性变化斜率（Sen's Slope / Trend）、移动平均线与异常点检测。",
        tier=2,
        domains=["temporal"],
        args_model=TemporalTrendArgs,
        execution_policy=ToolExecutionPolicy.THREAD,
    )
    async def temporal_trend(
        dataset: Any,
        value_field: str,
        temporal_field: Optional[str] = None,
        session_id: str = "",
    ) -> dict:
        try:
            res = await engine.execute_trend(
                dataset=dataset,
                value_field=value_field,
                temporal_field=temporal_field,
                session_id=session_id,
            )
            return trim_temporal_result(res.model_dump())
        except Exception as e:
            logger.error(f"[temporal_trend] Failed: {e}", exc_info=True)
            return {"type": "error", "message": f"时间序列趋势分析失败: {str(e)}"}

    @tool(
        registry,
        name="spatiotemporal_hotspot",
        description="时空联合聚类与热点发现（ST-DBSCAN）：识别在特定空间距离与时间窗口内聚集的时空持续/偶发热点。",
        tier=3,
        domains=["temporal", "statistics"],
        args_model=SpatiotemporalHotspotArgs,
        execution_policy=ToolExecutionPolicy.CELERY,
    )
    async def spatiotemporal_hotspot(
        dataset: Any,
        temporal_field: Optional[str] = None,
        eps_spatial_m: float = 1000.0,
        eps_temporal_days: float = 30.0,
        min_samples: int = 5,
        session_id: str = "",
    ) -> dict:
        try:
            res = await engine.execute_spatiotemporal_hotspot(
                dataset=dataset,
                temporal_field=temporal_field,
                eps_spatial_m=eps_spatial_m,
                eps_temporal_days=eps_temporal_days,
                min_samples=min_samples,
                session_id=session_id,
            )
            return trim_temporal_result(res.model_dump())
        except Exception as e:
            logger.error(f"[spatiotemporal_hotspot] Failed: {e}", exc_info=True)
            return {"type": "error", "message": f"时空热点分析失败: {str(e)}"}

    @tool(
        registry,
        name="temporal_raster",
        description=(
            "遥感/栅格时间序列分析：在指定 AOI 区域内，执行多时相栅格切片提取、栅格统计差异与变化趋势分析"
            "（采用窗口化流式读取，不装载整幅大图到内存）。"
            "operation 可选 all（默认，统计+差异+趋势）/ difference / mean / trend。"
        ),
        tier=3,
        domains=["temporal", "raster"],
        args_model=TemporalRasterArgs,
    )
    async def temporal_raster(
        raster_series: List[Any],
        aoi_geometry: Optional[Dict[str, Any]] = None,
        operation: str = "all",
        session_id: str = "",
    ) -> dict:
        try:
            res = await engine.execute_temporal_raster(
                raster_series=raster_series,
                aoi_geometry=aoi_geometry,
                operation=operation,
                session_id=session_id,
            )
            return trim_temporal_result(res.model_dump())
        except Exception as e:
            logger.error(f"[temporal_raster] Failed: {e}", exc_info=True)
            return {"type": "error", "message": f"栅格时间序列分析失败: {str(e)}"}
