"""Atomic Component Template Library — 原子组件模板库.

每个 ComponentTemplate 描述一个可复用组件变体（variant + 默认样式/选项）。
Harness 通过 ComponentResolver 从中选择，ComponentComposer 将其填充为实例。
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

RuntimeStatus = Literal["native", "planned", "unavailable"]


class ComponentTemplate(BaseModel):
    id: str
    component_type: str
    category: str
    name: str = ""
    description: str = ""
    variant: str = "default"
    default_options: Dict[str, Any] = Field(default_factory=dict)
    default_style: Dict[str, Any] = Field(default_factory=dict)
    supported_outputs: List[str] = Field(default_factory=lambda: ["interactive", "png", "pdf"])
    compatible_map_models: List[str] = Field(default_factory=list)
    renderer_support: List[str] = Field(default_factory=list)
    preview_metadata: Dict[str, Any] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)
    schema_version: int = 1
    template_version: str = "1.0"
    deprecated: bool = False
    runtime_status: RuntimeStatus = "native"
    priority: int = 50


SEED_COMPONENT_TEMPLATES: List[ComponentTemplate] = [
    # ── north_arrow 4 variants ─────────────────────────────────────
    ComponentTemplate(
        id="north-arrow/minimal-black", component_type="north_arrow", category="navigation.north_arrow",
        name="Minimal Black", variant="compass_minimal_black",
        default_options={"variant": "compass_minimal_black"}, priority=10,
        supported_outputs=["interactive", "png", "pdf", "svg"],
    ),
    ComponentTemplate(
        id="north-arrow/simple-arrow", component_type="north_arrow", category="navigation.north_arrow",
        name="Simple Arrow", variant="arrow_simple",
        default_options={"variant": "arrow_simple"}, priority=20,
    ),
    ComponentTemplate(
        id="north-arrow/compass-needle", component_type="north_arrow", category="navigation.north_arrow",
        name="Compass Needle", variant="compass_needle",
        default_options={"variant": "compass_needle"}, priority=30,
    ),
    ComponentTemplate(
        id="north-arrow/compass-rose", component_type="north_arrow", category="navigation.north_arrow",
        name="Compass Rose", variant="compass_rose",
        default_options={"variant": "compass_rose"}, priority=40,
    ),
    # ── scale_bar 3 variants ───────────────────────────────────────
    ComponentTemplate(
        id="scale-bar/minimal", component_type="scale_bar", category="navigation.scale_bar",
        name="Minimal", variant="minimal",
        default_options={"orientation": "horizontal", "unit": "metric", "style": "minimal"}, priority=10,
    ),
    ComponentTemplate(
        id="scale-bar/boxed", component_type="scale_bar", category="navigation.scale_bar",
        name="Boxed", variant="boxed",
        default_options={"orientation": "horizontal", "unit": "metric", "style": "boxed"}, priority=20,
    ),
    ComponentTemplate(
        id="scale-bar/academic", component_type="scale_bar", category="navigation.scale_bar",
        name="Academic", variant="academic",
        default_options={"orientation": "horizontal", "unit": "metric", "style": "academic"}, priority=30,
    ),
    # ── legend 3 variants ──────────────────────────────────────────
    ComponentTemplate(
        id="legend/academic", component_type="legend", category="legend.graduated",
        name="Academic", variant="academic",
        default_options={"style": "academic"}, priority=10,
        compatible_map_models=["administrative_choropleth", "aggregate_grid"],
    ),
    ComponentTemplate(
        id="legend/compact", component_type="legend", category="legend.graduated",
        name="Compact", variant="compact",
        default_options={"style": "compact"}, priority=20,
    ),
    ComponentTemplate(
        id="legend/report", component_type="legend", category="legend.graduated",
        name="Report", variant="report",
        default_options={"style": "report"}, priority=30,
    ),
    # ── continuous_colorbar 3 variants ─────────────────────────────
    ComponentTemplate(
        id="colorbar/horizontal", component_type="continuous_colorbar", category="legend.continuous_colorbar",
        name="Horizontal", variant="horizontal",
        default_options={"orientation": "horizontal"}, priority=10,
        compatible_map_models=["visual_heatmap", "raster_surface"],
    ),
    ComponentTemplate(
        id="colorbar/vertical", component_type="continuous_colorbar", category="legend.continuous_colorbar",
        name="Vertical", variant="vertical",
        default_options={"orientation": "vertical"}, priority=20,
    ),
    ComponentTemplate(
        id="colorbar/slim", component_type="continuous_colorbar", category="legend.continuous_colorbar",
        name="Slim", variant="slim",
        default_options={"orientation": "horizontal", "style": "slim"}, priority=30,
    ),
    # ── title 3 variants ───────────────────────────────────────────
    ComponentTemplate(
        id="title/academic", component_type="title", category="annotation.title",
        name="Academic Title", variant="academic",
        default_style={"fontWeight": "600", "fontSize": "18px"}, priority=10,
    ),
    ComponentTemplate(
        id="title/report", component_type="title", category="annotation.title",
        name="Report Title", variant="report",
        default_style={"fontWeight": "700", "fontSize": "20px"}, priority=20,
    ),
    ComponentTemplate(
        id="title/presentation", component_type="title", category="annotation.title",
        name="Presentation Title", variant="presentation",
        default_style={"fontWeight": "700", "fontSize": "24px"}, priority=30,
    ),
    # ── subtitle 2 variants ────────────────────────────────────────
    ComponentTemplate(
        id="subtitle/default", component_type="subtitle", category="annotation.subtitle",
        name="Default Subtitle", variant="default",
        default_style={"fontSize": "14px", "opacity": "0.8"}, priority=10,
    ),
    ComponentTemplate(
        id="subtitle/academic", component_type="subtitle", category="annotation.subtitle",
        name="Academic Subtitle", variant="academic",
        default_style={"fontSize": "13px", "opacity": "0.7"}, priority=20,
    ),
    # ── attribution 2 variants ─────────────────────────────────────
    ComponentTemplate(
        id="attribution/default", component_type="attribution", category="annotation.attribution",
        name="Default Attribution", variant="default", priority=10,
    ),
    ComponentTemplate(
        id="attribution/compact", component_type="attribution", category="annotation.attribution",
        name="Compact Attribution", variant="compact",
        default_style={"fontSize": "10px"}, priority=20,
    ),
    # ── map_border 3 variants ──────────────────────────────────────
    ComponentTemplate(
        id="frame/minimal", component_type="map_border", category="frame.map_border",
        name="Minimal Border", variant="minimal",
        default_options={"style": "minimal"}, priority=10,
        supported_outputs=["png", "pdf", "svg"],
    ),
    ComponentTemplate(
        id="frame/academic", component_type="map_border", category="frame.map_border",
        name="Academic Border", variant="academic",
        default_options={"style": "academic"}, priority=20,
        supported_outputs=["png", "pdf", "svg"],
    ),
    ComponentTemplate(
        id="frame/report", component_type="map_border", category="frame.map_border",
        name="Report Border", variant="report",
        default_options={"style": "report"}, priority=30,
        supported_outputs=["png", "pdf", "svg"],
    ),
    # ── graticule 2 variants ───────────────────────────────────────
    ComponentTemplate(
        id="graticule/light", component_type="graticule", category="navigation.graticule",
        name="Light Graticule", variant="light",
        default_options={"style": "light", "opacity": "0.3"}, priority=10,
    ),
    ComponentTemplate(
        id="graticule/geographic", component_type="graticule", category="navigation.graticule",
        name="Geographic Graticule", variant="geographic",
        default_options={"style": "geographic"}, priority=20,
    ),
    # ── statistics_panel 2 variants ────────────────────────────────
    ComponentTemplate(
        id="statistics-panel/default", component_type="statistics_panel", category="analysis.statistics_panel",
        name="Default Statistics", variant="default", priority=10,
    ),
    ComponentTemplate(
        id="statistics-panel/compact", component_type="statistics_panel", category="analysis.statistics_panel",
        name="Compact Statistics", variant="compact",
        default_style={"fontSize": "12px"}, priority=20,
    ),
    # ── chart_panel 2 variants ─────────────────────────────────────
    ComponentTemplate(
        id="chart-panel/default", component_type="chart_panel", category="analysis.chart_panel",
        name="Default Chart", variant="default", priority=10,
    ),
    ComponentTemplate(
        id="chart-panel/compact", component_type="chart_panel", category="analysis.chart_panel",
        name="Compact Chart", variant="compact",
        default_style={"fontSize": "12px"}, priority=20,
    ),
    # ── export_layout 4 variants ───────────────────────────────────
    ComponentTemplate(
        id="export-layout/A4-landscape", component_type="export_layout", category="export.page_layout",
        name="A4 Landscape", variant="A4_landscape",
        default_options={"paperSize": "A4", "orientation": "landscape", "dpi": 300}, priority=10,
        supported_outputs=["png", "pdf"],
    ),
    ComponentTemplate(
        id="export-layout/A4-portrait", component_type="export_layout", category="export.page_layout",
        name="A4 Portrait", variant="A4_portrait",
        default_options={"paperSize": "A4", "orientation": "portrait", "dpi": 300}, priority=20,
        supported_outputs=["png", "pdf"],
    ),
    ComponentTemplate(
        id="export-layout/A3-landscape", component_type="export_layout", category="export.page_layout",
        name="A3 Landscape", variant="A3_landscape",
        default_options={"paperSize": "A3", "orientation": "landscape", "dpi": 300}, priority=30,
        supported_outputs=["png", "pdf"],
    ),
    ComponentTemplate(
        id="export-layout/letter", component_type="export_layout", category="export.page_layout",
        name="Letter", variant="letter",
        default_options={"paperSize": "Letter", "orientation": "landscape", "dpi": 300}, priority=40,
        supported_outputs=["png", "pdf"],
    ),
    # ── categorical_legend ─────────────────────────────────────────
    ComponentTemplate(
        id="categorical-legend/academic", component_type="categorical_legend", category="legend.categorical",
        name="Academic Categorical", variant="academic", priority=10,
    ),
    ComponentTemplate(
        id="categorical-legend/compact", component_type="categorical_legend", category="legend.categorical",
        name="Compact Categorical", variant="compact", priority=20,
    ),
    # ── V3（ADR-0101 D3）：variant library 扩容 ────────────────────────
    # 新变体的 priority 一律大于该类型既有首个模板 —— resolver 无偏好时
    # 取 (priority, id) 首个，扩容不得改变既有缺省选型（golden 兼容）。
    # V3 标题/副标题模板不带 default_style —— 标题排版唯一真值是渲染器
    # 的 TITLE_VARIANT_CLASS（style 通道当前无消费方，双写即漂移温床）。
    ComponentTemplate(
        id="title/minimal", component_type="title", category="annotation.title",
        name="Minimal Title", variant="minimal", priority=40,
    ),
    ComponentTemplate(
        id="title/government", component_type="title", category="annotation.title",
        name="Government Title", variant="government", priority=50,
    ),
    ComponentTemplate(
        id="subtitle/report", component_type="subtitle", category="annotation.subtitle",
        name="Report Subtitle", variant="report", priority=30,
    ),
    ComponentTemplate(
        id="north-arrow/monochrome", component_type="north_arrow", category="navigation.north_arrow",
        name="Monochrome Compass", variant="monochrome",
        default_options={"variant": "monochrome"}, priority=50,
        supported_outputs=["interactive", "png", "pdf", "svg"],
    ),
    ComponentTemplate(
        id="scale-bar/dual-unit", component_type="scale_bar", category="navigation.scale_bar",
        name="Dual Unit", variant="dual_unit",
        default_options={"orientation": "horizontal", "unit": "dual", "style": "dual_unit"}, priority=40,
    ),
    ComponentTemplate(
        id="colorbar/scientific", component_type="continuous_colorbar", category="legend.continuous_colorbar",
        name="Scientific Colorbar", variant="scientific",
        default_options={"orientation": "horizontal", "style": "scientific", "ticks": 3}, priority=40,
        compatible_map_models=["visual_heatmap", "raster_surface"],
    ),
    ComponentTemplate(
        id="colorbar/stepped", component_type="continuous_colorbar", category="legend.continuous_colorbar",
        name="Stepped Colorbar", variant="stepped",
        default_options={"orientation": "horizontal", "style": "stepped"}, priority=50,
        compatible_map_models=["visual_heatmap", "raster_surface"],
    ),
    ComponentTemplate(
        id="legend/horizontal", component_type="legend", category="legend.graduated",
        name="Horizontal Legend", variant="horizontal",
        default_options={"style": "horizontal", "orientation": "horizontal"}, priority=40,
    ),
    ComponentTemplate(
        id="categorical-legend/horizontal", component_type="categorical_legend", category="legend.categorical",
        name="Horizontal Categorical", variant="horizontal",
        default_options={"style": "horizontal", "orientation": "horizontal"}, priority=30,
    ),
    ComponentTemplate(
        id="frame/neatline", component_type="map_border", category="frame.map_border",
        name="Neatline Frame", variant="neatline",
        default_options={"style": "neatline"}, priority=40,
        supported_outputs=["png", "pdf", "svg"],
    ),
    ComponentTemplate(
        id="statistics-panel/kpi", component_type="statistics_panel", category="analysis.statistics_panel",
        name="KPI Statistics", variant="kpi",
        default_style={"fontSize": "18px"}, priority=30,
    ),
    # ── V3：descriptor 词表 ↔ 模板目录补全（既有缺口收编）─────────────
    # 此前若干 descriptor variants 从未有模板条目（annotation 三形态、
    # inset 两变体、table/披露族、chart 透明/报告、categorical report）；
    # variant 是一等公民就必须在模板目录可寻址、catalog 可导出。
    ComponentTemplate(
        id="categorical-legend/report", component_type="categorical_legend", category="legend.categorical",
        name="Report Categorical", variant="report", priority=40,
    ),
    ComponentTemplate(
        id="chart-panel/transparent", component_type="chart_panel", category="analysis.chart_panel",
        name="Transparent Chart", variant="transparent",
        default_style={"background": "transparent"}, priority=30,
    ),
    ComponentTemplate(
        id="chart-panel/report", component_type="chart_panel", category="analysis.chart_panel",
        name="Report Chart", variant="report",
        default_style={"fontSize": "13px"}, priority=40,
    ),
    ComponentTemplate(
        id="table-panel/default", component_type="table_panel", category="analysis.table_panel",
        name="Default Table", variant="default", priority=10,
    ),
    ComponentTemplate(
        id="table-panel/compact", component_type="table_panel", category="analysis.table_panel",
        name="Compact Table", variant="compact",
        default_style={"fontSize": "11px"}, priority=20,
    ),
    ComponentTemplate(
        id="annotation/text", component_type="annotation", category="annotation.text",
        name="Text Annotation", variant="text",
        default_options={"variant": "text"}, priority=10,
    ),
    ComponentTemplate(
        id="annotation/callout", component_type="annotation", category="annotation.callout",
        name="Callout Annotation", variant="callout",
        default_options={"variant": "callout"}, priority=20,
    ),
    ComponentTemplate(
        id="annotation/group", component_type="annotation", category="annotation.text",
        name="Group Annotation", variant="group",
        default_options={"variant": "group"}, priority=30,
    ),
    ComponentTemplate(
        id="inset-map/overview", component_type="inset_map", category="inset.map",
        name="Overview Inset", variant="overview",
        default_options={"variant": "overview"}, priority=10,
    ),
    ComponentTemplate(
        id="inset-map/location", component_type="inset_map", category="inset.location_map",
        name="Location Inset", variant="location",
        default_options={"variant": "location"}, priority=20,
    ),
    ComponentTemplate(
        id="methodology-note/default", component_type="methodology_note", category="disclosure.methodology_note",
        name="Default Methodology Note", variant="default", priority=10,
    ),
    ComponentTemplate(
        id="methodology-note/compact", component_type="methodology_note", category="disclosure.methodology_note",
        name="Compact Methodology Note", variant="compact",
        default_style={"fontSize": "11px"}, priority=20,
    ),
    ComponentTemplate(
        id="uncertainty-panel/default", component_type="uncertainty_panel", category="disclosure.uncertainty_panel",
        name="Default Uncertainty Panel", variant="default", priority=10,
    ),
    ComponentTemplate(
        id="uncertainty-panel/compact", component_type="uncertainty_panel", category="disclosure.uncertainty_panel",
        name="Compact Uncertainty Panel", variant="compact",
        default_style={"fontSize": "11px"}, priority=20,
    ),
    ComponentTemplate(
        id="decision-panel/default", component_type="decision_panel", category="disclosure.decision_panel",
        name="Default Decision Panel", variant="default", priority=10,
    ),
    ComponentTemplate(
        id="decision-panel/compact", component_type="decision_panel", category="disclosure.decision_panel",
        name="Compact Decision Panel", variant="compact",
        default_style={"fontSize": "11px"}, priority=20,
    ),
    # ── V4：原前瞻变体转正（bivariate/uncertainty/hierarchy 渲染链落地）──
    ComponentTemplate(
        id="legend/bivariate", component_type="legend", category="legend.bivariate",
        name="Bivariate Legend", variant="bivariate",
        default_options={"style": "bivariate"}, priority=90,
        compatible_map_models=["bivariate_choropleth", "bivariate_raster"],
        tags=["bivariate", "v4"],
    ),
    ComponentTemplate(
        id="legend/uncertainty", component_type="legend", category="legend.graduated",
        name="Uncertainty Legend", variant="uncertainty",
        default_options={"style": "uncertainty"}, priority=91,
        compatible_map_models=["uncertainty_choropleth", "uncertainty_surface",
                               "uncertainty_point_symbol"],
        tags=["uncertainty", "v4"],
    ),
    ComponentTemplate(
        id="inset-map/hierarchy-locator", component_type="inset_map", category="inset.map",
        name="Hierarchy Locator Inset", variant="hierarchy",
        default_options={"variant": "hierarchy"}, priority=90,
        tags=["inset", "v4"],
    ),
    # ── V4：图例族扩展 ────────────────────────────────────────────────
    ComponentTemplate(
        id="legend/size", component_type="legend", category="legend.graduated",
        name="Proportional Size Legend", variant="size",
        default_options={"style": "size"}, priority=92,
        compatible_map_models=["proportional_symbol", "flow_hub_map",
                               "extrusion_3d"],
        tags=["size", "v4"],
    ),
    ComponentTemplate(
        id="legend/line", component_type="legend", category="legend.graduated",
        name="Line Width Legend", variant="line",
        default_options={"style": "line"}, priority=93,
        compatible_map_models=["graduated_line", "network_flow_map",
                               "stream_order_map", "flow_od_arc",
                               "accessibility_network", "route_map",
                               "network_centrality_map"],
        tags=["line", "v4"],
    ),
    ComponentTemplate(
        id="legend/composite", component_type="legend", category="legend.graduated",
        name="Multi-layer Composite Legend", variant="composite",
        default_options={"style": "composite"}, priority=94,
        tags=["composite", "v4"],
    ),
    ComponentTemplate(
        id="categorical-legend/nested", component_type="categorical_legend",
        category="legend.categorical",
        name="Nested Categorical Legend", variant="nested",
        default_options={"style": "nested"}, priority=40,
        tags=["nested", "v4"],
    ),
    # ── V4：版面附注族 ────────────────────────────────────────────────
    ComponentTemplate(
        id="annotation/footer", component_type="annotation", category="annotation.text",
        name="Footer Note", variant="footer",
        default_options={"variant": "footer", "positionHint": "bottom"},
        priority=60, tags=["footer", "v4"],
    ),
    ComponentTemplate(
        id="annotation/timestamp", component_type="annotation", category="annotation.text",
        name="Timestamp Note", variant="timestamp",
        default_options={"variant": "timestamp"}, priority=61,
        tags=["timestamp", "v4"],
    ),
    ComponentTemplate(
        id="annotation/projection-note", component_type="annotation", category="annotation.text",
        name="Projection Note", variant="projection_note",
        default_options={"variant": "projection_note"}, priority=62,
        tags=["projection", "v4"],
    ),
    ComponentTemplate(
        id="annotation/data-source", component_type="annotation", category="annotation.text",
        name="Data Source Note", variant="data_source",
        default_options={"variant": "data_source"}, priority=63,
        tags=["data-source", "v4"],
    ),
    ComponentTemplate(
        id="annotation/highlight", component_type="annotation", category="annotation.text",
        name="Highlight Region", variant="highlight",
        default_options={"variant": "highlight"}, priority=64,
        tags=["highlight", "v4"],
    ),
    ComponentTemplate(
        id="title/banner", component_type="title", category="annotation.title",
        name="Banner Title", variant="banner",
        default_style={"fontWeight": "700", "fontSize": "24px"}, priority=30,
        tags=["banner", "v4"],
    ),
    ComponentTemplate(
        id="title/compact", component_type="title", category="annotation.title",
        name="Compact Title", variant="compact",
        default_style={"fontWeight": "600", "fontSize": "14px"}, priority=40,
        tags=["compact", "v4"],
    ),
    ComponentTemplate(
        id="subtitle/compact", component_type="subtitle", category="annotation.subtitle",
        name="Compact Subtitle", variant="compact",
        default_style={"fontSize": "12px"}, priority=40,
        tags=["compact", "v4"],
    ),
    ComponentTemplate(
        id="graticule/projected", component_type="graticule", category="navigation.graticule",
        name="Projected Grid", variant="projected",
        default_options={"variant": "projected"}, priority=30,
        tags=["projected", "v4"],
    ),
    ComponentTemplate(
        id="north-arrow/dual-convention", component_type="north_arrow",
        category="navigation.north_arrow",
        name="Dual Convention Arrow", variant="dual_convention",
        default_options={"variant": "dual_convention"}, priority=50,
        tags=["dual", "v4"],
    ),
    ComponentTemplate(
        id="statistics-panel/explanation", component_type="statistics_panel",
        category="analysis.statistics_panel",
        name="Explanation Panel", variant="explanation",
        default_options={"style": "explanation"}, priority=40,
        tags=["explanation", "v4"],
    ),
    ComponentTemplate(
        id="table-panel/dense", component_type="table_panel",
        category="analysis.table_panel",
        name="Dense Table", variant="dense",
        default_options={"style": "dense"}, priority=30,
        tags=["dense", "v4"],
    ),
    ComponentTemplate(
        id="methodology-note/data-quality", component_type="methodology_note",
        category="disclosure.methodology_note",
        name="Data Quality Note", variant="data_quality",
        default_options={"style": "data_quality"}, priority=30,
        tags=["data-quality", "v4"],
    ),
]
# ── V4：图表 kind preset 模板（chart_kinds 词表；violin planned 不设）──
_CHART_KIND_TEMPLATES = [
    ("bar", 100), ("horizontal_bar", 101), ("grouped_bar", 102),
    ("stacked_bar", 103), ("line", 104), ("area", 105), ("scatter", 106),
    ("histogram", 107), ("box_plot", 108), ("pie", 109), ("donut", 110),
    ("radar", 111), ("rose", 112), ("timeseries", 113), ("cumulative", 114),
    ("heat_matrix", 115), ("kpi_card", 116), ("ranking_list", 117),
]
for _kind, _pri in _CHART_KIND_TEMPLATES:
    SEED_COMPONENT_TEMPLATES.append(ComponentTemplate(
        id="chart-panel/kind-" + _kind.replace("_", "-"),
        component_type="chart_panel", category="analysis.chart_panel",
        name="Chart Kind: " + _kind, variant=_kind,
        default_options={"chartType": _kind}, priority=_pri,
        tags=["chart-kind", "v4"],
    ))


class ComponentTemplateRegistry:
    def __init__(self) -> None:
        self._by_id: Dict[str, ComponentTemplate] = {}
        self._by_type: Dict[str, List[str]] = {}
        self._by_category: Dict[str, List[str]] = {}

    def load_builtins(self) -> None:
        self._by_id.clear()
        self._by_type.clear()
        self._by_category.clear()
        for tpl in SEED_COMPONENT_TEMPLATES:
            self.register(tpl)

    def register(self, tpl: ComponentTemplate) -> None:
        if tpl.id in self._by_id:
            raise ValueError(f"duplicate component template id: {tpl.id}")
        self._by_id[tpl.id] = tpl
        self._by_type.setdefault(tpl.component_type, []).append(tpl.id)
        self._by_category.setdefault(tpl.category, []).append(tpl.id)

    def get(self, template_id: str) -> Optional[ComponentTemplate]:
        return self._by_id.get(template_id)

    def has(self, template_id: str) -> bool:
        return template_id in self._by_id

    def find_by_type(self, component_type: str) -> List[ComponentTemplate]:
        ids = self._by_type.get(component_type, [])
        return sorted([self._by_id[i] for i in ids], key=lambda t: (t.priority, t.id))

    def find_by_category(self, category: str) -> List[ComponentTemplate]:
        ids = self._by_category.get(category, [])
        # also check prefix descendants
        result_ids = set(ids)
        for cid, tmpl in self._by_id.items():
            if tmpl.category.startswith(category + "."):
                result_ids.add(cid)
        return sorted([self._by_id[i] for i in result_ids], key=lambda t: (t.priority, t.id))

    def find_for_map_model(self, map_model_id: str) -> List[ComponentTemplate]:
        return sorted(
            [t for t in self._by_id.values() if not t.compatible_map_models or map_model_id in t.compatible_map_models],
            key=lambda t: (t.priority, t.id),
        )

    def validate(self) -> List[str]:
        issues: List[str] = []
        try:
            from app.lib.cartography.component_registry import get_component_registry
            from app.lib.cartography.component_taxonomy import get_component_category_registry
            comp_reg = get_component_registry()
            cat_reg = get_component_category_registry()
            for tpl in self._by_id.values():
                if not comp_reg.has(tpl.component_type) and not comp_reg.get_by_type(tpl.component_type):
                    # check by type or id
                    if tpl.component_type not in comp_reg.all_ids:
                        issues.append(f"template {tpl.id}: unknown component type {tpl.component_type}")
                if not cat_reg.has(tpl.category):
                    issues.append(f"template {tpl.id}: unknown category {tpl.category}")
                # V3（ADR-0101 D3）：native 模板的 variant 必须在 descriptor
                # variants 词表内（渲染器真实支持集）；planned 模板豁免 ——
                # 它们就是尚未落地的 roadmap 变体，resolver 不会选择。
                desc = comp_reg.get(tpl.component_type) or comp_reg.get_by_type(tpl.component_type)
                if desc is not None and tpl.runtime_status == "native":
                    if desc.variants and tpl.variant not in desc.variants:
                        issues.append(
                            f"template {tpl.id}: variant '{tpl.variant}' not in "
                            f"descriptor variants {desc.variants}")
                # options 中的 variant 双写必须与模板 variant 一致（既有约定）
                opt_variant = tpl.default_options.get("variant")
                if opt_variant is not None and opt_variant != tpl.variant:
                    issues.append(
                        f"template {tpl.id}: default_options.variant '{opt_variant}' "
                        f"!= template variant '{tpl.variant}'")
        except Exception:
            pass
        return issues

    @property
    def all_ids(self) -> List[str]:
        return sorted(self._by_id.keys())

    @property
    def count(self) -> int:
        return len(self._by_id)


_registry: Optional[ComponentTemplateRegistry] = None


def get_component_template_registry() -> ComponentTemplateRegistry:
    global _registry
    if _registry is None:
        _registry = ComponentTemplateRegistry()
        _registry.load_builtins()
    return _registry


def reset_component_template_registry() -> None:
    global _registry
    _registry = None


__all__ = [
    "ComponentTemplate",
    "ComponentTemplateRegistry",
    "get_component_template_registry",
    "reset_component_template_registry",
    "SEED_COMPONENT_TEMPLATES",
]
