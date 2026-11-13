"""G33 同版本统计与分布预览（WP03）。

单一事实：统计总体 = 提交源（session ref 全量），**绝不**从当前视窗/可见
瓦片推断全域分位数。大数据采用有界步进采样并如实标注 approximate/样本量；
精确类计数与近似分布不得混称（``basis`` 分型披露）。

统一求值器 ``assign_class`` 与 MapLibre ``step`` 语义逐字一致（v ≥ 断点归
上类；首类吸收 −∞、末类吸收 +∞），地图/图例/类计数共享它，杜绝
「图例说一类、地图画另一类」。
"""
from __future__ import annotations

import json
import logging
import math
from collections import Counter, OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: 统计合同版本（缓存键组成部分；语义变化时递增使旧缓存全量失效）。
STATS_VERSION = 1

#: 扫描值上限（超过 → 步进采样 + approximate=True；资源纪律 §六）。
MAX_SCAN_VALUES = 250_000
#: 直方图 bin 上限（§六：64–128 → 取 96）。
MAX_HISTOGRAM_BINS = 96
#: 类别 top-N 上限（类别表分页由前端负责）。
MAX_CATEGORY_TOP = 60
#: distinct 扫描帽（超过即返回 None=未知，不伪造）。
MAX_DISTINCT_SCAN = 20_000

_INVALID_KINDS = ("bool", "string", "nan", "inf", "null", "missing")

#: 原始类型 → legend_spec 类别键的类型标记（paint match 类型化用）。
def key_kind_of(raw: Any) -> str:
    if isinstance(raw, bool):
        return "bool"
    if isinstance(raw, (int, float)):
        return "number"
    return "string"


def assign_class(value: float, breaks: Sequence[float]) -> int:
    """统一分类求值器：step 语义（``_graduated_to_step`` / MapLibre step）。

    类 i = [breaks[i], breaks[i+1])；阈值只有 breaks[1..n-1]（末断点是
    图例上界，**不是** step 阈值——``_graduated_to_step`` 的 stops 逐字
    如此）；类 0 吸收 (-∞, breaks[1])，类 n-1 吸收 [breaks[-2], +∞)。
    恰等于断点的值归**上**类。
    """
    cls = 0
    for b in breaks[1:-1]:
        if value >= b:
            cls += 1
        else:
            break
    return cls


def _classify_invalid(raw: Any) -> Optional[str]:
    """原始属性值 → 数值口径无效分型（None = 有效数值）。零是有效值。"""
    if raw is None:
        return "null"
    if isinstance(raw, bool):
        return "bool"
    if isinstance(raw, (int, float)):
        f = float(raw)
        if math.isnan(f):
            return "nan"
        if math.isinf(f):
            return "inf"
        return None
    return "string"


@dataclass
class FieldScan:
    """一次有界扫描的结果（统计总体锚定 ref 的 content_revision）。"""

    field: str
    total_features: int
    scanned: int                     # 实际见到的数值属性值个数（采样前）
    approximated: bool               # True = 步进采样后的近似统计
    values: List[float]              # 有限数值样本（≤ MAX_SCAN_VALUES）
    invalid: Dict[str, int]          # 数值口径分型无效计数
    category_counts: Counter         # 规范化字符串键 → 计数（str/bool/数值）
    key_kinds: Dict[str, str]        # 文本键 → 主导原始类型（类型化 match 用）
    ambiguous_keys: List[str]        # 同一文本键来自混合类型的键（≤64，作者须消除歧义）
    distinct_capped: bool            # distinct 计数是否越过扫描帽

    @property
    def sample_size(self) -> int:
        return len(self.values)


