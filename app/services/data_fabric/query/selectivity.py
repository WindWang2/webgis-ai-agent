"""选择性估算（Data Fabric V3，ADR-0096 D3）。

**行为保持**是无统计时的硬约束：所有默认值与 V2 planner 的四个常数
逐一相等（planner.py 原 `_SELECTIVITY_*`）。有统计时按列统计给出
估计并标注 basis；缺统计的子谓词回落常数并标 assumption。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from app.services.data_fabric.query.statistics import DatasetStatistics

# 与 V2 planner 完全相同的常数（无统计路径必须逐位一致）
SELECTIVITY_EQ = 0.05
SELECTIVITY_RANGE = 0.25
SELECTIVITY_IN = 0.3
SELECTIVITY_NE = 0.95
SELECTIVITY_NULL = 0.05
_MIN_SEL = 1e-9
_MAX_SEL_EQ = 0.9

_RANGE_OPS = ("gt", "ge", "lt", "le", "between", "before", "after", "during")


class SelectivityEstimate(BaseModel):
    value: float
    basis: str = "default"          # statistics | default | assumption
    detail: Dict[str, Any] = {}

    @property
    def is_default(self) -> bool:
        return self.basis == "default"


def _clamp(v: float, lo: float = _MIN_SEL, hi: float = 1.0) -> float:
    return max(lo, min(1.0, v))


def _field_of(node: Any) -> Optional[str]:
    return getattr(node, "field", None)


def estimate_predicate_selectivity(
    node: Any, stats: Optional[DatasetStatistics] = None
) -> SelectivityEstimate:
    """谓词选择性估算：统计优先、常数兜底、假设标注。"""
    if node is None:
        return SelectivityEstimate(value=1.0, basis="default")
    op = getattr(node, "op", None)

    if op == "and":
        combined = 1.0
        used_stats = False
        for a in node.args:
            est = estimate_predicate_selectivity(a, stats)
            combined *= est.value
            used_stats = used_stats or est.basis == "statistics"
        return SelectivityEstimate(
            value=max(combined, _MIN_SEL),
            basis="statistics" if used_stats else "default",
        )
    if op == "or":
        total = 0.0
        used_stats = False
        for a in node.args:
            est = estimate_predicate_selectivity(a, stats)
            total += est.value
            used_stats = used_stats or est.basis == "statistics"
        return SelectivityEstimate(
            value=min(1.0, total),
            basis="statistics" if used_stats else "default",
        )
    if op == "not":
        inner = estimate_predicate_selectivity(node.arg, stats)
        return SelectivityEstimate(
            value=max(0.0, 1.0 - inner.value), basis=inner.basis
        )

    col = stats.column(_field_of(node)) if stats is not None else None

    if op in ("eq", "like"):
        if col is not None and col.ndv and col.ndv > 0:
            return SelectivityEstimate(
                value=_clamp(1.0 / col.ndv, _MIN_SEL, _MAX_SEL_EQ),
                basis="statistics",
                detail={"field": col.name, "ndv": col.ndv},
            )
        return SelectivityEstimate(value=SELECTIVITY_EQ, basis="default")
    if op == "is_null":
        if col is not None and col.null_fraction is not None:
            frac = col.null_fraction if not node.negated else 1.0 - col.null_fraction
            return SelectivityEstimate(
                value=_clamp(frac), basis="statistics",
                detail={"field": col.name, "null_fraction": col.null_fraction},
            )
        value = SELECTIVITY_NULL if not node.negated else 1.0 - SELECTIVITY_NULL
        return SelectivityEstimate(value=value, basis="default")
    if op in _RANGE_OPS:
        if col is not None and col.min_value is not None and col.max_value is not None:
            span = col.max_value - col.min_value
            if span and span > 0:
                lo = getattr(node, "value", None)
                hi = getattr(node, "second_value", None) if op == "between" else lo
                try:
                    if op in ("gt", "ge") and lo is not None:
                        frac = (col.max_value - float(lo)) / span
                    elif op in ("lt", "le") and lo is not None:
                        frac = (float(lo) - col.min_value) / span
                    elif op == "between" and lo is not None and hi is not None:
                        frac = (float(hi) - float(lo)) / span
                    else:
                        frac = SELECTIVITY_RANGE
                except (TypeError, ValueError):
                    frac = SELECTIVITY_RANGE
                if 0.0 < frac < 1.0:
                    return SelectivityEstimate(
                        value=_clamp(frac), basis="statistics",
                        detail={"field": col.name, "min": col.min_value, "max": col.max_value},
                    )
        return SelectivityEstimate(value=SELECTIVITY_RANGE, basis="default")
    if op == "in":
        k = max(1, len(getattr(node, "values", []) or []))
        if col is not None and col.ndv and col.ndv > 0:
            per = 1.0 / col.ndv
            if 0.0 < per < 1.0:
                return SelectivityEstimate(
                    value=min(_MAX_SEL_EQ, k * per), basis="statistics",
                    detail={"field": col.name, "ndv": col.ndv, "members": k},
                )
        return SelectivityEstimate(
            value=min(0.9, SELECTIVITY_IN * k), basis="default"
        )
    if op == "not_in":
        inner_k = max(1, len(getattr(node, "values", []) or []))
        if col is not None and col.ndv and col.ndv > 0:
            per = 1.0 / col.ndv
            if 0.0 < per < 1.0:
                return SelectivityEstimate(
                    value=_clamp(1.0 - min(_MAX_SEL_EQ, inner_k * per)),
                    basis="statistics",
                )
        return SelectivityEstimate(
            value=max(0.05, 1.0 - min(0.9, SELECTIVITY_IN * inner_k)), basis="default"
        )
    if op == "ne":
        return SelectivityEstimate(value=SELECTIVITY_NE, basis="default")
    return SelectivityEstimate(value=0.5, basis="assumption")


def estimate_group_cardinality(
    group_by: List[str],
    stats: Optional[DatasetStatistics],
    rows_estimate: Optional[int],
) -> Optional[int]:
    """GROUP BY 基数：NDV 乘积（有界），无统计回落 planner 的 5000 常数。"""
    if not group_by:
        return None
    fallback = 5000
    if stats is None:
        return fallback
    product = 1.0
    for field in group_by:
        col = stats.column(field)
        if col is None or not col.ndv:
            return fallback
        product *= min(col.ndv, 10_000)
    est = int(min(product, 1_000_000))
    if rows_estimate is not None:
        est = min(est, max(1, rows_estimate))
    return max(1, est)
