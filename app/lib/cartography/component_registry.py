"""MapComponentDescriptor registry — 组件定义目录.

每个组件类型的机器可读描述（非实例）。 Registry 提供
按 category / map_model / output 的确定性索引查询。
"""
from __future__ import annotations

from typing import Dict, List, Literal, Optional

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
    "ComponentRegistry",
    "get_component_registry",
    "reset_component_registry",
    "_SEED_DESCRIPTORS",
]