def scan_field(features: Iterable[Any], field: str) -> FieldScan:
    """对 FeatureCollection 属性做一次有界扫描（数值 + 类别双口径）。

    - total_features 只数 dict 型要素（与 build_graduated_spec 同容错面）；
    - 数值超过 MAX_SCAN_VALUES → 均匀步进采样（确定性），``approximated=True``；
    - 类别键 = str(原始值)；NaN/Inf 不进类别口径（数值垃圾，非类别）。
      数值 1 与文本 "1" 的文本键相同但原始类型不同 → 记入 ambiguous_keys，
      作者计划必须显式消除歧义（拒绝或统一），不得偷偷合并后隐瞒。
    """
    total = 0
    raw_values: List[Any] = []
    invalid: Dict[str, int] = {k: 0 for k in _INVALID_KINDS}
    cat_counts: Counter = Counter()
    key_kinds: Dict[str, str] = {}
    key_kind_sets: Dict[str, set] = {}
    ambiguous: List[str] = []
    distinct_capped = False
    for f in features:
        if not isinstance(f, dict):
            continue
        total += 1
        props = f.get("properties") or {}
        present = isinstance(props, dict) and field in props
        raw = props.get(field) if present else None
        if not present:
            invalid["missing"] += 1
        elif raw is None:
            invalid["null"] += 1
        if present and raw is not None:
            num_kind = _classify_invalid(raw)
            if num_kind is not None:
                if num_kind in ("bool", "string"):
                    invalid[num_kind] += 1
                else:
                    invalid[num_kind] += 1  # nan / inf
            else:
                raw_values.append(float(raw))
            # 类别口径：NaN/Inf 排除（数值垃圾），其余非 null 值计入
            if num_kind not in ("nan", "inf"):
                key = str(raw)
                cat_counts[key] += 1
                kind = key_kind_of(raw)
                if key not in key_kind_sets:
                    key_kind_sets[key] = {kind}
                    key_kinds[key] = kind
                    if len(key_kind_sets) > MAX_DISTINCT_SCAN:
                        distinct_capped = True
                elif kind not in key_kind_sets[key]:
                    key_kind_sets[key].add(kind)
                    # 评审 P3-1：cap 5 → 64（作者拒绝面覆盖到实际类表规模；
                    # 仍受 MAX_DISTINCT_SCAN 扫描帽约束，回执有界）。
                    if key not in ambiguous and len(ambiguous) < 64:
                        ambiguous.append(key)

    approximated = len(raw_values) > MAX_SCAN_VALUES
    if approximated:
        step = len(raw_values) / float(MAX_SCAN_VALUES)
        values = [raw_values[int(i * step)] for i in range(MAX_SCAN_VALUES)]
    else:
        values = raw_values
    return FieldScan(
        field=field,
        total_features=total,
        scanned=len(raw_values),
        approximated=approximated,
        values=values,
        invalid=invalid,
        category_counts=cat_counts,
        key_kinds=key_kinds,
        ambiguous_keys=ambiguous,
        distinct_capped=distinct_capped,
    )


def histogram(values: Sequence[float], bins: int = MAX_HISTOGRAM_BINS) -> Dict[str, List[Any]]:
    """有限数值直方图（bin 数有界；常数域单 bin；空值空表）。"""
    if not values:
        return {"bin_edges": [], "counts": []}
    lo, hi = min(values), max(values)
    n = max(1, min(bins, len(values)))
    if hi <= lo:
        return {"bin_edges": [lo, hi], "counts": [len(values)]}
    width = (hi - lo) / n
    counts = [0] * n
    for v in values:
        idx = int((v - lo) / width)
        if idx >= n:
            idx = n - 1
        counts[idx] += 1
    edges = [lo + i * width for i in range(n + 1)]
    edges[-1] = hi
    return {"bin_edges": edges, "counts": counts}


def field_distribution(scan: FieldScan) -> Dict[str, Any]:
    """FieldScan → 有界回执（数值 + 类别 + 口径披露）。"""
    vals = scan.values
    invalid_total = sum(scan.invalid.values())
    numeric: Dict[str, Any] = {
        "valid": scan.scanned,
        "invalid": dict(scan.invalid),
        "invalid_total": invalid_total,
        "zero": sum(1 for v in vals if v == 0.0),
    }
    if vals:
        numeric["min"] = min(vals)
        numeric["max"] = max(vals)
        numeric["histogram"] = histogram(vals)
    else:
        numeric["min"] = None
        numeric["max"] = None
        numeric["histogram"] = {"bin_edges": [], "counts": []}

    distinct: Optional[int]
    if scan.distinct_capped:
        distinct = None
    else:
        distinct = len(scan.category_counts)
    top = scan.category_counts.most_common(MAX_CATEGORY_TOP)
    listed = sum(c for _, c in top)
    categorical: Dict[str, Any] = {
        "distinct": distinct,
        "distinct_capped": scan.distinct_capped,
        "top": [{"key": k, "count": c, "key_type": scan.key_kinds.get(k, "string")} for k, c in top],
        "listed": len(top),
        "other_count": sum(scan.category_counts.values()) - listed,
        "ambiguous_keys": scan.ambiguous_keys,
    }
    return {
        "field": scan.field,
        "basis": {
            "mode": "sampled" if scan.approximated else "full_scan",
            "total_features": scan.total_features,
            "scanned_values": scan.scanned,
            "sample_size": scan.sample_size,
            "approximate": scan.approximated,
            "stats_version": STATS_VERSION,
        },
        "numeric": numeric,
        "categorical": categorical,
    }


