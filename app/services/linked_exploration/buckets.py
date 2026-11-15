"""G46 同代多图表统计：类别分桶与数值区间分箱（facet 口径由调用方组 where）。

诚实规则（任务书 WP05）：

- count 的分母 = 进入统计的行数（应用了 facet where 后的匹配集）；
- sum/mean 只吃有限数值（bool 不算数值）；非数值/缺失计入
  ``non_numeric_count``，绝不静默当 0；mean 不可从桶均值再平均（桶内直接
  对原始值求和/计数）；
- 直方图桶 ``[a, b)`` 左闭右开、**末桶闭** ``[a_{n-1}, b_n]``（E08 断点规则
  全链一致：本模块同时供 live 预览、服务端统计与导出快照使用）；
- 类别键保留 typed identity（bool 归 "true"/"false" 显示但机器值集合保留
  原始 bool；大整数/字符串/空串互不混同）；显示截断只影响 label；
- 溢出桶（other）：保留 ``kept_values``（机器值）+ 有界披露 ``other_values``
  （≤ MAX_OTHER_DISCLOSED 个 typed 值）；超限 → ``other_overflow=true`` 且
  ``other_filterable=false``（明确不可筛选，不假匹配，E12/E13）；
- 真实类别名恰为 ``__other__``：机器值集合区分（kept/other 各自独立，
  label 冲突时显示侧加标签，不并入）。
"""
from __future__ import annotations

import math
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.services.feature_pages import evaluate_where
from app.services.linked_exploration.contract import (
    ChartSpec,
    MAX_CATEGORY_GROUPS,
    MAX_OTHER_DISCLOSED,
)
from app.services.linked_exploration.errors import LinkedExplorationError

#: 统计扫描帽（与 feature_aggregate.AGG_SCAN_LIMIT 同语义的独立声明：
#: 本域在一次 preview 中最多做 1+N 次扫描，每次都受此帽）。上限须 ≤
#: freeze 的 MAX_SOURCE_FEATURES（250k）才是活守卫 —— review P3-3：
#: 此前 500k 永不可触发（死守卫）。
STATS_SCAN_LIMIT = 250_000

_OTHER_KEY = "__other__"


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _display_key(v: Any) -> str:
    """类别显示键（仅 label/排序；机器 identity 另存 typed 值）。"""
    if v is None:
        return "（空值）"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and not math.isfinite(v):
        return repr(v)
    s = str(v)
    return s if len(s) <= 128 else s[:125] + "…"


def _group_key(k: Any) -> Tuple[str, Any]:
    """type-aware 分组键。

    Python dict 语义 ``hash(True)==hash(1)`` 会把 bool 类别与数字归并
    （E06 typed identity 破坏）；int/float 跨型同组与 evaluate_where
    ``_num_eq``（1 == 1.0）一致 —— 数值归并是**求值语义一致**，bool 单列。
    """
    if isinstance(k, bool):
        return ("b", bool(k))
    if isinstance(k, (int, float)):
        return ("n", float(k))
    if isinstance(k, str):
        return ("s", k)
    return ("o", type(k).__name__ + ":" + str(k))


def _agg_value(agg: str, values: List[float]) -> Optional[float]:
    if agg == "count" or not values:
        return None
    if agg == "sum":
        return math.fsum(values)
    if agg == "mean":
        return math.fsum(values) / len(values)
    if agg == "min":
        return min(values)
    return max(values)


def _numeric_of(props: Dict[str, Any], field: str) -> Optional[float]:
    v = props.get(field)
    return float(v) if _is_number(v) else None


