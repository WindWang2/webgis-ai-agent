"""G41 双轴分类与表达投影（WP04 / WP05）。

复用 ``bivariate.py`` 的色阵与分位/等距计算，但把「每轴独立方法 + 手工断点
+ 显式端点闭开规则 + 矩阵转置/反转」做成有版本的作者合同：

* ``upper_inclusive``：恰等断点归**上**类（G33/MapLibre ``step``，新作者缺省）；
* ``legacy_lower_inclusive``：恰等断点归**下**类（旧 ``bivariate._bin_index``）。

旧 bivariate 图从不被本服务改写；两套规则的差异由合同字段显式记录，且
oracle 测试手算验证（不调用生产 assign 生成 expected）。

矩阵行=B、列=A，行主序。``swap_axes`` 同时转置类别索引、色阵意义、联合
计数和图例；``flip_rows`` 同时反转格序、数值端点、paint 类别和箭头。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.lib.cartography.bivariate import (
    BIVARIATE_MATRICES,
    DEFAULT_BIVARIATE_MATRIX,
    bivariate_class_colors,
)
from app.services.bivariate_reliability_authoring.contract import (
    BivariateSpec,
)
from app.services.bivariate_reliability_authoring.errors import AuthoringError
from app.services.bivariate_reliability_authoring.stats import PairedScan

#: 独立命名的派生类字段——绝不写回源、绝不覆盖用户已有的 ``__biv_class``。
PAIR_CLASS_FIELD = "__g41_pair_class"


def quantiles(values: List[float], n: int) -> List[float]:
    """n-1 个内部分位断点（复用 bivariate 的 (len-1) 线性插值语义）。

    常数段相邻相等断点去重 → 可能少于 n-1 个，如实少分级（B05）。
    """
    s = sorted(v for v in values if math.isfinite(v))
    if not s:
        return []
    out: List[float] = []
    m = len(s) - 1
    for i in range(1, n):
        pos = m * i / n
        lo = int(math.floor(pos))
        hi = min(lo + 1, m)
        frac = pos - lo
        out.append(s[lo] * (1 - frac) + s[hi] * frac)
    deduped: List[float] = []
    lo, hi = s[0], s[-1]
    for b in out:
        # 落在值域端点的断点必然制造空类（常数轴会产生 [v,v] 断点）；
        # 如实丢弃而不是保留一个「看起来够数」的等级（B05）。
        if b <= lo or b >= hi:
            continue
        if not deduped or b > deduped[-1]:
            deduped.append(b)
    return deduped


def equal_interval(values: List[float], n: int) -> List[float]:
    finite = [v for v in values if math.isfinite(v)]
    if not finite:
        return []
    lo, hi = min(finite), max(finite)
    if hi <= lo:
        return []
    step = (hi - lo) / n
    return [lo + step * i for i in range(1, n)]


def axis_breaks(
    values: List[float], axis: Any, n: int,
) -> Tuple[List[float], str, List[str]]:
    """计算单轴实际内部断点；返回 (breaks, method_used, disclosures)。"""
    disclosures: List[str] = []
    if axis.method == "manual":
        breaks = list(axis.breaks or [])
        return breaks, "manual", disclosures
    fn = quantiles if axis.method == "quantiles" else equal_interval
    breaks = fn(values, n)
    if len(breaks) < n - 1:
        disclosures.append(
            f"{axis.field}: 请求 {n} 级，实际只有 {len(breaks)+1} 级"
            "（常数段/重复分位断点去重；不伪造足够类数，空格如实保留）")
    return breaks, axis.method, disclosures


def assign_axis(value: float, breaks: List[float], rule: str) -> int:
    """端点闭开合同。``upper_inclusive`` 用 ``>=``，``legacy`` 用严格 ``>``。"""
    idx = 0
    for b in breaks:
        if (value >= b) if rule == "upper_inclusive" else (value > b):
            idx += 1
        else:
            break
    return idx


def axis_intervals(
    breaks: List[float], rule: str, unit: str = "",
) -> List[Dict[str, Any]]:
    """每个类的可解释区间（图例/格子共用同一份，避免文字与 paint 漂移）。"""
    out: List[Dict[str, Any]] = []
    count = len(breaks) + 1
    suffix = f" {unit}" if unit else ""
    for i in range(count):
        lo = breaks[i - 1] if i > 0 else None      # None = -inf
        hi = breaks[i] if i < len(breaks) else None  # None = +inf
        if rule == "upper_inclusive":
            lower_closed, upper_closed = True, False
            left, right = "[", ")"
        else:
            lower_closed, upper_closed = False, True
            left, right = "(", "]"
        lo_txt = "−∞" if lo is None else f"{lo:g}"
        hi_txt = "+∞" if hi is None else f"{hi:g}"
        out.append({
            "index": i,
            "lo": lo, "hi": hi,
            "lower_closed": lower_closed, "upper_closed": upper_closed,
            "label": f"{left}{lo_txt}, {hi_txt}{right}{suffix}",
            "unit": unit or None,
        })
    return out


@dataclass
class BivariateResult:
    """一次冻结代次上的分类 + 矩阵 + 派生类索引。"""

    n: int
    requested_classes: int
    actual_classes_a: int
    actual_classes_b: int
    breaks_a: List[float]
    breaks_b: List[float]
    method_a: str
    method_b: str
    boundary_rule: str
    colors: List[str]                       # 显示序（行主序），与 paint cases 同构
    base_colors: List[str]
    joint: List[List[int]]                  # 显示序 [row][col]
    cells: List[Dict[str, Any]]             # 屏幕序
    class_by_feature: List[Optional[int]]   # 与冻结 features 同长
    intervals_a: List[Dict[str, Any]]
    intervals_b: List[Dict[str, Any]]
    disclosures: List[str] = field(default_factory=list)
    class_field: str = PAIR_CLASS_FIELD

    def cell_table(self) -> Dict[str, Any]:
        return {
            "n": self.n,
            "row_major": True,
            "colors": list(self.colors),
            "joint": [list(r) for r in self.joint],
            "cells": self.cells,
        }


def _base_colors(spec: BivariateSpec) -> List[str]:
    n = spec.n
    if spec.colors is not None:
        return list(spec.colors)
    matrix = spec.matrix_id or DEFAULT_BIVARIATE_MATRIX
    if matrix not in BIVARIATE_MATRICES:
        raise AuthoringError(
            "UNKNOWN_MATRIX",
            f"未知双变量色阵 {matrix!r}；可用：{', '.join(sorted(BIVARIATE_MATRICES))}",
            field="matrix_id")
    try:
        return bivariate_class_colors(matrix, n)
    except ValueError as exc:
        raise AuthoringError(
            "INSUFFICIENT_COLORS", str(exc), field="matrix_id") from exc


def compute_bivariate(scan: PairedScan, spec: BivariateSpec) -> BivariateResult:
    """完整冻结总体上的两轴分级 + 联合计数 + 派生类索引（单遍）。"""
    n = spec.n
    if scan.valid_pairs == 0:
        raise AuthoringError(
            "NO_VALID_PAIRS",
            "冻结源没有两轴同时有效的数值对——无法建立双变量表达",
            status_code=409,
            correction_hint="检查字段类型/缺失；bool、数字字符串、NaN/Inf 均不入统计。")

    br_a, method_a, disc_a = axis_breaks(scan.values_a, spec.axis_a, n)
    br_b, method_b, disc_b = axis_breaks(scan.values_b, spec.axis_b, n)
    disclosures = list(disc_a) + list(disc_b)
    actual_a, actual_b = len(br_a) + 1, len(br_b) + 1
    if actual_a < n or actual_b < n:
        disclosures.append(
            f"requested={n}×{n}，actual={actual_a}×{actual_b}；"
            "超出实际类数的格子为空，不伪造等级、不重排色阵")

    base = _base_colors(spec)
    if len(base) != n * n:
        raise AuthoringError(
            "INSUFFICIENT_COLORS",
            f"色阵只有 {len(base)} 色，无法支撑 {n}×{n}", field="colors")

    colors = [""] * (n * n)
    joint = [[0 for _ in range(n)] for _ in range(n)]
    class_by_feature: List[Optional[int]] = [None] * scan.total_features

    for pos, idx in enumerate(scan.indices):
        ia = assign_axis(scan.values_a[pos], br_a, spec.boundary_rule)
        ib = assign_axis(scan.values_b[pos], br_b, spec.boundary_rule)
        # 显示位置：swap 转置两轴角色。flip_rows 是**纯呈现**方向选择，
        # 不改变类别索引 / paint 语义；它只反转图例格子的屏幕行序与箭头，
        # 避免「主循环取反 + cells 取反」互相抵消导致图面不变却谎报方向。
        r = ia if spec.swap_axes else ib
        c = ib if spec.swap_axes else ia
        class_index = r * n + c
        # 同一变换把色值意义、类别索引、联合计数一起搬过去（B11）。
        colors[class_index] = base[ib * n + ia]
        joint[r][c] += 1
        class_by_feature[idx] = class_index

    # 未命中任何有效对的格子：颜色仍由 base 变换确定，保证色阵完整且与
    # paint cases 同构；count=0 的格子如实标记为空。
    for ib in range(n):
        for ia in range(n):
            r = ia if spec.swap_axes else ib
            c = ib if spec.swap_axes else ia
            k = r * n + c
            if not colors[k]:
                colors[k] = base[ib * n + ia]
    if any(not c for c in colors):
        raise AuthoringError(
            "COLOR_PROJECTION_INCOMPLETE",
            "色阵投影未覆盖全部格子", status_code=500)

    intervals_a = axis_intervals(br_a, spec.boundary_rule, spec.axis_a.unit)
    intervals_b = axis_intervals(br_b, spec.boundary_rule, spec.axis_b.unit)

    # 有效轴（swap 后角色交换）——图例标签/单位/方法/断点整体转置。
    row_axis, col_axis = ((spec.axis_a, spec.axis_b) if spec.swap_axes
                          else (spec.axis_b, spec.axis_a))
    row_intervals, col_intervals = ((intervals_a, intervals_b) if spec.swap_axes
                                    else (intervals_b, intervals_a))
    cells: List[Dict[str, Any]] = []
    for screen_row in range(n):
        # 屏幕第一行 = 高 B（未 flip，箭头 ↑）；flip 后第一行 = 低 B
        # （箭头 ↓）。这里只反转**屏幕行序**，类别索引与 paint 保持同源。
        r = (n - 1 - screen_row) if not spec.flip_rows else screen_row
        for c in range(n):
            # 显示位置即类别行/列；swap 时行列角色互换。
            r0 = r
            ia = r0 if spec.swap_axes else c
            ib = c if spec.swap_axes else r0
            ia_int = intervals_a[ia] if ia < len(intervals_a) else None
            ib_int = intervals_b[ib] if ib < len(intervals_b) else None
            row_int = ia_int if spec.swap_axes else ib_int
            col_int = ib_int if spec.swap_axes else ia_int
            empty = (ia >= actual_a) or (ib >= actual_b)
            cells.append({
                "screen_row": screen_row, "screen_col": c,
                "row": r, "col": c,
                "class_index": r * n + c,
                "color": colors[r * n + c],
                "count": joint[r][c],
                "empty": empty,
                "row_interval": row_int,
                "col_interval": col_int,
                "row_value_class": ib if not spec.swap_axes else ia,
                "col_value_class": ia if not spec.swap_axes else ib,
            })

    return BivariateResult(
        n=n, requested_classes=n, actual_classes_a=actual_a,
        actual_classes_b=actual_b, breaks_a=br_a, breaks_b=br_b,
        method_a=method_a, method_b=method_b,
        boundary_rule=spec.boundary_rule, colors=colors, base_colors=base,
        joint=joint, cells=cells, class_by_feature=class_by_feature,
        intervals_a=intervals_a, intervals_b=intervals_b,
        disclosures=disclosures,
    )


def axis_meta(axis: Any, intervals: List[Dict[str, Any]], method: str,
              breaks: List[float], actual: int) -> Dict[str, Any]:
    """图例轴元信息（单位未声明时如实写 unknown，不猜测、不换算）。"""
    return {
        "field": axis.field,
        "label": axis.label or axis.field,
        "unit": axis.unit or None,
        "unit_declared": bool(axis.unit),
        "measurement_note": axis.measurement_note or None,
        "method": method,
        "breaks": [float(b) for b in breaks],
        "requested_classes": None,
        "actual_classes": actual,
        "intervals": intervals,
    }


def row_col_meta(spec: BivariateSpec, result: BivariateResult) -> Dict[str, Any]:
    row_axis, col_axis = ((spec.axis_a, spec.axis_b) if spec.swap_axes
                          else (spec.axis_b, spec.axis_a))
    row_method, col_method = ((result.method_a, result.method_b) if spec.swap_axes
                              else (result.method_b, result.method_a))
    row_br, col_br = ((result.breaks_a, result.breaks_b) if spec.swap_axes
                      else (result.breaks_b, result.breaks_a))
    row_actual, col_actual = ((result.actual_classes_a, result.actual_classes_b)
                              if spec.swap_axes
                              else (result.actual_classes_b, result.actual_classes_a))
    row_int, col_int = ((result.intervals_a, result.intervals_b) if spec.swap_axes
                        else (result.intervals_b, result.intervals_a))
    return {
        # direction 描述「自屏幕顶部向底部」的数值走向；箭头指向增大方向。
        "row": {**axis_meta(row_axis, row_int, row_method, row_br, row_actual),
                "direction": "low_to_high" if spec.flip_rows else "high_to_low",
                "arrow": "down" if spec.flip_rows else "up"},
        "col": {**axis_meta(col_axis, col_int, col_method, col_br, col_actual),
                "direction": "low_to_high", "arrow": "right"},
        "swap_axes": spec.swap_axes,
        "flip_rows": spec.flip_rows,
    }
