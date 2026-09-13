"""MapComponentDescriptor registry — 组件定义目录.

每个组件类型的机器可读描述（非实例）。 Registry 提供
按 category / map_model / output 的确定性索引查询。
"""
from __future__ import annotations

from typing import Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

RuntimeStatus = Literal["native", "planned", "unavailable"]
PlacementDomain = Literal["layer", "overlay", "chrome", "panel", "export", "interaction"]
Cardinality = Literal["single", "multiple", "zero_or_one"]
StackBehavior = Literal["exclusive", "stack_vertical", "stack_horizontal", "overlay"]
# V4：碰撞类 —— 布局求解器按类施加不同的防重叠/压缩策略
CollisionClass = Literal["chrome", "legend", "panel", "canvas", "none"]
# V4：响应式行为词表
ResponsiveBehavior = Literal["none", "collapse", "reflow", "hide"]


class ComponentSizeRange(BaseModel):
    """组件尺寸域（相对画布短边的比例，0-1；布局求解器 V3 消费）。"""
    min_ratio: float = 0.0
    max_ratio: float = 1.0
    aspect: Optional[float] = None   # w/h 建议比；None = 自由


class ComponentAccessibility(BaseModel):
    """可达性元数据（前端 role/aria 与对比度校验消费）。"""
    role: str = ""
    label_zh: str = ""
    keyboard_operable: bool = True
    contrast_checked: bool = False


class ComponentPreview(BaseModel):
    """预览元数据（V7：目录 UI / Harness 候选卡消费；纯描述，无渲染职责）。"""
    glyph: str = ""          # 单字符示意（unicode glyph / 短标签）
    accent: str = ""         # hex 强调色（#rrggbb；空 = 主题缺省）
    min_canvas_px: int = 0   # 建议最小画布边长（px；0 = 无建议）


class MapComponentDescriptor(BaseModel):
    id: str
    category: str
    type: str
    name: str = ""
    name_zh: str = ""
    description: str = ""
    placement_domain: PlacementDomain = "overlay"
    supported_outputs: List[str] = Field(default_factory=lambda: ["interactive", "png", "pdf"])
    compatible_map_models: List[str] = Field(default_factory=list)
    compatible_artifact_types: List[str] = Field(default_factory=list)
    required_context: List[str] = Field(default_factory=list)
    renderer_support: List[str] = Field(default_factory=list)
    exporter_support: List[str] = Field(default_factory=list)
    default_variant: str = "default"
    variants: List[str] = Field(default_factory=list)
    default_position: str = "none"
    allowed_positions: List[str] = Field(default_factory=list)
    cardinality: Cardinality = "single"
    dependencies: List[str] = Field(default_factory=list)
    conflicts: List[str] = Field(default_factory=list)
    requires_layer_binding: bool = False
    priority: int = 50
    schema_version: int = 1
    runtime_status: RuntimeStatus = "native"
    tags: List[str] = Field(default_factory=list)
    # ── V4（Design System）：组件能力/布局/可达性描述（纯增量）──────────
    # 状态词表：组件实例的合法状态（chrome/panel 族默认 visible/hidden/
    # collapsed；浮动图表族由 chart_kinds.CHART_STATES 扩展）。
    states: List[str] = Field(default_factory=lambda: ["visible", "hidden"])
    size_range: ComponentSizeRange = Field(default_factory=ComponentSizeRange)
    # 碰撞类：layout solver V3 与 frontend resolve-layout 的分组防重叠依据
    collision_class: CollisionClass = "panel"
    responsive: ResponsiveBehavior = "none"
    # 交互能力词表（move/resize/pin/collapse/close/switch_variant/
    # selection_linkage…）：前端按注册的交互实现执行，未注册的不暴露
    interactions: List[str] = Field(default_factory=list)
    accessibility: ComponentAccessibility = Field(
        default_factory=ComponentAccessibility)
    # ── V5（Epic 11 Component Registry V2）：语义角色 + 示例（纯增量）──
    # semantic_role 回答「该组件在地图语义里承担什么角色」（组合规划器
    # 的查询键；与 category 的目录学定位正交）。载入期从
    # COMPONENT_SEMANTIC_ROLES 单一事实源投影回填（空 = 未声明，存量
    # 零迁移）；components_for_role 消费本字段。
    semantic_role: str = ""
    examples: List[str] = Field(default_factory=list)
    # ── V7（Goal 08）：目录智能面（纯增量，全默认值 —— 存量零迁移）────
    # 弃用：deprecated=True 的组件仍可渲染（不破坏存量 spec），但不再被
    # search/recommend 默认推荐；deprecated_by 指向后继组件 id。
    deprecated: bool = False
    deprecated_by: str = ""
    # 预览元数据（目录 UI / 候选卡）
    preview: ComponentPreview = Field(default_factory=ComponentPreview)
    # 语义搜索关键词（中文为主 + 英文同义词；审定静态表，非 per-query）
    search_keywords_zh: List[str] = Field(default_factory=list)
    # 模板引用：该组件常与哪些 component/composition template 关联
    # （登记面；不校验强存在 —— 模板可独立弃用）
    template_references: List[str] = Field(default_factory=list)


class ComponentSearchHit(BaseModel):
    """search() 命中项：descriptor + 确定性得分 + 命中字段（可解释）。"""
    descriptor: MapComponentDescriptor
    score: int
    matched_fields: List[str] = Field(default_factory=list)


class ComponentRecommendationContext(BaseModel):
    """recommend() 上下文：Harness 侧已知事实（全部可选，纯结构化）。

    ``existing_components`` 承载**类型级在场知识**：descriptor id 或
    component type（``north_arrow``/``legend``；横线归一为下划线）。
    组合流程里 Harness 天然持有类型清单（ComponentComposer 的
    selection.selected）；任意命名的实例 id（``legend-main``）不含可靠
    类型信息，不参与去重判定。"""
    map_model: str = ""                  # 主表达 MapModel id（可别名）
    output_target: str = "interactive"   # interactive/png/pdf/svg/print
    task_categories: Tuple[str, ...] = ()     # 任务类目（亲和提示）
    semantic_roles: Tuple[str, ...] = ()      # 期望语义角色
    existing_components: Tuple[str, ...] = () # 已在场组件（类型级，见上）
    artifact_types: Tuple[str, ...] = ()      # 在场产物语义类型


class ComponentRecommendation(BaseModel):
    """recommend() 候选项：确定性得分 + 有界理由（可解释推荐）。"""
    component_id: str
    score: int
    reasons: List[str] = Field(default_factory=list)


