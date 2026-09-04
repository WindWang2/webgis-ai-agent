"""插值 域算法包（ADR-0099 §34 domain packs）。

描述符逐字迁自 algorithm_registry._SEED_ALGORITHMS（2026-09 split）；
中央 registry 只聚合与校验 —— 本模块是 interpolation 域的唯一事实源，
新算法在各自的域模块注册，勿回填中央文件。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.algorithm_registry import AlgorithmDescriptor

ALGORITHMS: List[AlgorithmDescriptor] = [

        AlgorithmDescriptor(
            id="interpolation.idw", name="IDW 插值", category="interpolation",
            capabilities=["spatial_interpolation"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="terrain_surface", unit_requirements="meters",
            parameter_contract_ref="idw_interpolation", tool_candidates=["idw_interpolation"],
            cpu_cost="high", memory_cost="high", io_cost="low",
            preferred_execution_policy="CELERY", compatible_map_models=["raster_surface"],
            fallback_algorithms=["interpolation.kriging"], priority=10,
        fallback_semantics={"interpolation.kriging": "equivalent"},
        ),

        AlgorithmDescriptor(
            id="interpolation.kriging", name="普通克里金插值", category="interpolation",
            capabilities=["spatial_interpolation"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="terrain_surface", runtime_status="native",
            # 与核心 MIN_SAMPLES 对齐（resolver 侧同值镜像；去重后 <8 点克里金无意义）
            min_features=8,
            parameter_contract_ref="kriging_interpolation",
            tool_candidates=["kriging_interpolation"],
            cpu_cost="high", memory_cost="high", io_cost="low",
            preferred_execution_policy="CELERY", compatible_map_models=["raster_surface"],
            fallback_algorithms=["interpolation.idw"], priority=20,
        fallback_semantics={"interpolation.idw": "approximation"},
        ),
]
