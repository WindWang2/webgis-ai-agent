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


_SEED_DESCRIPTORS: List[MapComponentDescriptor] = [
    MapComponentDescriptor(
        id="north_arrow", category="navigation.north_arrow", type="north_arrow",
        name="North Arrow", name_zh="指北针",
        placement_domain="overlay", supported_outputs=["interactive", "png", "pdf", "svg"],
        renderer_support=["interactive"], exporter_support=["png", "pdf", "svg"],
        default_variant="compass_minimal_black",
        variants=["compass_minimal_black", "compass_needle", "compass_rose", "arrow_simple"],
        default_position="top-right", allowed_positions=["top-right", "top-left", "bottom-right", "bottom-left", "none"],
        cardinality="single", priority=30, tags=["navigation"],
    ),
    MapComponentDescriptor(
        id="scale_bar", category="navigation.scale_bar", type="scale_bar",
        name="Scale Bar", name_zh="比例尺",
        placement_domain="overlay", supported_outputs=["interactive", "png", "pdf", "svg"],
        renderer_support=["interactive"], exporter_support=["png", "pdf", "svg"],
        default_variant="minimal",
        variants=["minimal", "boxed", "academic"],
        default_position="bottom-right", allowed_positions=["bottom-right", "bottom-left", "bottom-center", "none"],
        cardinality="single", priority=20,
    ),
    MapComponentDescriptor(
        id="legend", category="legend.graduated", type="legend",
        name="Graduated Legend", name_zh="分级图例",
        placement_domain="overlay", supported_outputs=["interactive", "png", "pdf", "svg"],
        compatible_map_models=["administrative_choropleth", "graduated", "aggregate_grid", "hotspot_overlay", "proximity_overlay", "administrative_aggregation"],
        compatible_artifact_types=["admin_aggregate_table", "grid_aggregate"],
        renderer_support=["interactive"], exporter_support=["png", "pdf", "svg"],
        default_variant="academic", variants=["academic", "compact", "report"],
        default_position="bottom-left", allowed_positions=["bottom-left", "bottom-right", "top-left", "top-right", "none"],
        cardinality="single", requires_layer_binding=True, priority=16,
    ),
    MapComponentDescriptor(
        id="continuous_colorbar", category="legend.continuous_colorbar", type="continuous_colorbar",
        name="Continuous Colorbar", name_zh="连续色条",
        placement_domain="overlay", supported_outputs=["interactive", "png", "pdf", "svg"],
        compatible_map_models=["visual_heatmap", "raster_surface", "density_overview"],
        compatible_artifact_types=["density_surface", "terrain_surface"],
        renderer_support=["interactive"], exporter_support=["png", "pdf", "svg"],
        default_variant="horizontal", variants=["horizontal", "vertical", "slim"],
        default_position="bottom-right", allowed_positions=["bottom-right", "bottom-left", "bottom-center", "top-right", "none"],
        cardinality="single", requires_layer_binding=True, priority=15,
        conflicts=["legend", "categorical_legend"],
    ),
    MapComponentDescriptor(
        id="categorical_legend", category="legend.categorical", type="categorical_legend",
        name="Categorical Legend", name_zh="分类图例",
        placement_domain="overlay", supported_outputs=["interactive", "png", "pdf", "svg"],
        compatible_map_models=["categorical_thematic", "proportional_symbol"],
        renderer_support=["interactive"], exporter_support=["png", "pdf", "svg"],
        default_variant="academic", variants=["academic", "compact", "report"],
        default_position="bottom-left", allowed_positions=["bottom-left", "bottom-right", "top-left", "none"],
        cardinality="single", requires_layer_binding=True, priority=17,
        conflicts=["continuous_colorbar"],
    ),
    MapComponentDescriptor(
        id="title", category="annotation.title", type="title",
        name="Title", name_zh="标题",
        placement_domain="chrome", supported_outputs=["interactive", "png", "pdf", "svg"],
        renderer_support=["interactive"], exporter_support=["png", "pdf", "svg"],
        default_variant="academic", variants=["academic", "report", "presentation"],
        default_position="top-center", allowed_positions=["top-center", "top-left", "none"],
        cardinality="single", priority=10,
    ),
    MapComponentDescriptor(
        id="subtitle", category="annotation.subtitle", type="subtitle",
        name="Subtitle", name_zh="副标题",
        placement_domain="chrome", supported_outputs=["interactive", "png", "pdf", "svg"],
        renderer_support=["interactive"], exporter_support=["png", "pdf", "svg"],
        default_variant="default", variants=["default", "academic"],
        default_position="top-center", allowed_positions=["top-center", "top-left", "none"],
        cardinality="zero_or_one", priority=11,
    ),
    MapComponentDescriptor(
        id="attribution", category="annotation.attribution", type="attribution",
        name="Attribution", name_zh="版权信息",
        placement_domain="chrome", supported_outputs=["interactive", "png", "pdf", "svg"],
        renderer_support=["interactive"], exporter_support=["png", "pdf", "svg"],
        default_variant="default", variants=["default", "compact"],
        default_position="bottom-left", allowed_positions=["bottom-left", "bottom-right", "none"],
        cardinality="single", priority=50,
    ),
    MapComponentDescriptor(
        id="graticule", category="navigation.graticule", type="graticule",
        name="Graticule", name_zh="经纬网",
        placement_domain="overlay", supported_outputs=["interactive", "png", "pdf", "svg"],
        renderer_support=["interactive"], exporter_support=["png", "pdf"],
        default_variant="light", variants=["light", "geographic"],
        default_position="none", allowed_positions=["none"],
        cardinality="single", priority=60,
    ),
    MapComponentDescriptor(
        id="map_border", category="frame.map_border", type="map_border",
        name="Map Border", name_zh="图框边框",
        placement_domain="chrome", supported_outputs=["png", "pdf", "svg"],
        renderer_support=["interactive"], exporter_support=["png", "pdf", "svg"],
        default_variant="minimal", variants=["minimal", "academic", "report"],
        default_position="none", allowed_positions=["none"],
        cardinality="single", priority=70,
    ),
    MapComponentDescriptor(
        id="statistics_panel", category="analysis.statistics_panel", type="statistics_panel",
        name="Statistics Panel", name_zh="统计面板",
        placement_domain="panel", supported_outputs=["interactive", "png", "pdf"],
        required_context=["statistics"],
        renderer_support=["interactive"], exporter_support=["png", "pdf"],
        default_variant="default", variants=["default", "compact"],
        default_position="top-left", allowed_positions=["top-left", "top-right", "none"],
        cardinality="zero_or_one", priority=40,
    ),
    MapComponentDescriptor(
        id="chart_panel", category="analysis.chart_panel", type="chart_panel",
        name="Chart Panel", name_zh="图表面板",
        placement_domain="panel", supported_outputs=["interactive", "png", "pdf"],
        required_context=["chart"],
        renderer_support=["interactive"], exporter_support=["png", "pdf"],
        default_variant="default", variants=["default", "compact", "transparent", "report"],
        default_position="top-left", allowed_positions=["top-left", "top-right", "bottom-left", "bottom-right", "none"],
        cardinality="zero_or_one", priority=41,
    ),
    MapComponentDescriptor(
        id="export_layout", category="export.page_layout", type="export_layout",
        name="Export Layout", name_zh="输出版式",
        placement_domain="export", supported_outputs=["png", "pdf", "print"],
        renderer_support=[], exporter_support=["png", "pdf"],
        default_variant="A4_landscape", variants=["A4_landscape", "A4_portrait", "A3_landscape", "letter"],
        default_position="none", allowed_positions=["none"],
        cardinality="single", priority=90,
    ),
    MapComponentDescriptor(
        id="annotation", category="annotation.text", type="annotation",
        name="Annotation", name_zh="文本注记",
        placement_domain="chrome", supported_outputs=["interactive", "png", "pdf", "svg"],
        renderer_support=["interactive"], exporter_support=["png", "pdf", "svg"],
        default_variant="default", variants=["default"],
        default_position="top-left", allowed_positions=["top-left", "top-right", "bottom-left", "bottom-right", "none"],
        cardinality="multiple", priority=55,
    ),
    MapComponentDescriptor(
        id="inset_map", category="inset.map", type="inset_map",
        name="Inset Map", name_zh="插图",
        placement_domain="overlay", supported_outputs=["interactive", "png", "pdf"],
        renderer_support=[], exporter_support=[],
        default_variant="overview", variants=["overview", "location"],
        default_position="top-right", allowed_positions=["top-right", "top-left", "bottom-right", "bottom-left"],
        cardinality="zero_or_one", priority=65, runtime_status="planned",
    ),
]


