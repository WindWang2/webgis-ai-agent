"""CartographyComponent 契约核（自 services/gis_harness/components.py 下沉，ADR-0216）。

一个地图产品 = 图层 + 组件。组件（标题/指北针/比例尺/图例/色条…）是
独立可寻址、可单独替换的个体。本模块只收**契约核**：ComponentType/
Position 词表、ComponentPlacement / CartographyComponent Pydantic 模型、
放置归一化、payload 校验器与体积上界 —— 全部纯代码（math/typing/
pydantic），零 app 内依赖。

组件工厂（*_component）、variant 目录权威（coerce_variant /
valid_variants_for_type，查询 lib 组件目录）、组合/突变编排
（build_default_components / mutate_component 族）留在 services 权威模块
``app/services/gis_harness/components.py``（其 import 本模块并 re-export，
全部既有 import path 兼容；lib/cartography 等跨层消费方直取本模块）。
"""
from __future__ import annotations

import math

from typing import Any, Dict, Literal, Optional

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
    # 区位插图（全国→省→市）：v2 全链路 native —— live SVG 渲染器
    # （inset-map.tsx）与导出 drawChromeInset 同链落地（AC-07 P6 复核：
    # descriptor.runtime_status="native"，本注释此前仍写 planned，已校正）。
    "inset_map",
    # ── VNext §5/§9/§13：披露族组件（方法论诚实的产品面）──────────────
    "methodology_note",         # 方法论披露（警告码 + 文案，随产品渲染）
    "uncertainty_panel",        # 不确定性面板（区间/置信度/样本限制）
    "decision_panel",           # 决策面板（候选排名/权重来源/硬约束否决）
    # ── ac-05（ADR-0154）：标注图层（可寻址的标注绑定/决策面）────────
    # options 持有 label_plan 的字段挑选与策略编排（field/mode/topN/
    # priorityField/zoomBands）；rebind(field) 换字段 = 局部突变。
    "label_layer",
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

MAX_CHART_DATA_POINTS = 500

MAX_STAT_ITEMS = 24

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

MAX_METHODOLOGY_NOTES = 6

MAX_METHODOLOGY_TEXT = 240

MAX_UNCERTAINTY_ITEMS = 8

MAX_DECISION_ROWS = 12

def validate_methodology_payload(options: Any) -> "str | None":
    """methodology_note options.warnings：[{code?, pattern, text}]（有界）。"""
    if not isinstance(options, dict):
        return "options 必须是对象"
    warnings = options.get("warnings")
    if warnings is None:
        return None  # 空占位面板合法（渲染端空态）
    if not isinstance(warnings, list):
        return "warnings 必须是数组 [{code?, pattern?, text}]"
    if len(warnings) > MAX_METHODOLOGY_NOTES:
        return f"warnings 超过上限 {MAX_METHODOLOGY_NOTES} 条"
    for i, w in enumerate(warnings):
        if not isinstance(w, dict):
            return f"warnings[{i}] 必须是对象"
        text = w.get("text")
        if not isinstance(text, str) or not text.strip():
            return f"warnings[{i}].text 必须是非空字符串"
        if len(text) > MAX_METHODOLOGY_TEXT:
            return f"warnings[{i}].text 超过 {MAX_METHODOLOGY_TEXT} 字符"
    return None

def validate_uncertainty_payload(options: Any) -> "str | None":
    """uncertainty_panel options.uncertainty：{items, sampleNote?}。"""
    if not isinstance(options, dict):
        return "options 必须是对象"
    unc = options.get("uncertainty")
    if unc is None:
        return None
    if not isinstance(unc, dict):
        return "uncertainty 必须是对象 {items, sampleNote?}"
    items = unc.get("items")
    if items is not None:
        if not isinstance(items, list) or not items:
            return "uncertainty.items 必须是非空数组 [{label, kind, detail?}]"
        if len(items) > MAX_UNCERTAINTY_ITEMS:
            return f"uncertainty.items 超过上限 {MAX_UNCERTAINTY_ITEMS} 条"
        for i, it in enumerate(items):
            if not isinstance(it, dict) or not isinstance(it.get("label"), str):
                return f"uncertainty.items[{i}] 必须含 label 字符串"
    return None

def validate_decision_payload(options: Any) -> "str | None":
    """decision_panel options.decision：{method?, rows, weightSource?, vetoes?}。"""
    if not isinstance(options, dict):
        return "options 必须是对象"
    dec = options.get("decision")
    if dec is None:
        return None
    if not isinstance(dec, dict):
        return "decision 必须是对象 {method?, rows, weightSource?, vetoes?}"
    rows = dec.get("rows")
    if rows is not None:
        if not isinstance(rows, list) or not rows:
            return "decision.rows 必须是非空数组 [{rank, name, score, basis?}]"
        if len(rows) > MAX_DECISION_ROWS:
            return f"decision.rows 超过上限 {MAX_DECISION_ROWS} 行"
        for i, r in enumerate(rows):
            if not isinstance(r, dict) or not isinstance(r.get("name"), str):
                return f"decision.rows[{i}] 必须含 name 字符串"
            # review M-Adv5：score/rank 必须是有限数值 —— NaN/Inf 会产出
            # 浏览器 JSON.parse 拒绝的非法 JSON（json.dumps 默认放行）。
            score = r.get("score")
            if score is not None:
                if isinstance(score, bool) or not isinstance(score, (int, float)):
                    return f"decision.rows[{i}].score 必须是数值"
                if not math.isfinite(score):
                    return f"decision.rows[{i}].score 必须是有限数值"
            rank = r.get("rank")
            if rank is not None and (
                isinstance(rank, bool) or not isinstance(rank, int)
            ):
                return f"decision.rows[{i}].rank 必须是整数"
    return None

MULTI_INSTANCE_TYPES = frozenset({
    "legend", "categorical_legend", "continuous_colorbar",
    "chart_panel", "table_panel", "annotation", "inset_map",
})

__all__ = [
    "ComponentType",
    "Position",
    "ComponentPlacement",
    "normalize_placement",
    "CartographyComponent",
    "MAX_CHART_DATA_POINTS",
    "MAX_STAT_ITEMS",
    "MAX_ANNOTATION_ITEMS",
    "MAX_ANNOTATION_TEXT",
    "_valid_lnglat",
    "validate_annotation_payload",
    "MAX_INSET_BOUNDARY_POINTS",
    "_valid_bbox4",
    "validate_inset_payload",
    "validate_stats_payload",
    "MAX_TABLE_COLUMNS",
    "MAX_TABLE_COLUMN_NAME",
    "validate_table_binding",
    "MAX_METHODOLOGY_NOTES",
    "MAX_METHODOLOGY_TEXT",
    "MAX_UNCERTAINTY_ITEMS",
    "MAX_DECISION_ROWS",
    "validate_methodology_payload",
    "validate_uncertainty_payload",
    "validate_decision_payload",
    "MULTI_INSTANCE_TYPES",
]