def class_counts(
    scan: FieldScan, breaks: Sequence[float],
) -> Dict[str, Any]:
    """用统一求值器数类；同时披露越界（under/over）与无效值。

    under/over：值 < breaks[0] / > breaks[-1] 的个数。step 语义下它们分别
    画成首/末类颜色（现有合同），此处必须显式计数——不得静默落入普通类
    而不告知（§三.4 / 验收 7）。
    """
    counts = [0] * (len(breaks) - 1)
    under = 0
    over = 0
    for v in scan.values:
        if v < breaks[0]:
            under += 1
        elif v > breaks[-1]:
            over += 1
        counts[assign_class(v, breaks)] += 1
    return {
        "counts": counts,
        "under_domain": under,
        "over_domain": over,
        "valid": scan.sample_size,
        "invalid": sum(scan.invalid.values()),
        "total_features": scan.total_features,
        "approximate": scan.approximated,
    }


def category_match_counts(
    scan: FieldScan, categories: Sequence[Dict[str, str]],
) -> Dict[str, Any]:
    """类别表 → 每类计数 + other（未列出）计数 + null/missing 分母披露。"""
    listed = {str(c["key"]) for c in categories}
    per_key = {str(c["key"]): 0 for c in categories}
    other = 0
    for key, cnt in scan.category_counts.items():
        if key in listed:
            per_key[key] += cnt
        else:
            other += cnt
    return {
        "counts": [
            {"key": str(c["key"]), "count": per_key[str(c["key"])]}
            for c in categories
        ],
        "other_count": other,
        "matched": sum(per_key.values()),
        "missing_or_null": scan.invalid["missing"] + scan.invalid["null"],
        "total_features": scan.total_features,
        "approximate": scan.approximated,
    }


# ─── 统计缓存（条目 + 字节双上限；content_revision 变化即失效） ─────────────


class _BoundedCache:
    """进程内 LRU：条目 ≤64、序列化字节 ≤2MB。键含源内容版本。"""

    def __init__(self, max_entries: int = 64, max_bytes: int = 2 * 1024 * 1024) -> None:
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._data: "OrderedDict[Tuple[str, int, str, int], Tuple[Dict[str, Any], int]]" = OrderedDict()
        self._bytes = 0
        self.hits = 0
        self.misses = 0

    def get(self, key: Tuple[str, int, str, int]) -> Optional[Dict[str, Any]]:
        entry = self._data.get(key)
        if entry is None:
            self.misses += 1
            return None
        self._data.move_to_end(key)
        self.hits += 1
        return entry[0]

    def put(self, key: Tuple[str, int, str, int], value: Dict[str, Any]) -> None:
        try:
            size = len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))
        except (TypeError, ValueError):
            size = 4_096
        if size > self._max_bytes:
            return
        if key in self._data:
            self._bytes -= self._data[key][1]
            del self._data[key]
        self._data[key] = (value, size)
        self._bytes += size
        while (len(self._data) > self._max_entries or self._bytes > self._max_bytes) and self._data:
            oldest = next(iter(self._data))
            self._bytes -= self._data[oldest][1]
            del self._data[oldest]

    def clear(self) -> None:
        self._data.clear()
        self._bytes = 0


#: 模块级共享缓存（键 = (ref_id, content_revision, field, STATS_VERSION)；
#: 值 = 有界分布回执。原始值样本不缓存——每次预览重新有界扫描，重型分类
#: 遵守公共锁与单任务预算）。
_stats_cache = _BoundedCache()


def cached_distribution(
    ref_id: str, content_revision: int, field: str, features: Iterable[Any],
) -> Dict[str, Any]:
    """带缓存的分布回执（同源同版本同字段只扫一次）。"""
    key = (ref_id, content_revision, field, STATS_VERSION)
    hit = _stats_cache.get(key)
    if hit is not None:
        return hit
    dist = field_distribution(scan_field(features, field))
    _stats_cache.put(key, dist)
    return dist


def cache_stats() -> Dict[str, int]:
    return {
        "entries": len(_stats_cache._data),
        "bytes": _stats_cache._bytes,
        "hits": _stats_cache.hits,
        "misses": _stats_cache.misses,
    }
