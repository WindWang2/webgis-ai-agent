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
        compatible_map_models=["administrative_choropleth", "aggregate_grid", "hotspot_overlay", "proximity_overlay", "administrative_aggregation", "proportional_symbol"],  # #1075(D-6): 移除悬空 "graduated"（非 id 非别名；真别名 graduated_choropleth/choropleth 均解析到已列出的 administrative_choropleth）
        compatible_artifact_types=["admin_aggregate_table", "grid_aggregate"],
        renderer_support=["interactive"], exporter_support=["png", "pdf", "svg"],
        default_variant="academic", variants=["academic", "compact", "report"],
        default_position="bottom-left", allowed_positions=["bottom-left", "bottom-right", "top-left", "top-right", "none"],
        cardinality="multiple", requires_layer_binding=True, priority=16,
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
        cardinality="multiple", requires_layer_binding=True, priority=15,
    ),
    MapComponentDescriptor(
        id="categorical_legend", category="legend.categorical", type="categorical_legend",
        name="Categorical Legend", name_zh="分类图例",
        placement_domain="overlay", supported_outputs=["interactive", "png", "pdf", "svg"],
        compatible_map_models=["categorical_thematic"],
        renderer_support=["interactive"], exporter_support=["png", "pdf", "svg"],
        default_variant="academic", variants=["academic", "compact", "report"],
        default_position="bottom-left", allowed_positions=["bottom-left", "bottom-right", "top-left", "none"],
        cardinality="multiple", requires_layer_binding=True, priority=17,
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
        # P3：live 渲染器落地（graticule.tsx SVG overlay）—— #1075(D-5) 的
        # "前端无渲染器"现状解除；导出侧 _drawGraticules 与 live 共享
        # graticule-math 间隔/吸附语义（单一矩阵见 component_renderers）。
        placement_domain="overlay", supported_outputs=["interactive", "png", "pdf", "svg"],
        renderer_support=["interactive"], exporter_support=["png", "pdf", "svg"],
        default_variant="light", variants=["light", "geographic"],
        default_position="none", allowed_positions=["none"],
        cardinality="single", priority=60,
    ),
    MapComponentDescriptor(
        id="map_border", category="frame.map_border", type="map_border",
        name="Map Border", name_zh="图框边框",
        # P6：全链路组件（live CSS 框 + 导出 strokeRect；variants 两侧同语义）
        placement_domain="chrome",
        supported_outputs=["interactive", "png", "pdf", "svg"],
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
        renderer_support=["interactive"], exporter_support=["png", "pdf", "svg"],
        default_variant="default", variants=["default", "compact"],
        default_position="top-left", allowed_positions=["top-left", "top-right", "none"],
        cardinality="zero_or_one", priority=40,
    ),
    MapComponentDescriptor(
        id="chart_panel", category="analysis.chart_panel", type="chart_panel",
        name="Chart Panel", name_zh="图表面板",
        placement_domain="panel", supported_outputs=["interactive", "png", "pdf"],
        required_context=["chart"],
        renderer_support=["interactive"], exporter_support=["png", "pdf", "svg"],
        default_variant="default", variants=["default", "compact", "transparent", "report"],
        default_position="top-left", allowed_positions=["top-left", "top-right", "bottom-left", "bottom-right", "none"],
        # v2：多图表产品（各一 chart：各区数量/类别构成/排名）—— 每实例
        # 独立 id/chartRef/placement；上游 artifact 协议复用 ref:chart-*。
        cardinality="multiple", priority=41,
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
        # v2：注记框架 —— text（静态卡）/ callout（anchor 坐标 + 引线）/ 
        # group（一个逻辑组 → 多条相关注记，options.items 有界）。三种形态
        # 共享同一语义模型，live 与 export 同链（exporter.drawChromeAnnotation）。
        default_variant="text", variants=["text", "callout", "group"],
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
