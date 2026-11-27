"""多源联动指标解释求值器（WP2）—— 确定性、有界、分母治理。

语义边界（与 contract.MetricSpec 一致）：
- 输入行集是**联动范围过滤后的**冻结行（服务层保证）；指标永远回答
  "当前范围下"，本模块不做过滤。
- typed 纪律与 buckets/keys 同源：bool 与数字互不等、int==float 归并、
  字符串严格、null/缺失显式计数、非有限数并入无效计数披露。
- 分母治理：rate 分母合计为零/无有效数值 → 该组 ``unavailable`` +
  机读原因码，绝不返回 0 冒充率。
- 有界性：median/distinct_count 超帽 typed unavailable
  （``unsupported_at_scale``）并给精确路径指引，不声明近似值；
  组数超帽并入 ``__other__`` 披露（与 feature_aggregate 同语义）。
- 跨源 rate 按关系键聚合（每键一组），只按键聚合、不物化行乘积；
  空间关系跨源指标 typed 拒绝（指引走物化空间连接 ref）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.services.data_join_authoring.keys import (
    KeyFieldSpec,
)
from app.services.linked_exploration import contract
from app.services.linked_exploration.errors import LinkedExplorationError
from app.services.linked_exploration.relations import (
    normalize_link_key_safe,
)

#: 扫描帽（≤ 冻结源 250k 硬帽才是活守卫 —— 与 buckets P3-3 同理由）
METRIC_SCAN_LIMIT = 250_000
#: distinct_count 精确帽（超帽 unavailable，不声明近似）
DISTINCT_VALUE_CAP = 10_000
#: median 精确帽（超帽 unavailable；不引入近似分位数）
QUANTILE_MAX_VALUES = 10_000
#: 组数硬帽（与 feature_aggregate.AGG_MAX_GROUPS_HARD 对齐）
MAX_GROUPS = 256
#: 面积权重的 CRS 口径披露（面积按源坐标系坐标单位计算）
_AREA_NOTE = (
    "面积权重按源坐标系坐标单位计算（投影 CRS 下才是真实面积口径；"
    "地理度数坐标系下仅为序权重，已在 area_note 披露）")

_UNAVAILABLE_ZERO = "denominator_zero"
_UNAVAILABLE_MISSING = "denominator_missing"
_UNAVAILABLE_NO_NUMERIC = "no_numeric_values"
_UNAVAILABLE_SCALE = "unsupported_at_scale"
_UNAVAILABLE_WEIGHT = "zero_total_weight"


def _err(code: str, message: str, **kw: Any) -> LinkedExplorationError:
    kw.setdefault("status_code", 422)
    return LinkedExplorationError(code, message, **kw)


def _numeric(value: Any) -> Optional[float]:
    """数值语义：int/float（非 bool）且有限 → float；其余 → None。
    与 buckets._agg_value 的分母纪律一致（非数值绝不当 0 计入）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    return v if math.isfinite(v) else None


def _group_token(value: Any) -> Tuple[str, Any]:
    """typed 组键 token（与 buckets._group_key 同纪律）：
    bool 单列、数值归并、字符串、其它型按类型名槽；None → null 组。"""
    if value is None:
        return ("null", None)
    if isinstance(value, bool):
        return ("b", value)
    if isinstance(value, (int, float)):
        f = float(value)
        if not math.isfinite(f):
            return ("null", None)  # NaN/Inf 并入 null 组 + 无效披露
        return ("n", f)
    if isinstance(value, str):
        return ("s", value)
    return ("o", type(value).__name__)


def _serializable_key(token: Tuple[str, Any]) -> Any:
    family, value = token
    if family == "__other__":
        return "__other__"   # 溢出槽（不再包一层类型标记）
    if family == "null":
        return None
    if family == "o":
        return f"__{value}__"
    return value


@dataclass
class _RowStats:
    count: int = 0
    numeric_count: int = 0
    null_count: int = 0
    non_numeric_count: int = 0


