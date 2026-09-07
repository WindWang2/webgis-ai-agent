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

        # ── Foundation V2 (A3)：点格局/时空统计扩展能力 ─────────────────
        CapabilityDescriptor(
            id="nearest_neighbor_functions", name="最近邻距离函数", category="density",
            description="G/F/J 距离函数（Diggle 1983 / van Lieshout–Baddeley 1996）——"
                        "最近邻与空空间分布的 CDF 对比 CSR，配固定种子模拟包络。",
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_types=["stats_table"],
            geometry_requirements=["point"],
            deterministic=True,
            purpose_template="最近邻距离函数分析",
        ),

        CapabilityDescriptor(
            id="pair_correlation_function", name="成对相关函数", category="density",
            description="成对相关函数 g(r)=K′(r)/(2πr)（Illian 2008）——"
                        "随半径的聚集/规则尺度谱，配固定种子 CSR 包络。",
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_types=["stats_table"],
            geometry_requirements=["point"],
            deterministic=True,
            purpose_template="成对相关函数分析",
        ),

        CapabilityDescriptor(
            id="cross_k_function", name="双变量交叉 K 函数", category="density",
            description="双变量 K12（Besag 1977 随机标记）——两类点间的空间"
                        "吸引/分离检验（如连锁品牌 vs 竞品共现）。",
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_types=["stats_table"],
            geometry_requirements=["point"],
            deterministic=True,
            purpose_template="双变量点格局分析",
        ),

        CapabilityDescriptor(
            id="space_time_interaction", name="时空交互检验", category="density",
            description="Knox 时空交互检验（1964）——事件在空间与时间上是否"
                        "同时邻近（如传染病聚集），时间置换 p 值。",
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_types=["stats_table"],
            geometry_requirements=["point"],
            deterministic=True,
            purpose_template="时空交互检验",
        ),

        # ── Foundation V3：时空 K / Mantel / 双变量 g12 ─────────────────
        CapabilityDescriptor(
            id="space_time_k_function", name="时空 K 函数", category="density",
            description="时空 K 函数 K_st(r,t)（Diggle 1995）——二阶时空"
                        "聚集强度随空间/时间尺度的谱（与 Knox 单一阈值检验互补），"
                        "时间置换包络。",
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_types=["stats_table"],
            geometry_requirements=["point"],
            deterministic=True,
            purpose_template="时空 K 函数分析",
        ),

        CapabilityDescriptor(
            id="mantel_test", name="Mantel 时空检验", category="density",
            description="Mantel 检验（1967）——空间距离矩阵与时间距离矩阵的"
                        "相关（标准化 Mantel r），时间标签置换 p 值。",
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_types=["stats_table"],
            geometry_requirements=["point"],
            deterministic=True,
            purpose_template="Mantel 时空关联检验",
        ),

        CapabilityDescriptor(
            id="cross_pair_correlation", name="双变量成对相关函数 g12", category="density",
            description="双变量 g12(r)=K12′(r)/(2πr)——两类点空间吸引/相斥"
                        "随尺度的谱（cross-K 的导数形式），随机标记包络。",
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_types=["stats_table"],
            geometry_requirements=["point"],
            deterministic=True,
            purpose_template="双变量成对相关分析",
        ),
]
