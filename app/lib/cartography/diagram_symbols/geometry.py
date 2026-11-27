"""图符几何 oracle —— 角度/面积/长度语义的唯一权威。

所有渲染通道（live GL custom layer、前端 canvas 合成、前端/后端 SVG twin、
PDF）必须消费本模块（或其 TS 孪生的 golden-parity 等价输出）的几何，
禁止任何通道自行重算半径/角度/长度。

精度纪律（与 G47 "落盘精度即核验精度" 同源）：全部输出坐标/角度只在
本模块量化一次（GEOMETRY_DECIMALS），消费方与测试按同一精度复核，
不得二次 round。

角度约定（本域冻结）：0° = 12 点钟方向，顺时针为正，单位度，域 [0, 360)。
饼/环形 slice 序列按类别序首尾相接（slice i 的 end = slice i+1 的 start），
全圆闭合。segmented_proportional 的角度槽位 = 类别索引（非方向）。

条形约定：glyph-local 坐标，y 轴向上为正，基线 y=0；正值段向上、负值段
向下；宽度沿 x 轴，glyph 在 x 方向居中。
"""
from __future__ import annotations

import hashlib
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.lib.cartography.diagram_symbols.contracts import (
    DiagramSymbolError,
    canonical_json,
    normalize_size_policy,
)

#: 唯一量化点：坐标/角度/半径统一 3 位小数（≈0.001px / 0.001°）。
GEOMETRY_DECIMALS = 3

_FULL_TURN_DEG = 360.0
_QUARTER = 90.0


def _q(v: float) -> float:
    """量化到 GEOMETRY_DECIMALS（一次性；-0.0 归 0.0 保证 canonical 稳定）。"""
    r = round(float(v), GEOMETRY_DECIMALS)
    return 0.0 if r == 0.0 else r


def _checked_value(value: float, *, category: str, allow_negative: bool) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DiagramSymbolError(
            "FIELD_NOT_NUMERIC",
            f"类别 {category!r} 的值必须是数值，got {type(value).__name__}",
            field=category,
        )
    v = float(value)
    if not math.isfinite(v):
        raise DiagramSymbolError(
            "NON_FINITE_VALUE", f"类别 {category!r} 的值非有限（NaN/Inf）", field=category,
            correction_hint="缺失值应走 missing_policy，而不是以 NaN 参与几何",
        )
    if not allow_negative and v < 0:
        raise DiagramSymbolError(
            "NEGATIVE_VALUE",
            f"类别 {category!r} 的值为负（{v}）：组成比例/分段面积语义不接受负值",
            field=category,
            correction_hint="负值请改用 series_bars（0 基线长度语义）",
        )
    return v


# ── 组成比例（pie / donut）─────────────────────────────────────────────


def build_composition_glyph(
    values: Sequence[Tuple[str, float]],
    scale: Dict[str, float],
    size_policy: Optional[Dict[str, Any]],
    *,
    kind: str = "composition_pie",
) -> Dict[str, Any]:
    """一个要素的组成比例图符几何。

    values：按类别序对齐的 (category, value)（缺失已由资格层处置）。
    角度 = 360°·value/Σ；半径由 scale 决定（total_area_encoding 开时
    radius = r_max·sqrt(total/v_max)，低于 r_min 时 clamp 并披露 clamped）。
    会计：zero_slices = 零值 slice 数（零角度不绘制）；缺失类别的剔除会计
    在资格层（qualification.py），本层只对参算值负责。
    """
    if kind not in ("composition_pie", "composition_donut"):
        raise DiagramSymbolError("UNKNOWN_KIND", f"build_composition_glyph 不接受 kind={kind!r}")
    sp = normalize_size_policy(kind, size_policy)
    checked: List[Tuple[str, float]] = [
        (cat, _checked_value(v, category=cat, allow_negative=False)) for cat, v in values
    ]
    total = math.fsum(v for _, v in checked)
    if total <= 0:
        raise DiagramSymbolError(
            "ZERO_TOTAL",
            f"组成比例分母 Σ={total} 必须 > 0（分母显式 = 参算类别之和）",
            correction_hint="检查是否全为零/缺失被剔除",
            details={"participating_categories": len(checked)},
        )
    radius = sp["r_max_px"]
    clamped = False
    if scale.get("total_area_encoding") and "v_ref" in scale:
        raw = sp["r_max_px"] * math.sqrt(total / scale["v_ref"])
        r_min = scale.get("r_min_px", 0.0)
        if raw < r_min:
            radius = r_min
            clamped = True
        else:
            radius = raw
    slices: List[Dict[str, Any]] = []
    acc = 0.0
    zero_slices = 0
    for cat, v in checked:
        share = v / total
        start = acc * _FULL_TURN_DEG
        acc += share
        end = acc * _FULL_TURN_DEG
        if share == 0.0:
            zero_slices += 1
        slices.append({
            "category": cat,
            "value": _q(v),
            "share": _q(share),
            "start_angle_deg": _q(start),
            "end_angle_deg": _q(end),
        })
    glyph: Dict[str, Any] = {
        "kind": kind,
        "radius_px": _q(radius),
        "total": _q(total),
        "slices": slices,
        "zero_slices": zero_slices,
    }
    if kind == "composition_donut":
        glyph["inner_radius_ratio"] = sp["inner_radius_ratio"]
        glyph["inner_radius_px"] = _q(radius * sp["inner_radius_ratio"])
    if clamped:
        glyph["radius_clamped_to_min"] = True
    return glyph


