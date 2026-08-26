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


class ComponentPlacement(BaseModel):
    """自由布局放置（可选增强，向后兼容）。

    旧组件只有 ``position`` 六槽锚点；``placement`` 引入显式 typed 布局：
    - anchor 模式：等价旧语义（anchor = 七槽字面量），与 ``position`` 双写
      保持一致，不新增第二种真相；
    - floating 模式：x/y 像素自由定位 + 可选 width/height/zIndex/collapsed，
      服务拖拽/缩放后的持久化。缺省字段由渲染端兜底。
    """
    mode: Literal["anchor", "floating"] = "anchor"
    anchor: Optional[Position] = None
    x: Optional[int] = Field(None, ge=-4096, le=8192)
    y: Optional[int] = Field(None, ge=-4096, le=8192)
    width: Optional[int] = Field(None, ge=120, le=960)
    height: Optional[int] = Field(None, ge=100, le=720)
    zIndex: Optional[int] = Field(None, ge=0, le=200)
    collapsed: bool = False

    def model_post_init(self, __context: Any) -> None:
        if self.mode == "anchor" and self.anchor is None:
            raise ValueError("placement.mode=anchor 需要 anchor 槽位")
        if self.mode == "floating" and (self.x is None or self.y is None):
            raise ValueError("placement.mode=floating 需要 x/y 像素坐标")

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"mode": self.mode}
        if self.mode == "anchor":
            out["anchor"] = self.anchor
        else:
            out["x"] = self.x
            out["y"] = self.y
            if self.width is not None:
                out["width"] = self.width
            if self.height is not None:
                out["height"] = self.height
        if self.zIndex is not None:
            out["zIndex"] = self.zIndex
        if self.collapsed:
            out["collapsed"] = True
        return out


def normalize_placement(
    position: Position, placement: Optional["ComponentPlacement"],
) -> tuple:
    """placement ↔ position 一致化：anchor 模式双写，floating 保留 position 不变。

    返回 (position, placement)。anchor placement 的 anchor 同步进 position，
    保证只读 position 的旧消费者（export/前端兜底）永远看到一致状态。
    """
    if placement is None:
        return position, None
    if placement.mode == "anchor":
        return placement.anchor, placement  # type: ignore[return-value]
    return position, placement


class CartographyComponent(BaseModel):
    """统一组件 schema。各类型通过 ``options`` 扩展各自 payload。

    新增的 ``category`` / ``variant`` / ``templateId`` / ``placement`` 为
    componentized 模板库的可选增强字段；旧 MapSpec（无这些字段）仍可通过
    model_validate 正常读取（默认值兜底）。
    """
    id: str
    type: ComponentType
    enabled: bool = True
    position: Position = "none"
    placement: Optional[ComponentPlacement] = None
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
        if self.placement is not None:
            out["placement"] = self.placement.to_dict()
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
        """旧 MapSpec 条目兼容构造（无 category/variant/placement 亦可）。"""
        return cls.model_validate(data)


# legend（离散）与 colorbar（连续）是两种不同的专题表达配套 —— 由
# Recipe/表现类型决定，不混用：
#   choropleth/graduated → legend
#   heatmap/连续栅格     → continuous_colorbar
#   分类 match 专题       → categorical_legend
#
# variant 的单一权威是组件描述符目录（component_registry.py descriptors
# .variants）；组件工厂/突变校验一律经 valid_variants_for_type() 查目录，
# 不再各自维护字符串元组（消灭 components.py 私有 _NORTH_ARROW_VARIANTS
# 这类第二事实源）。


def valid_variants_for_type(component_type: str) -> tuple:
    """从 descriptor registry 读取类型合法 variant 集（目录缺失 → 空集=不限）。"""
    try:
        from app.lib.cartography.component_registry import get_component_registry
        desc = get_component_registry().get_by_type(component_type)
        if desc is not None and desc.variants:
            return tuple(desc.variants)
    except Exception:  # noqa: BLE001 - 目录不可用不阻塞组件构造（宽松回退）
        pass
    return ()


def coerce_variant(component_type: str, variant: str) -> str:
    """非法 variant 确定性回退到类型默认 variant（不抛错——组件突变不因
    variant 拼写失败而整单失败）。"""
    candidates = valid_variants_for_type(component_type)
    if not candidates or variant in candidates:
        return variant
    try:
        from app.lib.cartography.component_registry import get_component_registry
        desc = get_component_registry().get_by_type(component_type)
        if desc is not None and desc.default_variant:
            return desc.default_variant
    except Exception:  # noqa: BLE001
        pass
    return candidates[0]


