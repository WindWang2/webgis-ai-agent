"""时序分析 域算法包（ADR-0099 §34 domain packs）。

描述符逐字迁自 algorithm_registry._SEED_ALGORITHMS（2026-09 split）；
中央 registry 只聚合与校验 —— 本模块是 temporal 域的唯一事实源，
新算法在各自的域模块注册，勿回填中央文件。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.algorithm_registry import AlgorithmDescriptor

ALGORITHMS: List[AlgorithmDescriptor] = [

        AlgorithmDescriptor(
            id="temporal.profile", name="时间画像", category="temporal_analysis",
            capabilities=["temporal_profile"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="stats_table", tool_candidates=["temporal_profile"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            preferred_execution_policy="INLINE", priority=10,
        ),

        AlgorithmDescriptor(
            id="temporal.aggregate", name="时间聚合", category="temporal_analysis",
            capabilities=["temporal_aggregate"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="stats_table", tool_candidates=["temporal_aggregate"],
            cpu_cost="medium", memory_cost="low", io_cost="low",
            preferred_execution_policy="THREAD", priority=10,
        ),

        AlgorithmDescriptor(
            id="temporal.trend", name="时序趋势", category="temporal_analysis",
            capabilities=["temporal_trend"],
            input_artifact_types=["stats_table"],
            output_artifact_type="stats_table", tool_candidates=["temporal_trend"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            preferred_execution_policy="INLINE", priority=10,
        ),

        AlgorithmDescriptor(
            id="temporal.change", name="时序变化", category="temporal_analysis",
            capabilities=["change_detection"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="change_set", tool_candidates=["temporal_change"],
            cpu_cost="medium", memory_cost="low", io_cost="low",
            preferred_execution_policy="THREAD", priority=10,
        ),

        AlgorithmDescriptor(
            id="temporal.hotspot", name="时空热点", category="temporal_analysis",
            capabilities=["spatiotemporal_clustering"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="hotspot_result", tool_candidates=["spatiotemporal_hotspot"],
            cpu_cost="high", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", priority=15,
        ),

        AlgorithmDescriptor(
            id="temporal.raster_ts", name="时序栅格", category="temporal_analysis",
            capabilities=["temporal_trend"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface", tool_candidates=["temporal_raster"],
            cpu_cost="medium", memory_cost="medium", io_cost="high",
            preferred_execution_policy="THREAD", priority=30,
        ),
]
