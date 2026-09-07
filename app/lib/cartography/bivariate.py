"""双变量分级（bivariate choropleth）— Design System V4 原生化.

两个字段的联合分级（n×n 色阵，缺省 3×3）表达共现/相关。实现要点：

- 每个轴独立分位（quantiles）或等距（equal_interval）分级，组合成
  ``row * n + col`` 的类别索引；
- 色阵是**独立于单色 ramp 的语义族**（BIVARIATE_MATRICES）：行=变量 B
  分级、列=变量 A 分级，9 色逐格给出 —— 与 ArcGIS/QGIS 双变量配色同构；
- 类别索引写入要素属性 ``__biv_class``，fill paint 用 ``match`` 表达式
  投影（与 categorical_thematic 同机制族）；图例走 ``legend/bivariate``
  组件变体（3×3 色阵 + 双轴标注）。

诚实约束：双变量图读者负荷高 —— 调用方必须确认两变量确有交互语义；
本模块只做分级与色阵投影，不替用户判断语义合理性。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Sequence, Tuple

# 双变量色阵（3×3，行=变量 B 低→高，列=变量 A 低→高；行主序 9 色）。
# 配色结构：行内沿 A 轴亮度递进，列间沿 B 轴色相偏移（两轴各自单调可辨）。
BIVARIATE_MATRICES: Dict[str, List[str]] = {
    # 紫-橙系：A 轴 紫→黄，B 轴 叠加加深（色盲相对友好、印刷可辨）
    "BiPurpleOrange": [
        "#e8e8f0", "#cac2e0", "#ac9ad0",
        "#f0d9c8", "#cfb0a8", "#b08888",
        "#f8c0a0", "#d49a78", "#b07450",
    ],
    # 青-玫红系：A 轴 青→浅黄绿，B 轴 加深玫红（感知均匀近似）
    "BiTealRose": [
        "#e4f0e8", "#a8d8c8", "#6cb8a8",
        "#f0d8d8", "#c09aa8", "#905c78",
        "#f8b8c0", "#d87890", "#b83860",
    ],
    # 注：曾登记的 BiBlueYellow 因最弱相邻对 ΔE00≈7.5 低于本系统可分性
    # warn 阈值（10）而移除 —— 不保留自己都判为低可分性的库存。
}

DEFAULT_BIVARIATE_MATRIX = "BiPurpleOrange"
SUPPORTED_BIVARIATE_N = (2, 3)   # 库存矩阵均为 3×3 —— 未登记 16 色阵前不开放 n=4


def _breaks_quantiles(values: Sequence[float], n: int) -> List[float]:
    """n 分位断点（numpy percentile 语义：(len-1) 比例尺线性插值）。

    返回 n-1 个内部断点；最高断点严格小于最大值（顶类非空）。常数段
    断点去重（返回可能少于 n-1 个 —— 数据本身不支持 n 分级时如实少分级）。
    """
    s = sorted(v for v in values if math.isfinite(v))
    if not s:
        return []
    breaks: List[float] = []
    m = len(s) - 1
    for i in range(1, n):
        pos = m * i / n
        lo = int(math.floor(pos))
        hi = min(lo + 1, m)
        frac = pos - lo
        v = s[lo] * (1 - frac) + s[hi] * frac
        breaks.append(v)
    # 去重相邻相等断点（常数段：同值多的数据）
    deduped: List[float] = []
    for b in breaks:
        if not deduped or b > deduped[-1]:
            deduped.append(b)
    return deduped


def _breaks_equal_interval(values: Sequence[float], n: int) -> List[float]:
    finite = [v for v in values if math.isfinite(v)]
    if not finite:
        return []
    lo, hi = min(finite), max(finite)
    if hi <= lo:
        return []
    step = (hi - lo) / n
    return [lo + step * i for i in range(1, n)]


def _bin_index(value: float, breaks: Sequence[float]) -> int:
    idx = 0
    for b in breaks:
        if value > b:
            idx += 1
        else:
            break
    return idx


def compute_bivariate_classes(
    features: List[Dict[str, Any]],
    *,
    field_a: str,
    field_b: str,
    n: int = 3,
    method: str = "quantiles",
    out_field: str = "__biv_class",
) -> Dict[str, Any]:
    """就地计算每要素双变量类别索引（写 ``out_field`` 属性）。

    返回 payload：断点、矩阵名、n、参与要素数、跳过数（任一字段非数值
    的要素不参与分级也不着色 —— NaN 语义诚实）。
    """
    if n not in SUPPORTED_BIVARIATE_N:
        raise ValueError(f"bivariate n 必须在 {SUPPORTED_BIVARIATE_N}")
    breaks_fn = _breaks_quantiles if method == "quantiles" else (
        _breaks_equal_interval if method == "equal_interval" else None)
    if breaks_fn is None:
        raise ValueError("method 仅支持 quantiles / equal_interval")

    vals_a: List[float] = []
    vals_b: List[float] = []
    for f in features:
        props = f.get("properties") or {}
        try:
            va = float(props.get(field_a))
            vb = float(props.get(field_b))
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(va) and math.isfinite(vb)):
            continue
        vals_a.append(va)
        vals_b.append(vb)

    br_a = breaks_fn(vals_a, n)
    br_b = breaks_fn(vals_b, n)

    matched = 0
    skipped = 0
    for f in features:
        props = f.get("properties")
        if props is None:
            f["properties"] = props = {}
        try:
            va = float(props.get(field_a))
            vb = float(props.get(field_b))
        except (TypeError, ValueError):
            props[out_field] = -1   # 无数据：透明，不伪装低类
            skipped += 1
            continue
        if not (math.isfinite(va) and math.isfinite(vb)):
            props[out_field] = -1
            skipped += 1
            continue
        ia = _bin_index(va, br_a)
        ib = _bin_index(vb, br_b)
        props[out_field] = ib * n + ia
        matched += 1

    return {
        "n": n,
        "method": method,
        "field_a": field_a,
        "field_b": field_b,
        "breaks_a": br_a,
        "breaks_b": br_b,
        "matched": matched,
        "skipped": skipped,
        "class_field": out_field,
    }


def bivariate_class_colors(matrix: str, n: int = 3) -> List[str]:
    """取 n×n 色阵的行主序颜色表。

    库存矩阵按 3×3 行主序存储；n<3 时取**左上 n×n 子阵**（行/列独立
    截取 —— 保持轴语义：前 n 行 × 前 n 列），不是前 n² 个元素的平铺
    截断（那会混入越轴颜色，双变量两轴失义）。n=4 需矩阵本身 16 色。
    """
    colors = BIVARIATE_MATRICES.get(matrix)
    if colors is None:
        raise ValueError(f"未知双变量色阵: {matrix}")
    if n > 3:
        if len(colors) < n * n:
            raise ValueError(f"色阵 {matrix} 不足以支撑 n={n}")
        return list(colors[: n * n])
    sub = []
    for row in range(n):
        for col in range(n):
            sub.append(colors[row * 3 + col])
    return sub


def bivariate_match_expression(
    class_field: str, matrix: str, n: int = 3
) -> Dict[str, Any]:
    """fill paint 的 match 表达式载荷（converter 消费）。

    ``-1``（无数据）映射为透明 —— 『没有数据』不得画成最低类。
    """
    colors = bivariate_class_colors(matrix, n)
    stops: List[Any] = []
    for i, color in enumerate(colors):
        stops.extend([i, color])
    return {
        "property": class_field,
        "stops": stops,
        "no_data": "rgba(0,0,0,0)",
    }


def bivariate_legend_spec(
    matrix: str,
    *,
    label_a: str,
    label_b: str,
    breaks_a: Sequence[float],
    breaks_b: Sequence[float],
    n: int = 3,
) -> Dict[str, Any]:
    """legend_spec 类型 ``bivariate`` 的构建（前端 bivariate 图例变体与
    导出侧 drawChromeBivariateLegend 共同消费）。"""
    return {
        "type": "bivariate",
        "matrix": matrix,
        "colors": bivariate_class_colors(matrix, n),
        "n": n,
        "label_a": label_a,
        "label_b": label_b,
        "breaks_a": list(breaks_a),
        "breaks_b": list(breaks_b),
    }


__all__ = [
    "BIVARIATE_MATRICES",
    "DEFAULT_BIVARIATE_MATRIX",
    "SUPPORTED_BIVARIATE_N",
    "compute_bivariate_classes",
    "bivariate_class_colors",
    "bivariate_match_expression",
    "bivariate_legend_spec",
]
