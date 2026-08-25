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
]


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