def category_buckets(
    features: Sequence[Dict[str, Any]],
    chart: ChartSpec,
    *,
    where: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """分类条形图统计：按 ``chart.field`` 分组 + 聚合 + kept/other typed 值集。

    分组键：缺席/null 显式归 ``null`` 桶（count 单列）；非有限数值键
    （NaN/Inf）并入 null 桶并披露 ``invalid_key_count``。字符串/bool/数字
    按 typed 值归组（1 与 "1" 不同组）。
    """
    if len(features) > STATS_SCAN_LIMIT:
        raise LinkedExplorationError(
            "STATS_OVER_LIMIT",
            f"源要素 {len(features)} 超过统计扫描帽 {STATS_SCAN_LIMIT}",
            status_code=422)
    agg = chart.aggregation
    groups: "OrderedDict[Tuple[str, Any], Dict[str, Any]]" = OrderedDict()
    null_count = 0
    invalid_key_count = 0
    non_numeric_count = 0
    scanned = 0
    matched = 0
    for feat in features:
        scanned += 1
        if where is not None and not evaluate_where(where, feat):
            continue
        matched += 1
        props = feat.get("properties") if isinstance(feat, dict) else None
        props = props if isinstance(props, dict) else {}
        key = props.get(chart.field)
        val = None
        if agg != "count":
            v = _numeric_of(props, chart.value_field or "")
            if v is None:
                non_numeric_count += 1
            else:
                val = v
        if key is None:
            null_count += 1
            gkey: Optional[Tuple[str, Any]] = None
        elif isinstance(key, float) and not math.isfinite(key):
            invalid_key_count += 1
            null_count += 1
            gkey = None
        else:
            gkey = _group_key(key)
        if gkey is None:
            bucket = groups.setdefault(None, {"count": 0, "values": [], "first": None})
        else:
            bucket = groups.setdefault(
                gkey, {"count": 0, "values": [], "first": key})
        bucket["count"] += 1
        if val is not None:
            bucket["values"].append(val)

    rows_all = [
        {
            "key": (b["first"] if gkey is not None else None),
            "label": _display_key(b["first"] if gkey is not None else None),
            "count": b["count"],
            agg if agg != "count" else "count": (
                _agg_value(agg, b["values"]) if agg != "count" else b["count"]
            ),
        }
        for gkey, b in groups.items()
    ]
    # 排序：count desc, key asc（typed 混合序：num<str<其它，与
    # feature_aggregate._key_sort_token 同语义）
    def sort_token(item: Dict[str, Any]) -> Tuple[int, Any]:
        k = item["key"]
        if k is None:
            return (3, "")
        if isinstance(k, bool):
            return (1, "true" if k else "false")
        if isinstance(k, (int, float)):
            return (0, k)
        if isinstance(k, str):
            return (1, k)
        return (2, str(k))

    rows_all.sort(key=lambda r: (-r["count"], sort_token(r)))
    truncated = len(rows_all) > MAX_CATEGORY_GROUPS
    kept = rows_all[:MAX_CATEGORY_GROUPS]
    other_rows = rows_all[MAX_CATEGORY_GROUPS:]
    kept_values: List[Any] = [r["key"] for r in kept if r["key"] is not None]
    other_values: List[Any] = [r["key"] for r in other_rows if r["key"] is not None]
    other_filterable = (
        len(other_values) <= MAX_OTHER_DISCLOSED and bool(other_values)
    ) or not other_values
    other_disclosed = other_values[:MAX_OTHER_DISCLOSED]
    other_overflow = len(other_values) > MAX_OTHER_DISCLOSED

    rows: List[Dict[str, Any]] = []
    for r in kept:
        row: Dict[str, Any] = {
            "label": r["label"],
            "count": r["count"],
            "machine_value": r["key"],
            "is_null": r["key"] is None,
            "is_other": False,
        }
        if agg != "count":
            row[agg] = r.get(agg)
        rows.append(row)
    if other_rows:
        total_count = sum(r["count"] for r in other_rows)
        row_other: Dict[str, Any] = {
            "label": _OTHER_KEY,
            "count": total_count,
            "machine_value": None,
            "is_null": False,
            "is_other": True,
            "other_values": other_disclosed,
            "other_value_count": len(other_values),
            "other_overflow": other_overflow,
            "other_filterable": other_filterable,
        }
        if agg != "count":
            vals: List[float] = []
            for r in other_rows:
                v = r.get(agg)
                if v is not None:
                    vals.append(float(v))
            row_other[agg] = _agg_value(agg, vals)
        rows.append(row_other)
    return {
        "kind": "category",
        "field": chart.field,
        "aggregation": agg,
        "rows": rows,
        "group_count": len(rows_all),
        "kept_values": kept_values,
        "truncated": truncated,
        "matched": matched,
        "scanned": scanned,
        "null_count": null_count,
        "invalid_key_count": invalid_key_count,
        "non_numeric_count": non_numeric_count if agg != "count" else 0,
        "denominator_note": (
            "matched=应用当前口径 where 后的行数；sum/mean 仅计有限数值"
            if agg != "count" else "matched=应用当前口径 where 后的行数"
        ),
    }


def _bin_edges(min_v: float, max_v: float, bins: int) -> List[float]:
    if min_v == max_v:
        return [min_v, min_v + 1.0]
    width = (max_v - min_v) / bins
    edges = [min_v + width * i for i in range(bins + 1)]
    edges[-1] = max_v  # 末桶上界精确闭合到 max（浮点累积误差防御）
    return edges


def range_buckets(
    features: Sequence[Dict[str, Any]],
    chart: ChartSpec,
    *,
    where: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """数值区间直方图：等宽桶 ``[a,b)`` 末桶闭；null/非数值/无效数各自披露。

    桶边界来自**本统计口径内的**有限数值 min/max（facet where 后），固定
    桶数不重算边界（同代快照内稳定；刷新只要 where 与源不变则桶不变）。
    """
    if len(features) > STATS_SCAN_LIMIT:
        raise LinkedExplorationError(
            "STATS_OVER_LIMIT",
            f"源要素 {len(features)} 超过统计扫描帽 {STATS_SCAN_LIMIT}",
            status_code=422)
    vals: List[float] = []
    null_count = 0
    non_numeric_count = 0
    invalid_count = 0
    scanned = 0
    matched = 0
    for feat in features:
        scanned += 1
        if where is not None and not evaluate_where(where, feat):
            continue
        matched += 1
        props = feat.get("properties") if isinstance(feat, dict) else None
        props = props if isinstance(props, dict) else {}
        v = props.get(chart.field)
        if v is None:
            null_count += 1
        elif isinstance(v, bool):
            non_numeric_count += 1
        elif isinstance(v, (int, float)):
            if math.isfinite(v):
                vals.append(float(v))
            else:
                invalid_count += 1
        else:
            non_numeric_count += 1
    if not vals:
        return {
            "kind": "range",
            "field": chart.field,
            "bins": [],
            "bin_count": 0,
            "min": None,
            "max": None,
            "matched": matched,
            "scanned": scanned,
            "valid_count": 0,
            "null_count": null_count,
            "non_numeric_count": non_numeric_count,
            "invalid_count": invalid_count,
            "denominator_note": "有效样本=有限数值个数；非数值/缺失不计入桶（不当 0）",
        }
    min_v, max_v = min(vals), max(vals)
    edges = _bin_edges(min_v, max_v, chart.bins)
    counts = [0] * (len(edges) - 1)
    for v in vals:
        # [a,b) 末桶闭：等价于 clamp 到最后桶
        idx = int((v - min_v) / (edges[1] - edges[0])) if edges[1] > edges[0] else 0
        if idx >= len(counts):
            idx = len(counts) - 1
        counts[idx] += 1
    bins_out: List[Dict[str, Any]] = []
    for i, c in enumerate(counts):
        last = i == len(counts) - 1
        bins_out.append({
            "index": i,
            "gte": edges[i],
            "lte": edges[i + 1],
            "upper_exclusive": not last,   # 末桶闭：v <= lte；其余 v < lte
            "label": (
                f"[{edges[i]:.6g}, {edges[i+1]:.6g}]" if last
                else f"[{edges[i]:.6g}, {edges[i+1]:.6g})"
            ),
            "count": c,
        })
    return {
        "kind": "range",
        "field": chart.field,
        "bins": bins_out,
        "bin_count": len(bins_out),
        "min": min_v,
        "max": max_v,
        "matched": matched,
        "scanned": scanned,
        "valid_count": len(vals),
        "null_count": null_count,
        "non_numeric_count": non_numeric_count,
        "invalid_count": invalid_count,
        "denominator_note": "有效样本=有限数值个数；非数值/缺失不计入桶（不当 0）",
    }


def chart_statistics(
    features: Sequence[Dict[str, Any]],
    chart: ChartSpec,
    *,
    where: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if chart.kind == "category":
        return category_buckets(features, chart, where=where)
    return range_buckets(features, chart, where=where)


__all__ = [
    "STATS_SCAN_LIMIT", "category_buckets", "range_buckets", "chart_statistics",
]
