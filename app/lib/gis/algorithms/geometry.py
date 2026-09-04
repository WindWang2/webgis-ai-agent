"""几何处理 域算法包（ADR-0099 §34 domain packs）。

描述符逐字迁自 algorithm_registry._SEED_ALGORITHMS（2026-09 split）；
中央 registry 只聚合与校验 —— 本模块是 geometry 域的唯一事实源，
新算法在各自的域模块注册，勿回填中央文件。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.algorithm_registry import AlgorithmDescriptor

ALGORITHMS: List[AlgorithmDescriptor] = [

        AlgorithmDescriptor(
            id="spatial.buffer.proximity", name="距离缓冲区",
            capabilities=["proximity_buffer"],
            input_artifact_types=["poi_feature_set", "point_feature_set",
                                  "line_feature_set", "polygon_feature_set"],
            output_artifact_type="proximity_zone",
            tool_candidates=["buffer_analysis"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD",
            compatible_map_models=["proximity_overlay"],
            priority=10,
        ),

        AlgorithmDescriptor(
            id="geometry.buffer", name="几何缓冲", category="geometry_processing",
            capabilities=["geometry_buffer"],
            input_artifact_types=["poi_feature_set", "point_feature_set", "line_feature_set", "polygon_feature_set"],
            output_artifact_type="proximity_zone", unit_requirements="meters",
            parameter_contract_ref="buffer_analysis", tool_candidates=["buffer_analysis"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", priority=20,
        ),

        AlgorithmDescriptor(
            id="geometry.clip", name="几何裁剪", category="geometry_processing",
            capabilities=["geometry_clip"],
            input_artifact_types=["poi_feature_set", "polygon_feature_set"],
            output_artifact_type="polygon_feature_set", tool_candidates=["clip_layer"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", priority=10,
        ),

        AlgorithmDescriptor(
            id="geometry.dissolve", name="融合溶解", category="geometry_processing",
            capabilities=["geometry_dissolve"],
            input_artifact_types=["polygon_feature_set", "admin_boundary_set"],
            output_artifact_type="polygon_feature_set", tool_candidates=["dissolve_layer"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", priority=10,
        ),

        AlgorithmDescriptor(
            id="geometry.spatial_join", name="空间连接", category="spatial_relationship",
            capabilities=["spatial_join"],
            input_artifact_types=["poi_feature_set", "polygon_feature_set"],
            output_artifact_type="polygon_feature_set", tool_candidates=["spatial_join"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", priority=20,
        ),
]