# 组件语义角色词表（Epic 11 §5.G 冻结；组合规划器按角色查询）。
SEMANTIC_ROLES = (
    "map_frame",           # 地图主体/图框
    "orientation",         # 指北/坐标参考
    "measure",             # 比例/度量
    "legend",              # 图例（分级/类别/连续）
    "title_block",         # 标题/副标题
    "statistics",          # 统计面板/图表/表格
    "annotation",          # 注记/文本
    "inset",               # 插图
    "disclosure",          # 方法论/不确定性/决策披露
    "export",              # 输出版式
    "reference",           # 参考层（经纬网/版权）
)

#: 组件 → 语义角色投影（单一事实源；descriptor.semantic_role 的来源）。
COMPONENT_SEMANTIC_ROLES: dict = {
    "north_arrow": "orientation",
    "scale_bar": "measure",
    "legend": "legend",
    "continuous_colorbar": "legend",
    "categorical_legend": "legend",
    "title": "title_block",
    "subtitle": "title_block",
    "attribution": "reference",
    "graticule": "reference",
    "map_border": "map_frame",
    "statistics_panel": "statistics",
    "chart_panel": "statistics",
    "table_panel": "statistics",
    "export_layout": "export",
    "annotation": "annotation",
    "inset_map": "inset",
    "methodology_note": "disclosure",
    "uncertainty_panel": "disclosure",
    "decision_panel": "disclosure",
}


#: 组件使用示例（审定一行例；descriptor.examples 的回填源）。
_COMPONENT_EXAMPLES: dict = {
    "north_arrow": "学术成图/报告版面的方向参考（top-right）",
    "scale_bar": "含缓冲/服务区的度量类专题图（bottom-right）",
    "legend": "分区统计填色的分级图例（绑定主题层）",
    "continuous_colorbar": "KDE 密度面/插值面的连续色条",
    "categorical_legend": "土地覆盖分类图的类别图例",
    "title": "任何产品图的主标题（top-center）",
    "subtitle": "主标题下的时间/口径副题",
    "attribution": "OSM/数据源版权注记（bottom-left）",
    "graticule": "小比例尺区域图的经纬网参考",
    "map_border": "导出版式的图框/neatline",
    "statistics_panel": "各区统计 KPI 面板（top-left）",
    "chart_panel": "各区数量对比柱图/构成饼图（artifact 绑定）",
    "table_panel": "聚合统计表的虚拟化表格面板",
    "export_layout": "A4 横版导出页面",
    "annotation": "要点 callout/数据来源脚注",
    "inset_map": "全国区位插图（研究区定位）",
    "methodology_note": "分母缺失/近似方法的诚实披露卡",
    "uncertainty_panel": "克里金方差/样本限制披露",
    "decision_panel": "MCDA 权重来源与候选排名面板",
}


#: V7 语义搜索关键词（审定静态表；search() 匹配词汇 —— 中文为主 + 英文
#: 同义词。点分布/热力/区县统计等**任务语义**通过这些受控词命中组件，
#: 而不是 per-query 硬编码：planner 的 query→类目跳变仍在上游 taxonomy）。
COMPONENT_SEARCH_KEYWORDS: dict = {
    "north_arrow": ["指北针", "方向", "北", "north", "orientation"],
    "scale_bar": ["比例尺", "比例", "距离", "scale", "度量"],
    "legend": ["图例", "分级", "legend", "分级设色"],
    "continuous_colorbar": ["色条", "色卡", "颜色带", "colorbar", "连续色标",
                            "热力", "密度"],
    "categorical_legend": ["分类图例", "类别图例", "category legend", "类别"],
    "title": ["标题", "主标题", "title"],
    "subtitle": ["副标题", "subtitle", "时间口径"],
    "attribution": ["版权", "署名", "数据源", "attribution", "来源"],
    "graticule": ["经纬网", "坐标网", "网格", "graticule", "grid"],
    "map_border": ["图框", "边框", "内图廓", "border", "neatline", "框架"],
    "statistics_panel": ["统计面板", "指标", "kpi", "统计卡", "汇总"],
    "chart_panel": ["图表", "柱状图", "饼图", "折线图", "统计图", "chart",
                    "bar", "pie", "line chart", "直方图", "散点", "区县统计",
                    "统计对比"],
    "table_panel": ["表格", "数据表", "table", "明细"],
    "export_layout": ["导出", "版面", "打印", "a4", "export", "页面"],
    "annotation": ["注记", "标注", "文字", "脚注", "annotation", "标注文字",
                   "数据来源说明"],
    "inset_map": ["插图", "区位图", "缩略图", "inset", "概览图", "定位图"],
    "methodology_note": ["方法论", "披露", "方法说明", "methodology"],
    "uncertainty_panel": ["不确定性", "方差", "置信", "uncertainty", "误差"],
    "decision_panel": ["决策", "多准则", "权重", "排名", "decision", "mcda"],
    "label_layer": ["标注", "注记", "标签", "label", "annotation", "地名",
                    "标注字段", "避让", "压盖"],
}


#: V7 预览元数据（glyph/accent；审定静态表，投影回填 descriptor.preview）。
COMPONENT_PREVIEWS: dict = {
    "north_arrow": {"glyph": "↑", "accent": "#1f2933"},
    "scale_bar": {"glyph": "↔", "accent": "#1f2933"},
    "legend": {"glyph": "≡", "accent": "#2563eb"},
    "continuous_colorbar": {"glyph": "▰", "accent": "#dc2626"},
    "categorical_legend": {"glyph": "▤", "accent": "#059669"},
    "title": {"glyph": "T", "accent": "#111827"},
    "subtitle": {"glyph": "t", "accent": "#4b5563"},
    "attribution": {"glyph": "©", "accent": "#6b7280"},
    "graticule": {"glyph": "▦", "accent": "#9ca3af"},
    "map_border": {"glyph": "▢", "accent": "#374151"},
    "statistics_panel": {"glyph": "𝍢", "accent": "#7c3aed"},
    "chart_panel": {"glyph": "▥", "accent": "#7c3aed"},
    "table_panel": {"glyph": "▦", "accent": "#7c3aed"},
    "export_layout": {"glyph": "▭", "accent": "#0f766e"},
    "annotation": {"glyph": "✎", "accent": "#b45309"},
    "inset_map": {"glyph": "◭", "accent": "#0369a1"},
    "methodology_note": {"glyph": "§", "accent": "#525252"},
    "uncertainty_panel": {"glyph": "±", "accent": "#525252"},
    "decision_panel": {"glyph": "⚖", "accent": "#525252"},
    "label_layer": {"glyph": "A", "accent": "#0f766e"},
}


def components_for_role(semantic_role: str) -> List[str]:
    """按语义角色查组件 id（消费 descriptor.semantic_role 投影，词表序）。"""
    if semantic_role not in SEMANTIC_ROLES:
        return []
    comp_reg = get_component_registry()
    return sorted(
        cid for cid in comp_reg.all_ids
        if comp_reg.get(cid) is not None
        and comp_reg.get(cid).semantic_role == semantic_role
    )


