"""算法科学适用性的统一只读预检（G06 / CAP-03）。

本模块**不新建判定引擎**：它把既有权威面的结果收敛成一个机器可读的三态
投影 ——

* 五值 + deferred 前置条件：``scientific_preconditions`` 的
  ``evaluate_preconditions`` / ``combine_verdicts`` / ``pass_evidence_state``；
* 量纲/CRS 语义：``SpatialSemanticContract`` 的 vocabulary
  （``DISTANCE_UNIT_VOCABULARY`` / ``AREA_UNIT_VOCABULARY``）与
  ``ParameterContract`` 的 ``PARAM_UNIT_VOCABULARY``；
* 回退等价性：``AlgorithmDescriptor.fallback_semantics`` 的
  ``FallbackSemanticsClass`` 与 ``science_final_gate.NON_EQUIVALENT_SEMANTICS``；
* 方法学声明：``MethodUseContract`` / ``AlgorithmDescriptor``。

三条硬纪律
----------
1. **空间距离 ≠ 行程时间**：``seconds``/``s``/``min`` 与
   ``m``/``meters``/``km`` 出现在同一请求的空间语义里即
   ``ineligible`` + ``UNIT_DIMENSION_CONFLICT``，绝不把秒当米。
2. **经纬度 ≠ 投影坐标**：``crs_class=geographic`` 下要求线性距离/面积时
   ``ineligible`` + ``GEOGRAPHIC_LINEAR_DISTANCE``，并给出重投影提示。
3. **proxy 不是 equivalent**：``fallback_class`` 逐字取自算法声明，
   绝不把 ``proxy`` / ``approximation`` / ``degraded`` 抬升为等价。

``unknown != pass``：任一 deferred 前置条件都不得判 ``eligible``，
降级到 ``not_evaluated`` 并显式披露。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

ELIGIBILITY_VOCABULARY: tuple[str, ...] = (
    "eligible", "ineligible", "not_evaluated",
)
FALLBACK_CLASSES: tuple[str, ...] = (
    "equivalent", "approximation", "proxy", "degraded", "not_allowed",
)

# 稳定 reason codes（与既有 scientific_errors / dispatch 码面同源，不空挂）。
REASON_UNIT_DIMENSION_CONFLICT = "UNIT_DIMENSION_CONFLICT"
REASON_GEOGRAPHIC_LINEAR_DISTANCE = "GEOGRAPHIC_LINEAR_DISTANCE"
REASON_REQUIRES_TRANSFORM = "REQUIRES_TRANSFORM"
REASON_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
REASON_INVALID_METHOD = "INVALID_METHOD"
REASON_DEFERRED_EVIDENCE = "DEFERRED_EVIDENCE_NOT_EVALUATED"
REASON_PRECONDITION_FAILED = "SCIENTIFIC_PRECONDITION_FAILED"

#: 时间量纲词表（来自 PARAM_UNIT_VOCABULARY 与路网成本字段口径）。
#: F-06（独立冷审）：补齐常见时间单位/字段名，避免封闭词表漏检落到 eligible。
TIME_UNITS = frozenset({
    "seconds", "second", "s", "sec", "secs", "minutes", "minute", "min",
    "mins", "hours", "hour", "h", "hrs", "time", "travel_time",
    "duration_s", "duration", "traveltime", "time_s", "time_cost",
})
#: 线性距离/面积量纲词表。
LENGTH_UNITS = frozenset({
    "meters", "meter", "m", "km", "kilometers", "kilometer",
    "m2", "km2", "ha", "distance", "metres", "metre",
    "miles", "mile", "mi", "feet", "foot", "ft", "cm", "mm",
    "nautical_miles", "nm", "yd", "yards",
})
UNIT_KEYS = (
    "unit", "units", "distance_unit", "area_unit", "cost_unit",
    "output_unit", "numerator_unit", "denominator_unit",
    "requested_unit", "resolution_unit", "distance_units",
    "radius_units", "radius_unit", "length_unit", "speed_unit",
    "buffer_unit", "time_unit", "duration_unit",
)
CRS_KEYS = (
    "crs_class", "source_crs_class", "crs", "source_crs",
    "working_crs", "crs_type", "coordinate_system",
)
GEOGRAPHIC_CRS_MARKERS = (
    "geographic", "geodetic", "wgs84", "wgs 84", "epsg:4326", "4326",
    "crs:84", "lonlat", "longlat", "latitude_longitude", "degree",
    "degrees", "gcj02", "cgcs2000_geo",
)


@dataclass(frozen=True)
class MethodEligibility:
    """一次预检的只读结果。"""

    algorithm_id: str = ""
    verdict: str = "not_evaluated"
    reason_codes: tuple[str, ...] = ()
    backend_candidates: tuple[str, ...] = ()
    fallback_class: str = "not_allowed"
    transform_hint: str = ""
    disclosures: tuple[str, ...] = ()
    precondition_verdicts: tuple[tuple[str, str, bool], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "algorithm_id": self.algorithm_id,
            "verdict": self.verdict,
            "reason_codes": list(self.reason_codes),
            "backend_candidates": list(self.backend_candidates),
            "fallback_class": self.fallback_class,
            "transform_hint": self.transform_hint,
            "disclosures": list(self.disclosures),
            "precondition_verdicts": [list(v) for v in self.precondition_verdicts],
        }


def _collect_units(*sources: Optional[Mapping[str, Any]]) -> set[str]:
    """从 space/params 中收集所有显式单位声明（归一化为小写）。"""
    out: set[str] = set()
    for src in sources:
        if not isinstance(src, Mapping):
            continue
        for key in UNIT_KEYS:
            value = src.get(key)
            if isinstance(value, str) and value.strip():
                out.add(value.strip().lower())
        # 嵌套的 unit_contract（analysis envelope 口径）
        nested = src.get("unit_contract")
        if isinstance(nested, Mapping):
            for key in UNIT_KEYS:
                value = nested.get(key)
                if isinstance(value, str) and value.strip():
                    out.add(value.strip().lower())
    return out


def _crs_class(*sources: Optional[Mapping[str, Any]]) -> str:
    for src in sources:
        if not isinstance(src, Mapping):
            continue
        for key in CRS_KEYS:
            value = src.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
    return ""


def _requires_linear(params: Mapping[str, Any], space: Mapping[str, Any]) -> bool:
    """是否要求线性距离/面积语义（度不能当米算）。"""
    for src in (params, space):
        if not isinstance(src, Mapping):
            continue
        for key in (
            "requires_linear_distance", "requires_linear_area",
            "requires_metric_crs", "linear_distance_required",
            "operation",
        ):
            value = src.get(key)
            if value is True:
                return True
            if isinstance(value, str) and value.lower() in (
                "distance", "buffer", "linear_distance", "area",
                "zonal_statistics", "proximity", "euclidean",
            ):
                return True
    units = _collect_units(params, space)
    return bool(units & LENGTH_UNITS)


def _fallback_class_of(algo: Any) -> str:
    """逐字取算法声明的非等价语义，绝不抬升。"""
    semantics = dict(getattr(algo, "fallback_semantics", {}) or {})
    seen: list[str] = []
    for value in semantics.values():
        cls = str(getattr(value, "value", value) or "").lower()
        if cls in FALLBACK_CLASSES and cls not in seen:
            seen.append(cls)
    if not seen:
        return "not_allowed"
    # 有多个时取最保守（非等价优先）；proxy/degraded/approximation 都
    # 不得被等价语义覆盖 —— 这是 CAP-O07 的硬约束。
    for cls in ("proxy", "degraded", "approximation", "equivalent"):
        if cls in seen:
            return cls
    return seen[0] if seen[0] in FALLBACK_CLASSES else "not_allowed"


def preflight_method(
    algorithm_id: str,
    *,
    profile: Optional[dict[str, Any]] = None,
    space: Optional[dict[str, Any]] = None,
    params: Optional[dict[str, Any]] = None,
    allow_transform: bool = False,
) -> MethodEligibility:
    """对某个算法做科学适用性预检（纯只读，不产生副作用）。

    返回 ``eligible`` / ``ineligible`` / ``not_evaluated`` 三态、稳定理由码、
    可执行后端候选、以及**逐字来自算法声明**的 fallback 分类。
    """
    from app.lib.gis.algorithm_registry import get_algorithm_registry
    from app.lib.gis.method_use_contract import build_method_use_contract
    from app.lib.gis.science_final_gate import NON_EQUIVALENT_SEMANTICS
    from app.lib.gis.scientific_preconditions import (
        combine_verdicts, evaluate_preconditions, pass_evidence_state,
    )

    space = dict(space or {})
    params = dict(params or {})
    algo = get_algorithm_registry().get(algorithm_id)
    exists = algo is not None

    reason_codes: list[str] = []
    disclosures: list[str] = []
    backend_candidates: list[str] = []
    fallback_class = _fallback_class_of(algo) if algo is not None else "not_allowed"

    if algo is not None:
        for variant in (getattr(algo, "backend_variants", None) or ()):
            bid = str(getattr(variant, "id", "") or "")
            if bid:
                backend_candidates.append(bid)
        preferred = str(getattr(algo, "preferred_execution_policy", "") or "")
        if preferred and preferred not in backend_candidates:
            backend_candidates.append(preferred)
        for assumption in (getattr(algo, "assumptions", None) or ())[:3]:
            disclosures.append(f"assumption:{assumption}")
        for limitation in (getattr(algo, "limitations", None) or ())[:3]:
            disclosures.append(f"limitation:{limitation}")

    # 方法学契约（scientific_status 只读消费，不改写）。
    try:
        contract = build_method_use_contract(algorithm_id)
        status = str(getattr(contract, "scientific_status", "") or "")
        if status:
            disclosures.append(f"scientific_status:{status}")
    except Exception:  # noqa: BLE001 — 契约投影缺席诚实留白
        disclosures.append("method_use_contract:not_evaluated")

    # ── 前置条件（既有五值引擎，deferred 不冒充通过）────────────────
    preconds: list[tuple[str, str, bool]] = []
    combined = "PASS"
    deferred_any = False
    try:
        ids = list(getattr(algo, "scientific_preconditions", None) or ()) \
            if algo is not None else []
        if ids:
            results = evaluate_preconditions(ids, profile or {})
            combined = combine_verdicts(results)
            for r in results:
                state = pass_evidence_state(r)
                deferred = bool(getattr(r, "deferred", False)) or \
                    state == "unknown_deferred"
                deferred_any = deferred_any or deferred
                preconds.append((str(r.precondition_id), str(r.verdict), deferred))
    except Exception as exc:  # noqa: BLE001
        preconds.append(("precondition_engine", "NOT_EVALUATED", True))
        deferred_any = True
        disclosures.append(f"precondition_engine_error:{type(exc).__name__}")

    # ── 硬门 1：秒 / 米 绝不混用 ────────────────────────────────────
    units = _collect_units(space, params)
    has_time = bool(units & TIME_UNITS)
    has_length = bool(units & LENGTH_UNITS)
    unit_conflict = has_time and has_length

    # ── 硬门 2：经纬度不按米直接计算 ────────────────────────────────
    crs_class = _crs_class(space, params)
    geographic = any(m in crs_class for m in GEOGRAPHIC_CRS_MARKERS)
    linear_required = _requires_linear(params, space)
    geo_conflict = geographic and linear_required

    verdict = "not_evaluated"
    transform_hint = ""

    if unit_conflict:
        verdict = "ineligible"
        reason_codes.append(REASON_UNIT_DIMENSION_CONFLICT)
        disclosures.append(
            "unit_dimension: 时间量纲(seconds) 与长度量纲(m/meters) 混用；"
            "行程秒数不可当作空间距离")
        if fallback_class in NON_EQUIVALENT_SEMANTICS:
            disclosures.append(f"fallback:{fallback_class} 非等价，不得冒充 equivalent")
    elif geo_conflict:
        verdict = "ineligible"
        reason_codes.append(REASON_GEOGRAPHIC_LINEAR_DISTANCE)
        reason_codes.append(REASON_REQUIRES_TRANSFORM)
        transform_hint = (
            "先将经纬度要素重投影到合适的投影/局部米制 CRS"
            "（如 CGCS2000 / UTM / EPSG:3857），再执行线性距离或面积运算")
        disclosures.append(
            "crs: 经纬度坐标的角度量纲不可直接按米计算距离/面积")
    elif not exists:
        verdict = "ineligible"
        reason_codes.append(REASON_INVALID_METHOD)
        disclosures.append(f"algorithm:{algorithm_id} 未在 AlgorithmRegistry 注册")
    elif combined in ("INVALID_METHOD", "INSUFFICIENT_DATA"):
        verdict = "ineligible"
        reason_codes.append(REASON_PRECONDITION_FAILED)
        reason_codes.append(combined)
    elif deferred_any or combined == "REQUIRES_TRANSFORM":
        # F-05（独立冷审）：修掉 `or` / `and` 优先级 bug。
        # 本函数是纯只读预检，**不执行任何变换**；因此 REQUIRES_TRANSFORM
        # 在 allow_transform=True 时同样不得判 eligible（除非调用方真的
        # 变换后重新预检）。allow_transform 只控制提示措辞，不改变结论。
        verdict = "not_evaluated"
        reason_codes.append(REASON_DEFERRED_EVIDENCE)
        if combined == "REQUIRES_TRANSFORM":
            reason_codes.append(REASON_REQUIRES_TRANSFORM)
            transform_hint = (
                "重投影到方法声明要求的 CRS 类后**重新调用 preflight_method**"
                + ("（已请求 allow_transform，但本函数不执行变换）"
                   if allow_transform else "（allow_transform=False 不自动变换）"))
    else:
        verdict = "eligible"

    if fallback_class in NON_EQUIVALENT_SEMANTICS:
        disclosures.append(
            f"fallback:{fallback_class} 属非等价语义，"
            "不得标为 equivalent 或宣称科学等价")

    return MethodEligibility(
        algorithm_id=str(algorithm_id),
        verdict=verdict,
        reason_codes=tuple(dict.fromkeys(reason_codes)),
        backend_candidates=tuple(backend_candidates[:8]),
        fallback_class=fallback_class,
        transform_hint=transform_hint,
        disclosures=tuple(disclosures[:12]),
        precondition_verdicts=tuple(preconds),
    )


def eligibility_summary(results: Sequence[MethodEligibility]) -> dict[str, Any]:
    """批量预检的有界汇总（诊断/CLI 用）。"""
    counts = {v: 0 for v in ELIGIBILITY_VOCABULARY}
    for r in results:
        counts[r.verdict] = counts.get(r.verdict, 0) + 1
    reasons: dict[str, int] = {}
    for r in results:
        for code in r.reason_codes:
            reasons[code] = reasons.get(code, 0) + 1
    return {
        "count": len(results),
        "by_verdict": counts,
        "top_reasons": sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))[:16],
    }
