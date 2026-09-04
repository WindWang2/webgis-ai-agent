"""地形分析 域算法包（ADR-0099 §34 domain packs）。

描述符逐字迁自 algorithm_registry._SEED_ALGORITHMS（2026-09 split）；
中央 registry 只聚合与校验 —— 本模块是 terrain 域的唯一事实源，
新算法在各自的域模块注册，勿回填中央文件。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.algorithm_registry import AlgorithmDescriptor

ALGORITHMS: List[AlgorithmDescriptor] = [

        AlgorithmDescriptor(
            id="terrain.slope", name="坡度", category="terrain_analysis",
            capabilities=["terrain_slope"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="terrain_surface", tool_candidates=["compute_terrain"],
            cpu_cost="medium", memory_cost="high", io_cost="low",
            preferred_execution_policy="THREAD", compatible_map_models=["raster_surface"], priority=10,
        ),

        AlgorithmDescriptor(
            id="terrain.hillshade", name="山体阴影", category="terrain_analysis",
            capabilities=["terrain_hillshade"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="terrain_surface", tool_candidates=["compute_terrain"],
            cpu_cost="medium", memory_cost="high", io_cost="low",
            preferred_execution_policy="THREAD", compatible_map_models=["raster_surface"], priority=20,
        ),

        AlgorithmDescriptor(
            id="terrain.aspect", name="坡向", category="terrain_analysis",
            capabilities=["terrain_aspect"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="terrain_surface", tool_candidates=["compute_terrain"],
            cpu_cost="medium", memory_cost="high", io_cost="low",
            preferred_execution_policy="THREAD", compatible_map_models=["raster_surface"], priority=30,
        ),
]
