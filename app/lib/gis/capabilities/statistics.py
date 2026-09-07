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
            id="global_gearys_c", name="全局 Geary 指数", category="statistics",
            description="全局空间自相关检验（成对差版本，对局部差异更敏感）。",
            input_artifact_types=["poi_feature_set", "point_feature_set", "admin_aggregate_table"],
            output_artifact_types=["stats_table"],
            purpose_template="全局 Geary 指数",
        ),

        CapabilityDescriptor(
            id="general_g", name="Getis-Ord General G", category="statistics",
            description="全局高值聚集检验（非负值；高值/低值聚集判别）。",
            input_artifact_types=["poi_feature_set", "point_feature_set", "admin_aggregate_table"],
            output_artifact_types=["stats_table"],
            purpose_template="General G 高值聚集检验",
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

        # science-v3 R1（审计 03 §8）：Emerging Hot Spot 演化分类能力——
        # 热点的时间形态学（new/persistent/intensifying/...），与单期 Gi*
        # 热点图（getis_ord_gi_star）和 ST-DBSCAN 时空聚类正交。
        CapabilityDescriptor(
            id="emerging_hotspot_analysis", name="时空热点演化（EHA）",
            category="statistics",
            description="逐期 Getis-Ord Gi*（BH-FDR）+ 逐箱 Mann-Kendall 的 "
                        "Emerging Hot Spot 演化分类（new/consecutive/"
                        "intensifying/persistent/diminishing/sporadic/"
                        "oscillating/historical + none，热点冷点镜像）。",
            input_artifact_types=["poi_feature_set", "point_feature_set",
                                  "grid_aggregate", "admin_aggregate_table"],
            output_artifact_types=["stats_table"],
            purpose_template="时空热点演化分析",
        ),

        CapabilityDescriptor(
            id="spatiotemporal_clustering", name="时空聚类", category="statistics",
            description="ST-DBSCAN 等时空聚类（与 LISA 局部自相关是不同检验）。",
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_types=["hotspot_result"],
            purpose_template="时空聚类",
        ),

        # ── Foundation V2（A1）：局部 Geary / Join Count / 双变量 Moran /
        #    地理探测器 / 空间回归 / 权重敏感性 / GWR ──────────────────
        CapabilityDescriptor(
            id="local_gearys_c", name="局部 Geary's C", category="statistics",
            description="局部相似性/相异性检测（Local Geary's C_i，Anselin 1995；"
                        "与 LISA 的方向配对互补）。",
            input_artifact_types=["admin_aggregate_table", "grid_aggregate"],
            output_artifact_types=["hotspot_result"],
            compatible_map_models=["hotspot_overlay"],
            purpose_template="局部 Geary 相似性聚类",
        ),

        CapabilityDescriptor(
            id="join_count_statistics", name="Join Count 统计", category="statistics",
            description="二值场的邻接同异类连接计数检验（Cliff-Ord free sampling）。",
            input_artifact_types=["admin_aggregate_table"],
            output_artifact_types=["stats_table"],
            purpose_template="Join Count 二值空间关联",
        ),

        CapabilityDescriptor(
            id="bivariate_morans_i", name="双变量 Moran's I", category="statistics",
            description="x 与 W·y 的空间共变（Wartenberg 1985；共位相关非因果）。",
            input_artifact_types=["admin_aggregate_table", "grid_aggregate"],
            output_artifact_types=["stats_table"],
            purpose_template="双变量空间共变",
        ),

        CapabilityDescriptor(
            id="geographical_detector", name="地理探测器", category="statistics",
            description="分层解释力 q 统计与双因子交互检测（Wang 2010）。",
            input_artifact_types=["admin_aggregate_table", "grid_aggregate",
                                  "poi_feature_set", "point_feature_set"],
            output_artifact_types=["stats_table"],
            purpose_template="地理探测器因子/交互检测",
        ),

        CapabilityDescriptor(
            id="spatial_regression", name="空间回归", category="statistics",
            description="OLS+空间诊断 / SLX / SAR-ML / SEM-ML（LM 决策树支撑）。",
            input_artifact_types=["admin_aggregate_table", "grid_aggregate",
                                  "poi_feature_set", "point_feature_set"],
            output_artifact_types=["stats_table"],
            purpose_template="空间回归建模",
        ),

        CapabilityDescriptor(
            id="weights_sensitivity", name="权重敏感性", category="statistics",
            description="同一统计量在多个空间权重方案下的结论稳定性包络。",
            input_artifact_types=["admin_aggregate_table", "grid_aggregate",
                                  "poi_feature_set", "point_feature_set"],
            output_artifact_types=["stats_table"],
            purpose_template="权重方案敏感性分析",
        ),

        CapabilityDescriptor(
            id="gwr", name="地理加权回归 (GWR)", category="statistics",
            description="空间非平稳性探测：逐观测局地 WLS（bisquare 自适应核）。",
            input_artifact_types=["admin_aggregate_table", "grid_aggregate",
                                  "poi_feature_set", "point_feature_set"],
            output_artifact_types=["stats_table"],
            purpose_template="地理加权回归",
        ),

        # ── Foundation V3：真正的新能力族（局部 Join Count 是二值局部
        #    统计、双变量局部 Moran 是双变量 LISA、权重诊断是权重结构
        #    体检 —— 均不与既有能力同语义，不拆现有能力凑数）──────────
        CapabilityDescriptor(
            id="local_join_count", name="局部 Join Count", category="statistics",
            description="二值场的逐位置共位簇检测（Anselin & Li 2019；"
                        "全局 Join Count 的局部对应物）。",
            input_artifact_types=["admin_aggregate_table"],
            output_artifact_types=["hotspot_result"],
            compatible_map_models=["hotspot_overlay"],
            purpose_template="二值共位簇检测",
        ),

        CapabilityDescriptor(
            id="bivariate_local_moran", name="双变量局部 Moran", category="statistics",
            description="x 与 y 空间滞后的逐位置共位/互斥检测（双变量 LISA；"
                        "与全局双变量 Moran、单变量 LISA 是不同检验）。",
            input_artifact_types=["admin_aggregate_table", "grid_aggregate"],
            output_artifact_types=["hotspot_result"],
            compatible_map_models=["hotspot_overlay"],
            purpose_template="双变量 LISA 共位检测",
        ),

        CapabilityDescriptor(
            id="spatial_weights_diagnostics", name="空间权重诊断", category="statistics",
            description="权重结构体检：稀疏度/对称性/邻居分布/孤岛/连通分量。",
            input_artifact_types=["admin_aggregate_table", "grid_aggregate",
                                  "poi_feature_set", "point_feature_set"],
            output_artifact_types=["stats_table"],
            purpose_template="空间权重结构诊断",
        ),

        # ── Foundation V3：经验贝叶斯率平滑（与 rate_aggregation 是不同
        #    语义——聚合产出原始率；EB 平滑对原始率做先验收缩）──────────
        CapabilityDescriptor(
            id="rate_smoothing", name="经验贝叶斯率平滑", category="statistics",
            description="计数/人口率的经验贝叶斯收缩平滑（Marshall 1991 MOM "
                        "先验；全局或邻居先验；零人口区不产率值）。",
            input_artifact_types=["admin_boundary_set", "admin_aggregate_table",
                                  "polygon_feature_set"],
            output_artifact_types=["admin_aggregate_table"],
            compatible_map_models=["administrative_choropleth"],
            purpose_template="经验贝叶斯率平滑",
        ),
]
