"""CartographyComponent —— 可替换制图组件契约。

一个地图产品 = 图层 + 组件。组件（标题/指北针/比例尺/图例/色条…）是
独立可寻址、可单独替换的个体：用户说「换一个指南针」「色条竖向」时，
只发生**组件局部突变**（component mutation），绝不触发数据重查/重分析。

组件最终进入 MapSpec ``layout.components``（与 legend/controls 并列的
新分支），live 渲染与 export 共用同一份 —— 消灭两套版面参数。
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

ComponentType = Literal[
    "basemap",
    "legend",                # 离散/分级图例（choropleth 等）
    "continuous_colorbar",   # 连续色条（heatmap / 连续栅格）
    "categorical_legend",    # 分类图例
    "north_arrow",
    "scale_bar",
    "title",
    "subtitle",
    "annotation",
    "graticule",
    "map_border",
    "attribution",
    "statistics_panel",
    "chart_panel",
    "export_layout",
]

Position = Literal[
    "top-left", "top-center", "top-right",
    "bottom-left", "bottom-center", "bottom-right",
    "none",
]


class CartographyComponent(BaseModel):
    """统一组件 schema。各类型通过 ``options`` 扩展各自 payload。

    新增的 ``category`` / ``variant`` / ``templateId`` 为 componentized
    模板库的可选增强字段；旧 MapSpec（无这些字段）仍可通过 model_validate
    正常读取（默认值兜底）。
    """
    id: str
    type: ComponentType
    enabled: bool = True
    position: Position = "none"
    priority: int = Field(0, description="渲染顺序（小者先），稳定排序用")
    style: Dict[str, Any] = Field(default_factory=dict)
    options: Dict[str, Any] = Field(default_factory=dict)
    compatibility: Dict[str, Any] = Field(default_factory=dict)
    category: str = ""
    variant: str = ""
    templateId: str = ""
    schemaVersion: int = 1

    def to_mapspec(self) -> Dict[str, Any]:
        """MapSpec layout.components 条目形态（确定性、可 diff）。"""
        out: Dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "enabled": self.enabled,
            "position": self.position,
            "priority": self.priority,
        }
        if self.style:
            out["style"] = self.style
        if self.options:
            out["options"] = self.options
        if self.compatibility:
            out["compatibility"] = self.compatibility
        if self.category:
            out["category"] = self.category
        if self.variant:
            out["variant"] = self.variant
        if self.templateId:
            out["templateId"] = self.templateId
        if self.schemaVersion != 1:
            out["schemaVersion"] = self.schemaVersion
        return out

    @classmethod
    def from_legacy(cls, data: Dict[str, Any]) -> "CartographyComponent":
        """旧 MapSpec 条目兼容构造（无 category/variant 亦可）。"""
        return cls.model_validate(data)


# legend（离散）与 colorbar（连续）是两种不同的专题表达配套 —— 由
# Recipe/表现类型决定，不混用：
#   choropleth/graduated → legend
#   heatmap/连续栅格     → continuous_colorbar
#   分类 match 专题       → categorical_legend
_NORTH_ARROW_VARIANTS = ("compass_minimal_black", "compass_needle", "compass_rose", "arrow_simple")


def north_arrow_component(
    variant: str = "compass_minimal_black",
    position: str = "top-right",
    component_id: str = "north-arrow",
) -> CartographyComponent:
    if variant not in _NORTH_ARROW_VARIANTS:
        variant = "compass_minimal_black"
    return CartographyComponent(
        id=component_id, type="north_arrow", position=position, priority=30,
        options={"variant": variant},
    )


def scale_bar_component(
    position: str = "bottom-right",
    orientation: str = "horizontal",
    component_id: str = "scale-bar",
) -> CartographyComponent:
    return CartographyComponent(
        id=component_id, type="scale_bar", position=position, priority=20,
        options={"orientation": orientation, "unit": "metric"},
    )


def title_component(text: str, position: str = "top-center", component_id: str = "title") -> CartographyComponent:
    return CartographyComponent(
        id=component_id, type="title", position=position, priority=10,
        options={"text": text},
    )


def subtitle_component(text: str, position: str = "top-center", component_id: str = "subtitle") -> CartographyComponent:
    return CartographyComponent(
        id=component_id, type="subtitle", position=position, priority=11,
        options={"text": text},
    )


def attribution_component(text: str, component_id: str = "attribution") -> CartographyComponent:
    return CartographyComponent(
        id=component_id, type="attribution", position="bottom-left", priority=50,
        options={"text": text},
    )


def colorbar_component(
    orientation: str = "horizontal",
    position: str = "bottom-right",
    layer_id: str = "",
    title: str = "",
    component_id: str = "colorbar-main",
) -> CartographyComponent:
    return CartographyComponent(
        id=component_id, type="continuous_colorbar", position=position, priority=15,
        options={"orientation": orientation, "layerId": layer_id, "title": title},
    )


def legend_component(
    position: str = "bottom-left",
    layer_id: str = "",
    title: str = "",
    component_id: str = "legend-main",
) -> CartographyComponent:
    return CartographyComponent(
        id=component_id, type="legend", position=position, priority=16,
        options={"layerId": layer_id, "title": title},
    )


def categorical_legend_component(
    position: str = "bottom-left",
    layer_id: str = "",
    title: str = "",
    component_id: str = "legend-categorical",
) -> CartographyComponent:
    return CartographyComponent(
        id=component_id, type="categorical_legend", position=position, priority=17,
        options={"layerId": layer_id, "title": title},
    )


def statistics_panel_component(
    position: str = "top-left",
    component_id: str = "statistics",
) -> CartographyComponent:
    return CartographyComponent(
        id=component_id, type="statistics_panel", position=position, priority=40,
        options={},
    )


def export_layout_component(
    paper_size: str = "A4",
    orientation: str = "landscape",
    dpi: int = 300,
    component_id: str = "export-layout",
) -> CartographyComponent:
    return CartographyComponent(
        id=component_id, type="export_layout", position="none", priority=90,
        options={"paperSize": paper_size, "orientation": orientation, "dpi": dpi},
    )


def graticule_component(enabled: bool = False, component_id: str = "graticule") -> CartographyComponent:
    return CartographyComponent(
        id=component_id, type="graticule", position="none", priority=60,
        enabled=enabled,
    )


def map_border_component(component_id: str = "map-border") -> CartographyComponent:
    return CartographyComponent(
        id=component_id, type="map_border", position="none", priority=70,
        options={"style": "neutral"},
    )


def build_default_components(
    *,
    primary_cartography: str,
    title: str = "",
    subtitle: str = "",
    attribution: str = "© OpenStreetMap contributors",
    report_product: bool = False,
    scope_name: str = "",
    subject_category: str = "",
    extra_types: Optional[List[str]] = None,
) -> List[CartographyComponent]:
    """按主专题表达派生默认组件集（确定性）。

    组件规则的首要权威是模型库（MapModel.recommended_components，
    app/lib/cartography/model_library.py）；模型库没有的旧词汇
    （如 "graduated"）走下方兼容分支。规则不散落在 planner 的 if/else。

    - 视觉热力/连续面 → continuous_colorbar；
    - 分级填色（choropleth/graduated/hotspot/proximity 覆盖面）→ legend（离散）；
    - 分类专题 → categorical_legend；
    - 报告成果 → 额外附 title/subtitle/export_layout/map_border；
    - ``extra_types``：recipe 声明的附加组件（如 statistics_panel）按需并入。
    """
    components: List[CartographyComponent] = []

    if not title:
        title = f"{scope_name}{subject_category}分布" if (scope_name or subject_category) else "专题地图"
    components.append(title_component(title))
    if subtitle:
        components.append(subtitle_component(subtitle))

    legend_types: List[str] = []  # 模型库/兼容分支推导出的图例组件
    model = None
    try:
        from app.lib.cartography.model_library import get_map_model_registry
        model = get_map_model_registry().resolve(primary_cartography)
    except Exception:  # noqa: BLE001 - 模型库不可用不阻塞组件推导
        model = None
    if model is not None and model.recommended_components:
        legend_types = [
            t for t in model.recommended_components
            if t in ("continuous_colorbar", "legend", "categorical_legend")
        ]
    else:
        # 兼容分支：模型库未收录的旧词汇（"graduated" 等）
        if primary_cartography in ("visual_heatmap", "density_overview", "raster_surface"):
            legend_types = ["continuous_colorbar"]
        elif primary_cartography in (
            "administrative_choropleth", "graduated", "aggregate_grid",
            "proportional_symbol",
            "hotspot_overlay", "proximity_overlay", "administrative_aggregation",
        ):
            legend_types = ["legend"]
        elif primary_cartography in ("categorical_thematic",):
            legend_types = ["categorical_legend"]

    for t in legend_types:
        if t == "continuous_colorbar":
            components.append(colorbar_component())
        elif t == "categorical_legend":
            components.append(categorical_legend_component())
        elif t == "legend":
            components.append(legend_component())

    components.append(north_arrow_component())
    components.append(scale_bar_component())
    components.append(attribution_component(attribution))

    for extra in extra_types or []:
        if extra == "statistics_panel" and not any(
            c.type == "statistics_panel" for c in components
        ):
            components.append(statistics_panel_component())

    if report_product:
        components.append(map_border_component())
        components.append(export_layout_component())

    # 稳定排序：priority 升序 + id 字典序（确定性 diff）
    components.sort(key=lambda c: (c.priority, c.id))
    return components


def mutate_component(
    components: List[CartographyComponent],
    *,
    component_id: Optional[str] = None,
    component_type: Optional[str] = None,
    enabled: Optional[bool] = None,
    position: Optional[str] = None,
    style: Optional[Dict[str, Any]] = None,
    options: Optional[Dict[str, Any]] = None,
) -> tuple:
    """局部组件突变。只改命中的单个组件，其余不动。

    Returns (mutated_list, change_record)。change_record 记录 from→to，
    供 Harness evidence（ComponentMutation 只动组件、不动数据层）。
    """
    target_idx = -1
    for idx, comp in enumerate(components):
        if component_id and comp.id == component_id:
            target_idx = idx
            break
        if component_id is None and component_type and comp.type == component_type:
            target_idx = idx
            break
    if target_idx < 0:
        return list(components), None

    original = components[target_idx]
    mutated = original.model_copy(deep=True)
    changes: Dict[str, Any] = {"id": mutated.id, "type": mutated.type}

    if enabled is not None:
        changes["enabled"] = {"from": mutated.enabled, "to": enabled}
        mutated.enabled = enabled
    if position is not None:
        changes["position"] = {"from": mutated.position, "to": position}
        mutated.position = position  # type: ignore[assignment]
    if style is not None:
        changes["style"] = {"from": mutated.style, "to": style}
        mutated.style = {**mutated.style, **style}
    if options is not None:
        # options 合并语义：嵌套 dict 深合并，标量整体替换
        merged_opts = {**mutated.options}
        for k, v in options.items():
            if isinstance(v, dict) and isinstance(merged_opts.get(k), dict):
                merged_opts[k] = {**merged_opts[k], **v}
            else:
                merged_opts[k] = v
        changes["options"] = {"from": mutated.options, "to": merged_opts}
        mutated.options = merged_opts

    out = list(components)
    out[target_idx] = mutated
    return out, changes


__all__ = [
    "CartographyComponent",
    "build_default_components",
    "mutate_component",
    "north_arrow_component",
    "scale_bar_component",
    "title_component",
    "subtitle_component",
    "attribution_component",
    "colorbar_component",
    "legend_component",
    "categorical_legend_component",
    "statistics_panel_component",
    "export_layout_component",
    "graticule_component",
    "map_border_component",
]
