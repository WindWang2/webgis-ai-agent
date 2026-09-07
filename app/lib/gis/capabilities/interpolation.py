"""插值 能力包（ADR-0099 §34 domain packs）。

描述符逐字迁自 capability_registry._SEED_CAPS（2026-09 split）。
新能力在各自域模块注册，勿回填中央文件。

Foundation V2 · A2：新增 triangulation_interpolation（TIN）、trend_surface
（全局多项式趋势面）、regression_kriging（回归克里金）、
interpolation_model_selection（插值模型比较选择）——每个能力均已有
≥1 个算法注册（registry validate() 门）。

Foundation V3（Geostatistics/Interpolation 批次）：新增 indicator_kriging
（指示克里金概率面）、cokriging（协同定位协同克里金 MM1）、
variogram_analysis（方向变异函数 + 模型选择，统计表输出）、block_kriging
（块支撑克里金）。nearest_neighbor 归入既有 spatial_interpolation（通用
插值能力）、natural_neighbor 归入既有 triangulation_interpolation（同为
Delaunay 家族、凸包外不外推）——不拆语义重复的新能力。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.capability_registry import CapabilityDescriptor

CAPABILITIES: List[CapabilityDescriptor] = [

        CapabilityDescriptor(
            id="spatial_interpolation", name="空间插值", category="analysis",
            description="IDW / Kriging 等插值。",
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_types=["terrain_surface"],
            purpose_template="空间插值",
        ),

        CapabilityDescriptor(
            id="triangulation_interpolation", name="三角网插值", category="analysis",
            description="Delaunay TIN 三角网插值（linear / clough_tocher），凸包外不外推。",
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_types=["terrain_surface"],
            purpose_template="三角网插值",
        ),

        CapabilityDescriptor(
            id="trend_surface", name="趋势面分析", category="analysis",
            description="全局多项式趋势面（阶数 1-3 OLS），R²/残差方差证据，外推逐格标记。",
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_types=["terrain_surface"],
            purpose_template="趋势面分析",
        ),

        CapabilityDescriptor(
            id="regression_kriging", name="回归克里金", category="analysis",
            description="OLS 趋势（协变量）+ 残差克里金的混合插值（Odeh 1995）。",
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_types=["terrain_surface"],
            purpose_template="回归克里金",
        ),

        CapabilityDescriptor(
            id="interpolation_model_selection", name="插值模型选择", category="analysis",
            description="以 LOOCV/CV 证据比较可行插值方法并确定性推荐（RMSE 排名）。",
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_types=["stats_table"],
            purpose_template="插值模型比较",
        ),

        # ── Foundation V3（Geostatistics/Interpolation 批次）────────────

        CapabilityDescriptor(
            id="indicator_kriging", name="指示克里金", category="analysis",
            description="逐阈值指示克里金（Journel 1983）：P(Z≤t) 概率面 + p50 阈值面 + 可选 E-type 估计。",
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_types=["terrain_surface"],
            purpose_template="指示克里金",
        ),

        CapabilityDescriptor(
            id="cokriging", name="协同克里金", category="analysis",
            description="协同定位协同克里金（MM1 近似）：主/次变量联合建模；|ρ|<0.2 结构化拒绝。",
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_types=["terrain_surface"],
            purpose_template="协同克里金",
        ),

        CapabilityDescriptor(
            id="variogram_analysis", name="变异函数分析", category="analysis",
            description="方向变异函数 + 6 家族模型选择（加权 RSS + AICc），结构分析统计表输出。",
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_types=["stats_table"],
            purpose_template="变异函数分析",
        ),

        CapabilityDescriptor(
            id="block_kriging", name="块克里金", category="analysis",
            description="块支撑克里金（2×2 离散化，Isaaks & Srivastava 1989）：块均值 + 块方差面。",
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_types=["terrain_surface"],
            purpose_template="块克里金",
        ),
]
