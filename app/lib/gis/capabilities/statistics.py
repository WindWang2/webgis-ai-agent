"""空间统计 能力包（ADR-0099 §34 domain packs）。

描述符逐字迁自 capability_registry._SEED_CAPS（2026-09 split）。
新能力在各自域模块注册，勿回填中央文件。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.capability_registry import CapabilityDescriptor

CAPABILITIES: List[CapabilityDescriptor] = [

        CapabilityDescriptor(
            id="point_profile", name="数据画像", category="statistics",
            description="点数/几何/字段画像（不产出新数据，产出元数据）。",
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_types=["point_feature_set"],
            geometry_requirements=["point"],
            purpose_template="数据画像（点数/几何/字段）",
        ),

        CapabilityDescriptor(
            id="category_breakdown", name="类别构成统计", category="statistics",
            description="按类别字段统计构成。",
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_types=["stats_table"],
            purpose_template="类别构成统计",
        ),

        CapabilityDescriptor(
            id="global_morans_i", name="全局莫兰指数", category="statistics",
            description="全局空间自相关检验。",
            input_artifact_types=["poi_feature_set", "point_feature_set", "admin_aggregate_table"],
            output_artifact_types=["stats_table"],
            purpose_template="全局莫兰指数",
        ),

        CapabilityDescriptor(
            id="local_morans_i", name="局部莫兰/LISA", category="statistics",
            description="局部热点/冷点聚类。",
            input_artifact_types=["poi_feature_set", "point_feature_set", "admin_aggregate_table"],
            output_artifact_types=["hotspot_result"],
            compatible_map_models=["hotspot_overlay"],
            purpose_template="LISA 聚类",
        ),

        CapabilityDescriptor(
            id="getis_ord_gi_star", name="Getis-Ord Gi*", category="statistics",
            description="热点显著性 Gi*。",
            input_artifact_types=["poi_feature_set", "point_feature_set", "grid_aggregate"],
            output_artifact_types=["hotspot_result"],
            compatible_map_models=["hotspot_overlay"],
            purpose_template="Gi* 热点分析",
        ),

        CapabilityDescriptor(
            id="spatiotemporal_clustering", name="时空聚类", category="statistics",
            description="ST-DBSCAN 等时空聚类（与 LISA 局部自相关是不同检验）。",
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_types=["hotspot_result"],
            purpose_template="时空聚类",
        ),
]
