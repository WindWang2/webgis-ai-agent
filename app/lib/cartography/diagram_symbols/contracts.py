"""地理锚定统计图符（feature-bound diagram symbols）—— 表达模型合同。

本域 = 以**地理要素为锚**、携带明确统计量语义（角度/面积/长度）的复合统计
图符，渲染在地图坐标上。与既有能力的边界（查重结论，见 planning/DEDUP_MATRIX）：

- ``chart_panel``（chart_kinds.py）是**浮动面板**组件：屏幕空间 chrome，锚在
  anchor 槽/浮窗矩形，永不绑定地理坐标 —— 不是本域；
- ``proportional_symbol``（model_library.py）是**单字段**比例圆（circle-radius
  面积律）—— 单变量半径，无类别组成/多序列语义，是本域的退化特例而非替代；
- G47 Dorling（cartogram_authoring）是**示意坐标**纸面图（无 CRS、非地图图层）
  —— 坐标空间不同。

三种图符族（本文件冻结其科学语义；几何唯一权威 = geometry.py）：

1. ``composition_pie`` / ``composition_donut``（组成比例）
   角度语义：slice 角 = 360° · value/Σvalue；0° = 12 点钟方向、顺时针。
   分母显式 = 参与计数的非缺失类别之和（Σ 必须 > 0）。负值/非有限值 typed 拒绝。
   可选总量面积编码：radius ∝ sqrt(total / max_total)（面积 ∝ 总量）；
   donut = 同 pie + inner_radius_ratio ∈ (0,1)。
2. ``series_bars``（同量纲多序列条形，stacked | grouped）
   长度语义：线性尺度，全图唯一 px/unit；0 基线，正值向上、负值向下。
   各序列必须同量纲（unit 缺失时显式声明 unit=None 并在图例/证据中披露
   "未声明量纲，不可跨序列比较结论"）。
3. ``segmented_proportional``（等角分段比例符号，夜莺式极区图）
   角度槽位 = 类别顺序（360°/n 等分），**与风玫瑰无关**（风玫瑰角度编码
   方向统计，本域角度编码类别索引）。
   面积语义：radius_i = r_max · sqrt(value_i / v_ref)，v_ref = 全图
   (feature, category) 最大值 → 每扇区面积 ∝ value 且跨要素可比（k 全图唯一）。
   负值 typed 拒绝（面积不能为负）。

诚实约束（合同级，调用方不可绕过）：
- 图例与几何**同源**：图例条目必须由同一 scale/geometry 计算产生（legend.py），
  禁止渲染端自行发明半径/长度；
- 缺失/零值不是错误但有会计：每次几何计算输出 omitted/zero 计数（evidence），
  不静默丢类；
- 半径/长度全部为 glyph-local 像素（px @1x），锚定与投影是消费方（WP2 layout /
  WP3 渲染）的职责 —— 本模块不接触经纬度。
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: 图符族词汇（schema ``diagram_symbol.v1`` 的 kind Literal 与此对齐）。
DIAGRAM_SYMBOL_KINDS: Tuple[str, ...] = (
    "composition_pie",
    "composition_donut",
    "series_bars",
    "segmented_proportional",
)

#: series_bars 布局词汇。
SERIES_BAR_LAYOUTS: Tuple[str, ...] = ("stacked", "grouped")

#: 缺失值处置：omit = 该类别本要素不参与（有会计）；block = 整个图符层
#: 构建失败（typed 拒绝，用于分母语义不可妥协的场景）。
MISSING_POLICIES: Tuple[str, ...] = ("omit", "block")

#: 类别数/字段数上限（防无界 spec；与 symbol_asset 绑定 map 的 max_length=64 同量级）。
MAX_CATEGORIES = 64
MIN_CATEGORIES = 2

#: 组成比例类别数下限（1 类的"饼"无组成语义）。
MIN_COMPOSITION_CATEGORIES = 2

#: scale 跨度上限：max/min 有限值比超过该值时声明 EXTREME_VALUE_RANGE
#: （与 G47 同纪律：跨度大到半径比 >100 时最小图符已不可辨，应显式分档
#: 或改表达，而不是渲染 0.02px 的圆假装表达了）。
EXTREME_VALUE_RANGE_RATIO = 10_000.0

#: 半径比上限（sqrt 后仍 >100 倍）。
EXTREME_RADIUS_RATIO = 100.0

#: 图符几何输出尺寸上限（px @1x；防单图符路径爆炸）。
MAX_GLYPH_RADIUS_PX = 120.0
MAX_GLYPH_EXTENT_PX = 240.0


class DiagramSymbolError(Exception):
    """图符域 typed 错误：code + message + 可执行修正建议。

    与 G47 AuthoringError 同构：错误是产品面的一部分，每个 code 对应
    用户可执行的下一步；服务层把它映射到 HTTP/工具回执，不吞不译。
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        field: Optional[str] = None,
        correction_hint: str = "",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field
        self.correction_hint = correction_hint
        self.details = details or {}

    def payload(self) -> Dict[str, Any]:
        """有界、可机读的错误回执（不含内部栈/路径）。"""
        out: Dict[str, Any] = {"success": False, "code": self.code, "message": self.message}
        if self.field:
            out["field"] = self.field
        if self.correction_hint:
            out["correction_hint"] = self.correction_hint
        if self.details:
            out["details"] = self.details
        return out


