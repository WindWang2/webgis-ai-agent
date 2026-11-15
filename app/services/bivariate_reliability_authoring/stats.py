"""G41 完整范围、同版本成对统计（WP03 / B08 / B09 / B10 / B16）。

统计总体 = ``FrozenSource`` 的**同一冻结代次全量要素**。绝不把 MVT 可见瓦片、
50 条选择样本或当前屏幕当总体；绝不两轴各抽一份样本再拼成点对；有界散点/
直方图只用于检查配对与分布并如实披露 ``approximate``，要素分类与联合计数
始终对已声明总体精确计算。

数值准入是**显式严格**的：只接受有限 int/float（bool 排除）。bool / 数字
字符串 / 其它字符串 / NaN / Inf / null / 字段缺失都分型计数并拒绝参与，
不 ``float()`` 静默接纳。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.services.bivariate_reliability_authoring.contract import (
    ALGORITHM_VERSION,
    MAX_HISTOGRAM_BINS,
    MAX_SCATTER_POINTS,
    BivariateSpec,
    ReliabilitySpec,
)
from app.services.bivariate_reliability_authoring.errors import AuthoringError

#: 分型词表（closed vocabulary；计数对账用）。
VALUE_KINDS = (
    "number", "bool", "numeric_string", "string", "nan", "inf",
    "null", "missing",
)
_MISSING = object()


def value_kind(value: Any) -> str:
    """不修改数据的显式分型。``_MISSING`` 表示字段不存在。"""
    if value is _MISSING:
        return "missing"
    if value is None:
        return "null"
    if type(value) is bool:
        return "bool"
    if isinstance(value, (int, float)):
        f = float(value)
        if math.isnan(f):
            return "nan"
        if math.isinf(f):
            return "inf"
        return "number"
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return "string"
        try:
            f = float(s)
        except ValueError:
            return "string"
        return "numeric_string"
    return "string"


def admit(value: Any) -> Tuple[bool, Optional[float], str]:
    """严格准入：只有有限实数进入统计。返回 (ok, number, kind)。"""
    kind = value_kind(value)
    if kind != "number":
        return False, None, kind
    f = float(value)
    return (True, f, kind) if math.isfinite(f) else (False, None, kind)


def _bounded_histogram(values: List[float], bins: int) -> Dict[str, Any]:
    if not values:
        return {"bins": [], "min": None, "max": None, "approximate": False}
    lo, hi = min(values), max(values)
    n = max(2, min(int(bins), MAX_HISTOGRAM_BINS))
    if hi <= lo:
        return {
            "bins": [{"lo": lo, "hi": hi, "count": len(values)}],
            "min": lo, "max": hi, "approximate": False,
        }
    step = (hi - lo) / n
    counts = [0] * n
    for v in values:
        idx = min(n - 1, int((v - lo) / step))
        counts[idx] += 1
    return {
        "bins": [
            {"lo": round(lo + step * i, 6), "hi": round(lo + step * (i + 1), 6),
             "count": counts[i]}
            for i in range(n)
        ],
        "min": lo, "max": hi, "approximate": False,
    }


def _bounded_sample(pairs: List[Tuple[float, float]], size: int) -> Dict[str, Any]:
    """有界、确定性、均匀步进的散点样本（只用于目视检查配对/分布）。"""
    total = len(pairs)
    if size <= 0 or total == 0:
        return {"points": [], "sample_size": 0, "total": total,
                "approximate": False, "method": "bounded_even_step"}
    take = min(size, MAX_SCATTER_POINTS, total)
    if take >= total:
        idxs = list(range(total))
    else:
        idxs = [min(total - 1, int(i * (total - 1) / max(1, take - 1)))
                for i in range(take)]
    return {
        "points": [{"a": pairs[i][0], "b": pairs[i][1]} for i in idxs],
        "sample_size": len(idxs),
        "total": total,
        "approximate": len(idxs) < total,
        "method": "bounded_even_step",
        "disclosure": (
            "散点为有界均匀步进样本，仅用于检查配对与分布；"
            "联合格子计数与要素分类来自完整冻结总体，不是抽样放大"
        ),
    }


@dataclass
class PairedScan:
    """一次冻结代次上的成对统计结果（精确计数 + 有界可视化样本）。"""

    total_features: int
    indices: List[int] = field(default_factory=list)
    values_a: List[float] = field(default_factory=list)
    values_b: List[float] = field(default_factory=list)
    valid_pairs: int = 0
    only_a_missing: int = 0
    only_b_missing: int = 0
    both_missing: int = 0
    kind_counts_a: Dict[str, int] = field(default_factory=dict)
    kind_counts_b: Dict[str, int] = field(default_factory=dict)

    def basis(self) -> Dict[str, Any]:
        """统计口径（回执/图例同源披露）。"""
        return {
            "mode": "full_freeze_exact",
            "algorithm_version": ALGORITHM_VERSION,
            "total_features": self.total_features,
            "valid_pairs": self.valid_pairs,
            "only_a_missing": self.only_a_missing,
            "only_b_missing": self.only_b_missing,
            "both_missing": self.both_missing,
            "approximate": False,
            "disclosure": (
                "成对统计基于同一冻结代次的完整授权源；valid_pairs 是两轴同时"
                "有效的**成对**总体，不等于两轴各自 non-null 计数的合并"
            ),
        }

    def reconcile(self) -> Dict[str, Any]:
        """分型对账：总数 = 有效对 + 仅A缺失 + 仅B缺失 + 双缺失。"""
        accounted = (self.valid_pairs + self.only_a_missing
                     + self.only_b_missing + self.both_missing)
        return {
            "total_features": self.total_features,
            "accounted": accounted,
            "balanced": accounted == self.total_features,
            "kind_counts_a": dict(self.kind_counts_a),
            "kind_counts_b": dict(self.kind_counts_b),
        }


def scan_pairs(
    features: List[Dict[str, Any]],
    spec: BivariateSpec,
) -> PairedScan:
    """单遍全量成对扫描（不在写锁内做 O(n) 之外的工作）。"""
    scan = PairedScan(total_features=len(features))
    fa, fb = spec.axis_a.field, spec.axis_b.field
    for idx, feat in enumerate(features):
        props = feat.get("properties")
        if not isinstance(props, dict):
            props = {}
        oka, va, ka = admit(props.get(fa, _MISSING))
        okb, vb, kb = admit(props.get(fb, _MISSING))
        scan.kind_counts_a[ka] = scan.kind_counts_a.get(ka, 0) + 1
        scan.kind_counts_b[kb] = scan.kind_counts_b.get(kb, 0) + 1
        if oka and okb:
            scan.indices.append(idx)
            scan.values_a.append(float(va))
            scan.values_b.append(float(vb))
            scan.valid_pairs += 1
        elif oka and not okb:
            scan.only_b_missing += 1
        elif okb and not oka:
            scan.only_a_missing += 1
        else:
            scan.both_missing += 1
    recon = scan.reconcile()
    if not recon["balanced"]:
        raise AuthoringError(
            "PAIR_RECONCILIATION_FAILED",
            f"成对分型对账不平：{recon}", status_code=500)
    return scan


def paired_visuals(scan: PairedScan, spec: BivariateSpec) -> Dict[str, Any]:
    """有界直方图 + 散点（明确标注近似口径）。"""
    return {
        "histogram_a": _bounded_histogram(scan.values_a, spec.histogram_bins),
        "histogram_b": _bounded_histogram(scan.values_b, spec.histogram_bins),
        "scatter": _bounded_sample(
            list(zip(scan.values_a, scan.values_b)), spec.scatter_sample_size),
    }


@dataclass
class ReliabilityScan:
    """逐要素误差量 / 状态（估计与误差是同一冻结代次、同一要素）。"""

    total_features: int
    magnitudes: List[Optional[float]] = field(default_factory=list)
    states: List[str] = field(default_factory=list)  # "valid" | "unknown" | "out_of_domain"
    estimate_values: List[Optional[float]] = field(default_factory=list)
    valid_estimate: int = 0
    valid_measure: int = 0
    unknown_measure: int = 0
    invalid_measure: int = 0
    clipped: int = 0
    zero_measure: int = 0
    #: 非居中/不含估计值的区间条数。作为**计数**披露一次，不逐要素追加文案：
    #: 逐要素写字符串会让 legend_spec 随要素数线性膨胀（25 万要素 → 十几 MB）。
    noncentered_measure: int = 0
    kind_counts: Dict[str, int] = field(default_factory=dict)
    diagnostics: List[str] = field(default_factory=list)

    def basis(self) -> Dict[str, Any]:
        return {
            "mode": "full_freeze_exact",
            "algorithm_version": ALGORITHM_VERSION,
            "total_features": self.total_features,
            "valid_estimate": self.valid_estimate,
            "valid_measure": self.valid_measure,
            "unknown_measure": self.unknown_measure,
            "invalid_measure": self.invalid_measure,
            "clipped": self.clipped,
            "zero_measure": self.zero_measure,
            "noncentered_measure": self.noncentered_measure,
            "approximate": False,
        }


def _measure_magnitude(
    props: Dict[str, Any], spec: ReliabilitySpec,
) -> Tuple[Optional[float], str, str, Dict[str, Any]]:
    """返回 (magnitude, state, kind, extra)。

    * SE：``value`` 即量值，必须有限且 ≥ 0（零是有效值，不用 truthiness）。
    * variance：必须有限且 ≥ 0；单位平方，不自动开方成 SD。
    * CI/PI：``high - low`` 是宽度；low>high 拒绝（不自动排序）；不强制
      区间包含 estimate（由 ``require_interval_contains_estimate`` 控制）；
      非居中区间默认允许，``allow_noncentered=False`` 时按来源模型合同判无效。
    """
    m = spec.measure
    extra: Dict[str, Any] = {}
    if m.kind in ("confidence_interval", "prediction_interval"):
        lo_raw = props.get(m.low_field, _MISSING)
        hi_raw = props.get(m.high_field, _MISSING)
        ok_lo, lo, klo = admit(lo_raw)
        ok_hi, hi, khi = admit(hi_raw)
        kind = klo if not ok_lo else khi
        if not (ok_lo and ok_hi):
            return None, "unknown", kind, extra
        lo, hi = float(lo), float(hi)
        if lo > hi:
            return None, "invalid", "low_gt_high", extra
        extra["low"] = lo
        extra["high"] = hi
        extra["width"] = hi - lo
        est_raw = props.get(spec.estimate_field, _MISSING)
        ok_est, est, _ = admit(est_raw)
        if m.require_interval_contains_estimate:
            if not ok_est or not (lo <= float(est) <= hi):
                return None, "invalid", "estimate_outside_interval", extra
        noncentered = bool(
            ok_est and abs((lo + hi) / 2.0 - float(est)) > 1e-9)
        if noncentered:
            extra["noncentered"] = True
            # allow_noncentered=False 是用户对来源模型合同的显式要求：
            # 非对称/不以估计值为中心的区间必须判为无效，不得静默接受。
            if not m.allow_noncentered:
                return None, "invalid", "interval_not_centered", extra
        return (hi - lo), "valid", "number", extra
    raw = props.get(m.value_field, _MISSING)
    ok, value, kind = admit(raw)
    if not ok:
        return None, "unknown", kind, extra
    value = float(value)
    if value < 0:
        # 负方差/负标准误不是「稍微不可靠」，是无效统计证据。
        return None, "invalid", "negative_measure", extra
    extra["measure_value"] = value
    return value, "valid", "number", extra