def north_arrow_component(
    variant: str = "compass_minimal_black",
    position: str = "top-right",
    component_id: str = "north-arrow",
) -> CartographyComponent:
    variant = coerce_variant("north_arrow", variant)
    return CartographyComponent(
        id=component_id, type="north_arrow", position=position, priority=30,
        variant=variant,
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


# ── 统计/图表 payload 校验（与 chat 侧 generate_chart 同一契约）───────────
# chart_panel 的数据协议就是 chat ChartData（bar/line/pie/scatter 单序列），
# 校验复用 app/tools/chart.py 的公共入口 —— 不允许出现第二套图表 schema。
# 上限沿用 generate_chart 的 DoS 防线：100KB / 500 点 / 200 字符。

MAX_CHART_DATA_POINTS = 500
MAX_STAT_ITEMS = 24


def validate_chart_payload(chart: Any) -> "str | None":
    """校验 inline ChartData。返回 None=合法，否则错误信息。"""
    from app.tools.chart import validate_chart_payload as _validate
    return _validate(chart)


def validate_stats_payload(stats: Any) -> "str | None":
    """校验 statistics_panel options.stats：{title?, items:[{label,value,unit?}]}。"""
    if not isinstance(stats, dict):
        return "stats 必须是对象 {title?, items:[...]}"
    items = stats.get("items")
    if not isinstance(items, list) or not items:
        return "stats.items 必须是非空数组 [{label, value, unit?}]"
    if len(items) > MAX_STAT_ITEMS:
        return f"stats.items 超过上限 {MAX_STAT_ITEMS} 条（聚合后再展示）"
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            return f"stats.items[{i}] 必须是对象"
        label, value = item.get("label"), item.get("value")
        if not isinstance(label, str) or not label.strip():
            return f"stats.items[{i}].label 必须是非空字符串"
        if not isinstance(value, (int, float, str)) or isinstance(value, bool):
            return f"stats.items[{i}].value 必须是数值或字符串"
        if isinstance(label, str) and len(label) > 200:
            return f"stats.items[{i}].label 超过 200 字符"
        unit = item.get("unit")
        if unit is not None and (not isinstance(unit, str) or len(unit) > 32):
            return f"stats.items[{i}].unit 必须是短字符串"
    title = stats.get("title")
    if title is not None and (not isinstance(title, str) or len(title) > 200):
        return "stats.title 必须是 ≤200 字符的字符串"
    return None


def statistics_panel_component(
    position: str = "top-left",
    component_id: str = "statistics",
    stats: Optional[Dict[str, Any]] = None,
    variant: str = "default",
    placement: Optional[ComponentPlacement] = None,
) -> CartographyComponent:
    """统计摘要面板。stats 契约见 validate_stats_payload（缺省=空面板占位）。"""
    options: Dict[str, Any] = {}
    if stats is not None:
        options["stats"] = stats
    position, placement = normalize_placement(position, placement)  # type: ignore[arg-type]
    return CartographyComponent(
        id=component_id, type="statistics_panel", position=position,
        placement=placement, priority=40,
        variant=coerce_variant("statistics_panel", variant),
        options=options,
    )


def chart_panel_component(
    position: str = "top-left",
    component_id: str = "chart-panel",
    chart: Optional[Dict[str, Any]] = None,
    chart_ref: str = "",
    variant: str = "default",
    placement: Optional[ComponentPlacement] = None,
    title: str = "",
) -> CartographyComponent:
    """图表面板：inline ChartData（options.chart）或 artifact ref（options.chartRef）。

    与 chat 图表共用 ChartData 协议（validate_chart_payload）；ref 路径用于
    大数据/派生数据 —— ref 本体存 session_data_manager，MapSpec 只持引用。
    """
    options: Dict[str, Any] = {}
    if chart is not None:
        options["chart"] = chart
    if chart_ref:
        options["chartRef"] = chart_ref
    if title:
        options["title"] = title
    position, placement = normalize_placement(position, placement)  # type: ignore[arg-type]
    return CartographyComponent(
        id=component_id, type="chart_panel", position=position,
        placement=placement, priority=41,
        variant=coerce_variant("chart_panel", variant),
        options=options,
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
    placement: Optional[Dict[str, Any]] = None,
    variant: Optional[str] = None,
    style: Optional[Dict[str, Any]] = None,
    options: Optional[Dict[str, Any]] = None,
    upsert: bool = False,
) -> tuple:
    """局部组件突变。只改命中的单个组件，其余不动。

    ``upsert=True`` 且按 id/type 未命中时创建新组件（需要 component_type；
    工厂默认值起步，再应用给定突变字段）——Agent 加 chart_panel 等新面板
    用同一入口，不开 per-type 工具。

    Returns (mutated_list, change_record)。change_record 记录 from→to，
    供 Harness evidence（ComponentMutation 只动组件、不动数据层）；
    新建组件 change_record 带 ``created: True``。
    """
    target_idx = -1
    for idx, comp in enumerate(components):
        if component_id and comp.id == component_id:
            target_idx = idx
            break
        if component_id is None and component_type and comp.type == component_type:
            target_idx = idx
            break

    created = False
    if target_idx < 0:
        if not upsert or not component_type:
            return list(components), None
        factory = _FACTORY_BY_TYPE.get(component_type)
        if factory is None:
            return list(components), None
        new_id = component_id or f"{component_type.replace('_', '-')}"
        base = factory(component_id=new_id)
        target_idx = len(components)
        components = list(components) + [base]
        created = True

    original = components[target_idx]
    mutated = original.model_copy(deep=True)
    changes: Dict[str, Any] = {"id": mutated.id, "type": mutated.type}
    if created:
        changes["created"] = True
        changes["id"] = mutated.id

    if enabled is not None:
        changes["enabled"] = {"from": mutated.enabled, "to": enabled}
        mutated.enabled = enabled
    if position is not None:
        changes["position"] = {"from": mutated.position, "to": position}
        mutated.position = position  # type: ignore[assignment]
        # position 槽位突变 → anchor placement 双写一致化（单一真相）。对已
        # floating 的面板是重新锚定（否则 position 突变是静默 no-op——change
        # 记录说改了而地图不动）。
        mutated.placement = ComponentPlacement(mode="anchor", anchor=position)  # type: ignore[arg-type]
    if placement is not None:
        parsed = ComponentPlacement.model_validate(placement)
        # anchor 模式同步 position；floating 模式 position 保留原值（旧消费
        # 者兜底显示不漂移）
        if parsed.mode == "anchor":
            mutated.position = parsed.anchor  # type: ignore[assignment]
        changes["placement"] = {"from": original.placement, "to": parsed}
        mutated.placement = parsed
    if variant is not None:
        coerced = coerce_variant(mutated.type, variant)
        changes["variant"] = {"from": mutated.variant, "to": coerced}
        mutated.variant = coerced
        # options.variant 是历史载体（north_arrow 等），保持同步
        mutated.options = {**mutated.options, "variant": coerced}
    if style is not None:
        changes["style"] = {"from": mutated.style, "to": style}
        mutated.style = {**mutated.style, **style}
    if options is not None:
        # options 合并语义：嵌套 dict 深合并，标量整体替换。
        # chart ↔ chartRef 互斥绑定：新值携带其一时移除另一个键——深合并会
        # 保留旧 inline chart，导致 renderer（inline 优先）与 catalog 一直
        # 显示旧数据（review P1-3）。
        merged_opts = {**mutated.options}
        if "chart" in options:
            merged_opts.pop("chartRef", None)
        if "chartRef" in options:
            merged_opts.pop("chart", None)
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


# upsert 用的类型默认工厂（新组件起步值；突变字段随后应用）
_FACTORY_BY_TYPE = {
    "chart_panel": lambda component_id: chart_panel_component(component_id=component_id),
    "statistics_panel": lambda component_id: statistics_panel_component(component_id=component_id),
    "north_arrow": lambda component_id: north_arrow_component(component_id=component_id),
    "scale_bar": lambda component_id: scale_bar_component(component_id=component_id),
    "annotation": lambda component_id: CartographyComponent(
        id=component_id, type="annotation", position="top-left", priority=55,
        options={"text": ""},
    ),
}


__all__ = [
    "CartographyComponent",
    "ComponentPlacement",
    "build_default_components",
    "mutate_component",
    "normalize_placement",
    "valid_variants_for_type",
    "coerce_variant",
    "validate_chart_payload",
    "validate_stats_payload",
    "north_arrow_component",
    "scale_bar_component",
    "title_component",
    "subtitle_component",
    "attribution_component",
    "colorbar_component",
    "legend_component",
    "categorical_legend_component",
    "statistics_panel_component",
    "chart_panel_component",
    "export_layout_component",
    "graticule_component",
    "map_border_component",
]
