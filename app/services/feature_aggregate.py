"""会话 ref 分组聚合（Track B vNext）—— 视口/过滤统计的服务端真值源。

图表联动（M7/M8）的诚实统计半边：``GET /layers/data/{ref}/aggregate`` 的
窗口语义（route 只做协议适配）。与 ``feature_pages`` 共享 where AST/bbox
粗滤语义 —— 同一 (where, bbox) 在分页与聚合两条通道上匹配集恒等。

科学诚实规则（ADR-0047 同源）：

- **有界**：全扫上限 AGG_SCAN_LIMIT —— 超限 422 ``aggregate_over_limit``，
  绝不静默降采样后冒充全量；
- **数值准入**：sum/mean/min/max 只吃数值（bool 不是数值）；非数值/缺失
  计入 ``non_numeric_count``，绝不静默当 0 —— 无分母的 per-capita 类
  结论在消费端被拒绝；
- **分组键**：null/缺失键计入 ``null_key_count``（key=null 行单独披露，
  不并入任意组）；组数超 ``max_groups`` → 其余合并入 ``__other__`` +
  ``truncated=true``；
- **确定性**：组按 (count desc, key asc) 输出；同输入恒同输出。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from app.services.feature_pages import (
    FeaturePageError,
    _feature_intersects_bbox,
    evaluate_where,
)

#: 聚合全扫上限 —— 会话 ref 是内存 FC；超限拒绝（typed 422）而不是慢扫。
AGG_SCAN_LIMIT = 500_000

#: 组数上限（调用方 max_groups 的硬帽）。
AGG_MAX_GROUPS_HARD = 256

AGG_OPS = ("count", "sum", "mean", "min", "max")

_OTHER_KEY = "__other__"


class AggregateOverLimit(Exception):
    """feature_count > AGG_SCAN_LIMIT —— 有界拒绝，不是失败重试。"""

    def __init__(self, feature_count: int):
        self.feature_count = feature_count
        super().__init__(f"feature_count {feature_count} exceeds aggregate scan limit {AGG_SCAN_LIMIT}")


def validate_aggregate_params(
    group_by: Optional[str], agg: str, agg_field: Optional[str], max_groups: int
) -> Tuple[str, str, Optional[str], int]:
    """route 参数 → 校验后的 (group_by, agg, agg_field, max_groups)。"""
    if not group_by or not isinstance(group_by, str) or len(group_by) > 64:
        raise FeaturePageError("group_by must be a 1-64 char field name")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in group_by):
        raise FeaturePageError("group_by contains control characters")
    if agg not in AGG_OPS:
        raise FeaturePageError(f"agg must be one of {AGG_OPS}")
    if agg != "count" and (not agg_field or not isinstance(agg_field, str) or len(agg_field) > 64):
        raise FeaturePageError(f"agg={agg} requires a 1-64 char agg_field")
    if agg_field is not None and any(ord(c) < 0x20 or ord(c) == 0x7F for c in agg_field):
        raise FeaturePageError("agg_field contains control characters")
    if max_groups < 1 or max_groups > AGG_MAX_GROUPS_HARD:
        raise FeaturePageError(f"max_groups must be 1-{AGG_MAX_GROUPS_HARD}")
    return group_by, agg, agg_field, max_groups


def _numeric(value: Any) -> Optional[float]:
    """值 → float；bool/NaN/非数值 → None（非数值计数，不当 0）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    return v if math.isfinite(v) else None


