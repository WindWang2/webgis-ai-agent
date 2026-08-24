"""MapCompositionTemplate — 地图组合模板.

CompositionTemplate = 一组 Component Slots + Layer Slots + Layout Rules.
描述最终地图由哪些组件槽位构成，供 Harness 自动填充。

每个模板声明：
- 兼容的 MapModel / Product 类别
- output_targets（interactive / pdf 等）
- component_slots（每个槽位的 required / optional / forbidden 语义）
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

SlotCardinality = Literal["required", "recommended", "optional", "conditional", "forbidden"]
CollisionPolicy = Literal["exclusive", "stack_vertical", "stack_horizontal", "allow_overlap"]
LayoutProfile = Literal["minimal", "standard", "academic", "report", "presentation", "dense"]


class ComponentSlot(BaseModel):
    id: str
    category: str = ""
    required: bool = False
    cardinality: SlotCardinality = "optional"
    min_count: int = 0
    max_count: int = 1
    preferred_templates: List[str] = Field(default_factory=list)
    allowed_component_types: List[str] = Field(default_factory=list)
    position_zone: str = "none"
    fallback_zones: List[str] = Field(default_factory=list)
    bind_role: str = ""  # e.g., "primary" for legend binding
    conditions: Dict[str, Any] = Field(default_factory=dict)
    stack_behavior: str = "exclusive"


class MapCompositionTemplate(BaseModel):
    id: str
    name: str = ""
    description: str = ""
    compatible_map_models: List[str] = Field(default_factory=list)
    compatible_product_types: List[str] = Field(default_factory=list)
    output_targets: List[str] = Field(default_factory=lambda: ["interactive"])
    component_slots: List[ComponentSlot] = Field(default_factory=list)
    layout_profile: LayoutProfile = "standard"
    collision_policy: CollisionPolicy = "exclusive"
    fallback_template: str = ""
    priority: int = 50
    schema_version: int = 1
    tags: List[str] = Field(default_factory=list)


SEED_COMPOSITION_TEMPLATES: List[MapCompositionTemplate] = [
    MapCompositionTemplate(
        id="composition.minimal_interactive",
        name="Minimal Interactive",
        description="最小交互地图：标题可选、色条/图例按需、比例尺必备。",
        compatible_map_models=[],
        compatible_product_types=[],
        output_targets=["interactive"],
        layout_profile="minimal",
        collision_policy="exclusive",
        fallback_template="composition.standard_analysis",
        priority=10,
        component_slots=[
            ComponentSlot(id="title", category="annotation.title", cardinality="optional", min_count=0, max_count=1, allowed_component_types=["title"], position_zone="top-center"),
            ComponentSlot(id="legend", category="legend.graduated", cardinality="conditional", min_count=0, max_count=1, allowed_component_types=["legend", "categorical_legend", "continuous_colorbar"], position_zone="bottom-left", bind_role="primary"),
            ComponentSlot(id="north_arrow", category="navigation.north_arrow", cardinality="optional", allowed_component_types=["north_arrow"], position_zone="top-right"),
            ComponentSlot(id="scale_bar", category="navigation.scale_bar", cardinality="required", required=True, min_count=1, max_count=1, allowed_component_types=["scale_bar"], position_zone="bottom-right"),
            ComponentSlot(id="attribution", category="annotation.attribution", cardinality="required", required=True, allowed_component_types=["attribution"], position_zone="bottom-left"),
            ComponentSlot(id="map_border", category="frame.map_border", cardinality="forbidden", max_count=0, allowed_component_types=["map_border"]),
            ComponentSlot(id="export_layout", category="export.page_layout", cardinality="forbidden", max_count=0, allowed_component_types=["export_layout"]),
        ],
    ),
    MapCompositionTemplate(
        id="composition.standard_analysis",
        name="Standard Analysis",
        description="标准分析地图：标题+图例/色条+指北针+比例尺+归属+统计可选。",
        compatible_map_models=["visual_heatmap", "administrative_choropleth", "aggregate_grid"],
        output_targets=["interactive", "png", "pdf"],
        layout_profile="standard",
        collision_policy="exclusive",
        fallback_template="composition.minimal_interactive",
        priority=20,
        component_slots=[
            ComponentSlot(id="title", category="annotation.title", cardinality="required", required=True, min_count=1, allowed_component_types=["title"], position_zone="top-center"),
            ComponentSlot(id="subtitle", category="annotation.subtitle", cardinality="optional", allowed_component_types=["subtitle"], position_zone="top-center", fallback_zones=["top-left"]),
            ComponentSlot(id="legend", category="legend.graduated", cardinality="conditional", allowed_component_types=["legend", "categorical_legend", "continuous_colorbar"], position_zone="bottom-left", bind_role="primary"),
            ComponentSlot(id="north_arrow", category="navigation.north_arrow", cardinality="required", required=True, allowed_component_types=["north_arrow"], position_zone="top-right"),
            ComponentSlot(id="scale_bar", category="navigation.scale_bar", cardinality="required", required=True, allowed_component_types=["scale_bar"], position_zone="bottom-right"),
            ComponentSlot(id="attribution", category="annotation.attribution", cardinality="required", required=True, allowed_component_types=["attribution"], position_zone="bottom-left"),
            ComponentSlot(id="statistics_panel", category="analysis.statistics_panel", cardinality="optional", allowed_component_types=["statistics_panel"], position_zone="top-left"),
            ComponentSlot(id="map_border", category="frame.map_border", cardinality="optional", allowed_component_types=["map_border"], position_zone="none"),
        ],
    ),
    MapCompositionTemplate(
        id="composition.academic_map",
        name="Academic Map",
        description="学术出版地图：含标题、图例/色条、指北针、比例尺、经纬网、归属、图框、数据来源，导出必备版式。",
        compatible_map_models=["visual_heatmap", "administrative_choropleth", "raster_surface"],
        output_targets=["png", "pdf", "svg"],
        layout_profile="academic",
        collision_policy="exclusive",
        fallback_template="composition.standard_analysis",
        priority=30,
        component_slots=[
            ComponentSlot(id="title", category="annotation.title", cardinality="required", required=True, allowed_component_types=["title"], position_zone="top-center", preferred_templates=["title/academic"]),
            ComponentSlot(id="subtitle", category="annotation.subtitle", cardinality="optional", allowed_component_types=["subtitle"], position_zone="top-center"),
            ComponentSlot(id="legend", category="legend.graduated", cardinality="conditional", allowed_component_types=["legend", "categorical_legend", "continuous_colorbar"], position_zone="bottom-left", bind_role="primary", preferred_templates=["legend/academic", "colorbar/horizontal"]),
            ComponentSlot(id="north_arrow", category="navigation.north_arrow", cardinality="required", required=True, allowed_component_types=["north_arrow"], position_zone="top-right", preferred_templates=["north-arrow/compass-rose"]),
            ComponentSlot(id="scale_bar", category="navigation.scale_bar", cardinality="required", required=True, allowed_component_types=["scale_bar"], position_zone="bottom-right", preferred_templates=["scale-bar/academic"]),
            ComponentSlot(id="graticule", category="navigation.graticule", cardinality="optional", allowed_component_types=["graticule"], position_zone="none"),
            ComponentSlot(id="attribution", category="annotation.attribution", cardinality="required", required=True, allowed_component_types=["attribution"], position_zone="bottom-left"),
            ComponentSlot(id="map_border", category="frame.map_border", cardinality="required", required=True, allowed_component_types=["map_border"], position_zone="none", preferred_templates=["frame/academic"]),
            ComponentSlot(id="statistics_panel", category="analysis.statistics_panel", cardinality="optional", allowed_component_types=["statistics_panel"], position_zone="top-left"),
            ComponentSlot(id="export_layout", category="export.page_layout", cardinality="required", required=True, allowed_component_types=["export_layout"], position_zone="none", preferred_templates=["export-layout/A4-landscape"]),
        ],
    ),
    MapCompositionTemplate(
        id="composition.report_map",
        name="Report Map",
        description="报告用图：标题必备、副标题推荐、图例/色条按主题、指北针/比例尺/图框/元数据/统计面板。",
        compatible_map_models=[],
        output_targets=["png", "pdf"],
        layout_profile="report",
        collision_policy="exclusive",
        fallback_template="composition.academic_map",
        priority=25,
        component_slots=[
            ComponentSlot(id="title", category="annotation.title", cardinality="required", required=True, allowed_component_types=["title"], position_zone="top-center", preferred_templates=["title/report"]),
            ComponentSlot(id="subtitle", category="annotation.subtitle", cardinality="recommended", allowed_component_types=["subtitle"], position_zone="top-center"),
            ComponentSlot(id="legend", category="legend.graduated", cardinality="conditional", allowed_component_types=["legend", "categorical_legend", "continuous_colorbar"], position_zone="bottom-left", bind_role="primary", preferred_templates=["legend/report"]),
            ComponentSlot(id="north_arrow", category="navigation.north_arrow", cardinality="required", required=True, allowed_component_types=["north_arrow"], position_zone="top-right"),
            ComponentSlot(id="scale_bar", category="navigation.scale_bar", cardinality="required", required=True, allowed_component_types=["scale_bar"], position_zone="bottom-right"),
            ComponentSlot(id="map_border", category="frame.map_border", cardinality="required", required=True, allowed_component_types=["map_border"], position_zone="none", preferred_templates=["frame/report"]),
            ComponentSlot(id="attribution", category="annotation.attribution", cardinality="required", required=True, allowed_component_types=["attribution"], position_zone="bottom-left"),
            ComponentSlot(id="statistics_panel", category="analysis.statistics_panel", cardinality="recommended", allowed_component_types=["statistics_panel"], position_zone="top-left"),
            ComponentSlot(id="export_layout", category="export.page_layout", cardinality="required", required=True, allowed_component_types=["export_layout"], position_zone="none", preferred_templates=["export-layout/A4-portrait"]),
        ],
    ),
    MapCompositionTemplate(
        id="composition.presentation_map",
        name="Presentation Map",
        description="演示用图：大标题、简洁图例、指北针可选、比例尺必备、无图框。",
        compatible_map_models=[],
        output_targets=["interactive", "png"],
        layout_profile="presentation",
        collision_policy="exclusive",
        fallback_template="composition.standard_analysis",
        priority=22,
        component_slots=[
            ComponentSlot(id="title", category="annotation.title", cardinality="required", required=True, allowed_component_types=["title"], position_zone="top-center", preferred_templates=["title/presentation"]),
            ComponentSlot(id="legend", category="legend.graduated", cardinality="conditional", allowed_component_types=["legend", "categorical_legend", "continuous_colorbar"], position_zone="bottom-left", preferred_templates=["legend/compact"]),
            ComponentSlot(id="north_arrow", category="navigation.north_arrow", cardinality="optional", allowed_component_types=["north_arrow"], position_zone="top-right"),
            ComponentSlot(id="scale_bar", category="navigation.scale_bar", cardinality="required", required=True, allowed_component_types=["scale_bar"], position_zone="bottom-right"),
            ComponentSlot(id="attribution", category="annotation.attribution", cardinality="required", required=True, allowed_component_types=["attribution"], position_zone="bottom-left"),
        ],
    ),
    MapCompositionTemplate(
        id="composition.statistical_map",
        name="Statistical Map",
        description="统计专题图：分级图例必备、统计面板推荐、学术风格。",
        compatible_map_models=["administrative_choropleth", "aggregate_grid"],
        output_targets=["interactive", "png", "pdf"],
        layout_profile="standard",
        collision_policy="exclusive",
        fallback_template="composition.standard_analysis",
        priority=28,
        component_slots=[
            ComponentSlot(id="title", category="annotation.title", cardinality="required", required=True, allowed_component_types=["title"], position_zone="top-center"),
            ComponentSlot(id="legend", category="legend.graduated", cardinality="required", required=True, allowed_component_types=["legend"], position_zone="bottom-left", bind_role="primary"),
            ComponentSlot(id="north_arrow", category="navigation.north_arrow", cardinality="required", required=True, allowed_component_types=["north_arrow"], position_zone="top-right"),
            ComponentSlot(id="scale_bar", category="navigation.scale_bar", cardinality="required", required=True, allowed_component_types=["scale_bar"], position_zone="bottom-right"),
            ComponentSlot(id="attribution", category="annotation.attribution", cardinality="required", required=True, allowed_component_types=["attribution"], position_zone="bottom-left"),
            ComponentSlot(id="statistics_panel", category="analysis.statistics_panel", cardinality="recommended", allowed_component_types=["statistics_panel"], position_zone="top-left"),
            ComponentSlot(id="chart_panel", category="analysis.chart_panel", cardinality="optional", allowed_component_types=["chart_panel"], position_zone="top-left", fallback_zones=["top-right"]),
            ComponentSlot(id="map_border", category="frame.map_border", cardinality="optional", allowed_component_types=["map_border"], position_zone="none"),
        ],
    ),
    MapCompositionTemplate(
        id="composition.density_map",
        name="Density Map",
        description="密度图：连续色条必备、指北针/比例尺必备。",
        compatible_map_models=["visual_heatmap", "raster_surface"],
        output_targets=["interactive", "png", "pdf"],
        layout_profile="standard",
        collision_policy="exclusive",
        fallback_template="composition.standard_analysis",
        priority=27,
        component_slots=[
            ComponentSlot(id="title", category="annotation.title", cardinality="required", required=True, allowed_component_types=["title"], position_zone="top-center"),
            ComponentSlot(id="colorbar", category="legend.continuous_colorbar", cardinality="required", required=True, allowed_component_types=["continuous_colorbar"], position_zone="bottom-right", bind_role="primary", preferred_templates=["colorbar/horizontal"]),
            ComponentSlot(id="north_arrow", category="navigation.north_arrow", cardinality="required", required=True, allowed_component_types=["north_arrow"], position_zone="top-right"),
            ComponentSlot(id="scale_bar", category="navigation.scale_bar", cardinality="required", required=True, allowed_component_types=["scale_bar"], position_zone="bottom-right", fallback_zones=["bottom-center"]),
            ComponentSlot(id="attribution", category="annotation.attribution", cardinality="required", required=True, allowed_component_types=["attribution"], position_zone="bottom-left"),
            ComponentSlot(id="statistics_panel", category="analysis.statistics_panel", cardinality="optional", allowed_component_types=["statistics_panel"], position_zone="top-left"),
        ],
    ),
    MapCompositionTemplate(
        id="composition.remote_sensing_map",
        name="Remote Sensing Map",
        description="遥感栅格图：连续色条、标题、指北针、比例尺、归属、图框。",
        compatible_map_models=["raster_surface"],
        output_targets=["png", "pdf", "svg"],
        layout_profile="academic",
        collision_policy="exclusive",
        fallback_template="composition.density_map",
        priority=35,
        component_slots=[
            ComponentSlot(id="title", category="annotation.title", cardinality="required", required=True, allowed_component_types=["title"], position_zone="top-center"),
            ComponentSlot(id="colorbar", category="legend.continuous_colorbar", cardinality="required", required=True, allowed_component_types=["continuous_colorbar"], position_zone="bottom-right", bind_role="primary"),
            ComponentSlot(id="north_arrow", category="navigation.north_arrow", cardinality="required", required=True, allowed_component_types=["north_arrow"], position_zone="top-right"),
            ComponentSlot(id="scale_bar", category="navigation.scale_bar", cardinality="required", required=True, allowed_component_types=["scale_bar"], position_zone="bottom-right"),
            ComponentSlot(id="attribution", category="annotation.attribution", cardinality="required", required=True, allowed_component_types=["attribution"], position_zone="bottom-left"),
            ComponentSlot(id="map_border", category="frame.map_border", cardinality="required", required=True, allowed_component_types=["map_border"], position_zone="none"),
            ComponentSlot(id="export_layout", category="export.page_layout", cardinality="optional", allowed_component_types=["export_layout"], position_zone="none"),
        ],
    ),
]


class CompositionTemplateRegistry:
    def __init__(self) -> None:
        self._by_id: Dict[str, MapCompositionTemplate] = {}

    def load_builtins(self) -> None:
        self._by_id.clear()
        for tpl in SEED_COMPOSITION_TEMPLATES:
            self._by_id[tpl.id] = tpl

    def register(self, tpl: MapCompositionTemplate) -> None:
        if tpl.id in self._by_id:
            raise ValueError(f"duplicate composition template id: {tpl.id}")
        self._by_id[tpl.id] = tpl

    def get(self, template_id: str) -> Optional[MapCompositionTemplate]:
        return self._by_id.get(template_id)

    def has(self, template_id: str) -> bool:
        return template_id in self._by_id

    def all_templates(self) -> List[MapCompositionTemplate]:
        return sorted(self._by_id.values(), key=lambda t: (t.priority, t.id))

    def find_for_map_model(self, map_model_id: str, output_target: str = "") -> List[MapCompositionTemplate]:
        candidates = []
        for tpl in self._by_id.values():
            if tpl.compatible_map_models and map_model_id not in tpl.compatible_map_models:
                # empty means generic — match all
                continue
            if output_target and output_target not in tpl.output_targets:
                continue
            candidates.append(tpl)
        return sorted(candidates, key=lambda t: (t.priority, t.id))

    def find_for_product(self, product_id: str) -> List[MapCompositionTemplate]:
        candidates = []
        for tpl in self._by_id.values():
            if tpl.compatible_product_types and product_id not in tpl.compatible_product_types:
                continue
            candidates.append(tpl)
        return sorted(candidates, key=lambda t: (t.priority, t.id))

    def validate(self) -> List[str]:
        issues: List[str] = []
        try:
            from app.lib.cartography.component_registry import get_component_registry
            from app.lib.cartography.component_templates import get_component_template_registry
            comp_reg = get_component_registry()
            tmpl_reg = get_component_template_registry()
            cat_exists = comp_reg.all_ids  # check slot allowed types
            for tpl in self._by_id.values():
                if tpl.fallback_template and tpl.fallback_template not in self._by_id:
                    issues.append(f"composition {tpl.id}: fallback {tpl.fallback_template} not found")
                for slot in tpl.component_slots:
                    for ctype in slot.allowed_component_types:
                        if ctype not in comp_reg.all_ids:
                            issues.append(f"composition {tpl.id} slot {slot.id}: unknown component type {ctype}")
                    for pt in slot.preferred_templates:
                        if not tmpl_reg.has(pt):
                            issues.append(f"composition {tpl.id} slot {slot.id}: preferred template {pt} not found")
        except Exception as e:
            issues.append(f"composition validation error: {e}")
        return issues

    @property
    def all_ids(self) -> List[str]:
        return sorted(self._by_id.keys())

    @property
    def count(self) -> int:
        return len(self._by_id)


_registry: Optional[CompositionTemplateRegistry] = None


def get_composition_template_registry() -> CompositionTemplateRegistry:
    global _registry
    if _registry is None:
        _registry = CompositionTemplateRegistry()
        _registry.load_builtins()
    return _registry


def reset_composition_template_registry() -> None:
    global _registry
    _registry = None


__all__ = [
    "ComponentSlot",
    "MapCompositionTemplate",
    "CompositionTemplateRegistry",
    "get_composition_template_registry",
    "reset_composition_template_registry",
    "SEED_COMPOSITION_TEMPLATES",
]
