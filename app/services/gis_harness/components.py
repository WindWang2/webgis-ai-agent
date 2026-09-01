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
    # Runtime V4（§10）：artifact-backed 交互表格面板（虚拟化 + 选择联动）。
    "table_panel",
    "export_layout",
    # 区位插图（全国→省→市）：schema/registry/composition 已建模，
    # descriptor.runtime_status=planned —— 渲染器未实现前 resolver 不会
    # 选出，不伪装 native（renderer 豁免见 export_component_catalog）。
    "inset_map",
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


# legend（离散）与 colorbar（连续）是两种不同的专题表达配套 —— 由每层
# 图层的 MapModel 决定（v2：图例族 cardinality=multiple，多图层地图各自
# 绑定自己的图例/色条）：
#   choropleth/graduated 层 → legend（binding=该层）
#   heatmap/连续栅格层     → continuous_colorbar（binding=该层）
#   分类 match 专题层      → categorical_legend（binding=该层）
# 类型级互斥已废除：同一 layerId 上图例族互相竞争才是冲突
# （composition_validation.validate_binding_conflicts 执行）。
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

# v2 注记框架预算：group ≤ 12 条、单条文本 ≤ 200 字符、callout anchor 必须是
# 合法经纬度。注记组件是 chrome DTO 的一部分 —— 无预算会被大 payload 撑爆
# MapSpec 与导出画布（组件 DTO bounded 契约）。
MAX_ANNOTATION_ITEMS = 12
MAX_ANNOTATION_TEXT = 200


def _valid_lnglat(raw: Any) -> bool:
    """[lng, lat] 合法性（经度 [-180,180]、纬度 [-90,90]、非 bool 数值）。"""
    if not (isinstance(raw, (list, tuple)) and len(raw) == 2):
        return False
    lng, lat = raw[0], raw[1]
    for v in (lng, lat):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return False
    return -180 <= float(lng) <= 180 and -90 <= float(lat) <= 90


def validate_annotation_payload(options: Any) -> "str | None":
    """校验 annotation options（callout anchor / group items / 文本预算）。

    合法形态：
    - text：options.text 非空字符串；
    - callout：options.text + options.anchor=[lng,lat]；
    - group：options.items=[{text, anchor?}, ...] ≤ 12 条（anchor 条目即组内
      callout，无 anchor 条目为普通注记）。
    """
    if not isinstance(options, dict):
        return "annotation options 必须是对象"
    variant = options.get("variant", "text")
    if variant not in ("text", "callout", "group"):
        return f"annotation.variant 必须是 text/callout/group，收到 {variant!r}"
    anchor = options.get("anchor")
    if anchor is not None and not _valid_lnglat(anchor):
        return "annotation.anchor 必须是 [lng, lat]（经度 ±180、纬度 ±90）"
    items = options.get("items")
    if items is not None:
        if not isinstance(items, list) or not items:
            return "annotation.items 必须是非空数组"
        if len(items) > MAX_ANNOTATION_ITEMS:
            return f"annotation.items 超过上限 {MAX_ANNOTATION_ITEMS} 条"
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                return f"annotation.items[{i}] 必须是对象 {{text, anchor?}}"
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                return f"annotation.items[{i}].text 必须是非空字符串"
            if len(text) > MAX_ANNOTATION_TEXT:
                return f"annotation.items[{i}].text 超过 {MAX_ANNOTATION_TEXT} 字符"
            item_anchor = item.get("anchor")
            if item_anchor is not None and not _valid_lnglat(item_anchor):
                return f"annotation.items[{i}].anchor 必须是 [lng, lat]"
    else:
        text = options.get("text")
        if not isinstance(text, str) or not text.strip():
            return "annotation.text 必须是非空字符串（group 形态用 items）"
        if len(text) > MAX_ANNOTATION_TEXT:
            return f"annotation.text 超过 {MAX_ANNOTATION_TEXT} 字符"
        if variant == "callout" and anchor is None:
            return "annotation.variant=callout 需要 options.anchor=[lng, lat]"
    return None


