"""密度与点格局 能力包（ADR-0099 §34 domain packs）。

描述符逐字迁自 capability_registry._SEED_CAPS（2026-09 split）。
新能力在各自域模块注册，勿回填中央文件。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.capability_registry import CapabilityDescriptor

CAPABILITIES: List[CapabilityDescriptor] = [

        CapabilityDescriptor(
            id="density_surface", name="视觉密度面", category="density",
            description="视觉热力（回答『大概哪儿密』，非定量）。",
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_types=["density_surface"],
            geometry_requirements=["point"],
            deterministic=False,
            compatible_map_models=["visual_heatmap"],
            # ADR-0083：超过原生渲染上限（FETCH_FEATURE_CAP 20k）时的确定性
            # 降级 —— 聚合通道（H3/渔网）承接大规模点数据。与 grid_binning 的
            # 反向 fallback（稀疏点 → 视觉热力）构成双向边，环路由 resolver
            # 的 _visited 守卫截断。
            fallback_capabilities=["grid_binning"],
            purpose_template="密度面",
        ),

        CapabilityDescriptor(
            id="kde_density", name="核密度估计", category="density",
            description="KDE 连续密度面/等值线（定量密度表达）。",
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_types=["density_surface"],
            geometry_requirements=["point"],
            deterministic=False,
            compatible_map_models=["visual_heatmap", "isoline_contour"],
            purpose_template="核密度分析",
        ),

        CapabilityDescriptor(
            id="hotspot", name="热点显著性分析", category="statistics",
            description="Getis-Ord Gi* 等空间聚类显著性检验。",
            input_artifact_types=["poi_feature_set", "point_feature_set", "grid_aggregate"],
            output_artifact_types=["hotspot_result"],
            geometry_requirements=["point"],
            compatible_map_models=["hotspot_overlay"],
            purpose_template="热点显著性分析",
        ),

        CapabilityDescriptor(
            id="analytical_density", name="分析密度", category="density",
            description="定量密度（每平方公里密度等）——拒绝把视觉热力当定量结果。",
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_types=["density_surface", "admin_aggregate_table"],
            geometry_requirements=["point"],
            deterministic=False,
            compatible_map_models=["administrative_choropleth", "aggregate_grid"],
            purpose_template="分析密度面/密度聚合",
        ),

        CapabilityDescriptor(
            id="point_pattern_analysis", name="点格局分析", category="density",
            description="点格局统计（Ripley K / 样方 χ² / NNI / 密度聚类）——"
                        "回答『点的空间分布是聚集/均匀/随机』，与密度面表达正交。",
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_types=["stats_table", "hotspot_result"],
            geometry_requirements=["point"],
            deterministic=True,
            purpose_template="点格局分析",
        ),
]