# ── 同量纲多序列条形（stacked / grouped）───────────────────────────────


def build_series_bars_glyph(
    values: Sequence[Tuple[str, float]],
    scale: Dict[str, float],
    size_policy: Optional[Dict[str, Any]],
    *,
    layout: str = "stacked",
) -> Dict[str, Any]:
    """一个要素的多序列条形图符几何（glyph-local，基线 y=0）。

    stacked：段沿值轴首尾相接，正段向上累加、负段向下累加；
    grouped：段沿类别轴并排（段宽 bar_w，间距 bar_gap），每段从基线起。
    全部长度 = value · px_per_unit（全图唯一线性尺度）。
    """
    if layout not in ("stacked", "grouped"):
        raise DiagramSymbolError(
            "UNKNOWN_LAYOUT", f"未知 series_bars 布局 {layout!r}",
            correction_hint="可选：stacked | grouped",
        )
    sp = normalize_size_policy("series_bars", size_policy)
    checked: List[Tuple[str, float]] = [
        (cat, _checked_value(v, category=cat, allow_negative=True)) for cat, v in values
    ]
    px_per_unit = scale["px_per_unit"]
    bar_w = sp["bar_w_px"]
    segments: List[Dict[str, Any]] = []
    pos_run = 0.0
    neg_run = 0.0
    zero_values = 0
    y_top = 0.0
    y_bottom = 0.0
    n = len(checked)
    if layout == "stacked":
        x0 = _q(-bar_w / 2.0)
        x1 = _q(bar_w / 2.0)
        for cat, v in checked:
            length = v * px_per_unit
            if v >= 0:
                y0, y1 = pos_run, pos_run + length
                pos_run += length
            else:
                y0, y1 = neg_run + length, neg_run
                neg_run += length
            if v == 0:
                zero_values += 1
            y_top = max(y_top, y1)
            y_bottom = min(y_bottom, y0)
            segments.append({
                "category": cat, "value": _q(v),
                "x0": x0, "x1": x1, "y0": _q(y0), "y1": _q(y1),
            })
    else:  # grouped
        gap = sp.get("bar_gap_px", 1.0)
        total_w = n * bar_w + (n - 1) * gap
        left = -total_w / 2.0
        for i, (cat, v) in enumerate(checked):
            gx0 = left + i * (bar_w + gap)
            length = v * px_per_unit
            y0, y1 = (0.0, length) if v >= 0 else (length, 0.0)
            if v == 0:
                zero_values += 1
            y_top = max(y_top, y1)
            y_bottom = min(y_bottom, y0)
            segments.append({
                "category": cat, "value": _q(v),
                "x0": _q(gx0), "x1": _q(gx0 + bar_w), "y0": _q(y0), "y1": _q(y1),
            })
    return {
        "kind": "series_bars",
        "layout": layout,
        "baseline_y": 0.0,
        "extent_px": {"top": _q(y_top), "bottom": _q(y_bottom)},
        "segments": segments,
        "zero_values": zero_values,
    }


