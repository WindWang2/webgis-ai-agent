"""Point / Line / Network 表达模型域包（ADR-0101 §B1）。

native：categorized/graduated point、categorized/graduated line、
service-area 叠加 —— 机制与既有 native 模型同族（circle/line/fill +
classify + legend 投影）。
planned：dot-density、cluster、uncertainty point、route、accessibility、
centrality —— 需要点生成 / cluster source / 路径与网络分析 artifact 等
当前运行时不存在的能力。
"""
from __future__ import annotations

from typing import List

from app.lib.cartography.model_library import MapModel
from app.lib.cartography.model_packs._base import (
    _DECKGL_URL,
    _GEODA_URL,
    _KEPLER_URL,
    _MAPLIBRE_SPEC_URL,
    _QGIS_URL,
    m,
)

POINT_LINE_PACK: List[MapModel] = [
    # ── Point ─────────────────────────────────────────────────────────
    m(
        id="categorized_point", name_zh="分类点图",
        purpose_zh="点要素按类别字段着色（设施类型/站点等级），circle + match 表达",
        geometry_kinds=["point"], maplibre_layer_type="circle",
        classification="categorical",
        color_scheme_kind="qualitative", default_palette="Set1",
        aliases=["categorized_symbols"],
        accepted_artifact_types=["poi_feature_set", "point_feature_set"],
        recommended_components=["categorical_legend"],
        supported_template_kinds=["thematic", "symbology"],
        export_compatibility=["png", "pdf"],
        qgis_renderer="categorized",
        pitfalls_zh=[
            "类别上限 ~9-12（Set1 上限 9 类），长尾类别并入『其他』",
            "顺序语义字段（低/中/高）改用 graduated_point + sequential 色带",
        ],
        sources=[_QGIS_URL],
    ),
    m(
        id="graduated_point", name_zh="分级点图",
        purpose_zh="点要素按数值字段分级填色（circle + step 表达），数值排序对比",
        geometry_kinds=["point"], maplibre_layer_type="circle",
        classification="graduated",
        color_scheme_kind="sequential", default_palette="Blues",
        recommended_classifiers=["natural_breaks", "quantiles", "equal_interval"],
        default_class_count=5,
        accepted_artifact_types=["poi_feature_set", "point_feature_set"],
        recommended_components=["legend"],
        supported_template_kinds=["thematic", "symbology"],
        export_compatibility=["png", "pdf"],
        fallback_model_id="graduated_symbol",
        qgis_renderer="graduated",
        pitfalls_zh=[
            "点密度过高时分级色点互相叠压，回退 aggregate_grid 或 visual_heatmap",
            "点大小保持恒定，数值只编码在颜色 —— 尺寸编码走 proportional_symbol",
        ],
        sources=[_QGIS_URL, _GEODA_URL],
    ),
    m(
        id="dot_density_map", name_zh="点密度图（dot density）",
        purpose_zh="面单元统计值按比例撒点表达内部分布（人口/农田面状密度）",
        geometry_kinds=["polygon"], maplibre_layer_type="circle",
        classification="none", color_scheme_kind="none",
        runtime_status="planned",
        accepted_artifact_types=["admin_aggregate_table", "polygon_feature_set"],
        qgis_renderer="point displacement / dot density 插件",
        pitfalls_zh=[
            "planned：需要按面单元比例约束的确定性撒点算法（本分支未实现，不伪装 native）",
            "撒点位置是示意性重分布，不是真实位置 —— 图例必须披露『Random dot within polygon』",
        ],
        sources=[_QGIS_URL],
    ),
    m(
        id="point_cluster", name_zh="点聚类图",
        purpose_zh="近距离点聚合为计数簇，随缩放展开；概览期降噪",
        geometry_kinds=["point"], maplibre_layer_type="circle",
        classification="none", color_scheme_kind="sequential",
        default_palette="Blues",
        runtime_status="planned",
        accepted_artifact_types=["poi_feature_set", "point_feature_set"],
        deck_gl_layer="ScatterplotLayer(+CPU clustering)",
        qgis_renderer="point cluster 插件",
        pitfalls_zh=[
            "planned：依赖 maplibre source cluster 语义（前端编译器 union 未含 cluster 配置）",
            "簇计数是屏幕相关量 —— 同一数据不同缩放簇数不同，导出前固定 zoom",
        ],
        sources=[_MAPLIBRE_SPEC_URL],
    ),
    m(
        id="uncertainty_point_symbol", name_zh="不确定性点符号",
        purpose_zh="点估计值 + 不确定性双编码（颜色 ← 估计，尺寸/透明环 ← 区间）",
        geometry_kinds=["point"], maplibre_layer_type="circle",
        classification="graduated",
        color_scheme_kind="sequential", default_palette="Purples",
        runtime_status="planned",
        accepted_artifact_types=["point_feature_set"],
        qgis_renderer="graduated（辅助区间可视化）",
        pitfalls_zh=[
            "planned：需要区间/方差字段契约（artifact schema 未定义 uncertainty 列）",
        ],
        sources=[_QGIS_URL],
    ),
    # ── Line / Network ────────────────────────────────────────────────
    m(
        id="categorized_line", name_zh="分类线图",
        purpose_zh="线要素按类别着色（道路等级/管线类型/行政区划界）",
        geometry_kinds=["line"], maplibre_layer_type="line",
        classification="categorical",
        color_scheme_kind="qualitative", default_palette="Dark2",
        accepted_artifact_types=["line_feature_set"],
        recommended_components=["categorical_legend"],
        supported_template_kinds=["thematic", "symbology"],
        export_compatibility=["png", "pdf", "svg"],
        qgis_renderer="categorized",
        pitfalls_zh=["深色定性色（Dark2）保证细线在底图上可辨"],
        sources=[_QGIS_URL],
    ),
    m(
        id="graduated_line", name_zh="分级线图",
        purpose_zh="线要素按数值分级（交通流量/河宽/管径），颜色或宽度分级",
        geometry_kinds=["line"], maplibre_layer_type="line",
        classification="graduated",
        color_scheme_kind="sequential", default_palette="Blues",
        recommended_classifiers=["natural_breaks", "quantiles"],
        default_class_count=5,
        accepted_artifact_types=["line_feature_set", "network_graph"],
        recommended_components=["legend"],
        supported_template_kinds=["thematic"],
        export_compatibility=["png", "pdf", "svg"],
        qgis_renderer="graduated（宽度/颜色）",
        fallback_model_id="flow_od_arc",
        pitfalls_zh=[
            "宽度分级时同级线段重叠会互相吞没 —— 细级优先绘制（宽线在下）",
            "network_graph 仅消费其边表（几何 network 的线段部分），节点表另走点模型",
        ],
        sources=[_QGIS_URL],
    ),
    m(
        id="route_map", name_zh="路径图",
        purpose_zh="起终点间推荐/备选路径表达（导航结果/可达路径对比）",
        geometry_kinds=["line"], maplibre_layer_type="line",
        classification="none", color_scheme_kind="qualitative",
        default_palette="Set1",
        runtime_status="planned",
        accepted_artifact_types=["line_feature_set", "network_graph"],
        qgis_renderer="rule-based（路径高亮）",
        pitfalls_zh=[
            "planned：需要路由结果 artifact 契约（turn-by-turn/成本字段），本分支未实现",
        ],
        sources=[_QGIS_URL],
    ),
    m(
        id="service_area_overlay", name_zh="服务区叠加",
        purpose_zh="设施服务区（等时/网络缓冲）分级面叠加，回答『N 分钟能到哪』",
        geometry_kinds=["polygon"], maplibre_layer_type="fill",
        # 等时/距离带是有序量 —— 缺省 graduated + Blues（顺序感）；
        # 按设施多框叠加（每设施一色）的用法显式换 categorical_thematic
        classification="graduated",
        recommended_classifiers=["equal_interval"],
        color_scheme_kind="sequential", default_palette="Blues",
        aliases=["isochrone_overlay"],
        accepted_artifact_types=["service_area", "proximity_zone"],
        recommended_components=["legend"],
        supported_template_kinds=["thematic", "symbology"],
        export_compatibility=["png", "pdf"],
        qgis_renderer="rule-based（服务区分级）",
        fallback_model_id="proximity_overlay",
        pitfalls_zh=[
            "服务区等级（5/10/15 分钟）语义固定为有序 —— 缺省 Blues 渐变；"
            "『按设施分色』场景改走 categorical_thematic",
            "网络服务区 ≠ 欧氏缓冲；数据来自 network 分析时图例须注明方法",
        ],
        sources=[_DECKGL_URL],
    ),
    m(
        id="accessibility_network", name_zh="可达性网络图",
        purpose_zh="网络可达性指标沿线/沿网表达（通行成本、可达机会数）",
        geometry_kinds=["line"], maplibre_layer_type="line",
        classification="graduated",
        color_scheme_kind="perceptual_uniform", default_palette="Viridis",
        runtime_status="planned",
        accepted_artifact_types=["network_graph"],
        pitfalls_zh=[
            "planned：需要可达性计算 artifact（机会累积/引力模型），本分支未实现",
        ],
        sources=[],
    ),
    m(
        id="network_centrality_map", name_zh="网络中心性图",
        purpose_zh="节点/边中心性指标（度/接近/介数）分级表达",
        geometry_kinds=["point", "line"], maplibre_layer_type="line",
        classification="graduated",
        color_scheme_kind="perceptual_uniform", default_palette="Magma",
        recommended_classifiers=["natural_breaks", "quantiles"],
        runtime_status="planned",
        accepted_artifact_types=["network_graph"],
        geometry_layer_types={"point": "circle", "line": "line"},
        pitfalls_zh=[
            "planned：需要中心性计算 artifact（本分支网络算法未含 centrality）",
            "中心性对网络边界截断极敏感 —— 截断窗口必须在披露组件声明",
            "节点+边联合编码需多层组合；本模型按输入几何族单族渲染",
        ],
        sources=[],
    ),
]