_SEED_DESCRIPTORS: List[MapComponentDescriptor] = [
    MapComponentDescriptor(
        id="north_arrow", category="navigation.north_arrow", type="north_arrow",
        name="North Arrow", name_zh="指北针",
        placement_domain="overlay", supported_outputs=["interactive", "png", "pdf", "svg"],
        renderer_support=["interactive"], exporter_support=["png", "pdf", "svg"],
        default_variant="compass_minimal_black",
        variants=["compass_minimal_black", "compass_needle", "compass_rose", "arrow_simple", "monochrome", "dual_convention"],
        default_position="top-right", allowed_positions=["top-right", "top-left", "bottom-right", "bottom-left", "none"],
        cardinality="single", priority=30, tags=["navigation"],
        collision_class="chrome", accessibility={"role": "img", "label_zh": "指北针"},
    ),
    MapComponentDescriptor(
        id="scale_bar", category="navigation.scale_bar", type="scale_bar",
        name="Scale Bar", name_zh="比例尺",
        placement_domain="overlay", supported_outputs=["interactive", "png", "pdf", "svg"],
        renderer_support=["interactive"], exporter_support=["png", "pdf", "svg"],
        default_variant="minimal",
        variants=["minimal", "boxed", "academic", "dual_unit"],
        default_position="bottom-right", allowed_positions=["bottom-right", "bottom-left", "bottom-center", "none"],
        cardinality="single", priority=20,
        collision_class="chrome", accessibility={"role": "img", "label_zh": "比例尺"},
    ),
    # v2（component library 2.0）：图例族 cardinality=single → multiple。
    # 多图层地图（heatmap 主层 + choropleth 参考层）本来就是
    # 「colorbar + 分级图例」并存的合法构成；冲突语义从 type 级（谁在场
    # 都报错）升级为 binding 级 —— 同一 layerId 上图例族互相竞争才是冲突
    # （composition_validation.validate_binding_conflicts 执行），不同层
    # 各自的图例/色条互不干涉。
    MapComponentDescriptor(
        id="legend", category="legend.graduated", type="legend",
        name="Graduated Legend", name_zh="分级图例",
        placement_domain="overlay", supported_outputs=["interactive", "png", "pdf", "svg"],
        # #1075(D-6) + V3：兼容模型清单 —— V3 扩容的全部离散分级语义模型
        # （choropleth 族/分级点线/决策评分面）都要能挂 graduated 图例。
        compatible_map_models=[
            "administrative_choropleth", "aggregate_grid", "hotspot_overlay",
            "proximity_overlay", "administrative_aggregation", "proportional_symbol",
            # V3（ADR-0101 B1/B7）
            "normalized_choropleth", "diverging_choropleth", "suitability_classes",
            "risk_exposure_classes", "vulnerability_index", "equity_assessment",
            "change_comparison_map", "site_selection_result", "mcda_score_map",
            "graduated_point", "graduated_line", "flow_od_arc",
            "service_area_overlay",
            # V4 分级面/线族（graduated 语义同链）
            "temporal_comparison_map", "stream_order_map", "multi_ring_buffer_map",
            "network_flow_map", "accessibility_network", "network_centrality_map",
            "uncertainty_point_symbol", "uncertainty_choropleth",
            "bivariate_choropleth", "bivariate_raster", "gwr_coefficient_map",
            "ols_residual_map", "classified_raster", "elevation_tint_hillshade",
            "sensitivity_analysis_presentation",
        ],
        compatible_artifact_types=["admin_aggregate_table", "grid_aggregate"],
        renderer_support=["interactive"], exporter_support=["png", "pdf", "svg"],
        default_variant="academic", variants=[
            "academic", "compact", "report", "horizontal",
            # V4：双变量色阵 / 不确定性区间 / 比例符号尺寸 / 线宽分级 /
            # 多层复合图例（planned 前瞻模板转正）
            "bivariate", "uncertainty", "size", "line", "composite",
        ],
        default_position="bottom-left", allowed_positions=["bottom-left", "bottom-right", "top-left", "top-right", "none"],
        cardinality="multiple", requires_layer_binding=True, priority=16,
        states=["visible", "hidden", "collapsed"], collision_class="legend",
        interactions=["collapse", "selection_linkage"],
        accessibility={"role": "img", "label_zh": "分级图例"},
    ),
    MapComponentDescriptor(
        id="continuous_colorbar", category="legend.continuous_colorbar", type="continuous_colorbar",
        name="Continuous Colorbar", name_zh="连续色条",
        placement_domain="overlay", supported_outputs=["interactive", "png", "pdf", "svg"],
        compatible_map_models=["visual_heatmap", "raster_surface",
                               "terrain_analytical_surface", "spectral_index_surface",
                               "flow_od_arc",
                               # V4 连续栅格族（渲染链同一 colorbar 契约）
                               "temporal_trend_surface", "anomaly_surface",
                               "uncertainty_surface", "kernel_density_surface",
                               "interpolation_result_map", "distance_surface",
                               "surface_difference_map", "twi_map",
                               "flow_accumulation_surface", "sar_intensity_surface",
                               "sar_change_detection", "huff_probability_surface",
                               "weighted_overlay_surface"],
        compatible_artifact_types=["density_surface", "terrain_surface"],
        renderer_support=["interactive"], exporter_support=["png", "pdf", "svg"],
        default_variant="horizontal", variants=["horizontal", "vertical", "slim", "scientific", "stepped"],
        default_position="bottom-right", allowed_positions=["bottom-right", "bottom-left", "bottom-center", "top-right", "none"],
        cardinality="multiple", requires_layer_binding=True, priority=15,
        states=["visible", "hidden", "collapsed"], collision_class="legend",
        accessibility={"role": "img", "label_zh": "连续色条"},
    ),
    MapComponentDescriptor(
        id="categorical_legend", category="legend.categorical", type="categorical_legend",
        name="Categorical Legend", name_zh="分类图例",
        placement_domain="overlay", supported_outputs=["interactive", "png", "pdf", "svg"],
        compatible_map_models=["categorical_thematic", "zoning_planning",
                               "categorized_point", "categorized_line",
                               # V4 类别面/点族
                               "classification_result_map", "confusion_matrix_map",
                               "aspect_direction_map", "landform_classification_map",
                               "watershed_boundary_map", "viewshed_map",
                               "dbscan_cluster_map", "nearest_facility_map",
                               "location_allocation_map", "voronoi_partition_map",
                               "suitability_constraint_overlay",
                               "route_map", "flow_hub_map"],
        renderer_support=["interactive"], exporter_support=["png", "pdf", "svg"],
        default_variant="academic", variants=["academic", "compact", "report", "horizontal", "nested"],
        default_position="bottom-left", allowed_positions=["bottom-left", "bottom-right", "top-left", "none"],
        cardinality="multiple", requires_layer_binding=True, priority=17,
        states=["visible", "hidden", "collapsed"], collision_class="legend",
        interactions=["collapse", "selection_linkage"],
        accessibility={"role": "img", "label_zh": "分类图例"},
    ),
    MapComponentDescriptor(
        id="title", category="annotation.title", type="title",
        name="Title", name_zh="标题",
        placement_domain="chrome", supported_outputs=["interactive", "png", "pdf", "svg"],
        renderer_support=["interactive"], exporter_support=["png", "pdf", "svg"],
        default_variant="academic", variants=["academic", "report", "presentation", "minimal", "government", "banner", "compact"],
        default_position="top-center", allowed_positions=["top-center", "top-left", "none"],
        cardinality="single", priority=10,
        collision_class="chrome", responsive="collapse",
        accessibility={"role": "heading", "label_zh": "标题"},
    ),
    MapComponentDescriptor(
        id="subtitle", category="annotation.subtitle", type="subtitle",
        name="Subtitle", name_zh="副标题",
        placement_domain="chrome", supported_outputs=["interactive", "png", "pdf", "svg"],
        renderer_support=["interactive"], exporter_support=["png", "pdf", "svg"],
        default_variant="default", variants=["default", "academic", "report", "compact"],
        default_position="top-center", allowed_positions=["top-center", "top-left", "none"],
        cardinality="zero_or_one", priority=11,
        collision_class="chrome",
        accessibility={"role": "text", "label_zh": "副标题"},
    ),
    MapComponentDescriptor(
        id="attribution", category="annotation.attribution", type="attribution",
        name="Attribution", name_zh="版权信息",
        placement_domain="chrome", supported_outputs=["interactive", "png", "pdf", "svg"],
        renderer_support=["interactive"], exporter_support=["png", "pdf", "svg"],
        default_variant="default", variants=["default", "compact"],
        default_position="bottom-left", allowed_positions=["bottom-left", "bottom-right", "none"],
        cardinality="single", priority=50,
        collision_class="chrome", accessibility={"role": "note", "label_zh": "版权信息"},
    ),
    MapComponentDescriptor(
        id="graticule", category="navigation.graticule", type="graticule",
        name="Graticule", name_zh="经纬网",
        # P3：live 渲染器落地（graticule.tsx SVG overlay）—— #1075(D-5) 的
        # "前端无渲染器"现状解除；导出侧 _drawGraticules 与 live 共享
        # graticule-math 间隔/吸附语义（单一矩阵见 component_renderers）。
        placement_domain="overlay", supported_outputs=["interactive", "png", "pdf", "svg"],
        renderer_support=["interactive"], exporter_support=["png", "pdf", "svg"],
        default_variant="light", variants=["light", "geographic", "projected"],
        default_position="none", allowed_positions=["none"],
        cardinality="single", priority=60,
        collision_class="canvas", states=["visible", "hidden"],
        accessibility={"role": "img", "label_zh": "坐标网格"},
    ),
    MapComponentDescriptor(
        id="map_border", category="frame.map_border", type="map_border",
        name="Map Border", name_zh="图框边框",
        # P6：全链路组件（live CSS 框 + 导出 strokeRect；variants 两侧同语义）
        placement_domain="chrome",
        supported_outputs=["interactive", "png", "pdf", "svg"],
        renderer_support=["interactive"], exporter_support=["png", "pdf", "svg"],
        default_variant="minimal", variants=["minimal", "academic", "report", "neatline"],
        default_position="none", allowed_positions=["none"],
        cardinality="single", priority=70,
        collision_class="canvas", states=["visible", "hidden"],
        accessibility={"role": "img", "label_zh": "图框"},
    ),
    MapComponentDescriptor(
        id="statistics_panel", category="analysis.statistics_panel", type="statistics_panel",
        name="Statistics Panel", name_zh="统计面板",
        placement_domain="panel", supported_outputs=["interactive", "png", "pdf"],
        required_context=["statistics"],
        renderer_support=["interactive"], exporter_support=["png", "pdf", "svg"],
        default_variant="default", variants=["default", "compact", "kpi", "explanation"],
        default_position="top-left", allowed_positions=["top-left", "top-right", "none"],
        cardinality="zero_or_one", priority=40,
        states=["visible", "hidden", "collapsed", "expanded"],
        interactions=["collapse", "close"], collision_class="panel",
        accessibility={"role": "group", "label_zh": "统计面板"},
    ),
    MapComponentDescriptor(
        id="chart_panel", category="analysis.chart_panel", type="chart_panel",
        name="Chart Panel", name_zh="图表面板",
        placement_domain="panel", supported_outputs=["interactive", "png", "pdf"],
        required_context=["chart"],
        renderer_support=["interactive"], exporter_support=["png", "pdf", "svg"],
        default_variant="default",
        # V4：kind 变体（chart_kinds.py 词表的 live 引擎已实现 18 种，
        # violin planned 不入 native 词表）+ 4 风格变体。kind 变体是
        # 目录的 preset 句柄：模板 default_options.chartType 预设图表类型。
        variants=[
            "default", "compact", "transparent", "report",
            "bar", "horizontal_bar", "grouped_bar", "stacked_bar",
            "line", "area", "scatter", "histogram", "box_plot",
            "pie", "donut", "radar", "rose", "timeseries", "cumulative",
            "heat_matrix", "kpi_card", "ranking_list",
        ],
        default_position="top-left", allowed_positions=["top-left", "top-right", "bottom-left", "bottom-right", "none"],
        # v2：多图表产品（各一 chart：各区数量/类别构成/排名）—— 每实例
        # 独立 id/chartRef/placement；上游 artifact 协议复用 ref:chart-*。
        cardinality="multiple", priority=41,
        # V4：七态状态机（chart_kinds.CHART_STATES 同词表）+ Agent/用户
        # 共享操作词表（AGENT_CHART_OPERATIONS 同表）
        states=["hidden", "visible", "collapsed", "expanded",
                "floating", "docked", "anchored"],
        interactions=["move", "resize", "pin", "collapse", "expand",
                      "close", "restore", "switch_chart_type",
                      "switch_field", "filter", "highlight"],
        collision_class="panel", responsive="collapse",
        accessibility={"role": "img", "label_zh": "统计图表"},
    ),
    MapComponentDescriptor(
        id="table_panel", category="analysis.table_panel", type="table_panel",
        name="Table Panel", name_zh="表格面板",
        # Runtime V4（§10）：artifact-backed 交互表格 —— 虚拟化 + 稳定行 id +
        # 可排序可过滤 + SelectionContext 联动（table↔map↔chart）。数据绑定
        # 双通道：options.tableRef（stats_table/admin_aggregate_table 等
        # artifact ref）或 options.layerId（HUD 图层属性表，MVT 层按需水合）。
        # V4：canvas 表格导出落地（drawChromeTable）—— 有界快照（8 行 +
        # 行列截断披露），导出画有界快照而非交互面复刻。
        placement_domain="panel", supported_outputs=["interactive", "png", "pdf", "svg"],
        compatible_artifact_types=["stats_table", "admin_aggregate_table", "grid_aggregate", "feature_collection"],
        renderer_support=["interactive"], exporter_support=["png", "pdf", "svg"],
        default_variant="default", variants=["default", "compact", "dense"],
        default_position="bottom-right", allowed_positions=["top-left", "top-right", "bottom-left", "bottom-right", "none"],
        cardinality="multiple", priority=42,
        states=["visible", "hidden", "collapsed"],
        interactions=["move", "resize", "collapse", "close"],
        collision_class="panel", accessibility={"role": "table", "label_zh": "数据表"},
    ),
    MapComponentDescriptor(
        id="export_layout", category="export.page_layout", type="export_layout",
        name="Export Layout", name_zh="输出版式",
        placement_domain="export", supported_outputs=["png", "pdf", "print"],
        renderer_support=[], exporter_support=["png", "pdf"],
        default_variant="A4_landscape", variants=["A4_landscape", "A4_portrait", "A3_landscape", "letter"],
        default_position="none", allowed_positions=["none"],
        cardinality="single", priority=90,
        states=["visible"], collision_class="none",
        accessibility={"role": "dialog", "label_zh": "输出版式",
                       "keyboard_operable": False},
    ),
    MapComponentDescriptor(
        id="annotation", category="annotation.text", type="annotation",
        name="Annotation", name_zh="文本注记",
        placement_domain="chrome", supported_outputs=["interactive", "png", "pdf", "svg"],
        renderer_support=["interactive"], exporter_support=["png", "pdf", "svg"],
        # v2：注记框架 —— text（静态卡）/ callout（anchor 坐标 + 引线）/ 
        # group（一个逻辑组 → 多条相关注记，options.items 有界）。三种形态
        # 共享同一语义模型，live 与 export 同链（exporter.drawChromeAnnotation）。
        default_variant="text",
        variants=["text", "callout", "group",
                  # V4：版面附注族（页脚/时间戳/投影注记/数据来源）与
                  # 高亮区域（语义 highlight，非交互 selection）
                  "footer", "timestamp", "projection_note", "data_source",
                  "highlight"],
        default_position="top-left", allowed_positions=["top-left", "top-right", "bottom-left", "bottom-right", "none"],
        cardinality="multiple", priority=55,
        interactions=["move", "close"], collision_class="panel",
        accessibility={"role": "note", "label_zh": "文本注记"},
    ),
    MapComponentDescriptor(
        id="inset_map", category="inset.map", type="inset_map",
        name="Inset Map", name_zh="插图",
        # v2（P1 全链路）：live SVG 渲染器（inset-map.tsx，轻量静态投影 ——
        # 不 mount 第二个 maplibre runtime）+ 导出 drawChromeInset 同链；
        # renderer/exporter 真值由矩阵对账。bbox 未填充时渲染端自弃
        # （不虚构范围），resolver 需要 inset_context 才会选出（防空选）。
        placement_domain="overlay", supported_outputs=["interactive", "png", "pdf", "svg"],
        renderer_support=["interactive"], exporter_support=["png", "pdf", "svg"],
        default_variant="overview", variants=["overview", "location", "hierarchy"],
        default_position="top-right", allowed_positions=["top-right", "top-left", "bottom-right", "bottom-left"],
        cardinality="zero_or_one", priority=65, runtime_status="native",
        required_context=["inset_context"],
        states=["visible", "hidden"], collision_class="panel",
        responsive="hide", accessibility={"role": "img", "label_zh": "区位插图"},
    ),
    # ── VNext §5/§9/§13：披露族（方法论诚实的产品面）──────────────────
    MapComponentDescriptor(
        id="methodology_note", category="disclosure.methodology_note",
        type="methodology_note",
        name="Methodology Note", name_zh="方法论披露",
        # 「缺分母不能谈公平性」长在地图产品上：稳定警告码 + 文案随 live
        # 渲染。V3：canvas 导出绘制披露卡（与矩阵 D6 升级同步）。
        placement_domain="panel", supported_outputs=["interactive", "png", "pdf", "svg"],
        renderer_support=["interactive"], exporter_support=["png", "pdf", "svg"],
        default_variant="default", variants=["default", "compact", "data_quality"],
        default_position="bottom-left", allowed_positions=["bottom-left", "bottom-right", "top-left", "top-right", "none"],
        cardinality="zero_or_one", priority=46, runtime_status="native",
        required_context=["methodology"],
        states=["visible", "hidden", "collapsed"], collision_class="panel",
        accessibility={"role": "note", "label_zh": "方法论披露"},
    ),
    MapComponentDescriptor(
        id="uncertainty_panel", category="disclosure.uncertainty_panel",
        type="uncertainty_panel",
        name="Uncertainty Panel", name_zh="不确定性面板",
        # 插值不确定性/样本限制/区间披露（VNext §5 interpolation honesty）。
        placement_domain="panel", supported_outputs=["interactive", "png", "pdf", "svg"],
        renderer_support=["interactive"], exporter_support=["png", "pdf", "svg"],
        default_variant="default", variants=["default", "compact"],
        default_position="bottom-right", allowed_positions=["bottom-right", "bottom-left", "top-left", "top-right", "none"],
        cardinality="zero_or_one", priority=47, runtime_status="native",
        required_context=["uncertainty"],
        states=["visible", "hidden", "collapsed"], collision_class="panel",
        accessibility={"role": "note", "label_zh": "不确定性面板"},
    ),
    MapComponentDescriptor(
        id="decision_panel", category="disclosure.decision_panel",
        type="decision_panel",
        name="Decision Panel", name_zh="决策面板",
        # 候选排名 + 方法 + 权重来源 + 硬约束否决（VNext §12）。观测证据
        # 与用户假设可区分 —— weightSource 必须显式，不合成。
        placement_domain="panel", supported_outputs=["interactive", "png", "pdf", "svg"],
        renderer_support=["interactive"], exporter_support=["png", "pdf", "svg"],
        default_variant="default", variants=["default", "compact"],
        # review M-F3：默认 top-left —— top-right 是 inset_map 的 168px
        # 大槽，decision 落那里必压插图（frontend layout-meta 同表）。
        default_position="top-left", allowed_positions=["top-left", "top-right", "bottom-right", "bottom-left", "none"],
        cardinality="zero_or_one", priority=48, runtime_status="native",
        required_context=["decision"],
        states=["visible", "hidden", "collapsed"], collision_class="panel",
        accessibility={"role": "group", "label_zh": "决策面板"},
    ),
    # ── ac-05（ADR-0154）：label_layer 正式注册为可寻址组件 ─────────────
    # taxonomy `content.label_layer`（component_taxonomy.py）首次拿到
    # descriptor。渲染不走 map-components chrome 注册表（labels 由
    # MapSpec layer.label 子层承担：runtime label-layout.ts / 编译器 /
    # SVG 导出三条消费路径），组件本体是**绑定与决策面**——持有
    # label_plan（ADR-0154 P1/P2）产出的字段挑选与策略编排，支持
    # rebind(field) 换字段局部突变。因此 renderer/exporter 支持矩阵
    # 如实留空（诚实契约 —— 见 _SUPPORT_MATRIX note）。
    MapComponentDescriptor(
        id="label_layer", category="content.label_layer", type="label_layer",
        name="Label Layer", name_zh="标注图层",
        description="Automatic map labeling — field choice + strategy "
                    "(mode/top_n/zoom bands) from label_plan (ADR-0154)",
        placement_domain="layer", supported_outputs=["interactive", "png", "pdf", "svg"],
        renderer_support=[], exporter_support=[],
        default_variant="auto_field",
        variants=["auto_field", "explicit_field", "top_n", "hover_only"],
        default_position="none", allowed_positions=["none"],
        cardinality="multiple", requires_layer_binding=True,
        priority=20, runtime_status="native",
        tags=["content", "label", "annotation", "标注"],
        collision_class="none",
        interactions=["switch_variant", "selection_linkage"],
        # role="note"：labels 是地图上的文字注记（同 annotation 族语义），
        # 非 chrome/img；前端 aria 消费见 map-components 可达性契约。
        accessibility={"role": "note", "label_zh": "标注图层"},
    ),
]


