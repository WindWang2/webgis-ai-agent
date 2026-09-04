"""遥感 域算法包（ADR-0099 §34 domain packs）。

描述符逐字迁自 algorithm_registry._SEED_ALGORITHMS（2026-09 split）；
中央 registry 只聚合与校验 —— 本模块是 remote_sensing 域的唯一事实源，
新算法在各自的域模块注册，勿回填中央文件。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.algorithm_registry import AlgorithmDescriptor

ALGORITHMS: List[AlgorithmDescriptor] = [

        AlgorithmDescriptor(
            id="remote.ndvi", name="NDVI 植被指数", category="remote_sensing",
            capabilities=["ndvi"],
            input_artifact_types=["raster_surface", "terrain_surface"],
            output_artifact_type="raster_surface", tool_candidates=["compute_ndvi", "compute_vegetation_index"],
            cpu_cost="medium", memory_cost="high", io_cost="medium",
            preferred_execution_policy="THREAD", compatible_map_models=["raster_surface"], priority=10,
        ),

        AlgorithmDescriptor(
            id="remote.change.raster", name="双时相栅格变化检测", category="remote_sensing",
            capabilities=["raster_change_detection"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["detect_raster_change"],
            cpu_cost="high", memory_cost="medium", io_cost="medium",
            preferred_execution_policy="THREAD",
            compatible_map_models=["raster_surface"], priority=10,
            version="1.0",
        ),
]
