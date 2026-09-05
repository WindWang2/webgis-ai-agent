"""栅格分析 域算法包（ADR-0099 §34 domain packs）。

描述符逐字迁自 algorithm_registry._SEED_ALGORITHMS（2026-09 split）；
中央 registry 只聚合与校验 —— 本模块是 raster 域的唯一事实源，
新算法在各自的域模块注册，勿回填中央文件。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.algorithm_registry import AlgorithmDescriptor

ALGORITHMS: List[AlgorithmDescriptor] = [

        AlgorithmDescriptor(
            id="remote.zonal_stats", name="分区统计", category="raster_analysis",
            capabilities=["zonal_statistics"],
            input_artifact_types=["raster_surface", "polygon_feature_set"],
            output_artifact_type="stats_table", tool_candidates=["zonal_stats"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", priority=20,
        ),

        AlgorithmDescriptor(
            id="raster.algebra", name="栅格计算器（窗口化）", category="raster_analysis",
            capabilities=["band_math"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["raster_calculator"],
            cpu_cost="high", memory_cost="medium", io_cost="medium",
            preferred_execution_policy="THREAD", priority=15,
            version="3.0",
        ),

        AlgorithmDescriptor(
            id="raster.reclassify.rule", name="规则重分类", category="raster_analysis",
            capabilities=["raster_reclassify"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["raster_reclassify"],
            cpu_cost="medium", memory_cost="medium", io_cost="medium",
            preferred_execution_policy="THREAD", priority=10,
        ),

        AlgorithmDescriptor(
            id="raster.resample.grid", name="网格重采样/重投影", category="raster_analysis",
            capabilities=["raster_resample"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["raster_resample"],
            cpu_cost="high", memory_cost="medium", io_cost="high",
            preferred_execution_policy="THREAD", priority=10,
        ),
]