# v2 插图（inset）预算：边界折线 ≤ 512 点（简化后的概略轮廓）、bbox 有序。
MAX_INSET_BOUNDARY_POINTS = 512


def _valid_bbox4(raw: Any) -> bool:
    if not (isinstance(raw, (list, tuple)) and len(raw) == 4):
        return False
    try:
        w, s, e, n = (float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]))
    except (TypeError, ValueError):
        return False
    return -180 <= w <= 180 and -180 <= e <= 180 and -90 <= s <= 90 and -90 <= n <= 90 and w <= e and s <= n


def validate_inset_payload(options: Any) -> "str | None":
    """校验 inset_map options（bbox / 边界折线 / 指示范围）。"""
    if not isinstance(options, dict):
        return "inset_map options 必须是对象"
    bbox = options.get("bbox")
    if bbox is None:
        return "inset_map 需要 options.bbox=[w, s, e, n]（插图范围）"
    if not _valid_bbox4(bbox):
        return "inset_map.bbox 必须是有序 [w, s, e, n]（经纬度合法范围）"
    main_bbox = options.get("mainBbox")
    if main_bbox is not None and not _valid_bbox4(main_bbox):
        return "inset_map.mainBbox 必须是有序 [w, s, e, n]（主图范围指示）"
    boundary = options.get("boundary")
    if boundary is not None:
        if not isinstance(boundary, list) or len(boundary) < 3:
            return "inset_map.boundary 必须是 ≥3 点的 [[lng, lat], ...] 折线/多边形"
        if len(boundary) > MAX_INSET_BOUNDARY_POINTS:
            return f"inset_map.boundary 超过上限 {MAX_INSET_BOUNDARY_POINTS} 点（简化后传入）"
        for i, pt in enumerate(boundary):
            if not _valid_lnglat(pt):
                return f"inset_map.boundary[{i}] 必须是 [lng, lat]"
    label = options.get("label")
    if label is not None and (not isinstance(label, str) or len(label) > 64):
        return "inset_map.label 必须是 ≤64 字符的字符串"
    return None


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


# Runtime V4（§10）：表格面板绑定契约上限 —— 绑定是引用不是数据。
MAX_TABLE_COLUMNS = 32
MAX_TABLE_COLUMN_NAME = 64


def validate_table_binding(options: Any) -> "str | None":
    """校验 table_panel options 的绑定面（合并后调用；数据本体不入 MapSpec）。

    双通道：tableRef（stats_table/admin_aggregate_table 等 artifact ref）或
    layerId（地图图层属性表）。columns/title 均有界。
    """
    if not isinstance(options, dict):
        return "table options 必须是对象"
    table_ref = options.get("tableRef")
    layer_id = options.get("layerId")
    if table_ref is None and layer_id is None:
        return "table_panel 需要绑定：options.tableRef（表 artifact ref）或 options.layerId（图层 id）"
    if table_ref is not None and (not isinstance(table_ref, str) or not table_ref.strip()):
        return "tableRef 必须是非空字符串（ref: 前缀 artifact 引用）"
    if layer_id is not None and (not isinstance(layer_id, str) or not layer_id.strip()):
        return "layerId 必须是非空字符串"
    if table_ref is not None and layer_id is not None:
        return "tableRef 与 layerId 互斥（一个面板一个数据面）"
    columns = options.get("columns")
    if columns is not None:
        if not isinstance(columns, list) or not columns:
            return "columns 必须是非空数组（缺省由数据推导）"
        if len(columns) > MAX_TABLE_COLUMNS:
            return f"columns 超过上限 {MAX_TABLE_COLUMNS} 列"
        for i, col in enumerate(columns):
            if not isinstance(col, str) or not col.strip() or len(col) > MAX_TABLE_COLUMN_NAME:
                return f"columns[{i}] 必须是 ≤{MAX_TABLE_COLUMN_NAME} 字符的非空列名"
    title = options.get("title")
    if title is not None and (not isinstance(title, str) or len(title) > 200):
        return "title 必须是 ≤200 字符的字符串"
    return None


