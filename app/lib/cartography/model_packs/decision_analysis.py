"""Decision / Analysis 表达模型域包（ADR-0101 §B1 + Design System V4）。

native：site-selection / MCDA 评分面（V3）+ V4 原生化的敏感性分析呈现
（情景对比经面板/图表族表达）与加权叠加面 / GWR 系数面 / OLS 残差面 /
Huff 概率面。
"""
from __future__ import annotations

from typing import List

from app.lib.cartography.model_library import MapModel
from app.lib.cartography.model_packs._base import (
    _DECKGL_URL,
    _QGIS_URL,
    m,
)

DECISION_ANALYSIS_PACK: List[MapModel] = [
    m(
        id="site_selection_result", name_zh="选址评价结果图",
        purpose_zh="多准则选址综合得分的分级面/格网，决策场景主表达",
        geometry_kinds=["polygon"], maplibre_layer_type="fill",
        classification="graduated",
        color_scheme_kind="sequential", default_palette="Greens",
        recommended_classifiers=["natural_breaks", "quantiles"],
        default_class_count=5,
        aliases=["site_selection"],
        accepted_artifact_types=["admin_aggregate_table", "grid_aggregate",
                                 "polygon_feature_set"],
        recommended_components=["legend", "decision_panel", "statistics_panel"],
        export_compatibility=["png", "pdf"],
        fallback_model_id="administrative_choropleth",
        deck_gl_layer="PolygonLayer(+CPU 分级)",
        qgis_renderer="graduated",
        pitfalls_zh=[
            "得分分级断点决定『可选址』语义 —— 断点来源（自然断裂/阈值）必须披露",
            "候选排名是决策面板职责，地图只承载空间分布，两者不得互相替代",
        ],
        sources=[_QGIS_URL, _DECKGL_URL],
    ),
    m(
        id="mcda_score_map", name_zh="MCDA 评分图",
        purpose_zh="多准则决策分析加权得分的空间分级（权重来源须随图披露）",
        geometry_kinds=["polygon"], maplibre_layer_type="fill",
        classification="graduated",
        color_scheme_kind="sequential", default_palette="Purples",
        recommended_classifiers=["natural_breaks", "quantiles", "equal_interval"],
        default_class_count=5,
        aliases=["mcda_result"],
        accepted_artifact_types=["admin_aggregate_table", "grid_aggregate"],
        recommended_components=["legend", "methodology_note", "decision_panel"],
        export_compatibility=["png", "pdf"],
        fallback_model_id="administrative_choropleth",
        qgis_renderer="graduated",
        pitfalls_zh=[
            "weightSource 必须显式（用户设定/默认配方/等权），不得合成默认权重冒充用户意图",
            "准则间量纲不一致时必须先归一再加权 —— 原始值加权是方法错误",
        ],
        sources=[_QGIS_URL],
    ),
    m(
        id="sensitivity_analysis_presentation", name_zh="敏感性分析呈现图",
        purpose_zh="权重/参数扰动下的评价稳定性对比（得分变化幅度的空间表达 + 情景图表）",
        geometry_kinds=["polygon"], maplibre_layer_type="fill",
        classification="graduated",
        color_scheme_kind="diverging", default_palette="RdBu",
        recommended_classifiers=["std_dev", "quantiles"],
        accepted_artifact_types=["admin_aggregate_table", "grid_aggregate",
                                 "stats_table"],
        recommended_components=["legend", "chart_panel", "decision_panel",
                                "methodology_note"],
        export_compatibility=["png", "pdf"],
        chart_needs=["grouped_bar", "box_plot", "ranking_list"],
        fallback_model_id="mcda_score_map",
        data_preconditions_zh=[
            "扰动情景下的得分变化量（Δscore）作为面分级字段；情景参数表"
            "经 stats_table artifact 进图表面板",
        ],
        pitfalls_zh=[
            "呈现的是扰动结果对比，不是最优解本身 —— 主结论仍以 mcda_score_map 承载",
            "情景数过多时图表聚合为分布（box_plot），禁止全量小倍数堆叠",
        ],
        sources=[],
    ),
    # ── V4 新增（Design System）：加权叠加/空间计量/概率面族 ────────────
    m(
        id="weighted_overlay_surface", name_zh="加权叠加分析面",
        purpose_zh="多因子归一加权叠加的连续适宜性/敏感性面（MCDA 连续版）",
        geometry_kinds=["raster"], maplibre_layer_type="raster",
        classification="none",
        color_scheme_kind="sequential", default_palette="Greens",
        aliases=["weighted_sum_map", "multi_factor_surface"],
        accepted_artifact_types=["raster_surface", "density_surface"],
        recommended_components=["continuous_colorbar", "methodology_note",
                                "decision_panel"],
        export_compatibility=["png", "pdf"],
        fallback_model_id="raster_surface",
        data_preconditions_zh=[
            "各因子归一到同量纲后加权求和（权重和为 1）；权重来源随披露",
        ],
        pitfalls_zh=[
            "因子未归一就加权是方法错误（量纲混叠）—— methodology_note 必须列因子清单与权重",
        ],
        sources=[],
    ),
    m(
        id="gwr_coefficient_map", name_zh="GWR 局部系数图",
        purpose_zh="地理加权回归的局部系数/显著性的发散空间表达",
        geometry_kinds=["polygon"], maplibre_layer_type="fill",
        classification="graduated",
        color_scheme_kind="diverging", default_palette="PuOr",
        recommended_classifiers=["std_dev", "quantiles"],
        aliases=["gwr_map"],
        accepted_artifact_types=["admin_aggregate_table", "polygon_feature_set",
                                 "grid_aggregate"],
        recommended_components=["legend", "methodology_note", "statistics_panel"],
        export_compatibility=["png", "pdf"],
        fallback_model_id="diverging_choropleth",
        data_preconditions_zh=[
            "GWR 局部系数（逐单元）+ 可选显著性；带宽与核函数随披露",
        ],
        pitfalls_zh=[
            "不显著局部系数应置灰/低饱和，不与显著系数争色",
            "局部共线性会产出离谱系数 —— 先查局部 VIF 再解读",
        ],
        default_theme="cartographic.scientific",
        sources=[],
    ),
    m(
        id="ols_residual_map", name_zh="回归残差图",
        purpose_zh="全局回归（OLS 等）残差的空间发散表达（遗漏变量/空间自相关诊断）",
        geometry_kinds=["polygon"], maplibre_layer_type="fill",
        classification="graduated",
        color_scheme_kind="diverging", default_palette="RdBu",
        recommended_classifiers=["std_dev", "equal_interval"],
        accepted_artifact_types=["admin_aggregate_table", "polygon_feature_set"],
        recommended_components=["legend", "methodology_note"],
        export_compatibility=["png", "pdf"],
        fallback_model_id="diverging_choropleth",
        data_preconditions_zh=[
            "残差为有符号数值；以 0 为中点对称分级",
        ],
        pitfalls_zh=[
            "残差空间聚集（Moran's I 显著）说明模型设定有漏 —— 披露中"
            "应建议 GWR/空间滞后模型",
        ],
        default_theme="cartographic.scientific",
        sources=[],
    ),
    m(
        id="huff_probability_surface", name_zh="Huff 概率面",
        purpose_zh="Huff/引力模型的患者/顾客光顾概率面（商业地理主表达）",
        geometry_kinds=["raster", "polygon"], maplibre_layer_type="raster",
        classification="none",
        color_scheme_kind="sequential", default_palette="Oranges",
        geometry_layer_types={"raster": "raster", "polygon": "fill"},
        accepted_artifact_types=["raster_surface", "density_surface",
                                 "admin_aggregate_table"],
        recommended_components=["continuous_colorbar", "methodology_note"],
        export_compatibility=["png", "pdf"],
        fallback_model_id="raster_surface",
        data_preconditions_zh=[
            "光顾概率 ∈ [0,1]（按设施取最大或按设施分层渲染）",
        ],
        pitfalls_zh=[
            "概率面依赖距离衰减参数 β —— β 取值必须随方法论披露",
            "多设施场景『最大概率设施』与『概率和』是两种图 —— 不得混用图例",
        ],
        sources=[],
    ),
]
