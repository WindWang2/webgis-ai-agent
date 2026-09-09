"""
Temporal GIS Runtime Tools for ToolRegistry.
Exposes temporal_profile, temporal_filter, temporal_aggregate,
temporal_change, temporal_trend, spatiotemporal_hotspot, and temporal_raster.

VNext（ADR-0099）：temporal_trend 增加非参数方法分支
（ols_sen 默认逐位不变 / mann_kendall / seasonal_mann_kendall，显著性
证据块附加），新增 temporal_changepoint（CUSUM 均值变点，固定种子
bootstrap 显著性）。实现位于 app/services/temporal/trend.py——工具只做
validate → 调实现 → 挂证据。

science-v3（审计 03 §8）：spatiotemporal_hotspot 话术修正为 ST-DBSCAN
真实语义（审计 F4）；新增 emerging_hotspot_analysis（R1 Emerging Hot
Spot Analysis：逐期 Gi* + 逐箱 MK → ESRI 17+1 分类），实现位于
app/lib/geo_analysis/spatiotemporal_eha.py，descriptor =
temporal.emerging_hotspot。
"""
import asyncio
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
    method: str = Field(
        default="ols_sen",
        description=(
            "Trend method: ols_sen (default: Sen's slope + OLS, historical behavior), "
            "mann_kendall (nonparametric test with tie-corrected variance), "
            "seasonal_mann_kendall (per-season pooled test; requires per-point dates)"
        ),
    )


class SpatiotemporalHotspotArgs(BaseModel):
    dataset: Any = Field(..., description="Spatio-temporal point dataset or ref ID")
    temporal_field: Optional[str] = Field(default=None, description="Temporal column name")
    eps_spatial_m: float = Field(default=1000.0, description="Spatial search radius in meters")
    eps_temporal_days: float = Field(default=30.0, description="Temporal search window in days")
    min_samples: int = Field(default=5, description="Minimum cluster size")


