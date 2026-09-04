"""Scientific Preconditions —— 可复用科学前置条件库 + 判定引擎（VNext §11）。

设计：

- 每个前置条件是一个**命名谓词**，吃 resolver profile 里的有界事实
  （featureCount/geometryTypes/crs/crsClass/temporalObservationCount/
  bandCount/bandSemantics/numericFields/hasTimeField/valueVariance...），
  输出五值判定：
      PASS / PASS_WITH_WARNINGS / REQUIRES_TRANSFORM /
      INSUFFICIENT_DATA / INVALID_METHOD
- 事实缺失 → PASS（未知 ≠ 不满足；resolver 既有哲学）。事实存在且违反
  才判罚 —— 这样画像粗（descriptor 派生）的调用方零成本兼容；
- 支持参数化 id（``min_temporal_observations:8``）；
- descriptor 声明 ``scientific_preconditions=["..."]``，resolver 执行门，
  实现/工具侧同 id 复用（运行时防线用 scientific_errors 抛错）。

不在此层做的事：不加载 FeatureCollection、不算实际方差（除非画像携带
``valueVariance`` 事实）、不查网络连通性（那是 engine 运行时事实）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from app.lib.gis.crs_safety import classify_crs, recommend_metric_crs

PreconditionVerdict = str  # PASS | PASS_WITH_WARNINGS | REQUIRES_TRANSFORM | INSUFFICIENT_DATA | INVALID_METHOD

_MAX_MSG = 160


@dataclass
class PreconditionResult:
    precondition_id: str
    verdict: PreconditionVerdict
    message: str = ""
    transform_hint: str = ""
    facts_used: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.message = self.message[:_MAX_MSG]


# ── 事实读取助手（缺省安全）──────────────────────────────────────────
def _fact(profile: Dict[str, Any], key: str, *types: type) -> Optional[Any]:
    v = (profile or {}).get(key)
    if v is None:
        return None
    if types and not isinstance(v, types):
        return None
    return v


def _int_fact(profile: Dict[str, Any], key: str) -> Optional[int]:
    v = _fact(profile, key, int, float)
    if v is None or isinstance(v, bool):
        return None
    return int(v)


def _fields_known(profile: Dict[str, Any]) -> bool:
    return bool(isinstance((profile or {}).get("fields"), dict)
                and (profile or {}).get("fields"))


def _numeric_fields(profile: Dict[str, Any]) -> List[str]:
    v = _fact(profile, "numericFields", list)
    return [str(x) for x in v] if v else []


# ── 前置条件实现 ─────────────────────────────────────────────────────
def _check_numeric_field_required(profile: Dict[str, Any]) -> PreconditionResult:
    rid = "numeric_field_required"
    if not _fields_known(profile):
        return PreconditionResult(rid, "PASS", "fields unknown — deferred")
    numeric = _numeric_fields(profile)
    if numeric:
        return PreconditionResult(rid, "PASS", facts_used={"numericFields": len(numeric)})
    return PreconditionResult(
        rid, "INSUFFICIENT_DATA",
        "画像字段已知但无数值字段 —— 本方法需要至少一个数值属性")


def _check_nonzero_variance(profile: Dict[str, Any]) -> PreconditionResult:
    rid = "nonzero_variance_required"
    var = _fact(profile, "valueVariance", int, float)
    if var is None:
        return PreconditionResult(rid, "PASS", "variance unknown — deferred")
    if float(var) > 0.0:
        return PreconditionResult(rid, "PASS", facts_used={"valueVariance": float(var)})
    return PreconditionResult(
        rid, "INSUFFICIENT_DATA",
        "数值字段方差为 0（常量场）—— 统计量无意义",
        facts_used={"valueVariance": float(var)})


def _check_projected_crs(profile: Dict[str, Any]) -> PreconditionResult:
    rid = "projected_crs_required"
    crs = _fact(profile, "crs", str)
    if not crs:
        return PreconditionResult(rid, "PASS", "crs unknown — deferred")
    data_class = classify_crs(crs)
    if data_class in ("projected", "projected_local_metric"):
        return PreconditionResult(rid, "PASS", facts_used={"crsClass": data_class})
    bbox = _fact(profile, "bbox", list) or None
    hint = recommend_metric_crs(bbox)
    return PreconditionResult(
        rid, "REQUIRES_TRANSFORM",
        f"CRS {crs} 为地理坐标（度）—— 距离/面积方法需先投影",
        transform_hint=f"reproject to metric CRS ({hint})" if hint else "reproject to metric CRS",
        facts_used={"crsClass": data_class})


def _check_local_metric_crs(profile: Dict[str, Any]) -> PreconditionResult:
    rid = "local_metric_crs_required"
    crs = _fact(profile, "crs", str)
    if not crs:
        return PreconditionResult(rid, "PASS", "crs unknown — deferred")
    data_class = classify_crs(crs)
    if data_class == "projected_local_metric":
        return PreconditionResult(rid, "PASS", facts_used={"crsClass": data_class})
    bbox = _fact(profile, "bbox", list) or None
    hint = recommend_metric_crs(bbox)
    if data_class == "projected":
        return PreconditionResult(
            rid, "REQUIRES_TRANSFORM",
            f"CRS {crs} 是投影但非局部度量（如 Web Mercator 尺度失真）",
            transform_hint=f"reproject to {hint or 'local metric CRS (UTM)'}",
            facts_used={"crsClass": data_class})
    return PreconditionResult(
        rid, "REQUIRES_TRANSFORM",
        f"CRS {crs} 为地理坐标（度）—— 需局部度量投影",
        transform_hint=f"reproject to metric CRS ({hint})" if hint else "reproject to metric CRS",
        facts_used={"crsClass": data_class})


def _check_temporal_field_required(profile: Dict[str, Any]) -> PreconditionResult:
    rid = "temporal_field_required"
    has_time = _fact(profile, "hasTimeField", bool)
    if has_time is None:
        return PreconditionResult(rid, "PASS", "time field status unknown — deferred")
    if has_time:
        return PreconditionResult(rid, "PASS")
    return PreconditionResult(
        rid, "INSUFFICIENT_DATA", "数据无时间字段 —— 时序方法不适用")


def _check_min_temporal_observations(n_min: int, profile: Dict[str, Any]) -> PreconditionResult:
    rid = f"min_temporal_observations:{n_min}"
    count = _int_fact(profile, "temporalObservationCount")
    if count is None:
        return PreconditionResult(rid, "PASS", "observation count unknown — deferred")
    if count >= n_min:
        return PreconditionResult(rid, "PASS", facts_used={"temporalObservationCount": count})
    if count >= max(2, n_min // 2):
        return PreconditionResult(
            rid, "PASS_WITH_WARNINGS",
            f"时序观测数 {count} < 建议 {n_min} —— 趋势推断证据不足，只能描述性解读",
            facts_used={"temporalObservationCount": count})
    return PreconditionResult(
        rid, "INSUFFICIENT_DATA",
        f"时序观测数 {count} 过少（< {max(2, n_min // 2)}）—— 不能做趋势声明",
        facts_used={"temporalObservationCount": count})


def _make_min_temporal(n_min: int) -> Callable[[Dict[str, Any]], PreconditionResult]:
    def _check(profile: Dict[str, Any]) -> PreconditionResult:
        return _check_min_temporal_observations(n_min, profile)
    return _check


def _check_raster_band_required(n_bands: int, profile: Dict[str, Any]) -> PreconditionResult:
    rid = f"raster_band_required:{n_bands}"
    count = _int_fact(profile, "bandCount")
    if count is None:
        return PreconditionResult(rid, "PASS", "band count unknown — deferred")
    if count >= n_bands:
        return PreconditionResult(rid, "PASS", facts_used={"bandCount": count})
    return PreconditionResult(
        rid, "INSUFFICIENT_DATA",
        f"栅格波段数 {count} < 所需 {n_bands}",
        facts_used={"bandCount": count})


def _make_raster_band(n_bands: int) -> Callable[[Dict[str, Any]], PreconditionResult]:
    def _check(profile: Dict[str, Any]) -> PreconditionResult:
        return _check_raster_band_required(n_bands, profile)
    return _check


def _check_band_semantics_required(profile: Dict[str, Any]) -> PreconditionResult:
    rid = "band_semantics_required"
    roles = _fact(profile, "bandSemantics", list)
    if roles is None:
        return PreconditionResult(rid, "PASS", "band semantics unknown — deferred")
    if [r for r in roles if str(r)]:
        return PreconditionResult(rid, "PASS", facts_used={"bandSemantics": len(roles)})
    return PreconditionResult(
        rid, "REQUIRES_TRANSFORM",
        "波段语义角色未标注 —— 需显式 band_map 映射（拒绝按位置猜测）",
        transform_hint="declare band_map roles (red/nir/...)")


def _check_point_support_required(profile: Dict[str, Any]) -> PreconditionResult:
    rid = "point_support_required"
    types = _fact(profile, "geometryTypes", list)
    if not types:
        return PreconditionResult(rid, "PASS", "geometry unknown — deferred")
    families = {str(t) for t in types}
    if families & {"Point", "MultiPoint"}:
        return PreconditionResult(rid, "PASS", facts_used={"geometryTypes": sorted(families)})
    return PreconditionResult(
        rid, "INVALID_METHOD",
        f"几何族 {sorted(families)} 不是点支撑 —— 方法需要点过程支撑")


def _check_positive_weights_required(profile: Dict[str, Any]) -> PreconditionResult:
    rid = "positive_weights_required"
    has_negative = _fact(profile, "hasNegativeWeights", bool)
    if has_negative is None:
        return PreconditionResult(rid, "PASS", "weight signs unknown — deferred")
    if not has_negative:
        return PreconditionResult(rid, "PASS")
    return PreconditionResult(
        rid, "INVALID_METHOD", "权重含负值 —— 该方法要求非负权重")


def _check_min_numeric_samples(n_min: int, profile: Dict[str, Any]) -> PreconditionResult:
    rid = f"min_numeric_samples:{n_min}"
    count = _int_fact(profile, "numericSampleCount")
    if count is None:
        # featureCount 是上界代理（有数值字段时近似成立）
        count = _int_fact(profile, "featureCount")
    if count is None:
        return PreconditionResult(rid, "PASS", "sample count unknown — deferred")
    if count >= n_min:
        return PreconditionResult(rid, "PASS", facts_used={"sampleCount": count})
    if count >= max(2, int(n_min * 0.5)):
        return PreconditionResult(
            rid, "PASS_WITH_WARNINGS",
            f"有效样本 {count} < 建议 {n_min} —— 结果置信度有限",
            facts_used={"sampleCount": count})
    return PreconditionResult(
        rid, "INSUFFICIENT_DATA",
        f"有效样本 {count} < 方法学下限 {n_min}",
        facts_used={"sampleCount": count})


def _make_min_numeric(n_min: int) -> Callable[[Dict[str, Any]], PreconditionResult]:
    def _check(profile: Dict[str, Any]) -> PreconditionResult:
        return _check_min_numeric_samples(n_min, profile)
    return _check


# ── 注册表 ───────────────────────────────────────────────────────────
# 固定 id → checker；参数化 id（prefix:N）由 _resolve_checker 解析。
_PRECONDITIONS: Dict[str, Callable[[Dict[str, Any]], PreconditionResult]] = {
    "numeric_field_required": _check_numeric_field_required,
    "nonzero_variance_required": _check_nonzero_variance,
    "projected_crs_required": _check_projected_crs,
    "local_metric_crs_required": _check_local_metric_crs,
    "temporal_field_required": _check_temporal_field_required,
    "min_temporal_observations:4": _make_min_temporal(4),
    "min_temporal_observations:8": _make_min_temporal(8),
    "raster_band_required:1": _make_raster_band(1),
    "raster_band_required:2": _make_raster_band(2),
    "raster_band_required:5": _make_raster_band(5),
    "band_semantics_required": _check_band_semantics_required,
    "point_support_required": _check_point_support_required,
    "positive_weights_required": _check_positive_weights_required,
    "min_numeric_samples:8": _make_min_numeric(8),
    "min_numeric_samples:20": _make_min_numeric(20),
    "min_numeric_samples:30": _make_min_numeric(30),
}

_PARAMETRIC_PREFIXES = {
    "min_temporal_observations": (int, (2, 365)),
    "raster_band_required": (int, (1, 500)),
    "min_numeric_samples": (int, (2, 10_000_000)),
}

PRECONDITION_DESCRIPTIONS: Dict[str, str] = {
    "numeric_field_required": "画像字段已知时，至少一个数值字段",
    "nonzero_variance_required": "数值场方差 > 0（画像携带方差事实时）",
    "projected_crs_required": "CRS 已知时须为投影（度被拒 + 重投影建议）",
    "local_metric_crs_required": "局部度量投影（UTM/极方位；Web Mercator 被拒）",
    "temporal_field_required": "时间字段存在（画像已知时）",
    "min_temporal_observations:N": "时序观测数 ≥ N（软警告带）",
    "raster_band_required:N": "栅格波段数 ≥ N",
    "band_semantics_required": "波段语义角色已标注（拒绝位置猜测）",
    "point_support_required": "几何为点支撑",
    "positive_weights_required": "权重非负（画像已知时）",
    "min_numeric_samples:N": "有效数值样本 ≥ N（软警告带）",
}


def _resolve_checker(precondition_id: str) -> Optional[Callable[[Dict[str, Any]], PreconditionResult]]:
    if precondition_id in _PRECONDITIONS:
        return _PRECONDITIONS[precondition_id]
    prefix = precondition_id.split(":", 1)[0]
    if prefix in _PARAMETRIC_PREFIXES:
        type_factory, (lo, hi) = _PARAMETRIC_PREFIXES[prefix]
        value_part = precondition_id.split(":", 1)[1] if ":" in precondition_id else ""
        try:
            n = type_factory(value_part)  # type: ignore[call-arg]
        except (TypeError, ValueError):
            return None
        if not (lo <= n <= hi):
            return None
        if prefix == "min_temporal_observations":
            return _make_min_temporal(n)
        if prefix == "raster_band_required":
            return _make_raster_band(n)
        if prefix == "min_numeric_samples":
            return _make_min_numeric(n)
    return None


def precondition_exists(precondition_id: str) -> bool:
    return _resolve_checker(precondition_id) is not None


def evaluate_precondition(
    precondition_id: str, profile: Optional[Dict[str, Any]],
) -> PreconditionResult:
    checker = _resolve_checker(precondition_id)
    if checker is None:
        return PreconditionResult(
            precondition_id, "INVALID_METHOD",
            f"unknown precondition id: {precondition_id}")
    return checker(profile or {})


def evaluate_preconditions(
    precondition_ids: List[str], profile: Optional[Dict[str, Any]],
) -> List[PreconditionResult]:
    return [evaluate_precondition(pid, profile) for pid in precondition_ids]


def combine_verdicts(results: List[PreconditionResult]) -> PreconditionVerdict:
    """多条件聚合：最严判决胜（INVALID_METHOD > INSUFFICIENT_DATA >
    REQUIRES_TRANSFORM > PASS_WITH_WARNINGS > PASS）。"""
    order = {
        "INVALID_METHOD": 4, "INSUFFICIENT_DATA": 3, "REQUIRES_TRANSFORM": 2,
        "PASS_WITH_WARNINGS": 1, "PASS": 0,
    }
    worst = "PASS"
    for r in results:
        if order.get(r.verdict, 0) > order[worst]:
            worst = r.verdict
    return worst
