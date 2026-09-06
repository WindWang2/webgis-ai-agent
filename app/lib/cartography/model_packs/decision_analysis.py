"""Decision / Analysis 表达模型域包（ADR-0101 §B1）。

native：site-selection / MCDA 评分面 —— 分级 fill + 决策/方法论披露
组件的既有机制族。
planned：敏感性分析呈现 —— 需要敏感性 artifact 契约。
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
        purpose_zh="权重/参数扰动下的评价稳定性对比（多情景小倍数或置信区间）",
        geometry_kinds=["polygon"], maplibre_layer_type="fill",
        classification="graduated",
        color_scheme_kind="diverging", default_palette="RdBu",
        runtime_status="planned",
        accepted_artifact_types=["admin_aggregate_table"],
        pitfalls_zh=[
            "planned：需要情景/扰动 artifact 契约与多面板组合语义，本分支未实现",
        ],
        sources=[],
    ),
]