#: 语义级错误 code（资格/几何合同；服务级 code 在 authoring 服务层追加）。
DIAGRAM_ERROR_CODES: Tuple[str, ...] = (
    "UNKNOWN_KIND",
    "UNKNOWN_LAYOUT",
    "UNKNOWN_MISSING_POLICY",
    "TOO_FEW_CATEGORIES",
    "TOO_MANY_CATEGORIES",
    "FIELD_NOT_FOUND",
    "FIELD_NOT_NUMERIC",
    "NEGATIVE_VALUE",
    "NON_FINITE_VALUE",
    "ZERO_TOTAL",
    "EXTREME_VALUE_RANGE",
    "INVALID_SIZE_POLICY",
    "MIXED_UNITS",
    "MISSING_UNIT_DECLARATION",
    "DUPLICATE_CATEGORY",
    "INNER_RADIUS_OUT_OF_RANGE",
    "INVALID_COORDINATES",
    "MISSING_VALUE_BLOCKED",
    "BUDGET_EXCEEDED",
)


# ── 尺寸政策（size policy）─────────────────────────────────────────────


def normalize_size_policy(
    kind: str,
    size_policy: Optional[Dict[str, Any]],
) -> Dict[str, float]:
    """归一化尺寸政策为带缺省的 typed dict（所有输出为有限正 float）。

    缺省口径：
    - composition/segmented：r_max_px=32（glyph-local 半径上限 @1x）；
      composition_donut inner_radius_ratio=0.55（QGIS/ArcGIS 缺省区间）。
    - series_bars：h_max_px=40（幅度上限），bar_w_px=8（单条宽）。
    total_area_encoding（仅 composition 族）缺省 False —— 半径恒定时组成
    只编码比例不编码总量；开启后 radius ∝ sqrt(total/max_total)。
    """
    sp = dict(size_policy or {})
    normalized: Dict[str, float] = {}
    if kind in ("composition_pie", "composition_donut", "segmented_proportional"):
        r_max = _finite_positive(sp.get("r_max_px", 32.0), "size_policy.r_max_px")
        if r_max > MAX_GLYPH_RADIUS_PX:
            raise DiagramSymbolError(
                "INVALID_SIZE_POLICY",
                f"r_max_px={r_max} 超过图符半径上限 {MAX_GLYPH_RADIUS_PX}px（单图符路径有界）",
                field="size_policy.r_max_px",
                correction_hint=f"取 r_max_px ∈ (0, {MAX_GLYPH_RADIUS_PX}]",
            )
        normalized["r_max_px"] = r_max
        if kind == "composition_donut":
            ratio = sp.get("inner_radius_ratio", 0.55)
            if not isinstance(ratio, (int, float)) or isinstance(ratio, bool):
                raise DiagramSymbolError(
                    "INVALID_SIZE_POLICY",
                    f"inner_radius_ratio 必须是数值，got {type(ratio).__name__}",
                    field="size_policy.inner_radius_ratio",
                )
            ratio_f = float(ratio)
            if not (0.0 < ratio_f < 1.0):
                raise DiagramSymbolError(
                    "INNER_RADIUS_OUT_OF_RANGE",
                    f"inner_radius_ratio={ratio_f} 不在 (0,1)（0=退化成 pie，1=无环身）",
                    field="size_policy.inner_radius_ratio",
                    correction_hint="取 (0,1) 开区间，如 0.55",
                )
            normalized["inner_radius_ratio"] = ratio_f
        if bool(sp.get("total_area_encoding", False)):
            normalized["total_area_encoding"] = 1.0
            r_min = sp.get("r_min_px", max(2.0, r_max * 0.15))
            normalized["r_min_px"] = _finite_positive(r_min, "size_policy.r_min_px")
            if normalized["r_min_px"] > r_max:
                raise DiagramSymbolError(
                    "INVALID_SIZE_POLICY",
                    f"r_min_px={normalized['r_min_px']} > r_max_px={r_max}",
                    field="size_policy.r_min_px",
                )
    elif kind == "series_bars":
        normalized["h_max_px"] = _finite_positive(sp.get("h_max_px", 40.0), "size_policy.h_max_px")
        if normalized["h_max_px"] > MAX_GLYPH_EXTENT_PX / 2:
            raise DiagramSymbolError(
                "INVALID_SIZE_POLICY",
                f"h_max_px={normalized['h_max_px']} 超过图符半幅上限 {MAX_GLYPH_EXTENT_PX / 2}px",
                field="size_policy.h_max_px",
            )
        normalized["bar_w_px"] = _finite_positive(sp.get("bar_w_px", 8.0), "size_policy.bar_w_px")
        normalized["bar_gap_px"] = _finite_non_negative(
            sp.get("bar_gap_px", 1.0), "size_policy.bar_gap_px"
        )
    else:
        raise DiagramSymbolError(
            "UNKNOWN_KIND", f"未知图符 kind={kind!r}", correction_hint=f"可选：{DIAGRAM_SYMBOL_KINDS}"
        )
    return normalized