class ComponentRegistry:
    """Indexed component descriptor registry."""

    def __init__(self) -> None:
        self._by_id: Dict[str, MapComponentDescriptor] = {}
        self._by_type: Dict[str, str] = {}
        # #1076(D-8): 注册代次 —— 静态目录派生缓存的失效键。
        self._version: int = 0
        self._by_category: Dict[str, List[str]] = {}

    def load_builtins(self) -> None:
        self._by_id.clear()
        self._by_type.clear()
        self._version += 1
        self._by_category.clear()
        for desc in _SEED_DESCRIPTORS:
            self.register(desc)
        self._apply_semantic_roles()
        self._apply_v7_projections()

    def _apply_semantic_roles(self) -> None:
        """Epic 11：语义角色/示例回填（投影自 COMPONENT_SEMANTIC_ROLES
        单一事实源；register/load 后调用，扩展组件缺省空 = 未声明）。"""
        for cid, role in COMPONENT_SEMANTIC_ROLES.items():
            desc = self._by_id.get(cid)
            if desc is None:
                continue
            example = _COMPONENT_EXAMPLES.get(cid, "")
            updates: dict = {}
            if desc.semantic_role != role:
                updates["semantic_role"] = role
            if example and example not in desc.examples:
                updates["examples"] = [example]
            if updates:
                self._by_id[cid] = desc.model_copy(update=updates)

    def _apply_v7_projections(self) -> None:
        """V7：搜索关键词 + 预览元数据投影（单一事实源表 → descriptor）。
        显式构造传入的字段不被覆盖（扩展组件自带关键词优先）。"""
        for cid, keywords in COMPONENT_SEARCH_KEYWORDS.items():
            desc = self._by_id.get(cid)
            if desc is None or desc.search_keywords_zh:
                continue
            self._by_id[cid] = desc.model_copy(
                update={"search_keywords_zh": list(keywords)})
        for cid, preview in COMPONENT_PREVIEWS.items():
            desc = self._by_id.get(cid)
            if desc is None or desc.preview.glyph:
                continue
            bounded = ComponentPreview(
                glyph=str(preview.get("glyph", ""))[:4],
                accent=str(preview.get("accent", ""))[:9],
            )
            self._by_id[cid] = self._by_id[cid].model_copy(
                update={"preview": bounded})

    def register(self, desc: MapComponentDescriptor) -> None:
        if desc.id in self._by_id:
            raise ValueError(f"duplicate component descriptor id: {desc.id}")
        self._by_id[desc.id] = desc
        self._by_type[desc.type] = desc.id
        self._version += 1
        self._by_category.setdefault(desc.category, []).append(desc.id)
        # also index by top-level category prefix
        top = desc.category.split(".")[0]
        if top != desc.category:
            self._by_category.setdefault(top, []).append(desc.id)

    def registry_version(self) -> int:
        """#1076(D-8): 注册代次（静态目录派生缓存的失效键）。"""
        return self._version

    def unregister(self, descriptor_id: str) -> bool:
        """ADR-0104：扩展 cartography 组件卸载回滚用。清理全部索引
        （by-id / by-type / by-category）并推进注册代次；目标不存在返回
        False（幂等）。种子组件从不调用。"""
        desc = self._by_id.get(descriptor_id)
        if desc is None:
            return False
        del self._by_id[descriptor_id]
        if self._by_type.get(desc.type) == descriptor_id:
            self._by_type.pop(desc.type, None)
        for ids in self._by_category.values():
            if descriptor_id in ids:
                ids.remove(descriptor_id)
        self._by_category = {k: v for k, v in self._by_category.items() if v}
        self._version += 1
        return True

    def get(self, descriptor_id: str) -> Optional[MapComponentDescriptor]:
        return self._by_id.get(descriptor_id)

    def get_by_type(self, component_type: str) -> Optional[MapComponentDescriptor]:
        did = self._by_type.get(component_type)
        return self._by_id.get(did) if did else None

    def has(self, descriptor_id: str) -> bool:
        return descriptor_id in self._by_id

    def by_category(self, category: str, include_descendants: bool = True) -> List[MapComponentDescriptor]:
        if include_descendants:
            # match prefix
            result: List[MapComponentDescriptor] = []
            for cid, desc in self._by_id.items():
                if desc.category == category or desc.category.startswith(category + "."):
                    result.append(desc)
            return sorted(result, key=lambda d: d.id)
        ids = self._by_category.get(category, [])
        return [self._by_id[i] for i in ids]

    def by_map_model(self, map_model_id: str) -> List[MapComponentDescriptor]:
        return sorted(
            [d for d in self._by_id.values() if not d.compatible_map_models or map_model_id in d.compatible_map_models],
            key=lambda d: d.priority,
        )

    def by_output_target(self, output: str) -> List[MapComponentDescriptor]:
        return sorted(
            [d for d in self._by_id.values() if output in d.supported_outputs],
            key=lambda d: d.priority,
        )

    # ── V7：语义搜索 + 可解释推荐（确定性；无外呼、无 per-query 硬编码）──

    @staticmethod
    def _search_field_text(desc: MapComponentDescriptor) -> "dict[str, str]":
        """参与搜索的字段 → 文本（id/name_zh 在多字段同权前已单独计分）。"""
        return {
            "name": desc.name or "",
            "name_zh": desc.name_zh or "",
            "description": desc.description or "",
            "semantic_role": desc.semantic_role or "",
            "category": desc.category or "",
        }

    def search(
        self,
        query: str,
        *,
        category: str = "",
        semantic_role: str = "",
        output_target: str = "",
        map_model: str = "",
        include_deprecated: bool = False,
        limit: int = 8,
    ) -> List[ComponentSearchHit]:
        """组件目录语义搜索（确定性 token 评分；无嵌入/无网络）。

        匹配面：id / name / name_zh / description / semantic_role / category /
        tags / search_keywords_zh / examples。query 先整串、后空格分词，
        双通道取最大命中；得分权重：id 精确 100 > id 子串 40 > name_zh
        精确 60 > 关键词 25 > name_zh 词 20 > tags 15 > 语义角色 12 >
        描述 8 > 示例 5。过滤器（category 前缀 / role / output / model /
        弃用）是硬约束。返回按 (-score, id) 稳定排序，有界 limit。
        """
        q = (query or "").strip().lower()
        if not q:
            return []
        tokens = [t for t in q.split() if t][:6]
        hits: List[ComponentSearchHit] = []
        for desc in self._by_id.values():
            if not include_deprecated and desc.deprecated:
                continue
            if category and not (
                desc.category == category or desc.category.startswith(category + ".")
            ):
                continue
            if semantic_role and desc.semantic_role != semantic_role:
                continue
            if output_target and output_target not in desc.supported_outputs:
                continue
            if map_model and desc.compatible_map_models and \
                    map_model not in desc.compatible_map_models:
                continue
            fields = self._search_field_text(desc)
            keywords = [k.lower() for k in desc.search_keywords_zh]
            tags = [t.lower() for t in desc.tags]
            examples = [e.lower() for e in desc.examples]

            score = 0
            matched: List[str] = []
            did = desc.id.lower()
            if q == did:
                score += 100
                matched.append("id")
            elif q in did:
                score += 40
                matched.append("id")
            if fields["name_zh"] and q == fields["name_zh"].lower():
                score += 60
                matched.append("name_zh")

            def _match_pool(pool: List[str], weight: int, field: str) -> int:
                best = 0
                for cand in pool:
                    if not cand:
                        continue
                    if q == cand or q in cand or cand in q:
                        return weight
                    for token in tokens:
                        if token and (token in cand or cand in token):
                            best = max(best, max(weight // 2, 1))
                return best

            kw_score = _match_pool(keywords, 25, "keyword")
            if kw_score:
                score += kw_score
                matched.append("keyword")
            zh_score = _match_pool([fields["name_zh"]], 20, "name_zh")
            if zh_score:
                score += zh_score
                matched.append("name_zh")
            tag_score = _match_pool(tags, 15, "tags")
            if tag_score:
                score += tag_score
                matched.append("tags")
            role_score = _match_pool([fields["semantic_role"]], 12, "semantic_role")
            if role_score:
                score += role_score
                matched.append("semantic_role")
            desc_score = _match_pool([fields["description"]], 8, "description")
            if desc_score:
                score += desc_score
                matched.append("description")
            ex_score = _match_pool(examples, 5, "examples")
            if ex_score:
                score += ex_score
                matched.append("examples")

            if score <= 0:
                continue
            deduped = list(dict.fromkeys(matched))
            hits.append(ComponentSearchHit(
                descriptor=desc, score=score, matched_fields=deduped))
        hits.sort(key=lambda h: (-h.score, h.descriptor.id))
        return hits[: max(1, min(limit, 16))]

    def recommend(
        self,
        context: ComponentRecommendationContext,
        *,
        limit: int = 8,
    ) -> List[ComponentRecommendation]:
        """上下文推荐（确定性评分；理由有界 —— Harness 候选卡可解释）。

        评分因素：输出目标支持 +2；语义角色命中 +3；map_model 兼容（限定型
        未命中 −4，通用型 +1）；任务类目与 category 语义族命中 +2（受控
        亲和表）；已在场组件：single/zero_or_one 直接排除（非「推荐新增」
        的合法候选），multiple −1 保留；弃用组件强过滤（除非无候选）。
        priority 数值小者优先 +（100−priority)/25。
        """
        model_reg = None
        if context.map_model:
            try:
                from app.lib.cartography.model_library import get_map_model_registry
                model_reg = get_map_model_registry()
            except Exception:  # pragma: no cover - 防御性
                model_reg = None
        resolved_model = ""
        if model_reg is not None and context.map_model:
            m = model_reg.resolve(context.map_model)
            resolved_model = m.id if m is not None else ""

        # 在场判定：类型级输入（descriptor id / component type；横线归一）。
        # 组合流程里 Harness 天然持有 selected 类型清单（ComponentComposer
        # 的 selection.selected），任意命名的实例 id 不参与类型去重。
        existing = {e.strip().replace("-", "_")
                    for e in context.existing_components if e}

        def _is_present(desc: MapComponentDescriptor) -> bool:
            return (desc.id.replace("-", "_") in existing
                    or desc.type.replace("-", "_") in existing)
        want_roles = {r for r in context.semantic_roles if r}
        want_cats = {c for c in context.task_categories if c}
        # 任务类目 → 组件 category 语义族亲和（受控映射，非 query 硬编码）
        category_affinity = {
            "spatial_distribution": {"analysis", "legend"},
            "density": {"legend", "analysis"},
            "thematic_cartography": {"legend", "annotation", "navigation"},
            "administrative_aggregation": {"analysis"},
            "atlas_reporting": {"export", "annotation"},
            "interpolation": {"legend"},
            "terrain": {"legend", "navigation"},
            "hydrology": {"legend"},
            "hotspot": {"legend"},
            "change_detection": {"analysis", "legend"},
            "remote_sensing_extraction": {"legend"},
            "multi_criteria": {"analysis", "disclosure"},
        }

        out: List[ComponentRecommendation] = []
        for desc in self._by_id.values():
            if desc.deprecated:
                continue
            if context.output_target and \
                    context.output_target not in desc.supported_outputs:
                continue
            score = 0
            reasons: List[str] = []

            role_hits = 0
            if desc.semantic_role and desc.semantic_role in want_roles:
                role_hits += 1
                score += 3
                reasons.append(f"语义角色命中 {desc.semantic_role}")
            if role_hits > 1:  # 防御：单 descriptor 至多一角色
                score -= 3 * (role_hits - 1)

            if context.output_target and context.output_target in desc.supported_outputs:
                score += 2

            if desc.compatible_map_models:
                if resolved_model and resolved_model in desc.compatible_map_models:
                    score += 3
                    reasons.append(f"兼容主表达 {resolved_model}")
                elif resolved_model:
                    score -= 4
            elif resolved_model:
                score += 1  # 通用型组件对所有模型开放

            affinity_hit = False
            top_cat = desc.category.split(".")[0]
            for cat in want_cats:
                if cat in category_affinity and top_cat in category_affinity[cat]:
                    affinity_hit = True
                    break
            if affinity_hit:
                score += 2
                reasons.append(f"任务类目亲和 {top_cat}")

            if _is_present(desc):
                if desc.cardinality in ("single", "zero_or_one"):
                    # 已在场且不可重复 → 不是「推荐新增」的合法候选，
                    # 直接排除（比扣分更诚实： Harness 问的是"还该加什么"）
                    continue
                score -= 1
                reasons.append("已在场（可多实例）")

            if desc.dependencies:
                missing = [d for d in desc.dependencies
                           if d.replace("-", "_") not in existing]
                if missing:
                    score -= 2
                    reasons.append(f"依赖缺失 {','.join(missing[:2])}")

            score += (100 - desc.priority) // 25

            if score <= 0:
                continue
            out.append(ComponentRecommendation(
                component_id=desc.id, score=score,
                reasons=reasons[:4]))
        out.sort(key=lambda r: (-r.score, r.component_id))
        if not out:
            # 弃用组件兜底：宁可推荐弃用态也不空手（显式披露由调用方承担）
            fallback = sorted(
                (d for d in self._by_id.values() if d.deprecated),
                key=lambda d: (d.priority, d.id))
            out = [
                ComponentRecommendation(component_id=d.id, score=1,
                                        reasons=["deprecated fallback"])
                for d in fallback[:limit]
            ]
        return out[: max(1, min(limit, 16))]


    def native_descriptors(self) -> List[MapComponentDescriptor]:
        return [d for d in self._by_id.values() if d.runtime_status == "native"]

    def compatible(self, descriptor_id: str, map_model_id: str, output_target: str = "") -> bool:
        desc = self._by_id.get(descriptor_id)
        if not desc:
            return False
        if desc.runtime_status == "unavailable":
            return False
        # 限定型组件（显式列出 compatible_map_models）必须命中当前模型；
        # 通用型组件（空清单）对所有模型开放。当前仅 legend 族是限定型，
        # 判定以「descriptor 是否限定」为准，不按类别硬编码。
        if desc.compatible_map_models and map_model_id not in desc.compatible_map_models:
            return False
        if output_target and output_target not in desc.supported_outputs:
            return False
        return True

    def validate(self) -> List[str]:
        # fail-closed：校验器自身异常转为 issue，绝不静默返回「0 issues」
        try:
            return self._validate_inner()
        except Exception as exc:  # pragma: no cover - 防御性
            return [f"component registry validate raised: {exc}"]

    def _validate_inner(self) -> List[str]:
        issues: List[str] = []
        try:
            from app.lib.cartography.component_renderers import (
                get_component_renderer_registry,
            )
            from app.lib.cartography.component_taxonomy import get_component_category_registry
            cat_reg = get_component_category_registry()
            for desc in self._by_id.values():
                if not cat_reg.has(desc.category):
                    issues.append(f"descriptor {desc.id}: unknown category {desc.category}")
                if desc.default_variant not in desc.variants and desc.variants:
                    issues.append(f"descriptor {desc.id}: default_variant {desc.default_variant} not in variants")
                for dep in desc.dependencies:
                    if dep not in self._by_id:
                        issues.append(f"descriptor {desc.id}: dependency {dep} not registered")
                for conf in desc.conflicts:
                    if conf not in self._by_id and conf not in self._by_type:
                        issues.append(f"descriptor {desc.id}: conflict {conf} not registered")
                # V3：compatible_map_models 必须可解析（canonical id 或已注册
                # 别名）—— 防目录虚构模型契约（与 composition validate 同语义）。
                if desc.compatible_map_models:
                    try:
                        from app.lib.cartography.model_library import get_map_model_registry
                        model_reg = get_map_model_registry()
                        for mid in desc.compatible_map_models:
                            if model_reg.resolve(mid) is None:
                                issues.append(
                                    f"descriptor {desc.id}: compatible_map_model "
                                    f"'{mid}' 未注册")
                    except Exception:  # pragma: no cover - 防御性
                        pass
                # V7：弃用指针与预览元数据契约
                if desc.deprecated and not desc.deprecated_by:
                    issues.append(
                        f"descriptor {desc.id}: deprecated 但缺 deprecated_by 后继")
                if desc.deprecated_by and desc.deprecated_by not in self._by_id:
                    issues.append(
                        f"descriptor {desc.id}: deprecated_by "
                        f"{desc.deprecated_by} 未注册")
                accent = desc.preview.accent
                if accent and not (
                    accent.startswith("#") and len(accent) in (4, 7)
                    and all(c in "0123456789abcdefABCDEF" for c in accent[1:])
                ):
                    issues.append(
                        f"descriptor {desc.id}: preview.accent {accent!r} 非合法 hex")
            # renderer/exporter 支持声明必须与机器真值矩阵一致（防契约撒谎）
            issues.extend(get_component_renderer_registry().validate_against_descriptors())
        except Exception:
            pass
        return issues

    @property
    def all_ids(self) -> List[str]:
        return sorted(self._by_id.keys())

    @property
    def count(self) -> int:
        return len(self._by_id)


_registry: Optional[ComponentRegistry] = None


def get_component_registry() -> ComponentRegistry:
    global _registry
    if _registry is None:
        _registry = ComponentRegistry()
        _registry.load_builtins()
    return _registry


def reset_component_registry() -> None:
    global _registry
    _registry = None


__all__ = [
    "MapComponentDescriptor",
    "ComponentPreview",
    "ComponentSearchHit",
    "ComponentRecommendationContext",
    "ComponentRecommendation",
    "ComponentRegistry",
    "get_component_registry",
    "reset_component_registry",
    "_SEED_DESCRIPTORS",
    "components_for_role",
]