def _empty_group(key_token: Tuple[str, Any]) -> Dict[str, Any]:
    return {
        "key": _serializable_key(key_token),
        "key_label": str(_serializable_key(key_token)),
        "value": None,
        "available": False,
        "unavailable_reason": None,
        "numerator": None,
        "denominator": None,
        "count": 0,
        "numeric_count": 0,
        "null_count": 0,
        "non_numeric_count": 0,
        "invalid_weight_count": 0,
    }


def _props(feature: Any) -> Dict[str, Any]:
    props = feature.get("properties") if isinstance(feature, dict) else None
    return props if isinstance(props, dict) else {}


# ── 组装：按 group_field 或关系键分组 ──────────────────────────────────

def _group_rows(
    features: Sequence[Any],
    group_field: Optional[str],
) -> List[Tuple[Tuple[str, Any], List[Any], _RowStats]]:
    """行集 → [(组 token, 行, 组统计)]；确定性排序：null 组最后、按
    (count desc, key 升序)。溢出并入 __other__（truncated 披露由调用方
    组装）。"""
    buckets: Dict[Tuple[str, Any], List[Any]] = {}
    stats: Dict[Tuple[str, Any], _RowStats] = {}
    for feat in features:
        props = _props(feat)
        raw = props.get(group_field) if group_field else None
        token = _group_token(raw) if group_field else ("all", None)
        buckets.setdefault(token, []).append(feat)
        st = stats.setdefault(token, _RowStats())
        st.count += 1
        if group_field:
            if raw is None:
                st.null_count += 1
            elif not isinstance(raw, (str, int, float, bool)):
                st.non_numeric_count += 1
    null_token = ("null", None)
    ordered = sorted(
        buckets.items(),
        key=lambda kv: (
            0 if kv[0] == ("all", None) else (1 if kv[0] != null_token else 2),
            -stats[kv[0]].count,
            str(_serializable_key(kv[0])),
        ),
    )
    return [(tok, rows, stats[tok]) for tok, rows in ordered]


def _cap_other_groups(
    groups: List[Tuple[Tuple[str, Any], List[Any], _RowStats]],
) -> Tuple[List[Tuple[Tuple[str, Any], List[Any], _RowStats]], bool]:
    """组数超帽：保留前 MAX_GROUPS-1 组（排序已确定），其余并入
    __other__ 槽（行聚合）。返回 (组列表, truncated)。"""
    if len(groups) <= MAX_GROUPS:
        return groups, False
    kept = groups[:MAX_GROUPS - 1]
    overflow_rows: List[Any] = []
    for _tok, rows, _st in groups[MAX_GROUPS - 1:]:
        overflow_rows.extend(rows)
    stats = _RowStats()
    stats.count = len(overflow_rows)
    return kept + [(("__other__", None), overflow_rows, stats)], True


# ── 单组算子求值 ────────────────────────────────────────────────────────