def _finite_positive(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DiagramSymbolError(
            "INVALID_SIZE_POLICY", f"{field} 必须是数值，got {type(value).__name__}", field=field
        )
    v = float(value)
    if not math.isfinite(v) or v <= 0:
        raise DiagramSymbolError(
            "INVALID_SIZE_POLICY",
            f"{field} 必须是有限正数，got {value!r}",
            field=field,
            correction_hint="0 与 NaN/Inf 均拒绝",
        )
    return v


def _finite_non_negative(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DiagramSymbolError(
            "INVALID_SIZE_POLICY", f"{field} 必须是数值，got {type(value).__name__}", field=field
        )
    v = float(value)
    if not math.isfinite(v) or v < 0:
        raise DiagramSymbolError(
            "INVALID_SIZE_POLICY", f"{field} 必须是有限非负数，got {value!r}", field=field
        )
    return v


# ── 跨要素公共尺度（scale；唯一权威，渲染端禁止自行发明）───────────────


def _row_numbers(row: Sequence[Any]) -> List[float]:
    """行归一：接受裸数值 [v, ...] 或资格层 (category, value) 对 [(c, v), ...]。"""
    out: List[float] = []
    for item in row:
        if isinstance(item, (tuple, list)) and len(item) == 2:
            out.append(float(item[1]))
        else:
            out.append(float(item))
    return out


def compute_glyph_scale(
    kind: str,
    per_feature_values: Sequence[Sequence[Any]],
    size_policy: Dict[str, float],
) -> Dict[str, float]:
    """计算全图唯一公共尺度（cross-feature common scale）。

    per_feature_values：每个要素一行、按类别序对齐的已资格化数值
    （缺失已按 policy 处置，负值/非有限已在此前被拒绝）。

    输出（typed dict，全部有限）：
    - composition（total_area_encoding 开）：v_max_total + r_max/r_min（几何按
      radius = r_max·sqrt(total/v_max_total)，并 clamp 到 [r_min, r_max] 下限披露）
    - composition（关）：r_max 恒定半径（组成只编码比例）
    - series_bars：v_abs_max + px_per_unit = h_max/v_abs_max（线性长度律）
    - segmented：v_ref = 全要素全类别最大值（半径律 sqrt(v/v_ref)·r_max）
    极端跨度：有限值 max/min 比 > EXTREME_VALUE_RANGE_RATIO 时 typed 拒绝
    （min 侧图符半径比 >100，视觉不可辨 —— 应分档或换表达）。
    """
    flat: List[float] = []
    for row in per_feature_values:
        flat.extend(_row_numbers(row))
    if not flat:
        raise DiagramSymbolError(
            "ZERO_TOTAL", "没有任何可用数值（全要素全类别为空）", correction_hint="检查字段选择与缺失策略"
        )
    positives = [v for v in flat if v > 0]
    absolutes = [abs(v) for v in flat]
    v_max_abs = max(absolutes)
    scale: Dict[str, float] = {}
    if kind in ("composition_pie", "composition_donut", "segmented_proportional"):
        if not positives:
            raise DiagramSymbolError(
                "ZERO_TOTAL", "全部数值为零/非正，无面积或角度语义可编码",
                correction_hint="检查数据是否全为零值",
            )
        r_max = size_policy["r_max_px"]
        scale["r_max_px"] = r_max
        if kind in ("composition_pie", "composition_donut") \
                and size_policy.get("total_area_encoding"):
            # 总量面积编码：半径律作用在**要素总量**上 → v_ref = 跨要素总量最大值
            #（单类别最大值会让半径比偏离面积律 √(total/v_max_total)）
            totals = [math.fsum(_row_numbers(row)) for row in per_feature_values]
            totals = [t for t in totals if t > 0]
            if not totals:
                raise DiagramSymbolError(
                    "ZERO_TOTAL", "全部要素总量为零，无总量面积语义可编码",
                    correction_hint="关闭 total_area_encoding（只编码组成）或检查数据",
                )
            v_max_total = max(totals)
            v_min_total = min(t for t in totals if t > 0)
            if v_max_total / v_min_total > EXTREME_VALUE_RANGE_RATIO:
                raise DiagramSymbolError(
                    "EXTREME_VALUE_RANGE",
                    f"总量跨度 max/min={v_max_total / v_min_total:.3g} 超过 "
                    f"{EXTREME_VALUE_RANGE_RATIO:.0f}，最小图符半径比将 >"
                    f"{EXTREME_RADIUS_RATIO:.0f} 倍，视觉不可辨",
                    correction_hint="先分档或关闭总量面积编码",
                    details={"v_max_total": v_max_total, "v_min_total": v_min_total},
                )
            scale["v_ref"] = v_max_total
            scale["total_area_encoding"] = 1.0
            scale["r_min_px"] = size_policy["r_min_px"]
        else:
            # segmented（等角分段）与未开总量编码的组成：v_ref = 全图
            # (要素,类别) 单值最大（分段的半径律作用在单值上）
            v_min_pos = min(positives)
            v_max_pos = max(positives)
            if v_max_pos / v_min_pos > EXTREME_VALUE_RANGE_RATIO:
                raise DiagramSymbolError(
                    "EXTREME_VALUE_RANGE",
                    f"数值跨度 max/min={v_max_pos / v_min_pos:.3g} 超过 {EXTREME_VALUE_RANGE_RATIO:.0f}，"
                    f"最小图符半径比将 >{EXTREME_RADIUS_RATIO:.0f} 倍，视觉不可辨",
                    correction_hint="先分档（graduated）或改用对数语义表达，再考虑图符",
                    details={"v_max": v_max_pos, "v_min": v_min_pos},
                )
            scale["v_ref"] = v_max_pos
            if size_policy.get("total_area_encoding"):
                scale["total_area_encoding"] = 1.0
                scale["r_min_px"] = size_policy["r_min_px"]
    elif kind == "series_bars":
        if v_max_abs <= 0:
            raise DiagramSymbolError(
                "ZERO_TOTAL", "全部数值为零，条形无长度语义", correction_hint="检查数据"
            )
        pos_vals = [v for v in absolutes if v > 0]
        v_min_abs = min(pos_vals)
        if v_max_abs / v_min_abs > EXTREME_VALUE_RANGE_RATIO:
            raise DiagramSymbolError(
                "EXTREME_VALUE_RANGE",
                f"数值跨度 max/min={v_max_abs / v_min_abs:.3g} 超过 {EXTREME_VALUE_RANGE_RATIO:.0f}，"
                "最小条形长度不可辨",
                correction_hint="先分档或拆层表达",
                details={"v_max": v_max_abs, "v_min": v_min_abs},
            )
        scale["v_abs_max"] = v_max_abs
        scale["px_per_unit"] = size_policy["h_max_px"] / v_max_abs
        scale["h_max_px"] = size_policy["h_max_px"]
        scale["bar_w_px"] = size_policy["bar_w_px"]
        scale["bar_gap_px"] = size_policy.get("bar_gap_px", 1.0)
    else:
        raise DiagramSymbolError("UNKNOWN_KIND", f"未知图符 kind={kind!r}")
    return scale


# ── 类别/字段对齐与 fingerprint ────────────────────────────────────────


def validate_category_spec(
    categories: Sequence[str],
    fields: Sequence[str],
    *,
    kind: str,
) -> List[Tuple[str, str]]:
    """校验类别×字段一一对应（wide 形状：fields[i] ↔ categories[i]）。

    组成比例族类别数 ≥ 2（单类无组成语义）；全族 ≤ MAX_CATEGORIES；
    类别名非空且不重复；字段名非空且不重复。
    """
    if len(categories) != len(fields):
        raise DiagramSymbolError(
            "FIELD_NOT_FOUND",
            f"categories({len(categories)}) 与 fields({len(fields)}) 数量不一致（wide 形状要求一一对应）",
            correction_hint="每个类别一个数值字段，两列表等长同序",
            details={"categories": len(categories), "fields": len(fields)},
        )
    min_n = MIN_COMPOSITION_CATEGORIES if kind in ("composition_pie", "composition_donut") else MIN_CATEGORIES
    if len(categories) < min_n:
        raise DiagramSymbolError(
            "TOO_FEW_CATEGORIES",
            f"类别数 {len(categories)} < {min_n}（{'组成比例需要 ≥2 类' if min_n == MIN_COMPOSITION_CATEGORIES else '至少 2 个序列'}）",
            correction_hint="合并次要类或改用单变量表达（proportional_symbol）",
        )
    if len(categories) > MAX_CATEGORIES:
        raise DiagramSymbolError(
            "TOO_MANY_CATEGORIES",
            f"类别数 {len(categories)} > {MAX_CATEGORIES}（图符扇区/条数有界，防路径爆炸）",
            correction_hint="聚合长尾类别（other），见缺失/聚合会计",
        )
    seen_cat: set = set()
    for c in categories:
        if not isinstance(c, str) or not c:
            raise DiagramSymbolError(
                "TOO_FEW_CATEGORIES", f"类别名必须是非空字符串，got {c!r}"
            )
        if c in seen_cat:
            raise DiagramSymbolError(
                "DUPLICATE_CATEGORY", f"类别名重复：{c!r}",
                correction_hint="重复类别先聚合或更名",
            )
        seen_cat.add(c)
    seen_field = set()
    for f in fields:
        if not isinstance(f, str) or not f:
            raise DiagramSymbolError(
                "FIELD_NOT_FOUND", f"字段名必须是非空字符串，got {f!r}"
            )
        if f in seen_field:
            raise DiagramSymbolError(
                "DUPLICATE_CATEGORY", f"字段名重复：{f!r}", correction_hint="一个字段只对应一个类别"
            )
        seen_field.add(f)
    return list(zip(categories, fields))


def canonical_json(payload: Any) -> str:
    """确定性序列化（sort_keys + 紧凑分隔符）—— fingerprint 与 parity 共用。"""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def diagram_block_fingerprint(block: Dict[str, Any]) -> str:
    """diagram_symbol 块的稳定指纹（sha256 of canonical JSON）。

    身份语义与 representation_binding.binding_fingerprint 同构：
    块内容（不含运行期 evidence）决定身份；CAS 与 parity 测试共用。
    """
    digest_input = {"schema_version": block.get("schema_version"), "body": block}
    return hashlib.sha256(canonical_json(digest_input).encode("utf-8")).hexdigest()
