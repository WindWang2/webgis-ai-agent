"""空间统计 域算法包（ADR-0099 §34 domain packs）。

描述符逐字迁自 algorithm_registry._SEED_ALGORITHMS（2026-09 split）；
中央 registry 只聚合与校验 —— 本模块是 statistics 域的唯一事实源，
新算法在各自的域模块注册，勿回填中央文件。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.algorithm_registry import AlgorithmDescriptor

ALGORITHMS: List[AlgorithmDescriptor] = [

        AlgorithmDescriptor(
            id="profile.spatial.stats", name="空间数据画像",
            capabilities=["point_profile"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="point_feature_set",
            geometry_requirements=["point"],
            tool_candidates=["spatial_stats", "webgis_source_profile"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            preferred_execution_policy="INLINE",
            priority=10,
        ),

        AlgorithmDescriptor(
            id="stats.category.breakdown", name="类别构成统计",
            capabilities=["category_breakdown"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="stats_table",
            tool_candidates=["spatial_stats"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            preferred_execution_policy="INLINE",
            priority=10,
        ),

        AlgorithmDescriptor(
            id="spatial.hotspot.local", name="局部热点显著性（Getis-Ord Gi*）",
            capabilities=["hotspot"],
            input_artifact_types=["poi_feature_set", "point_feature_set", "grid_aggregate"],
            output_artifact_type="hotspot_result",
            geometry_requirements=["point"],
            tool_candidates=["hotspot_analysis"],
            cpu_cost="high", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD",
            compatible_map_models=["hotspot_overlay"],
            priority=10,
        ),

        AlgorithmDescriptor(
            id="stats.morans_i", name="全局莫兰指数", category="spatial_statistics",
            capabilities=["global_morans_i"],
            input_artifact_types=["admin_aggregate_table", "grid_aggregate"],
            output_artifact_type="stats_table", tool_candidates=["moran_i"],
            cpu_cost="medium", memory_cost="low", io_cost="low",
            preferred_execution_policy="THREAD", priority=10,
        ),

        AlgorithmDescriptor(
            id="stats.h3_lisa", name="H3 LISA 局部自相关", category="spatial_statistics",
            capabilities=["local_morans_i"],
            input_artifact_types=["grid_aggregate", "admin_aggregate_table"],
            output_artifact_type="hotspot_result", tool_candidates=["h3_lisa"],
            cpu_cost="high", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", compatible_map_models=["hotspot_overlay"], priority=10,
        ),

        AlgorithmDescriptor(
            id="stats.h3_hotspot", name="H3 Gi* 热点", category="spatial_statistics",
            capabilities=["getis_ord_gi_star"],
            input_artifact_types=["grid_aggregate", "admin_aggregate_table"],
            output_artifact_type="hotspot_result", tool_candidates=["h3_lisa"],
            cpu_cost="high", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", compatible_map_models=["hotspot_overlay"], priority=15,
        ),

        AlgorithmDescriptor(
            id="stats.st_dbscan", name="时空 DBSCAN 聚类", category="point_pattern",
            capabilities=["spatiotemporal_clustering"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="hotspot_result", tool_candidates=["st_dbscan", "spatial_cluster"],
            cpu_cost="high", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", priority=20,
        ),
]
