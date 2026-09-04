"""空间聚合 域算法包（ADR-0099 §34 domain packs）。

描述符逐字迁自 algorithm_registry._SEED_ALGORITHMS（2026-09 split）；
中央 registry 只聚合与校验 —— 本模块是 aggregation 域的唯一事实源，
新算法在各自的域模块注册，勿回填中央文件。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.algorithm_registry import AlgorithmDescriptor

ALGORITHMS: List[AlgorithmDescriptor] = [

        AlgorithmDescriptor(
            id="spatial.aggregate.admin", name="点落入面聚合（行政区统计）",
            capabilities=["admin_aggregation"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="admin_aggregate_table",
            geometry_requirements=["point"],
            complexity="O(N·M) 点×面",
            tool_candidates=["spatial_aggregate"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD",
            compatible_map_models=["administrative_choropleth", "administrative_aggregation", "extrusion_3d"],
            priority=10,
        ),

        AlgorithmDescriptor(
            id="spatial.grid.h3", name="H3 六边形聚合",
            capabilities=["grid_binning"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="grid_aggregate",
            geometry_requirements=["point"],
            complexity="O(N) H3 索引",
            tool_candidates=["h3_binning"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD",
            compatible_map_models=["aggregate_grid"],
            fallback_algorithms=["spatial.grid.fishnet"],
            priority=10,
        fallback_semantics={"spatial.grid.fishnet": "approximation"},
        ),

        AlgorithmDescriptor(
            id="spatial.grid.fishnet", name="渔网格网聚合",
            capabilities=["grid_binning"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="grid_aggregate",
            geometry_requirements=["point"],
            complexity="O(N·M) 点×格",
            tool_candidates=["fishnet_grid"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD",
            compatible_map_models=["aggregate_grid"],
            fallback_algorithms=["spatial.grid.h3"],
            priority=20,
        fallback_semantics={"spatial.grid.h3": "approximation"},
        ),
]