class EmergingHotspotArgs(BaseModel):
    dataset: Any = Field(
        ..., description=(
            "GeoJSON FeatureCollection：每个 Feature = 一个空间箱"
            "（Point 或 Polygon，取质心），属性含逐期计数字段"
        ))
    counts_field: str = Field(
        default="counts",
        description="逐期计数数组属性名（数组长度=期数，所有箱一致；缺失期按 0 计入）")
    distance_band_m: float = Field(
        default=0.0,
        description="空间权重距离段（米，含自身）；0=自动（平均 8-NN 距离，输出中披露）")
    alpha: float = Field(
        default=0.05, description="显著性水平（逐期 Gi* 经 BH-FDR 校正后 q<alpha 判显著）")


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
        execution_policy=ToolExecutionPolicy.ASYNC,  # audit #827: async solver (honest declaration)
        side_effect="deterministic_compute",
        deterministic=True,
        network=False,
        latency_class="fast",
        memory_class="medium",
        scale_class="medium",
        output_semantic_type="stats",
        result_size_policy="inline_small",
        tags=("时间字段", "时间跨度", "数据探查", "temporal", "profile", "时间分辨率"),
        failure_modes=("missing_data", "invalid_args"),
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
        capabilities=['temporal_filtering'],
        description="基于时间点、时间段区间或相对时间窗口（如'最近 7 天'、'过去 3 个月'）对 GIS 数据进行精准筛选。",
        tier=2,
        domains=["temporal"],
        args_model=TemporalFilterArgs,
        execution_policy=ToolExecutionPolicy.ASYNC,  # audit #827: async solver (honest declaration)
        side_effect="deterministic_compute",
        deterministic=False,  # relative_window 按 datetime.now 解析（filter.py parse_relative_window）
        network=False,
        latency_class="fast",
        memory_class="medium",
        scale_class="medium",
        output_semantic_type="geojson_fc",
        result_size_policy="bounded",
        tags=("时间筛选", "时间过滤", "最近7天", "时间窗口", "temporal_filter", "时间段"),
        failure_modes=("missing_data", "empty_result", "invalid_args"),
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
        execution_policy=ToolExecutionPolicy.ASYNC,  # audit #827: async solver (honest declaration)
        side_effect="deterministic_compute",
        deterministic=True,
        network=False,
        latency_class="fast",
        memory_class="medium",
        scale_class="medium",
        output_semantic_type="table",
        result_size_policy="bounded",
        tags=("时间聚合", "重采样", "按月统计", "时间粒度", "temporal_aggregate"),
        failure_modes=("missing_data", "invalid_args", "empty_result"),
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
        execution_policy=ToolExecutionPolicy.ASYNC,  # audit #827: async solver (honest declaration)
        side_effect="deterministic_compute",
        deterministic=True,
        network=False,
        latency_class="medium",
        memory_class="medium",
        scale_class="medium",
        output_semantic_type="stats",
        result_size_policy="bounded",
        tags=("多时刻对比", "变化对比", "时间快照", "temporal_change", "属性变化"),
        failure_modes=("missing_data", "empty_result", "invalid_args"),
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
        anti_examples=("唯一突变发生点（用 temporal_changepoint）",),
        description=(
            "时间序列趋势分析：移动平均、Sen 斜率/OLS 回归与异常点检测；"
            "method 可选 mann_kendall / seasonal_mann_kendall（非参数检验，"
            "附 tie 校正方差、连续性校正 z、双侧 p 与序列相关警告的显著性证据）。"
        ),
        tier=2,
        domains=["temporal"],
        args_model=TemporalTrendArgs,
        execution_policy=ToolExecutionPolicy.ASYNC,  # audit #827: async solver (honest declaration)
        side_effect="deterministic_compute",
        deterministic=True,
        network=False,
        latency_class="fast",
        memory_class="medium",
        scale_class="medium",
        output_semantic_type="stats",
        result_size_policy="inline_small",
        tags=("趋势分析", "sen斜率", "mann_kendall", "时间序列", "trend", "显著性检验"),
        failure_modes=("missing_data", "invalid_args", "empty_result"),
    )
    async def temporal_trend(
        dataset: Any,
        value_field: str,
        temporal_field: Optional[str] = None,
        method: str = "ols_sen",
        session_id: str = "",
    ) -> dict:
        from app.lib.gis.parameter_contracts import apply_contract

        try:
            params = apply_contract("temporal_trend_analysis", {"method": method})
            method_key = str(params["method"])
            if method_key == "ols_sen":
                # 缺省路径：与历史行为逐位一致。
                res = await engine.execute_trend(
                    dataset=dataset,
                    value_field=value_field,
                    temporal_field=temporal_field,
                    session_id=session_id,
                )
                return trim_temporal_result(res.model_dump())

            def _sync_mk():
                from app.services.temporal.models import TemporalAnalysisResult

                features = dataset.get("features", []) if isinstance(dataset, dict) else dataset
                trend = engine.trend_engine.analyze_trend(
                    data=features,
                    metric_name=value_field or "value",
                    time_field=temporal_field,
                    method=method_key,
                )
                sig = getattr(trend, "significance_evidence", [])
                method_warnings = list(getattr(trend, "method_warnings", []))
                return TemporalAnalysisResult(
                    analysis_type="temporal_trend",
                    status="success",
                    trend_metrics={
                        "slope": trend.slope,
                        "intercept": trend.intercept,
                        "r_squared": trend.r_squared,
                        "direction": trend.direction,
                        "slope_unit": trend.slope_unit,
                        "trend_method": method_key,
                        "significance": sig,
                        "total_points": trend.total_points,
                    },
                    warnings=method_warnings,
                )

            res = await asyncio.to_thread(_sync_mk)
            payload = trim_temporal_result(res.model_dump())
            # 显著性证据块（StatisticalSignificance.to_evidence 形状）已入
            # trend_metrics.significance；此处再挂 VNext 科学证据块。
            sig_blocks = res.trend_metrics.get("significance") or []
            uncertainty = []
            for blk in sig_blocks:
                from app.lib.gis.uncertainty import StatisticalSignificance

                uncertainty.append(StatisticalSignificance(
                    target=blk.get("target", "trend"),
                    statistic_name=blk.get("statistic_name", ""),
                    statistic_value=blk.get("statistic_value"),
                    p_value=blk.get("p_value"),
                    method=blk.get("method", ""),
                    alternative=blk.get("alternative", ""),
                ))
            _attach_trend_evidence(
                payload, method=method_key,
                n_points=res.trend_metrics.get("total_points"),
                uncertainty=uncertainty,
                warnings=res.warnings or None)
            return payload
        except Exception as e:
            logger.error(f"[temporal_trend] Failed: {e}", exc_info=True)
            return {"type": "error", "message": f"时间序列趋势分析失败: {str(e)}"}

    @tool(
        registry,
        name="spatiotemporal_hotspot",
        description=(
            "时空密度聚类（ST-DBSCAN）：在给定空间半径（米）与时间窗口（天）内"
            "发现满足 min_samples 的高密度时空点簇，输出簇计数与成员要素"
            "（描述性聚类，无显著性检验；不做逐期热点演化分类——那请用 "
            "emerging_hotspot_analysis）。"
        ),
        tier=3,
        domains=["temporal", "statistics"],
        args_model=SpatiotemporalHotspotArgs,
        execution_policy=ToolExecutionPolicy.ASYNC,  # audit #827: async solver; CELERY channel was never implemented
        deterministic=True,
        network=False,
        latency_class="slow",
        memory_class="medium",
        scale_class="large",
        output_semantic_type="geojson_fc",
        result_size_policy="bounded",
        unit_semantics="meters",
        tags=("时空热点", "st-dbscan", "聚类", "热点分析", "spatiotemporal", "事件聚集"),
        failure_modes=("missing_data", "empty_result", "memory"),
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
        deterministic=True,
        latency_class="slow",
        memory_class="heavy",
        scale_class="large",
        output_semantic_type="stats",
        result_size_policy="bounded",
        tags=("栅格时间序列", "多时相", "遥感", "raster", "变化趋势", "差异分析"),
        failure_modes=("missing_data", "invalid_args", "memory"),
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

    # VNext（ADR-0099）：时序科学工具（CUSUM 变点）——挂在既有注册入口
    # 里（app/tools/__init__.py 只认 register_temporal_tools 这个模块入口）。
    register_temporal_science_tools(registry)


def _attach_trend_evidence(
    payload: dict,
    *,
    method: str,
    n_points: Optional[int],
    uncertainty: Optional[list],
    warnings: Optional[List[str]] = None,
    algorithm_id: str = "temporal.trend",
    tool: str = "temporal_trend",
    seed: Optional[int] = None,
    parameters_applied: Optional[dict] = None,
) -> dict:
    """挂 temporal.trend / temporal.changepoint / temporal.emerging_hotspot
    的 VNext 科学证据块（descriptor 为 assumptions/limitations 单一事实源）。"""
    from app.lib.gis.algorithm_registry import get_algorithm_registry
    from app.lib.gis.scientific_evidence import build_evidence

    descriptor = get_algorithm_registry().get(algorithm_id)
    if descriptor is None:
        logger.warning("scientific evidence requested for unknown algorithm %s", algorithm_id)
        return payload
    params = {"method": method}
    if parameters_applied:
        params.update(parameters_applied)
    payload["scientific_evidence"] = build_evidence(
        descriptor,
        tool=tool,
        parameters_applied=params,
        input_facts=(
            {"feature_count": int(n_points)} if n_points is not None else {}),
        warnings=warnings,
        uncertainty=uncertainty,
        seed=seed,
    )
    return payload


def register_temporal_science_tools(registry: ToolRegistry):
    """VNext（ADR-0099）时序科学工具：CUSUM 均值变点。

    注意：本函数由 ``register_temporal_tools`` 尾部调用（app/tools/
    __init__.py 的模块注册表只认既有入口名——新工具必须挂在既有注册
    函数里，不能新增模块入口）。
    """

    @tool(
        registry,
        name="temporal_changepoint",
        anti_examples=("长期斜率方向显著性（用 temporal_trend）",),
        description=(
            "CUSUM 均值变点检测：标准化累积和定位单一均值漂移位置，"
            "固定种子 bootstrap 评估显著性（无变化零假设下重排 max-CUSUM 分布）。"
            "p < 0.05 才给出 change_point_index（否则 None，candidate_index 恒给）。"
            "\n何时用：时序均值台阶检测（政策生效/事故前后基线漂移）；"
            "\n关键约束：至少 3 个观测（建议 n ≥ 10）；同 seed 同输入结果逐位可复现。"
        ),
        tier=2,
        domains=["temporal"],
        param_descriptions={
            "values": "数值序列（按时间序；NaN/Inf 自动剔除）",
            "bootstrap_draws": "bootstrap 重排次数（100-1000，默认 200）",
            "seed": "随机种子（默认 42，固定种子策略）",
        },
        side_effect="deterministic_compute",
        deterministic=True,
        network=False,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        output_semantic_type="stats",
        result_size_policy="inline_small",
        tags=("变点检测", "cusum", "均值漂移", "变点", "changepoint", "时间序列"),
        failure_modes=("invalid_args", "empty_result"),
    )
    async def temporal_changepoint(
        values: List[float],
        bootstrap_draws: int = 200,
        seed: int = 42,
    ) -> dict:
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.gis.uncertainty import StatisticalSignificance
        from app.services.temporal.trend import cusum_change_point

        try:
            params = apply_contract(
                "temporal_changepoint_analysis",
                {"bootstrap_draws": bootstrap_draws, "seed": seed})
            res = await asyncio.to_thread(
                cusum_change_point,
                values,
                int(params["bootstrap_draws"]),
                int(params["seed"]),
            )
            payload = {
                "success": True,
                "change_point_index": res["change_point_index"],
                "candidate_index": res["candidate_index"],
                "magnitude": res["magnitude"],
                "p_value": res["p_value"],
                "significant": res["significant"],
                "alpha": res["alpha"],
                "n": res["n"],
                "bootstrap_draws": res["bootstrap_draws"],
                "seed": res["seed"],
                "warnings": res["warnings"],
                "summary": (
                    f"{'检测到' if res['significant'] else '未检测到'}显著均值变点"
                    f"（候选位置 idx={res['candidate_index']}，p={res['p_value']:.4f}，"
                    f"bootstrap={res['bootstrap_draws']} 次重排，seed={res['seed']}）"
                ),
            }
            _attach_trend_evidence(
                payload,
                method="cusum",
                n_points=res["n"],
                uncertainty=[StatisticalSignificance(
                    target="cusum_max",
                    statistic_name="max CUSUM",
                    statistic_value=res["max_cusum"],
                    p_value=res["p_value"],
                    method="bootstrap",
                    permutations=res["bootstrap_draws"],
                    alternative="greater",
                )],
                warnings=res["warnings"] or None,
                algorithm_id="temporal.changepoint",
                tool="temporal_changepoint",
                seed=int(params["seed"]),
            )
            return payload
        except Exception as e:
            logger.error(f"[temporal_changepoint] Failed: {e}", exc_info=True)
            return {"type": "error", "message": f"变点检测失败: {str(e)}"}

    # Foundation V3：经典季节分解（centered MA；非 STL，诚实披露）。
    @tool(
        registry,
        name="temporal_seasonal_decompose",
        side_effect="deterministic_compute",
        tags=('季节分解', '时间序列', '趋势', '周期'),
        description=(
            "经典季节分解（classical decomposition，Makridakis 1998）："
            "奇数窗口中心滑动平均趋势 + 相位组均值季节指数（additive 归一化"
            "Σs=0）+ 余项。"
            "\n何时用：序列含固定周期（周/月/年）且想分离趋势-季节-余项三分量；"
            "\n何时不用：需要稳健/迭代季节估计时——这是经典 MA 分解，"
            "**不是 STL**（无迭代稳健拟合、无季节平滑，对离群值敏感）；"
            "\n关键约束：period 必须为奇数（偶数窗口显式拒绝）；"
            "至少 2 个完整周期（n ≥ 2×period）；首尾 (period−1)/2 个位置"
            "趋势/余项无定义。"
        ),
        tier=2,
        domains=["temporal"],
        param_descriptions={
            "values": "数值序列（按时间序；NaN/Inf 自动剔除并披露）",
            "period": "季节周期长度（奇数整数，≥3；n ≥ 2×period 才可分解）",
            "model": "'additive'(默认) / 'multiplicative'（要求序列严格为正）",
        },
    )
    async def temporal_seasonal_decompose(
        values: List[float],
        period: int,
        model: str = "additive",
    ) -> dict:
        from app.lib.gis.parameter_contracts import apply_contract
        from app.services.temporal.trend import seasonal_decompose_narrated

        try:
            params = apply_contract(
                "seasonal_decompose_analysis",
                {"period": period, "model": model})
            res = await asyncio.to_thread(
                seasonal_decompose_narrated,
                values,
                int(params["period"]),
                str(params["model"]),
            )
            payload = {
                "success": True,
                "n": res["n"],
                "period": res["period"],
                "model": res["model"],
                "trend": res["trend"],
                "seasonal": res["seasonal"],
                "seasonal_indices": res["seasonal_indices"],
                "remainder": res["remainder"],
                "n_valid_trend": res["n_valid_trend"],
                "dropped_nonfinite": res["dropped_nonfinite"],
                "method": res["method"],
                "disclosures": res["disclosures"],
                "summary": (
                    f"经典季节分解（period={res['period']}，{res['model']}）："
                    f"{res['n']} 个观测、{res['n_valid_trend']} 个有效趋势位置。"
                    "这是经典中心滑动平均分解，不是 STL（无迭代稳健拟合）。"
                ),
            }
            _attach_trend_evidence(
                payload,
                method="classical_ma",
                n_points=res["n"],
                uncertainty=None,
                warnings=list(res["disclosures"]),
                algorithm_id="temporal.seasonal_decompose",
                tool="temporal_seasonal_decompose",
            )
            return payload
        except Exception as e:
            logger.error(f"[temporal_seasonal_decompose] Failed: {e}", exc_info=True)
            return {"type": "error", "message": f"季节分解失败: {str(e)}"}

    # science-v3 R1（审计 03 §8）：Emerging Hot Spot Analysis——
    # temporal.hotspot 原失实宣称的「箱计数 × 逐期 Gi* × MK」语义落地。
    @tool(
        registry,
        name="emerging_hotspot_analysis",
        description=(
            "时空热点演化分析（Emerging Hot Spot Analysis）：对「空间箱 × 时间期」"
            "计数量矩阵逐期计算 Getis-Ord Gi*（BH-FDR 校正后判显著），再对每箱的 "
            "Gi* z 值时序跑 Mann-Kendall，按 ESRI 分类树输出 "
            "new/consecutive/intensifying/persistent/diminishing/sporadic/"
            "oscillating/historical（热点+冷点镜像）与 none——互斥完备 17+1 类。"
            "\n何时用：想知道热点在「新生/持续/增强/消退/反复」的哪个阶段"
            "（而非单期热点图）；"
            "\n关键约束：输入须是已聚合的箱×期计数（H3/格网聚合先行，缺失期按 0）；"
            "期数 ≥2（MK 趋势需 ≥4）；箱数 ≥3；需米制坐标（自动投影 UTM）。"
        ),
        tier=2,
        domains=["temporal", "statistics"],
        args_model=EmergingHotspotArgs,
        side_effect="deterministic_compute",
        deterministic=True,
        network=False,
        latency_class="medium",
        memory_class="medium",
        scale_class="medium",
        output_semantic_type="stats",
        result_size_policy="inline_small",
        unit_semantics="meters",
        tags=("时空热点演化", "emerging_hotspot", "getis_ord", "mann_kendall",
              "热点分析", "时空立方"),
        failure_modes=("missing_data", "invalid_args", "empty_result"),
    )
    async def emerging_hotspot_analysis(
        dataset: Any,
        counts_field: str = "counts",
        distance_band_m: float = 0.0,
        alpha: float = 0.05,
        session_id: str = "",
    ) -> dict:
        import json as _json

        import numpy as np

        from app.lib.geo_analysis._vector import extract_centroids
        from app.lib.geo_analysis.spatiotemporal_eha import emerging_hotspot_narrated
        from app.lib.geo_processor.core import (
            extract_declared_crs,
            safe_parse as safe_parse_geojson,
            to_utm_gdf,
        )
        from app.lib.gis.uncertainty import StatisticalSignificance

        try:
            data = safe_parse_geojson(dataset)
            if not isinstance(data, dict) or not data.get("features"):
                return {"type": "error",
                        "message": "时空热点演化分析失败: 输入不是含要素的 GeoJSON FeatureCollection"}
            res = to_utm_gdf(data)
            if res is None or res[0] is None:
                return {"type": "error",
                        "message": "时空热点演化分析失败: 输入无有效点/面要素"}
            gdf, _ = res
            field = str(counts_field)
            if field not in gdf.columns:
                return {"type": "error",
                        "message": (f"时空热点演化分析失败: 逐期计数字段 '{field}' "
                                    "不在要素属性中（每要素属性应含逐期计数数组）")}
            series: List[List[float]] = []
            for i, v in enumerate(gdf[field].tolist()):
                if isinstance(v, (list, tuple, np.ndarray)):
                    series.append([float(x) for x in v])
                elif isinstance(v, str):
                    try:
                        series.append([float(x) for x in _json.loads(v)])
                    except Exception:
                        return {"type": "error",
                                "message": (f"时空热点演化分析失败: 第 {i} 个要素的 "
                                            f"'{field}' 不是可解析的计数数组")}
                else:
                    return {"type": "error",
                            "message": (f"时空热点演化分析失败: 第 {i} 个要素的 "
                                        f"'{field}' 不是逐期计数数组")}
            if len({len(s) for s in series}) > 1:
                return {"type": "error",
                        "message": "时空热点演化分析失败: 各箱的逐期计数数组长度不一致（期数须相同）"}
            coords = np.asarray(extract_centroids(gdf), dtype=float)
            counts = np.asarray(series, dtype=float)
            declared_crs = extract_declared_crs(data) or "EPSG:4326"

            result = await asyncio.to_thread(
                emerging_hotspot_narrated,
                counts, coords, "", float(distance_band_m), float(alpha))

            payload = dict(result)
            payload["counts_field"] = field
            payload["declared_crs"] = declared_crs
            # 报文护栏：矩阵全量内联仅在小立方时；大立方只给分类与汇总。
            if result["n_locations"] * result["n_periods"] > 5000:
                for key in ("gi_star_z", "gi_star_p", "gi_star_q_fdr"):
                    payload[key] = None
                payload["_matrices_omitted"] = (
                    "箱数×期数 > 5000，逐格 z/p/q 矩阵未内联（分类/计数/证据完整）；"
                    "需要矩阵请缩小范围或分层调用")

            flat_z = [abs(v) for row in result["gi_star_z"] for v in row]
            mk_avail = [m for m in result["mk_trend"] if m["available"]]
            uncertainty = [
                StatisticalSignificance(
                    target="gi_star_periodic",
                    statistic_name="Getis-Ord Gi* (max |z|, bins x periods)",
                    statistic_value=(max(flat_z) if flat_z else 0.0),
                    p_value=(min(min(row) for row in result["gi_star_p"])
                             if result["gi_star_p"] else None),
                    method="analytic_normal",
                    alternative="two-sided",
                    multiple_testing="BH-FDR",
                ),
                StatisticalSignificance(
                    target="mk_trend_per_bin",
                    statistic_name="Mann-Kendall z on per-bin Gi* z-series",
                    statistic_value=(max((abs(m["z"]) for m in mk_avail), default=0.0)
                                     if mk_avail else None),
                    p_value=(min(m["p_value"] for m in mk_avail) if mk_avail else None),
                    method="analytic_normal",
                    alternative="two-sided",
                ),
            ]
            warnings = list(result["warnings"] or [])
            warnings.append(
                f"坐标已自动投影到局部 UTM（米制 Gi* 权重）；声明 CRS={declared_crs}")
            _attach_trend_evidence(
                payload,
                method="emerging_hotspot_analysis",
                n_points=result["n_locations"],
                uncertainty=uncertainty,
                warnings=warnings or None,
                algorithm_id="temporal.emerging_hotspot",
                tool="emerging_hotspot_analysis",
                parameters_applied={
                    "counts_field": field,
                    "distance_band_m": float(distance_band_m),
                    "alpha": float(alpha),
                    "n_periods": result["n_periods"],
                },
            )
            return payload
        except Exception as e:
            logger.error(f"[emerging_hotspot_analysis] Failed: {e}", exc_info=True)
            return {"type": "error", "message": f"时空热点演化分析失败: {str(e)}"}