def _apply_scale(value: Optional[float],
                 scale: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return value * (scale if scale is not None else 1.0)


def _polygon_area(geom: Any) -> Optional[float]:
    """ shoelace 面积（Polygon/MultiPolygon；坐标单位 = 源 CRS）。"""
    if not isinstance(geom, dict):
        return None
    gtype = geom.get("type")

    def _ring_area(ring: Any) -> Optional[float]:
        if not isinstance(ring, list) or len(ring) < 4:
            return None
        acc = 0.0
        for i in range(len(ring) - 1):
            p, q = ring[i], ring[i + 1]
            if not (isinstance(p, (list, tuple)) and len(p) >= 2
                    and isinstance(q, (list, tuple)) and len(q) >= 2):
                return None
            x1, y1 = _numeric(p[0]), _numeric(p[1])
            x2, y2 = _numeric(q[0]), _numeric(q[1])
            if x1 is None or x2 is None or y1 is None or y2 is None:
                return None
            acc += x1 * y2 - x2 * y1
        return abs(acc) / 2.0

    try:
        if gtype == "Polygon":
            rings = geom.get("coordinates")
            if not isinstance(rings, list) or not rings:
                return None
            outer = _ring_area(rings[0])
            if outer is None:
                return None
            holes = 0.0
            for hole in rings[1:]:
                ha = _ring_area(hole)
                if ha is None:
                    return None
                holes += ha
            return outer - holes
        if gtype == "MultiPolygon":
            total = 0.0
            for poly in geom.get("coordinates") or []:
                area = _polygon_area({"type": "Polygon", "coordinates": poly})
                if area is None:
                    return None
                total += area
            return total
    except (TypeError, ValueError):
        return None
    return None


def _aggregate_within(
    metric: contract.MetricSpec,
    rows: List[Any],
) -> Dict[str, Any]:
    """单组内算子：count/distinct_count/sum/mean/weighted_mean/median/rate。"""
    group = _empty_group(("all", None))
    group["count"] = len(rows)
    op = metric.op

    numerics: List[float] = []
    for feat in rows:
        v = _numeric(_props(feat).get(metric.value_field)) \
            if metric.value_field else None
        if metric.value_field:
            raw = _props(feat).get(metric.value_field)
            if raw is None:
                group["null_count"] += 1
            elif v is None:
                group["non_numeric_count"] += 1
            else:
                numerics.append(v)
                group["numeric_count"] += 1

    if op == "count":
        group["value"] = float(len(rows))
        group["available"] = True
        return group
    try:
        if op == "sum":
            group["value"] = _apply_scale(math.fsum(numerics), metric.scale)
            group["available"] = True
            return group
        if op == "mean":
            if not numerics:
                group["unavailable_reason"] = _UNAVAILABLE_NO_NUMERIC
                return group
            group["value"] = _apply_scale(
                math.fsum(numerics) / len(numerics), metric.scale)
            group["available"] = True
            return group
        if op == "weighted_mean":
            pairs: List[Tuple[float, float]] = []
            invalid_weight = 0
            if metric.weight_by_area:
                for feat in rows:
                    if not isinstance(feat, dict):
                        invalid_weight += 1
                        continue
                    w = _polygon_area(feat.get("geometry"))
                    v = _numeric(_props(feat).get(metric.value_field))
                    if w is None or v is None:
                        invalid_weight += 1
                        continue
                    pairs.append((v, w))
            else:
                for feat in rows:
                    w = _numeric(_props(feat).get(metric.weight_field))
                    v = _numeric(_props(feat).get(metric.value_field))
                    if w is None or v is None or w < 0:
                        invalid_weight += 1
                        continue
                    pairs.append((v, w))
            group["invalid_weight_count"] = invalid_weight
            if not pairs:
                group["unavailable_reason"] = _UNAVAILABLE_NO_NUMERIC
                return group
            total_w = math.fsum(w for _v, w in pairs)
            if total_w == 0:
                group["unavailable_reason"] = _UNAVAILABLE_WEIGHT
                return group
            group["value"] = _apply_scale(
                math.fsum(w * v for v, w in pairs) / total_w, metric.scale)
            group["available"] = True
            return group
    except OverflowError:
        # review A-P3-5：大而有限的输入（如 1e308 双行 fsum）中间溢出 ——
        # 该组 unavailable（超帽不近似纪律），绝不 500 整个 preview
        group["unavailable_reason"] = _UNAVAILABLE_SCALE
        return group
    if op == "median":
        if not numerics:
            group["unavailable_reason"] = _UNAVAILABLE_NO_NUMERIC
            return group
        if len(numerics) > QUANTILE_MAX_VALUES:
            group["unavailable_reason"] = _UNAVAILABLE_SCALE
            return group
        ordered = sorted(numerics)
        n = len(ordered)
        mid = n // 2
        median = ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2
        group["value"] = _apply_scale(median, metric.scale)
        group["available"] = True
        return group
    if op == "distinct_count":
        spec = KeyFieldSpec(left_field=metric.value_field or "",
                            right_field=metric.value_field or "")
        seen: set = set()
        invalid = 0
        for feat in rows:
            key, bad = normalize_link_key_safe(
                _props(feat).get(metric.value_field), spec)
            if bad:
                invalid += 1
                continue
            if key is not None:
                seen.add(key)
        if len(seen) > DISTINCT_VALUE_CAP:
            group["unavailable_reason"] = _UNAVAILABLE_SCALE
            return group
        group["value"] = float(len(seen))
        group["available"] = True
        group["invalid_weight_count"] = invalid  # 不可比键值计数披露
        return group
    if op == "rate":
        # 内部不变量违约（路由错误），非用户输入错误 → 5xx
        raise _err("METRIC_WITHIN_RATE_ROUTE",
                   "同源 rate 走 _rate_rows 路径（内部路由错误）",
                   status_code=500)
    raise _err("INVALID_METRIC_OP", f"未知指标算子 {op!r}")


def _rate_group(
    numerator_rows: List[Any],
    denominator_rows: List[Any],
    *,
    metric: contract.MetricSpec,
) -> Dict[str, Any]:
    """rate 组：分子/分母聚合 + 分母治理。"""
    group = _empty_group(("all", None))
    group["count"] = len(denominator_rows)

    if metric.value_field:
        nums = [_numeric(_props(f).get(metric.value_field))
                for f in numerator_rows]
        nums = [x for x in nums if x is not None]
        numerator: float = math.fsum(nums)
        group["numeric_count"] = len(nums)
    else:
        numerator = float(len(numerator_rows))

    if metric.denominator_field:
        dens = [_numeric(_props(f).get(metric.denominator_field))
                for f in denominator_rows]
        dens_valid = [x for x in dens if x is not None]
        if not dens_valid:
            group["unavailable_reason"] = _UNAVAILABLE_MISSING
            return group
        try:
            denominator = math.fsum(dens_valid)
        except OverflowError:
            group["unavailable_reason"] = _UNAVAILABLE_SCALE
            return group
    else:
        denominator = float(len(denominator_rows))

    group["numerator"] = numerator
    group["denominator"] = denominator
    if denominator == 0:
        group["unavailable_reason"] = _UNAVAILABLE_ZERO
        return group
    if denominator < 0:
        # review A-P3-6：负分母（如人口 -100）是脏数据 —— 与 weighted_mean
        # 负权重同纪律：unavailable + 原因码，不返回 available 负率
        group["unavailable_reason"] = "denominator_non_positive"
        return group
    try:
        group["value"] = _apply_scale(numerator / denominator, metric.scale)
    except OverflowError:
        group["unavailable_reason"] = _UNAVAILABLE_SCALE
        return group
    group["available"] = True
    return group


def _denominator_note(metric: contract.MetricSpec) -> str:
    if metric.denominator_field:
        return (f"分母 = {metric.denominator_field} 有效数值合计；"
                "0/空 → 该组 unavailable（不冒充 0 率）")
    return "分母 = 分母侧行计数"


def _key_index(
    rows: List[Any], field: str, spec: KeyFieldSpec,
) -> Tuple[Dict[Any, List[Any]], int, int]:
    """行集 → 规范键索引；返回 (索引, 缺失键行数, 类型不可比行数)。
    null/缺失键不参与匹配（G38 null 政策）；类型不可比逐行披露。"""
    idx: Dict[Any, List[Any]] = {}
    missing = 0
    invalid = 0
    for feat in rows:
        key, bad = normalize_link_key_safe(_props(feat).get(field), spec)
        if bad:
            invalid += 1
            continue
        if key is None:
            missing += 1
            continue
        idx.setdefault(key, []).append(feat)
    return idx, missing, invalid


def _cross_rate_aggregate(
    keys: List[Any],
    num_idx: Dict[Any, List[Any]],
    den_idx: Dict[Any, List[Any]],
    *,
    metric: contract.MetricSpec,
) -> Tuple[float, float]:
    """溢出键集合上的分子/分母合计（与单键组同口径聚合，不丢行）。"""
    num_sum = 0.0
    den_sum = 0.0
    for key in keys:
        n_rows = num_idx.get(key, [])
        if metric.value_field:
            vals = [_numeric(_props(f).get(metric.value_field))
                    for f in n_rows]
            num_sum += math.fsum(v for v in vals if v is not None)
        else:
            num_sum += float(len(n_rows))
        d_rows = den_idx.get(key, [])
        if metric.denominator_field:
            vals = [_numeric(_props(f).get(metric.denominator_field))
                    for f in d_rows]
            den_sum += math.fsum(v for v in vals if v is not None)
        else:
            den_sum += float(len(d_rows))
    return num_sum, den_sum


# ── 入口 ────────────────────────────────────────────────────────────────

def compute_metric(
    metric: contract.MetricSpec,
    features_by_source: Dict[str, Sequence[Any]],
    *,
    links_by_id: Optional[Dict[str, contract.LinkSpec]] = None,
) -> Dict[str, Any]:
    """指标求值（联动范围过滤后的行集；本模块不再过滤）。

    ``features_by_source``：source_id → 行集（缺源/空行集 = 无组 ——
    诚实空集，不造 unavailable 假组；review A-P3-8 docstring 对齐行为）。
    """
    if any(len(v) > METRIC_SCAN_LIMIT for v in features_by_source.values()):
        # review B-P3：per-source 口径（与冻结帽同界）——跨源合计帽会
        # 让 2×13 万行的合法多源计划全指标不可用
        raise _err(
            "METRIC_OVER_LIMIT",
            f"单源指标求值扫描行数超帽 {METRIC_SCAN_LIMIT}")
    links_by_id = links_by_id or {}
    # 缺省源解析（primary）由调用方传入显式 source_id；本模块只按显式
    # source_id 取行集；空行集 = 无组（诚实空，不造 unavailable 假组）
    source_id = metric.source_id
    numerator_rows = list(features_by_source.get(source_id) or [])
    # review A-P3-11：跨源 rate 的输出粒度 = 分母侧（键即分组）
    grain_id = metric.grain_source_id or (
        metric.denominator_source_id if metric.link_id is not None
        else source_id)

    disclosure: Dict[str, Any] = {"precision": "exact"}
    out: Dict[str, Any] = {
        "metric_id": metric.metric_id,
        "op": metric.op,
        "source_id": source_id,
        "grain_source_id": grain_id,
        "unit": metric.unit,
        "scale": metric.scale,
        "title": metric.title,
        "groups": [],
        "truncated": False,
        "disclosure": disclosure,
    }

    if metric.link_id is not None:
        link = links_by_id.get(metric.link_id)
        if link is None:
            raise _err(
                "INVALID_PLAN",
                f"指标 {metric.metric_id} 引用了不存在的 link {metric.link_id!r}")
        if link.kind != "attribute":
            raise _err(
                "SPATIAL_METRIC_UNSUPPORTED",
                "空间关系上的跨源指标暂不支持：请先物化空间连接"
                "（G38 spatial join / materialize）为属性 ref 后用键关系",
                correction_hint="用 data_join spatial_point_in_polygon "
                                "执行后，把结果 ref 作为分母源并声明键关系。")
        pair = link.keys[0]
        # 分母侧 = denominator_source_id（键即分组）；分子侧 = source_id。
        # 分子/分母各自按键索引聚合，不物化行乘积。
        denom_rows = list(
            features_by_source.get(metric.denominator_source_id) or [])
        num_is_left = (source_id == link.left_source_id)
        key_field_num = pair.left_field if num_is_left else pair.right_field
        key_field_den = pair.right_field if num_is_left else pair.left_field
        spec_num = KeyFieldSpec(
            left_field=key_field_num, right_field=key_field_den,
            transform=pair.transform, zfill_width=pair.zfill_width)
        spec_den = KeyFieldSpec(
            left_field=key_field_den, right_field=key_field_num,
            transform=pair.transform, zfill_width=pair.zfill_width)
        num_idx, num_missing, num_invalid = _key_index(
            numerator_rows, key_field_num, spec_num)
        den_idx, den_missing, den_invalid = _key_index(
            denom_rows, key_field_den, spec_den)
        # 分组 = 分母侧键（确定性排序）；超帽并入 __other__（其分子/分母
        # 在溢出键上按同口径聚合，不丢行）
        denom_keys = sorted(den_idx.keys(), key=lambda k: (k[0], str(k[1])))
        groups: List[Tuple[Any, List[Any], _RowStats]] = []
        for key in denom_keys:
            st = _RowStats()
            st.count = len(den_idx[key])
            groups.append((key, den_idx[key], st))
        kept, truncated = _cap_other_groups(groups)
        for key, rows, st in kept:
            if key == ("__other__", None):
                num_sum, den_sum = _cross_rate_aggregate(
                    denom_keys[MAX_GROUPS - 1:], num_idx, den_idx,
                    metric=metric)
                g = _empty_group(key)
                g["count"] = st.count
                g["numerator"] = num_sum
                g["denominator"] = den_sum
                if den_sum == 0:
                    g["unavailable_reason"] = _UNAVAILABLE_ZERO
                else:
                    g["value"] = _apply_scale(num_sum / den_sum, metric.scale)
                    g["available"] = True
                out["groups"].append(g)
                continue
            g = _rate_group(num_idx.get(key, []), rows, metric=metric)
            g["key"] = _serializable_key(key)
            g["key_label"] = str(_serializable_key(key))
            out["groups"].append(g)
        out["truncated"] = truncated
        disclosure["key_semantics"] = (
            "跨源 rate 按关系键分组；null/类型不可比键不参与匹配")
        disclosure["numerator_rows_missing_key"] = num_missing
        disclosure["numerator_rows_invalid_key"] = num_invalid
        # 分子键在分母侧无对应组（如学校落在人口表没有的区）—— 逐行
        # 计数披露，绝不无声丢行
        den_key_set = set(den_idx.keys())
        disclosure["numerator_rows_unmatched_key"] = sum(
            len(rows) for k, rows in num_idx.items() if k not in den_key_set)
        disclosure["denominator_rows_missing_key"] = den_missing
        disclosure["denominator_rows_invalid_key"] = den_invalid
        disclosure["denominator_note"] = _denominator_note(metric)
        return out

    # 同源指标
    grouped = _group_rows(numerator_rows, metric.group_field)
    grouped, truncated = _cap_other_groups(grouped)
    for token, rows, st in grouped:
        if metric.op == "rate":
            g = _rate_group(rows, rows, metric=metric)
            g["key"] = _serializable_key(token)
            g["key_label"] = str(_serializable_key(token))
            g["count"] = st.count
            if metric.group_field:
                g["null_count"] = st.null_count
                g["non_numeric_count"] = st.non_numeric_count
        else:
            g = _aggregate_within(metric, rows)
            g["key"] = _serializable_key(token)
            g["key_label"] = str(_serializable_key(token))
            g["count"] = st.count
        out["groups"].append(g)
    out["truncated"] = truncated
    if metric.op == "rate":
        disclosure["denominator_note"] = _denominator_note(metric)
    if metric.weight_by_area:
        disclosure["area_note"] = _AREA_NOTE
    disclosure["group_cap"] = MAX_GROUPS
    return out


__all__ = [
    "METRIC_SCAN_LIMIT", "DISTINCT_VALUE_CAP", "QUANTILE_MAX_VALUES",
    "MAX_GROUPS", "compute_metric",
]