class ComponentRegistry:
    """Indexed component descriptor registry."""

    def __init__(self) -> None:
        self._by_id: Dict[str, MapComponentDescriptor] = {}
        self._by_type: Dict[str, str] = {}
        self._by_category: Dict[str, List[str]] = {}

    def load_builtins(self) -> None:
        self._by_id.clear()
        self._by_type.clear()
        self._by_category.clear()
        for desc in _SEED_DESCRIPTORS:
            self.register(desc)

    def register(self, desc: MapComponentDescriptor) -> None:
        if desc.id in self._by_id:
            raise ValueError(f"duplicate component descriptor id: {desc.id}")
        self._by_id[desc.id] = desc
        self._by_type[desc.type] = desc.id
        self._by_category.setdefault(desc.category, []).append(desc.id)
        # also index by top-level category prefix
        top = desc.category.split(".")[0]
        if top != desc.category:
            self._by_category.setdefault(top, []).append(desc.id)

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
        if desc.compatible_map_models and map_model_id not in desc.compatible_map_models:
            # empty compatible_map_models means universal
            # but descriptors with specific models must match
            # legend/colorbar have restrictions, others are universal-ish
            # Only enforce if descriptor explicitly lists models and current model not in list
            # AND the descriptor is legend-family (requires specific model)
            if desc.category.startswith("legend"):
                return False
        if output_target and output_target not in desc.supported_outputs:
            return False
        return True

    def validate(self) -> List[str]:
        issues: List[str] = []
        try:
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
