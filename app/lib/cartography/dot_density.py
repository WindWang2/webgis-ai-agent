"""点密度图（dot density）确定性撒点 — Design System V4 原生化.

把面单元统计值按比例表达为内部撒点（QGIS『点密度』/ ArcGIS dot density
的同族表达）。实现要点：

- **确定性**：Halton 低差异序列（基 2/3）在面 bbox 内采样，无随机种子、
  同输入永远同输出 —— 与布局求解器/golden corpus 的确定性要求一致；
- **诚实披露**：撒点位置是示意性重分布而非真实位置，调用方必须在图例
  披露『Random dot within polygon』；本模块在输出 payload 中内建披露字段；
- **资源有界**：总点数硬上限（防超大面单元 × 大值的组合爆炸），触顶时
  按 value 降序截断（大值面优先保点 —— 比例语义从大到小保留）并披露
  ``truncated=true``。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List

# 总点数上限（资源护栏；触顶即截断并披露，不静默缩放语义）
MAX_TOTAL_DOTS = 20000
# 单面最小面积（bbox 面积）——退化面（窄条/零面积）跳过撒点
_MIN_POLYGON_AREA_DEG2 = 1e-12


def _halton(index: int, base: int) -> float:
    """Halton 低差异序列第 index 项（index 从 1 起；确定性、无种子）。"""
    f = 1.0
    r = 0.0
    i = index
    while i > 0:
        f /= base
        r += f * (i % base)
        i //= base
    return r


def dots_for_value(value: float, unit_value: float) -> int:
    """面单元值 → 撒点数（严格四舍五入，<0.5 点如实为 0）。

    经典点密度惯例（Dent/Slocum）：不足半点的面单元不撒点 —— 强制 ≥1
    会给小值面系统性超权，破坏比例语义。
    """
    if unit_value <= 0:
        raise ValueError("unit_value 必须为正（每点代表量）")
    if not math.isfinite(value) or value <= 0:
        return 0
    return int(round(value / unit_value))


def generate_dot_density_features(
    polygons: List[Dict[str, Any]],
    *,
    value_field: str,
    unit_value: float,
    id_field: str = "",
    max_total_dots: int = MAX_TOTAL_DOTS,
) -> Dict[str, Any]:
    """按面单元比例确定性撒点。

    参数
    ----
    polygons: GeoJSON Feature 列表（Polygon/MultiPolygon），携带
        ``value_field`` 数值字段。
    unit_value: 每个点代表的量（图例必须披露）。
    id_field: 面单元 id 字段（透传到输出点属性 ``__poly_id``）。

    返回
    ----
    GeoJSON FeatureCollection（Point）+ 元数据 payload：
    ``unit_value`` / ``total_dots`` / ``truncated`` / 披露文案。
    """
    try:
        from shapely.geometry import shape
        from shapely.prepared import prep
    except Exception as exc:  # pragma: no cover - 环境护栏
        raise RuntimeError("dot density 需要 shapely") from exc

    out_features: List[Dict[str, Any]] = []
    truncated = False
    total = 0
    # 截断语义 = 按值降序保留（大值面优先保点）；tie 用原始索引保持
    # 确定性。保证 docstring/前置契约『按 value 降序截断』为真。
    indexed = sorted(
        ((idx, feat) for idx, feat in enumerate(polygons)),
        key=lambda pair: -_value_of(pair[1], value_field),
    )
    # 全局 Halton 索引跨面连续递增 —— 面间不共享采样前缀（避免同相位对齐）
    halton_index = 1

    # 截断预算按剩余值比例分摊（巨值面不得独吞上限饿死同量级面）
    remaining_value_sum = sum(
        max(0.0, _value_of(f, value_field)) for _, f in indexed
    )
    remaining_budget = max_total_dots

    for _, feat in indexed:
        geom = feat.get("geometry") or {}
        props = feat.get("properties") or {}
        raw_val = props.get(value_field)
        try:
            val = float(raw_val)
        except (TypeError, ValueError):
            continue
        n = dots_for_value(val, unit_value)
        if n <= 0:
            continue
        # 比例截断：该面允许点数 ≤ 剩余预算 ×（自身值 / 剩余值和）
        v = _value_of(feat, value_field)
        if remaining_value_sum > 0 and v > 0:
            allowed = int(math.ceil(remaining_budget * v / remaining_value_sum))
            n = min(n, max(0, allowed))
        if n <= 0:
            continue
        try:
            shp = shape(geom)
        except Exception:
            continue
        if shp.is_empty or not shp.is_valid:
            continue
        minx, miny, maxx, maxy = shp.bounds
        if (maxx - minx) * (maxy - miny) < _MIN_POLYGON_AREA_DEG2:
            continue
        prepared = prep(shp)

        placed = 0
        # 面内 rejection sampling：Halton 候选 + prepared.contains。
        # 尝试上限按目标点数放宽（细长面 bbox 命中率低）；上限耗尽仍不足
        # n 时如实少撒（不虚增），元数据 total_dots 反映真实撒点数。
        max_attempts = max(n * 24, 240)
        attempts = 0
        while placed < n and attempts < max_attempts:
            ux = _halton(halton_index, 2)
            uy = _halton(halton_index, 3)
            halton_index += 1
            attempts += 1
            x = minx + ux * (maxx - minx)
            y = miny + uy * (maxy - miny)
            if prepared.contains(shapely_point(x, y)):
                out_features.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [round(x, 7), round(y, 7)]},
                    "properties": {
                        "__dot_value": unit_value,
                        "__poly_id": props.get(id_field, "") if id_field else "",
                        "__source_value": val,
                    },
                })
                placed += 1
                total += 1
                if total >= max_total_dots:
                    truncated = True
                    break
        # 面级结算：预算/值和同步扣减
        remaining_value_sum = max(0.0, remaining_value_sum - max(0.0, _value_of(feat, value_field)))
        remaining_budget = max(0, max_total_dots - total)
        if truncated:
            break

    return {
        "type": "FeatureCollection",
        "features": out_features,
        "__dot_density_meta": {
            "unit_value": unit_value,
            "value_field": value_field,
            "total_dots": total,
            "truncated": truncated,
            # 诚实披露：撒点是面内示意重分布（QGIS dot density 同语义）
            "disclosure_zh": "撒点为面单元内的示意性重分布（Random dot "
                             "within polygon），不代表真实位置",
        },
    }


def shapely_point(x: float, y: float):
    """模块内快捷（测试也用）。"""
    from shapely.geometry import Point
    return Point(x, y)


def _value_of(feat: Dict[str, Any], value_field: str) -> float:
    """面单元数值（非法/缺失 → -inf，排序时自然沉底）。"""
    try:
        v = float((feat.get("properties") or {}).get(value_field))
    except (TypeError, ValueError):
        return float("-inf")
    return v if math.isfinite(v) else float("-inf")


def suggest_unit_value(values: List[float], target_max_dots: int = 800) -> float:
    """按最大面单元值建议 unit_value（使最大面 ≈ target_max_dots 点）。

    取 1/2/5×10^k 整齐步长（图例可读性优先）。小量级数据（率/占比）时
    步长 <1 同样成立 —— 无脑返回 1.0 会让每个面恰好 1 点、比例信息归零。
    """
    positive = [v for v in values if math.isfinite(v) and v > 0]
    if not positive:
        return 1.0
    vmax = max(positive)
    raw = vmax / target_max_dots
    if raw <= 0:
        return 1.0
    mag = 10 ** math.floor(math.log10(raw))
    for mult in (1.0, 2.0, 5.0, 10.0):
        if raw <= mult * mag:
            return mult * mag
    return 10.0 * mag


__all__ = [
    "MAX_TOTAL_DOTS",
    "generate_dot_density_features",
    "dots_for_value",
    "suggest_unit_value",
    "_halton",
]
