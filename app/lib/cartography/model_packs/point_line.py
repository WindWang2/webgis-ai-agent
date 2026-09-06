"""Point / Line / Network 表达模型域包（ADR-0101 §B1 + Design System V4）。

native：categorized/graduated point、categorized/graduated line、
service-area 叠加（V3）+ V4 原生化的 dot-density（确定性撒点）、cluster
（前端 cluster source）、uncertainty point（双编码契约）、route（casing
双层）、accessibility / centrality（网络算法产物经分级线链）。
V4 新增：flow hub / network flow / DBSCAN cluster / nearest facility /
location-allocation / multi-ring buffer。
"""
from __future__ import annotations

from typing import List

from app.lib.cartography.model_library import MapModel
from app.lib.cartography.model_packs._base import (
    _DECKGL_URL,
    _GEODA_URL,
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
        aliases=["density_dots"],
        accepted_artifact_types=["admin_aggregate_table", "polygon_feature_set"],
        recommended_components=["legend"],
        data_preconditions_zh=[
            "value_field 为面单元数值；unit_value 为每点代表量（正数）",
            "总点数上限 20000（触顶按值降序截断并披露 truncated）",
        ],
        qgis_renderer="point displacement / dot density 插件",
        pitfalls_zh=[
            "撒点位置是示意性重分布（确定性 Halton 采样），不是真实位置 —— "
            "图例必须披露『Random dot within polygon』",
            "unit_value 过小会点数爆炸（上限截断后比例语义失真）—— 用 "
            "suggest_unit_value 取 1/2/5×10^k 整齐步长",
        ],
        sources=[_QGIS_URL],
    ),
    m(
        id="point_cluster", name_zh="点聚类图",
        purpose_zh="近距离点聚合为计数簇，随缩放展开；概览期降噪",
        geometry_kinds=["point"], maplibre_layer_type="circle",
        classification="none", color_scheme_kind="sequential",
        default_palette="Blues",
        accepted_artifact_types=["poi_feature_set", "point_feature_set"],
        recommended_components=["legend"],
        interaction_needs=["cluster_expand"],
        data_preconditions_zh=[
            "前端 GeoJSON source 开启 cluster（clusterRadius 缺省 60px）",
            "簇层三件套：簇圆 + 计数标注 + 未聚类点",
        ],
        deck_gl_layer="ScatterplotLayer(+CPU clustering)",
        qgis_renderer="point cluster 插件",
        pitfalls_zh=[
            "簇计数是屏幕相关量 —— 同一数据不同缩放簇数不同，导出前固定 zoom",
            "cluster 语义只在交互面展开/收起；导出画布按当前 zoom 快照",
        ],
        sources=[_MAPLIBRE_SPEC_URL],
    ),
    m(
        id="uncertainty_point_symbol", name_zh="不确定性点符号",
        purpose_zh="点估计值 + 不确定性双编码（颜色 ← 估计，描边宽 ← 区间宽度）",
        geometry_kinds=["point"], maplibre_layer_type="circle",
        classification="graduated",
        color_scheme_kind="sequential", default_palette="Purples",
        recommended_classifiers=["natural_breaks", "quantiles"],
        accepted_artifact_types=["point_feature_set"],
        recommended_components=["legend", "uncertainty_panel"],
        data_preconditions_zh=[
            "要素属性同时携带估计值字段与不确定性字段（区间半宽/标准差）",
            "双编码契约：fill 颜色 ← 估计分级；circle-stroke-width ← "
            "不确定性线性插值（描边越宽越不确定）",
        ],
        qgis_renderer="graduated（辅助区间可视化）",
        fallback_model_id="graduated_point",
        pitfalls_zh=[
            "不确定性字段缺失时自动退回 graduated_point（降级链）并披露",
            "双编码读者负荷高 —— uncertainty_panel 必须解释两个视觉通道",
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
        classification="categorical",
        color_scheme_kind="qualitative", default_palette="Set1",
        accepted_artifact_types=["line_feature_set", "network_graph"],
        recommended_components=["legend"],
        data_preconditions_zh=[
            "线要素携带 route_rank（0=推荐路径，1+ = 备选）类别字段",
            "双层子层契约：宽描边 casing（白/深底）+ 内芯彩色路径，"
            "保证路径在底图道路之上可辨",
        ],
        qgis_renderer="rule-based（路径高亮）",
        fallback_model_id="categorized_line",
        pitfalls_zh=[
            "route_rank 字段缺失时降级 categorized_line 并披露",
            "路径是线表达，不是真实车道级几何 —— 导航语义须在披露组件声明",
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
        recommended_classifiers=["natural_breaks", "quantiles"],
        accepted_artifact_types=["network_graph", "line_feature_set"],
        recommended_components=["legend"],
        fallback_model_id="graduated_line",
        data_preconditions_zh=[
            "可达性指标（机会累积/引力势）作为线要素数值字段经分级线链渲染",
        ],
        pitfalls_zh=[
            "可达性值依赖机会数据的时间窗（如 30 分钟工作地数）—— "
            "图例必须带窗口口径",
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
        accepted_artifact_types=["network_graph", "line_feature_set",
                                 "point_feature_set"],
        recommended_components=["legend", "methodology_note"],
        geometry_layer_types={"point": "circle", "line": "line"},
        fallback_model_id="graduated_line",
        data_preconditions_zh=[
            "中心性指标（degree/closeness/betweenness）由网络算法 "
            "（exact/sampled Brandes）产出并落在节点/边数值字段",
        ],
        pitfalls_zh=[
            "中心性对网络边界截断极敏感 —— 截断窗口必须在披露组件声明",
            "节点+边联合编码需多层组合；本模型按输入几何族单族渲染",
            "sampled Brandes 是近似值 —— 大图必须披露采样参数",
        ],
        sources=[],
    ),
    # ── V4 新增（Design System）：网络/流/点格局族 ────────────────────
    m(
        id="flow_hub_map", name_zh="流量枢纽图",
        purpose_zh="OD 流量的起讫枢纽以比例符号表达（枢纽等级/吞吐量）",
        geometry_kinds=["point"], maplibre_layer_type="circle",
        classification="none",
        color_scheme_kind="sequential", default_palette="Plasma",
        aliases=["flow_hubs"],
        accepted_artifact_types=["od_matrix", "od_table", "point_feature_set"],
        recommended_components=["legend"],
        supported_template_kinds=["thematic", "symbology"],
        export_compatibility=["png", "pdf"],
        fallback_model_id="proportional_symbol",
        data_preconditions_zh=[
            "枢纽吞吐量 = OD 矩阵行/列合计；半径按面积比例律 sqrt 映射",
        ],
        pitfalls_zh=[
            "与 flow_od_arc 组合时枢纽符号必须半透明，避免吞没弧线",
            "面积比例律：radius ∝ sqrt(value)，线性半径会平方级夸大大枢纽",
        ],
        sources=[_MAPLIBRE_SPEC_URL],
    ),
    m(
        id="network_flow_map", name_zh="网络流量图",
        purpose_zh="网络边上的流量/负荷分级线表达（路段流量、管网负荷）",
        geometry_kinds=["line"], maplibre_layer_type="line",
        classification="graduated",
        color_scheme_kind="perceptual_uniform", default_palette="Inferno",
        recommended_classifiers=["natural_breaks", "head_tail"],
        accepted_artifact_types=["network_graph", "line_feature_set", "od_matrix"],
        recommended_components=["legend"],
        export_compatibility=["png", "pdf", "svg"],
        fallback_model_id="graduated_line",
        data_preconditions_zh=[
            "边表携带流量/负荷数值字段；宽度或颜色分级投影",
        ],
        pitfalls_zh=[
            "重尾流量用 head_tail 分级更可读；等距分级会被少数干线吞没",
        ],
        sources=[_GEODA_URL],
    ),
    m(
        id="dbscan_cluster_map", name_zh="DBSCAN 聚类簇图",
        purpose_zh="密度聚类（DBSCAN/ST-DBSCAN）簇归属的分类点图 + 噪声点",
        geometry_kinds=["point"], maplibre_layer_type="circle",
        classification="categorical",
        color_scheme_kind="qualitative", default_palette="Set1",
        aliases=["cluster_map", "spatial_cluster_map"],
        accepted_artifact_types=["point_feature_set", "poi_feature_set"],
        recommended_components=["categorical_legend", "methodology_note"],
        export_compatibility=["png", "pdf"],
        data_preconditions_zh=[
            "要素携带 cluster id 字段（-1=噪声点置灰）",
            "eps/min_samples 参数必须随方法论披露组件声明",
        ],
        pitfalls_zh=[
            "噪声点（-1）必须低饱和置灰而非丢弃 —— 丢弃会高估簇覆盖",
            "簇 id 顺序不承载语义 —— 定性色而非渐变色",
        ],
        sources=[],
    ),
    m(
        id="nearest_facility_map", name_zh="最近设施归属图",
        purpose_zh="需求点到最近设施的指派关系线（closest facility 结果）",
        geometry_kinds=["line", "point"], maplibre_layer_type="line",
        classification="categorical",
        color_scheme_kind="qualitative", default_palette="Set2",
        geometry_layer_types={"point": "circle", "line": "line"},
        accepted_artifact_types=["network_graph", "line_feature_set",
                                 "point_feature_set", "service_area"],
        recommended_components=["legend"],
        fallback_model_id="categorized_line",
        data_preconditions_zh=[
            "指派线按所属设施分类着色；设施点单独符号层",
        ],
        pitfalls_zh=[
            "设施较多时指派线视觉过载 —— 先按服务区分组或过滤显示",
        ],
        sources=[],
    ),
    m(
        id="location_allocation_map", name_zh="区位配置图",
        purpose_zh="选址-分配结果（设施选址 + 需求指派）联合表达",
        geometry_kinds=["point", "line", "polygon"],
        maplibre_layer_type="fill",
        classification="categorical",
        color_scheme_kind="qualitative", default_palette="Dark2",
        geometry_layer_types={"point": "circle", "line": "line",
                              "polygon": "fill"},
        accepted_artifact_types=["network_graph", "admin_aggregate_table",
                                 "point_feature_set"],
        recommended_components=["legend", "decision_panel"],
        fallback_model_id="nearest_facility_map",
        data_preconditions_zh=[
            "选中设施高亮 + 需求单元按指派设施分类着色",
        ],
        pitfalls_zh=[
            "目标函数（最小化总距离/最大覆盖）决定『最优』含义 —— 必须随图披露",
        ],
        sources=[],
    ),
    m(
        id="multi_ring_buffer_map", name_zh="多环缓冲图",
        purpose_zh="以设施为中心的多圈距离带（0-N 米分级环）叠加",
        geometry_kinds=["polygon"], maplibre_layer_type="fill",
        classification="graduated",
        recommended_classifiers=["equal_interval"],
        color_scheme_kind="sequential", default_palette="Blues",
        aliases=["ring_buffer_map"],
        accepted_artifact_types=["proximity_zone", "service_area"],
        recommended_components=["legend"],
        export_compatibility=["png", "pdf"],
        fallback_model_id="proximity_overlay",
        data_preconditions_zh=[
            "环带是有序距离量 —— equal_interval + 顺序色带；环宽一致时图例等距",
        ],
        pitfalls_zh=[
            "欧氏缓冲 ≠ 路网可达 —— 交通语义场景改走 service_area_overlay",
        ],
        sources=[_QGIS_URL],
    ),
]
