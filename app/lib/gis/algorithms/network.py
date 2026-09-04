"""网络分析 域算法包（ADR-0099 §34 domain packs）。

描述符逐字迁自 algorithm_registry._SEED_ALGORITHMS（2026-09 split）；
中央 registry 只聚合与校验 —— 本模块是 network 域的唯一事实源，
新算法在各自的域模块注册，勿回填中央文件。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.algorithm_registry import AlgorithmDescriptor

ALGORITHMS: List[AlgorithmDescriptor] = [

        AlgorithmDescriptor(
            id="network.isochrone", name="网络等时圈",
            capabilities=["service_area"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="service_area",
            tool_candidates=["isochrone_analysis"],
            cpu_cost="high", memory_cost="medium", io_cost="high",
            preferred_execution_policy="ASYNC",
            compatible_map_models=["proximity_overlay"],
            priority=10,
        ),

        AlgorithmDescriptor(
            id="network.service_area.simple", name="简化服务区（速度表缓冲）",
            capabilities=["service_area"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="service_area",
            approximate=True,
            tool_candidates=["service_area_simple"],
            cpu_cost="medium", memory_cost="low", io_cost="low",
            preferred_execution_policy="ASYNC",
            compatible_map_models=["proximity_overlay"],
            fallback_algorithms=["network.isochrone"],
            priority=20,
        fallback_semantics={"network.isochrone": "equivalent"},
        ),

        AlgorithmDescriptor(
            id="network.shortest_path", name="最短路径", category="network_analysis",
            capabilities=["shortest_path"],
            output_artifact_type="line_feature_set", tool_candidates=["network_shortest_path"],
            cpu_cost="high", memory_cost="medium", io_cost="high",
            preferred_execution_policy="ASYNC", priority=10,
        ),

        AlgorithmDescriptor(
            id="network.closest_facility", name="最近设施", category="network_analysis",
            capabilities=["closest_facility"],
            output_artifact_type="line_feature_set",
            tool_candidates=["network_closest_facility", "nearest_facility"],
            cpu_cost="high", memory_cost="medium", io_cost="high",
            preferred_execution_policy="ASYNC", priority=10,
            fallback_algorithms=["network.shortest_path"],
        fallback_semantics={"network.shortest_path": "approximation"},
        ),

        AlgorithmDescriptor(
            id="network.od_matrix", name="OD 成本矩阵", category="network_analysis",
            capabilities=["od_matrix"],
            output_artifact_type="od_matrix",
            tool_candidates=["network_od_matrix", "distance_matrix_cn"],
            cpu_cost="high", memory_cost="medium", io_cost="high",
            preferred_execution_policy="ASYNC",
            compatible_map_models=["flow_od_arc"], priority=10,
        ),

        AlgorithmDescriptor(
            # ADR-0092 D：OD 边 → 有界带权流向线要素（flow_od_arc 渲染输入）。
            id="flow.od_arc_build", name="OD 流向构建", category="flow_analysis",
            capabilities=["od_flow_mapping"],
            input_artifact_type="od_table",
            output_artifact_type="line_feature_set",
            tool_candidates=["od_flow_edges"],
            cpu_cost="medium", memory_cost="medium", io_cost="medium",
            preferred_execution_policy="ASYNC",
            compatible_map_models=["flow_od_arc"], priority=10,
        ),

        AlgorithmDescriptor(
            id="network.service_area.multi", name="多断点服务区", category="network_analysis",
            capabilities=["service_area"],
            output_artifact_type="service_area", tool_candidates=["network_service_area"],
            cpu_cost="high", memory_cost="medium", io_cost="high",
            preferred_execution_policy="ASYNC",
            compatible_map_models=["proximity_overlay"], priority=25,
        ),

        AlgorithmDescriptor(
            id="network.accessibility", name="网络可达性", category="network_analysis",
            capabilities=["accessibility"],
            output_artifact_type="service_area", tool_candidates=["network_accessibility"],
            cpu_cost="high", memory_cost="medium", io_cost="high",
            preferred_execution_policy="ASYNC",
            compatible_map_models=["proximity_overlay"], priority=10,
        ),

        AlgorithmDescriptor(
            id="network.route_optimization", name="路线优化", category="network_analysis",
            capabilities=["route_optimization"],
            output_artifact_type="line_feature_set", tool_candidates=["optimize_route"],
            cpu_cost="high", memory_cost="medium", io_cost="high",
            preferred_execution_policy="ASYNC", priority=10,
        ),

        AlgorithmDescriptor(
            id="network.location_allocation", name="区位配置", category="network_analysis",
            capabilities=["location_allocation"],
            output_artifact_type="point_feature_set",
            tool_candidates=["location_allocation"],
            cpu_cost="high", memory_cost="medium", io_cost="medium",
            preferred_execution_policy="ASYNC", priority=30,
        ),

        AlgorithmDescriptor(
            id="network.optimize_route", name="路线优化（VRP）", category="network_analysis",
            capabilities=["route_optimization"],
            output_artifact_type="line_feature_set",
            tool_candidates=["optimize_route"],
            cpu_cost="high", memory_cost="medium", io_cost="medium",
            preferred_execution_policy="ASYNC", priority=30,
        ),
]