# ── 等角分段比例符号（segmented_proportional；夜莺式极区图）────────────


def build_segmented_glyph(
    values: Sequence[Tuple[str, float]],
    scale: Dict[str, float],
    size_policy: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """一个要素的等角分段比例图符几何。

    槽位 i 覆盖 [i·360/n, (i+1)·360/n)（角度=类别序，非方向语义）；
    radius_i = r_max·sqrt(v_i / v_ref)（v_ref 全图 (要素,类别) 最大值）→
    扇区面积 = (slot/360)·π·r² ∝ v_i，跨要素、跨类别可比。
    零值 → 半径 0（不可见，有会计）；负值在 _checked_value 拒绝。
    """
    sp = normalize_size_policy("segmented_proportional", size_policy)
    checked: List[Tuple[str, float]] = [
        (cat, _checked_value(v, category=cat, allow_negative=False)) for cat, v in values
    ]
    n = len(checked)
    slot_deg = _FULL_TURN_DEG / n
    r_max = sp["r_max_px"]
    v_ref = scale["v_ref"]
    segments: List[Dict[str, Any]] = []
    zero_segments = 0
    for i, (cat, v) in enumerate(checked):
        radius = r_max * math.sqrt(v / v_ref)
        if radius == 0.0:
            zero_segments += 1
        start = i * slot_deg
        segments.append({
            "category": cat,
            "value": _q(v),
            "start_angle_deg": _q(start),
            "end_angle_deg": _q(start + slot_deg),
            "radius_px": _q(radius),
            "area_px2": _q((slot_deg / _FULL_TURN_DEG) * math.pi * radius * radius),
        })
    return {
        "kind": "segmented_proportional",
        "slot_deg": _q(slot_deg),
        "radius_px": _q(r_max),
        "segments": segments,
        "zero_segments": zero_segments,
    }


# ── 统一入口 ───────────────────────────────────────────────────────────


def build_glyph_geometry(
    kind: str,
    values: Sequence[Tuple[str, float]],
    scale: Dict[str, float],
    size_policy: Optional[Dict[str, Any]] = None,
    *,
    layout: Optional[str] = None,
) -> Dict[str, Any]:
    """按 kind 分派的唯一几何入口（所有通道都走这里 / TS 孪生）。"""
    if kind in ("composition_pie", "composition_donut"):
        return build_composition_glyph(values, scale, size_policy, kind=kind)
    if kind == "series_bars":
        return build_series_bars_glyph(values, scale, size_policy, layout=layout or "stacked")
    if kind == "segmented_proportional":
        return build_segmented_glyph(values, scale, size_policy)
    raise DiagramSymbolError(
        "UNKNOWN_KIND", f"未知图符 kind={kind!r}",
        correction_hint="可选：composition_pie | composition_donut | series_bars | segmented_proportional",
    )


def glyph_bound_radius(glyph: Dict[str, Any]) -> float:
    """图符包络半径（px @1x）—— 布局/碰撞/密度用（WP2 layout 消费）。

    pie/donut/segmented：外半径；bars：extent box 半对角。
    """
    kind = glyph.get("kind")
    if kind in ("composition_pie", "composition_donut", "segmented_proportional"):
        return float(glyph.get("radius_px", 0.0))
    if kind == "series_bars":
        extent = glyph.get("extent_px", {})
        top = abs(float(extent.get("top", 0.0)))
        bottom = abs(float(extent.get("bottom", 0.0)))
        segs = glyph.get("segments") or []
        half_w = max((max(abs(float(s["x0"])), abs(float(s["x1"]))) for s in segs), default=0.0)
        half_h = max(top, bottom)
        return math.hypot(half_w, half_h)
    raise DiagramSymbolError("UNKNOWN_KIND", f"glyph_bound_radius 不认识 kind={kind!r}")


def geometry_digest(glyphs: Sequence[Dict[str, Any]]) -> str:
    """一批图符几何的确定性摘要（parity 测试与 evidence 共用）。"""
    return hashlib.sha256(
        canonical_json(list(glyphs)).encode("utf-8")
    ).hexdigest()
