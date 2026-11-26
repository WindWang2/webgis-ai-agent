"""图符图例 —— 与几何 oracle 同源的图例条目（禁止渲染端发明）。

图例与几何**同一尺度来源**（同一个 scale dict）：
- 面积律参考圆：radius = r_max·sqrt(v/v_max)（与 composition 总量编码
  完全同公式），取 1/4、1/2、全值三档（面积比 1:2:4 → 半径比 1:√2:2）；
- series_bars：线性长度标尺（px_per_unit + 基线说明，正/负分流说明）；
- segmented：参考扇区 = 全值扇形半径 + 等角/面积∝值说明。

颜色槽位只出索引（0..n-1，按类别序）；hex 由消费方经 palettes 权威
（resolve_palette_colors）解析 —— 本模块不复制色值，避免第二事实源。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

from app.lib.cartography.diagram_symbols.contracts import DiagramSymbolError

#: 默认类别色板（Qualitative Set1，9 类上限；超出循环并披露）。
DEFAULT_GLYPH_PALETTE = "Set1"


def build_glyph_legend(
    kind: str,
    categories: Sequence[str],
    scale: Dict[str, float],
    size_policy: Optional[Dict[str, Any]] = None,
    *,
    palette_id: str = DEFAULT_GLYPH_PALETTE,
    unit: Optional[str] = None,
    layout: Optional[str] = None,
) -> Dict[str, Any]:
    """构建图符图例（schema ``diagram_symbol_legend.v1``）。

    条目形状（全部可 JSON 化，渲染端逐字消费）：
    - ``categories``：[{category, color_slot}]（hex 解析在消费方）；
    - ``size_reference``：面积律参考圆 / 线性标尺 / 参考扇区（按 kind）；
    - ``notes_zh``：语义披露（只编码组成 vs 总量+组成、正负基线、
      等角非方向、颜色循环警告）。
    """
    if kind not in ("composition_pie", "composition_donut", "series_bars",
                    "segmented_proportional"):
        raise DiagramSymbolError("UNKNOWN_KIND", f"build_glyph_legend 不接受 kind={kind!r}")
    sp = dict(size_policy or {})
    cats = list(categories)
    notes: List[str] = []
    size_reference: Dict[str, Any] = {}

    if kind in ("composition_pie", "composition_donut"):
        if sp.get("total_area_encoding") and "v_ref" in scale:
            v_ref = float(scale["v_ref"])
            r_max = float(sp.get("r_max_px", scale.get("r_max_px", 32.0)))
            r_min = float(scale.get("r_min_px", 0.0))
            refs = []
            for frac in (0.25, 0.5, 1.0):
                v = v_ref * frac
                r = r_max * math.sqrt(frac)
                clamped = r < r_min
                refs.append({
                    "total": round(v, 3),
                    "radius_px": round(r, 3),
                    "fraction": frac,
                    "clamped_to_min": clamped,
                })
            size_reference = {"mode": "area_law_circles", "v_ref": v_ref, "references": refs}
            notes.append("圆面积 ∝ 总量（半径 ∝ √总量）；小圆触底半径时按最小半径渲染并在此披露")
        else:
            size_reference = {"mode": "constant_radius",
                              "radius_px": float(sp.get("r_max_px", scale.get("r_max_px", 32.0)))}
            notes.append("半径恒定：只编码组成比例，不编码总量")
        notes.append("扇区角度 ∝ 类别占比（12 点钟起顺时针）")
        if kind == "composition_donut":
            notes.append(f"环形内半径比 {float(sp.get('inner_radius_ratio', 0.55))}")
    elif kind == "series_bars":
        size_reference = {
            "mode": "linear_scale",
            "px_per_unit": float(scale["px_per_unit"]),
            "h_max_px": float(scale["h_max_px"]),
            "v_abs_max": float(scale["v_abs_max"]),
            "unit": unit,
            "layout": layout or "stacked",
        }
        notes.append("条长线性 ∝ 数值（全图同一尺度），0 基线：正向上、负向下")
        if unit is None:
            notes.append("未声明量纲：条长可比但不可下跨量纲结论")
        if layout == "stacked":
            notes.append("堆叠序 = 类别序；正负分别沿基线上/下堆叠")
    else:  # segmented_proportional
        v_ref = float(scale["v_ref"])
        r_max = float(sp.get("r_max_px", scale.get("r_max_px", 32.0)))
        size_reference = {
            "mode": "segment_area_law",
            "v_ref": v_ref,
            "r_max_px": r_max,
            "slot_deg": round(360.0 / max(len(cats), 1), 3),
        }
        notes.append("等角分段（角度槽位=类别序，非方向）：扇区面积 ∝ 数值，跨区域可比")
        notes.append("与风玫瑰无关：本图不编码任何方向统计")

    if len(cats) > 9:
        notes.append(f"类别数 {len(cats)} 超出 Qualitative 色板库存（9），颜色循环使用，"
                     "请配合图例标签阅读")
    return {
        "schema": "diagram_symbol_legend.v1",
        "kind": kind,
        "palette_id": palette_id,
        "categories": [{"category": c, "color_slot": i} for i, c in enumerate(cats)],
        "unit": unit,
        "size_reference": size_reference,
        "notes_zh": notes,
    }