def table_panel_component(
    position: str = "bottom-right",
    component_id: str = "table-panel",
    table_ref: str = "",
    layer_id: str = "",
    columns: Optional[List[str]] = None,
    title: str = "",
    variant: str = "default",
    placement: Optional[ComponentPlacement] = None,
) -> CartographyComponent:
    """交互表格面板（Runtime V4 §10）：ref/图层绑定，数据本体不入 MapSpec。

    与 chart_panel 同纪律：MapSpec 只持绑定引用（tableRef/layerId），表数据
    由前端按 ref 拉取或从 HUD 图层读取（MVT 层经 attribute-table 水合）。
    """
    options: Dict[str, Any] = {}
    if table_ref:
        options["tableRef"] = table_ref
    if layer_id:
        options["layerId"] = layer_id
    if columns:
        options["columns"] = list(columns)
    if title:
        options["title"] = title
    position, placement = normalize_placement(position, placement)  # type: ignore[arg-type]
    return CartographyComponent(
        id=component_id, type="table_panel", position=position,
        placement=placement, priority=42,
        variant=coerce_variant("table_panel", variant),
        options=options,
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


def annotation_component(
    text: str = "",
    component_id: str = "annotation",
    variant: str = "text",
    anchor: Optional[List[float]] = None,
    items: Optional[List[Dict[str, Any]]] = None,
    position: str = "top-left",
) -> CartographyComponent:
    """注记组件（v2 框架）：text 静态卡 / callout（anchor 坐标 + 引线）/
    group（options.items 多条相关注记）。

    三种形态共享同一组件类型与渲染/导出语义；payload 由
    validate_annotation_payload 把关（有界）。
    """
    options: Dict[str, Any] = {"variant": variant}
    if text:
        options["text"] = text
    if anchor is not None:
        options["anchor"] = list(anchor)
    if items is not None:
        options["items"] = items
    # 空载荷 = 工厂起步形态（upsert 后经突变填充），不做内容校验
    if text or items or anchor is not None:
        err = validate_annotation_payload(options)
        if err:
            raise ValueError(err)
    return CartographyComponent(
        id=component_id, type="annotation", position=position, priority=55,
        variant=coerce_variant("annotation", variant),
        options=options,
    )


def inset_map_component(
    bbox: List[float],
    component_id: str = "inset-map",
    variant: str = "overview",
    main_bbox: Optional[List[float]] = None,
    boundary: Optional[List[List[float]]] = None,
    label: str = "",
    position: str = "top-right",
) -> CartographyComponent:
    """区位插图（v2）：轻量静态小地图（bbox 范围 + 可选边界折线 + 主图范围
    指示框）。不 mount 第二个业务地图 runtime —— live 与 export 共享同一
    纯几何投影语义（前端 geo-anchor 模块）。payload 由
    validate_inset_payload 把关（有界）。"""
    options: Dict[str, Any] = {"bbox": list(bbox)}
    if main_bbox is not None:
        options["mainBbox"] = list(main_bbox)
    if boundary is not None:
        options["boundary"] = [list(pt) for pt in boundary]
    if label:
        options["label"] = label
    err = validate_inset_payload(options)
    if err:
        raise ValueError(err)
    return CartographyComponent(
        id=component_id, type="inset_map", position=position, priority=65,
        variant=coerce_variant("inset_map", variant),
        options=options,
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
        elif extra == "chart_panel" and not any(
            c.type == "chart_panel" for c in components
        ):
            components.append(chart_panel_component())

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
        # #1065: change 记录跨工具结果的 JSON 边界 —— pydantic 对象会在
        # dispatch 的 json.dumps(default=numpy_json_default) 处抛 TypeError，
        # 而此时变更已提交（重试 = 二次应用 + dedup 键滞留）。序列化为 dict。
        changes["placement"] = {
            "from": original.placement.model_dump() if original.placement else None,
            "to": parsed.model_dump(),
        }
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


# ─── Component Lifecycle V3（Runtime V4 §17-19）────────────────────────────
# 多实例组件的真删除 / 复制 / 重绑定 —— 纯函数，事务内由 lifecycle engine
# 调用；与 mutate_component 同入口纪律（user route 与 agent tool 共用）。

# 复制仅对多实例类型开放（单例复制 = 布局冲突源）。
MULTI_INSTANCE_TYPES = frozenset({
    "legend", "categorical_legend", "continuous_colorbar",
    "chart_panel", "table_panel", "annotation", "inset_map",
})

# 重绑定字段白名单（per-type）：键是 options 字段，值是校验语义。
_REBIND_FIELDS = {
    "chart_panel": ("chartRef", "layerId"),
    "table_panel": ("tableRef", "layerId"),
    "legend": ("layerId",),
    "categorical_legend": ("layerId",),
    "continuous_colorbar": ("layerId",),
    "statistics_panel": ("layerId",),
    "inset_map": (),
    "annotation": (),
}


def _find_component(
    components: List[CartographyComponent],
    component_id: str,
) -> int:
    for idx, comp in enumerate(components):
        if comp.id == component_id:
            return idx
    return -1


def remove_component(
    components: List[CartographyComponent],
    *,
    component_id: str,
) -> "tuple[List[CartographyComponent], Dict[str, Any] | None]":
    """真删除：从 layout.components 移除命中组件（enabled=False 是隐藏不是删除）。

    Returns (remaining, change_record)；未命中 → (原列表, None)。
    """
    idx = _find_component(components, component_id)
    if idx < 0:
        return list(components), None
    removed = components[idx]
    out = list(components)
    out.pop(idx)
    return out, {
        "id": removed.id,
        "type": removed.type,
        "removed": True,
        "had_binding": {
            k: removed.options[k]
            for k in ("chartRef", "tableRef", "layerId")
            if k in removed.options
        },
    }


def duplicate_component(
    components: List[CartographyComponent],
    *,
    component_id: str,
    new_id: str = "",
) -> "tuple[List[CartographyComponent], CartographyComponent | None, str | None]":
    """复制多实例组件：新 id + floating 偏移（+16px，避免完全重叠）。

    Returns (list_with_copy, copy, error)。单例类型 / 未命中 / id 冲突 →
    (原列表, None, error)。共享 artifact ref 是合法复制（引用不是所有权）。
    """
    idx = _find_component(components, component_id)
    if idx < 0:
        return list(components), None, f"component {component_id} not found"
    source = components[idx]
    if source.type not in MULTI_INSTANCE_TYPES:
        return (
            list(components),
            None,
            f"组件类型 {source.type} 是单例（cardinality=single），不支持复制",
        )
    base_id = new_id.strip() or f"{source.id}-copy"
    dup_id = base_id
    suffix = 2
    existing = {c.id for c in components}
    while dup_id in existing:
        dup_id = f"{base_id}{suffix}"
        suffix += 1
        if suffix > 99:
            return list(components), None, "无法生成唯一副本 id（上限 99）"
    copy = source.model_copy(deep=True)
    copy.id = dup_id
    # 副本浮动偏移：锚点组件复制后转为 floating（同槽双锚是布局冲突源），
    # 已 floating 的就地偏移。
    if copy.placement and copy.placement.mode == "floating":
        # review M：model_copy(update=…) 不重校验 —— 上界必须显式 clamp
        # （越界条目会让后续所有组件事务的 model_validate 失败）。
        copy.placement = copy.placement.model_copy(update={
            "x": min(8192, max(-4096, (copy.placement.x or 0) + 16)),
            "y": min(8192, max(-4096, (copy.placement.y or 0) + 16)),
        })
    else:
        copy.placement = ComponentPlacement(
            mode="floating", x=32, y=96, width=360, height=280, zIndex=42,
        )
        copy.position = "none"
    out = list(components)
    out.append(copy)
    return out, copy, None


def rebind_component(
    components: List[CartographyComponent],
    *,
    component_id: str,
    bindings: Dict[str, str],
) -> "tuple[List[CartographyComponent], Dict[str, Any] | None, str | None]":
    """重绑定：改写组件 options 中的引用字段（chartRef/tableRef/layerId）。

    绑定字段按类型白名单校验（chart_panel 不接受 tableRef 等）；互斥纪律
    由调用方（工具层）对绑定目标存在性做权威校验 —— 纯函数只管 schema。
    Returns (list, change_record, error)。
    """
    idx = _find_component(components, component_id)
    if idx < 0:
        return list(components), None, f"component {component_id} not found"
    target = components[idx]
    allowed = _REBIND_FIELDS.get(target.type, ())
    if not bindings:
        return list(components), None, "bindings 不能为空"
    unknown = [k for k in bindings if k not in allowed]
    if unknown:
        return (
            list(components),
            None,
            f"组件类型 {target.type} 不接受绑定字段 {unknown}（允许: {list(allowed) or '无'}）",
        )
    ref_keys = {"chartRef", "tableRef"}
    if len(ref_keys & set(bindings)) + ("layerId" in bindings) > 1:
        return (
            list(components),
            None,
            "一次重绑定只能换一个通道（chartRef / tableRef / layerId 互斥）",
        )
    for k, v in bindings.items():
        if not isinstance(v, str) or not v.strip():
            return list(components), None, f"绑定 {k} 必须是非空字符串"
    mutated = target.model_copy(deep=True)
    changes: Dict[str, Any] = {"id": mutated.id, "type": mutated.type, "rebound": {}}
    for k, v in bindings.items():
        changes["rebound"][k] = {"from": mutated.options.get(k), "to": v}
        mutated.options[k] = v
    # 互斥纪律：换绑一个通道时清掉另一通道的残留（chartRef↔layerId /
    # tableRef↔layerId），否则渲染端双通道歧义。
    if "chartRef" in bindings:
        mutated.options.pop("layerId", None)
    elif "tableRef" in bindings:
        mutated.options.pop("layerId", None)
    elif "layerId" in bindings:
        mutated.options.pop("chartRef", None)
        mutated.options.pop("tableRef", None)
    out = list(components)
    out[idx] = mutated
    return out, changes, None


# upsert 用的类型默认工厂（新组件起步值；突变字段随后应用）
_FACTORY_BY_TYPE = {
    "title": lambda component_id: title_component(text="", component_id=component_id),
    "subtitle": lambda component_id: subtitle_component(text="", component_id=component_id),
    "continuous_colorbar": lambda component_id: colorbar_component(component_id=component_id),
    "categorical_legend": lambda component_id: categorical_legend_component(component_id=component_id),
    "legend": lambda component_id: legend_component(component_id=component_id),
    "attribution": lambda component_id: attribution_component(text="© OpenStreetMap contributors", component_id=component_id),
    "chart_panel": lambda component_id: chart_panel_component(component_id=component_id),
    "table_panel": lambda component_id: table_panel_component(component_id=component_id),
    "statistics_panel": lambda component_id: statistics_panel_component(component_id=component_id),
    "north_arrow": lambda component_id: north_arrow_component(component_id=component_id),
    "scale_bar": lambda component_id: scale_bar_component(component_id=component_id),
    "map_border": lambda component_id: map_border_component(component_id=component_id),
    "export_layout": lambda component_id: export_layout_component(component_id=component_id),
    "annotation": lambda component_id: CartographyComponent(
        id=component_id, type="annotation", position="top-left", priority=55,
        variant="text", options={"variant": "text", "text": ""},
    ),
    "inset_map": lambda component_id: CartographyComponent(
        id=component_id, type="inset_map", position="top-right", priority=65,
        variant="overview", options={"variant": "overview", "bbox": []},
    ),
}


__all__ = [
    "CartographyComponent",
    "ComponentPlacement",
    "build_default_components",
    "mutate_component",
    "remove_component",
    "duplicate_component",
    "rebind_component",
    "MULTI_INSTANCE_TYPES",
    "normalize_placement",
    "valid_variants_for_type",
    "coerce_variant",
    "validate_chart_payload",
    "validate_stats_payload",
    "validate_table_binding",
    "validate_annotation_payload",
    "validate_inset_payload",
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
    "table_panel_component",
    "export_layout_component",
    "graticule_component",
    "map_border_component",
    "annotation_component",
    "inset_map_component",
    "MAX_ANNOTATION_ITEMS",
    "MAX_INSET_BOUNDARY_POINTS",
]