def aggregate_features(
    fc: Any,
    *,
    group_by: str,
    agg: str = "count",
    agg_field: Optional[str] = None,
    bbox: Optional[Tuple[float, float, float, float]] = None,
    where: Optional[Dict[str, Any]] = None,
    max_groups: int = 64,
) -> Dict[str, Any]:
    """FC → 分组聚合（单遍全扫，确定性输出）。

    返回 rows 每项 ``{key, count, sum?, mean?, min?, max?}``；``__other__``
    行只在组溢出时出现（其 sum/mean 汇总溢出组的对应量，min/max 为溢出组
    范围）。key 排序 = (count desc, key asc)；null 键组 key=null 置顶前
    单独披露（null_key_count>0 时保证存在一行 key=null）。
    """
    features = fc.get("features") if isinstance(fc, dict) else None
    if not isinstance(features, list):
        raise FeaturePageError("payload is not a FeatureCollection")
    n = len(features)
    if n > AGG_SCAN_LIMIT:
        raise AggregateOverLimit(n)

    groups: Dict[Any, Dict[str, Any]] = {}
    null_key_count = 0
    non_numeric = 0
    scanned = 0
    total_matching = 0

    def _bucket(key: Any) -> Dict[str, Any]:
        b = groups.get(key)
        if b is None:
            b = {"count": 0, "_sum": 0.0, "_n_num": 0, "_min": None, "_max": None}
            groups[key] = b
        return b

    for f in features:
        scanned += 1
        if where is not None and not evaluate_where(where, f):
            continue
        if bbox is not None and not _feature_intersects_bbox(f, bbox):
            continue
        total_matching += 1
        props = f.get("properties") if isinstance(f, dict) else None
        props = props if isinstance(props, dict) else {}
        raw_key = props.get(group_by)
        if raw_key is None:
            null_key_count += 1
            key = None
        elif isinstance(raw_key, bool):
            key = "true" if raw_key else "false"
        elif isinstance(raw_key, (int, float)) and not isinstance(raw_key, bool):
            key = raw_key if math.isfinite(raw_key) else None
            if key is None:
                null_key_count += 1
        elif isinstance(raw_key, str):
            key = raw_key if len(raw_key) <= 128 else raw_key[:128]
        else:
            # 容器/复杂键：以类型名归组（诚实：不丢行，不假装可比）
            key = f"<{type(raw_key).__name__}>"
        bucket = _bucket(key)
        bucket["count"] += 1
        if agg != "count":
            v = _numeric(props.get(agg_field))
            if v is None:
                non_numeric += 1
            else:
                bucket["_sum"] += v
                bucket["_n_num"] += 1
                if bucket["_min"] is None or v < bucket["_min"]:
                    bucket["_min"] = v
                if bucket["_max"] is None or v > bucket["_max"]:
                    bucket["_max"] = v

    ordered = sorted(
        groups.items(), key=lambda kv: (-kv[1]["count"], _key_sort_token(kv[0]))
    )
    truncated = len(ordered) > max_groups
    top = ordered[:max_groups]
    rows: List[Dict[str, Any]] = []
    for key, b in top:
        rows.append(_row(key, b, agg))
    if truncated:
        rest = ordered[max_groups:]
        other = {"count": 0, "_sum": 0.0, "_n_num": 0, "_min": None, "_max": None}
        for _k, b in rest:
            other["count"] += b["count"]
            other["_sum"] += b["_sum"]
            other["_n_num"] += b["_n_num"]
            if b["_min"] is not None and (other["_min"] is None or b["_min"] < other["_min"]):
                other["_min"] = b["_min"]
            if b["_max"] is not None and (other["_max"] is None or b["_max"] > other["_max"]):
                other["_max"] = b["_max"]
        rows.append(_row(_OTHER_KEY, other, agg))
    return {
        "rows": rows,
        "group_count": len(groups),
        "is_complete": not truncated,
        "truncated": truncated,
        "scanned": scanned,
        "total_matching": total_matching,
        "null_key_count": null_key_count,
        "non_numeric_count": non_numeric,
    }


def _key_sort_token(key: Any) -> Tuple[int, Any]:
    """混合类型键的确定性排序令牌：数值(0)/字符串(1)/其它(2)。"""
    if key is None:
        return (3, "")
    if isinstance(key, (int, float)):
        return (0, key)
    if isinstance(key, str):
        return (1, key)
    return (2, str(key))


def _row(key: Any, b: Dict[str, Any], agg: str) -> Dict[str, Any]:
    row: Dict[str, Any] = {"key": key, "count": b["count"]}
    if agg != "count":
        row["sum"] = b["_sum"] if b["_n_num"] else None
        row["mean"] = (b["_sum"] / b["_n_num"]) if b["_n_num"] else None
        row["min"] = b["_min"]
        row["max"] = b["_max"]
    return row


__all__ = [
    "AGG_SCAN_LIMIT",
    "AGG_MAX_GROUPS_HARD",
    "AGG_OPS",
    "AggregateOverLimit",
    "validate_aggregate_params",
    "aggregate_features",
]
