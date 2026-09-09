"""Method Qualification Engine V2 —— 数据事实 → 方法资格统一报告
（Epic 11 §5.D）。

对 V4 资格引擎（``workflow_v4.methodology.qualify_method_candidates``）
的**增强适配层**（不重写、不建第二裁决路径）：

- V4 硬准则（geometry/sample/roles/precondition + 软排序）保持权威；
  本引擎在其引用的同一些事实上补充 V4 未覆盖的维度：crs_scale
  （R1-F1：唯一事实源 = ``AlgorithmDescriptor.crs_class`` ×
  ``crs_safety.classify_crs``）、measure_semantics（R1-F4：categorical
  量测 × ``assumes_continuous_measure`` 方法 → 拒绝）、nodata_quality；
- 四态语义与 V4/data_qualification 同构：
  ``pass / unknown / transform / fail``——**unknown ≠ 不满足**；
- 科学性检查全部委托既有 oracle：
  * ``scientific_preconditions.evaluate_precondition``（前置条件）
  * ``crs_safety.crs_class_allows``（CRS 兼容，resolver 同款谓词）
  * ``AlgorithmDescriptor`` 声明（crs_class/geometry/min_sample）
  不重复实现任何科学语义；
- 输出统一报告：viable / degraded / rejected + 维度四态 + 稳定拒绝码
  （``QUAL_<DIM>_<VERDICT>``）+ 缺失需求 + 建议预处理（⊆
  ``data_qualification.REMEDIATION_OPS``）+ 置信度（有事实维度占比）；
- 零 LLM、零 I/O、确定性（同输入同报告）。

Epic §5.D 禁止项的机器可读落点（case corpus 锁定）：
无点数据选 KDE → geometry fail；categorical × 连续假设 →
measure_semantics fail；地理 CRS × LOCAL_METRIC_REQUIRED → transform
（非静默、非拒绝——修复链显式化）；tiny sample hotspot → sample_size
fail（与 V4 min_sample_size 同源）；raw-count 归一化场景 → 由
density.admin_rate 的 data_roles fail（分母缺失）表达。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from app.lib.gis.methodology.descriptors import (
    QUALIFICATION_DIMENSIONS,
    get_method_descriptor_registry,
)

#: 资格状态（报告级；rejected 对齐 V4 rejected 语义）。
QUAL_METHOD_STATUSES = ("viable", "degraded", "rejected")

#: 维度四态（与 V4 PRECONDITION_STATES 同构；unknown ≠ 不满足）。
DIMENSION_STATES = ("pass", "unknown", "transform", "fail")

#: 建议预处理操作词表（引用 data_qualification 单一事实源）。
def _remediation_ops() -> Tuple[str, ...]:
    from app.services.gis_harness.data_qualification import REMEDIATION_OPS
    return REMEDIATION_OPS


#: null 比例退化阈值（与 data_qualification._HIGH_NULL_RATIO 同口径；
#: parity test 锁定不漂移）。
_HIGH_NULL_RATIO = 0.5

#: 四态 → 报告级映射（确定性收敛规则）。
#> fail → rejected；transform/degrade 触发 → degraded；其余 → viable。


class DimensionState(BaseModel):
    """一个资格维度的四态裁决 + 有界证据。"""
    dimension: str                 # ⊆ QUALIFICATION_DIMENSIONS
    state: str = "unknown"         # ⊆ DIMENSION_STATES
    evidence: Dict[str, Any] = Field(default_factory=dict)
    #: structural pass = 「本方法无此类要求」的结构性满足（非事实支撑）；
    #: 报告级 unknown 判定只认事实支撑的 pass（防零事实虚标 viable）。
    structural: bool = False

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "dimension": self.dimension[:24],
            "state": self.state,
            "evidence": {
                str(k)[:24]: v for k, v in
                list(self.evidence.items())[:4]
            },
        }


class MethodQualificationReport(BaseModel):
    """一个方法的统一资格报告（可解释、可序列化、有界）。"""

    method_id: str
    family_id: str = ""
    status: str = "unknown"            # ⊆ QUAL_METHOD_STATUSES；全 unknown 时不虚标
    dimension_states: List[DimensionState] = Field(default_factory=list)
    reason_codes: List[str] = Field(default_factory=list)
    missing_requirements: List[str] = Field(default_factory=list)
    recommended_preprocessing: List[str] = Field(default_factory=list)
    disclosures: List[str] = Field(default_factory=list)
    confidence: float = Field(0.0, ge=0.0, le=1.0)

    @property
    def has_facts(self) -> bool:
        return any(d.state != "unknown" for d in self.dimension_states)

    def dimension(self, name: str) -> Optional[DimensionState]:
        return next((d for d in self.dimension_states if d.dimension == name),
                    None)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "method_id": self.method_id[:64],
            "family_id": self.family_id[:40],
            "status": self.status,
            "dimensions": [d.to_bounded_dict()
                           for d in self.dimension_states[:8]],
            "reason_codes": [c[:72] for c in self.reason_codes[:8]],
            "missing_requirements": [m[:120]
                                     for m in self.missing_requirements[:4]],
            "recommended_preprocessing": [p[:24] for p in
                                          self.recommended_preprocessing[:4]],
            "disclosures": [x[:160] for x in self.disclosures[:4]],
            "confidence": round(self.confidence, 2),
        }


class QualificationFacts(BaseModel):
    """裁决输入事实包（profile 为 resolver camelCase 形态）。"""

    profile: Dict[str, Any] = Field(default_factory=dict)
    #: 角色解析状态（role → eligible/transform_required/degraded/blocked/unknown）
    role_states: Dict[str, str] = Field(default_factory=dict)
    #: 量测字段语义（categorical/continuous/unknown；显式声明优先于画像推断）
    measure_kind: str = "unknown"

    @classmethod
    def from_profile(
        cls,
        profile: Optional[Dict[str, Any]],
        *,
        role_states: Optional[Dict[str, str]] = None,
        measure_kind: str = "unknown",
    ) -> "QualificationFacts":
        return cls(
            profile=dict(profile or {}),
            role_states=dict(role_states or {}),
            measure_kind=measure_kind,
        )


# ── 维度裁决（每维一个纯函数；事实缺席 → unknown）────────────────────────

def _profile_fact(profile: Dict[str, Any], key: str) -> Any:
    return profile.get(key)


def _geometry_fact(profile: Dict[str, Any]) -> str:
    kinds = _profile_fact(profile, "geometryKinds")
    if isinstance(kinds, list) and kinds:
        return str(kinds[0])
    gts = _profile_fact(profile, "geometryTypes")
    if isinstance(gts, list) and gts:
        from app.services.gis_harness.data_qualification import geometry_category
        return geometry_category([str(g) for g in gts])
    return "unknown"


def _sample_fact(profile: Dict[str, Any]) -> Optional[int]:
    n = _profile_fact(profile, "featureCount")
    if isinstance(n, (int, float)):
        return int(n)
    return None


def _crs_fact(profile: Dict[str, Any]) -> str:
    crs = _profile_fact(profile, "crs")
    return str(crs) if crs else ""


def _measure_kind_fact(facts: QualificationFacts) -> str:
    if facts.measure_kind in ("categorical", "continuous"):
        return facts.measure_kind
    # 画像推断：measure/数值字段全为非数值类型 → categorical 倾向
    fields = _profile_fact(facts.profile, "fields")
    if isinstance(fields, dict) and fields:
        measure_like = [v for k, v in fields.items()
                        if str(k).lower() in
                        ("measure", "value", "count", "rate", "price")]
        target = measure_like or list(fields.values())
        types = {str((v or {}).get("type", "")).lower()
                 for v in target if isinstance(v, dict)}
        if types and types <= {"string", "bool", "boolean", "category"}:
            return "categorical"
        if types & {"number", "integer", "int", "float", "double"}:
            return "continuous"
    return "unknown"


def _adjudicate_geometry(candidate: Any, profile: Dict[str, Any]) -> DimensionState:
    geometry = _geometry_fact(profile)
    evidence: Dict[str, Any] = {}
    if candidate.geometry_kinds and geometry != "unknown":
        evidence["geometry"] = geometry
        if geometry not in candidate.geometry_kinds:
            return DimensionState(
                dimension="geometry", state="fail",
                evidence={**evidence,
                          "required": list(candidate.geometry_kinds[:4])})
        return DimensionState(dimension="geometry", state="pass",
                              evidence=evidence)
    if not candidate.geometry_kinds:
        return DimensionState(dimension="geometry", state="pass",
                              structural=True)
    return DimensionState(dimension="geometry", state="unknown",
                          evidence={"declared": list(candidate.geometry_kinds[:4])})


def _adjudicate_sample(candidate: Any, profile: Dict[str, Any]) -> DimensionState:
    sample = _sample_fact(profile)
    if candidate.min_sample_size is None:
        return DimensionState(dimension="sample_size", state="pass",
                              structural=True)
    if sample is None:
        return DimensionState(
            dimension="sample_size", state="unknown",
            evidence={"min_declared": candidate.min_sample_size})
    if sample < candidate.min_sample_size:
        return DimensionState(
            dimension="sample_size", state="fail",
            evidence={"sample": sample, "min": candidate.min_sample_size})
    return DimensionState(dimension="sample_size", state="pass",
                          evidence={"sample": sample})


def _strictest_crs_class(algorithm_ids: Sequence[str]) -> str:
    """方法引用算法的 crs_class 并合（任一要求严格即取严格者）。"""
    from app.lib.gis.algorithm_registry import get_algorithm_registry
    reg = get_algorithm_registry()
    order = {"LOCAL_METRIC_REQUIRED": 3, "PROJECTED_REQUIRED": 2,
             "RASTER_GRID": 1}
    strictest = ""
    for aid in algorithm_ids:
        algo = reg.get(aid)
        if algo is None:
            continue
        cls = str(getattr(algo, "crs_class", "") or "")
        if order.get(cls, 0) > order.get(strictest, 0):
            strictest = cls
    return strictest


def _adjudicate_crs_scale(candidate: Any, profile: Dict[str, Any]) -> DimensionState:
    """CRS/尺度维（R1-F1）：唯一事实源 = AlgorithmDescriptor.crs_class。

    GEOGRAPHIC_OK/GEODESIC/CRS_AGNOSTIC 在任何数据 CRS 下 pass
    （如 geometry.buffer 的实现内建 UTM 投影——地理 CRS 输入不是错误）；
    PROJECTED/LOCAL_METRIC_REQUIRED × geographic 数据 → transform（soft，
    附 recommend_metric_crs 预处理）；数据 CRS 未知 → unknown。
    """
    from app.lib.gis.crs_safety import (
        classify_crs,
        crs_class_allows,
        recommend_metric_crs,
    )
    strictest = _strictest_crs_class(candidate.algorithm_ids)
    if not strictest:
        return DimensionState(dimension="crs_scale", state="pass",
                              structural=True)
    data_class = classify_crs(_crs_fact(profile))
    if data_class == "unknown" and strictest in (
            "PROJECTED_REQUIRED", "LOCAL_METRIC_REQUIRED"):
        # 有 CRS 诉求但数据 CRS 未知：unknown ≠ 允许（不虚标 pass）
        return DimensionState(
            dimension="crs_scale", state="unknown",
            evidence={"crs_class": strictest})
    if crs_class_allows(strictest, data_class):
        return DimensionState(
            dimension="crs_scale", state="pass",
            evidence={"crs_class": strictest, "data_class": data_class})
    if data_class == "unknown":
        return DimensionState(
            dimension="crs_scale", state="unknown",
            evidence={"crs_class": strictest})
    bbox = _profile_fact(profile, "bbox")
    metric = recommend_metric_crs(
        tuple(bbox) if isinstance(bbox, (list, tuple)) and len(bbox) == 4
        else None)
    return DimensionState(
        dimension="crs_scale", state="transform",
        evidence={"crs_class": strictest, "data_class": data_class,
                  "suggested_crs": metric or ""})


def _adjudicate_measure_semantics(
    candidate: Any, facts: QualificationFacts,
) -> DimensionState:
    """量测语义维（R1-F4）：按 descriptor.assumes_continuous_measure scope。"""
    desc_reg = get_method_descriptor_registry()
    desc = desc_reg.get(candidate.method_id)
    assumes_continuous = bool(
        desc.assumes_continuous_measure) if desc else True
    kind = _measure_kind_fact(facts)
    if kind == "unknown":
        return DimensionState(dimension="measure_semantics", state="unknown")
    evidence = {"measure_kind": kind,
                "assumes_continuous": assumes_continuous}
    if kind == "categorical" and assumes_continuous:
        return DimensionState(dimension="measure_semantics", state="fail",
                              evidence=evidence)
    return DimensionState(dimension="measure_semantics", state="pass",
                          evidence=evidence)


def normalize_precondition_state(result: Any) -> str:
    """算法层 precondition verdict → 四态（与 V4 同语义；parity test 锁定）。"""
    verdict = str(getattr(result, "verdict", "") or "")
    facts_used = bool(getattr(result, "facts_used", None))
    if verdict == "REQUIRES_TRANSFORM":
        return "transform"
    if verdict in ("PASS", "PASS_WITH_WARNINGS"):
        return "pass" if facts_used else "unknown"
    if verdict in ("INSUFFICIENT_DATA", "INVALID_METHOD"):
        return "fail"
    return "unknown"


def _evaluate_one_precondition(pid: str, profile: Dict[str, Any]) -> str:
    if not profile:
        return "unknown"
    from app.lib.gis.scientific_preconditions import evaluate_precondition
    return normalize_precondition_state(evaluate_precondition(pid, profile))


def _has_time_fact(profile: Dict[str, Any]) -> Optional[bool]:
    """时间维度事实（与 data_qualification._has_time_fact 同语义）。"""
    if "hasTimeField" in profile:
        return bool(profile.get("hasTimeField"))
    obs = _profile_fact(profile, "temporalObservationCount")
    if isinstance(obs, (int, float)):
        return obs >= 2
    return None


def _adjudicate_temporal(candidate: Any, profile: Dict[str, Any]) -> DimensionState:
    needs_time = any("temporal" in pid for pid in candidate.preconditions)
    needs_time = needs_time or "temporal_field_required" in candidate.preconditions
    if not needs_time:
        return DimensionState(dimension="temporal", state="pass",
                              structural=True)
    time_fact = _has_time_fact(profile)
    if time_fact is None:
        return DimensionState(dimension="temporal", state="unknown")
    if not time_fact:
        return DimensionState(dimension="temporal", state="fail",
                              evidence={"reason": "temporal_field_required"})
    return DimensionState(dimension="temporal", state="pass")


def _adjudicate_nodata_quality(
    candidate: Any, profile: Dict[str, Any],
) -> DimensionState:
    fields = _profile_fact(profile, "fields")
    if not isinstance(fields, dict) or not fields:
        return DimensionState(dimension="nodata_quality", state="unknown")
    null_heavy = [str(k) for k, v in fields.items()
                  if isinstance(v, dict)
                  and isinstance(v.get("null_ratio"), (int, float))
                  and v["null_ratio"] > _HIGH_NULL_RATIO]
    if null_heavy:
        return DimensionState(
            dimension="nodata_quality", state="transform",
            evidence={"null_heavy": null_heavy[:3]})
    return DimensionState(dimension="nodata_quality", state="pass")


def _adjudicate_data_roles(
    candidate: Any, role_states: Dict[str, str],
) -> Tuple[DimensionState, float]:
    from app.services.gis_harness.plan_candidates import DATA_FIT_SCORE
    from app.services.gis_harness.workflow_schema import role_reason_code
    if not candidate.requires_roles:
        return (DimensionState(dimension="data_roles", state="pass"), 0.5)
    states_seen: List[str] = []
    scores: List[float] = []
    fails: List[str] = []
    for role in candidate.requires_roles:
        st = role_states.get(role, "unknown")
        states_seen.append(st)
        scores.append(DATA_FIT_SCORE.get(st, 0.5))
        if st == "blocked":
            fails.append(f"{role}:{role_reason_code(role)}")
    if fails:
        return (DimensionState(
            dimension="data_roles", state="fail",
            evidence={"blocked": fails[:3]}), min(scores))
    if "degraded" in states_seen:
        return (DimensionState(
            dimension="data_roles", state="transform",
            evidence={"degraded_roles": [
                r for r in candidate.requires_roles
                if role_states.get(r) == "degraded"][:3]}),
            sum(scores) / len(scores))
    if all(s == "eligible" for s in states_seen):
        return (DimensionState(
            dimension="data_roles", state="pass",
            evidence={"roles": list(candidate.requires_roles[:4])}),
            1.0)
    return (DimensionState(dimension="data_roles", state="unknown"),
            sum(scores) / len(scores))


def _adjudicate_preconditions(
    candidate: Any, profile: Dict[str, Any],
) -> DimensionState:
    if not candidate.preconditions:
        return DimensionState(dimension="scientific_precondition",
                              state="pass", structural=True)
    results = {pid: _evaluate_one_precondition(pid, profile)
               for pid in candidate.preconditions}
    states = set(results.values())
    if "fail" in states:
        failed = [pid for pid, st in results.items() if st == "fail"]
        return DimensionState(
            dimension="scientific_precondition", state="fail",
            evidence={"failed": failed[:3]})
    if states == {"pass"}:
        return DimensionState(dimension="scientific_precondition", state="pass",
                              evidence=results)
    if "transform" in states:
        return DimensionState(
            dimension="scientific_precondition", state="transform",
            evidence={k: v for k, v in results.items()})
    return DimensionState(dimension="scientific_precondition", state="unknown",
                          evidence=results)


# ── 报告组装 ────────────────────────────────────────────────────────────

_REASON_BY_STATE = {
    "fail": "REJECTED",
    "transform": "TRANSFORM",
}


def qualify_method(
    method_id: str,
    facts: QualificationFacts,
    *,
    methodology_registry: Any = None,
) -> MethodQualificationReport:
    """单方法资格裁决（确定性纯函数；科学性全部委托既有 oracle）。"""
    if methodology_registry is None:
        from app.services.gis_harness.workflow_v4.methodology import (
            get_methodology_registry,
        )
        methodology_registry = get_methodology_registry()
    candidate = methodology_registry.method(method_id)
    if candidate is None:
        raise ValueError(f"unknown method_id: {method_id}")

    profile = facts.profile
    dims: List[DimensionState] = [
        _adjudicate_geometry(candidate, profile),
        _adjudicate_sample(candidate, profile),
        _adjudicate_crs_scale(candidate, profile),
        _adjudicate_measure_semantics(candidate, facts),
        _adjudicate_temporal(candidate, profile),
        _adjudicate_nodata_quality(candidate, profile),
    ]
    role_dim, _role_fit = _adjudicate_data_roles(
        candidate, facts.role_states)
    dims.append(role_dim)
    dims.append(_adjudicate_preconditions(candidate, profile))

    reason_codes: List[str] = []
    missing: List[str] = []
    preprocessing: List[str] = []
    disclosures: List[str] = []
    for d in dims:
        if d.state == "fail":
            reason_codes.append(f"QUAL_{d.dimension.upper()}_REJECTED")
            missing.append(
                f"{d.dimension}: {json.dumps(d.evidence, ensure_ascii=False)[:100]}"
                if d.evidence else f"{d.dimension}: 不满足")
        elif d.state == "transform":
            reason_codes.append(f"QUAL_{d.dimension.upper()}_TRANSFORM")
        elif d.state == "unknown":
            missing.append(f"{d.dimension}: 事实不足（unknown ≠ 不满足）")

    if any(d.state == "transform" and d.dimension == "crs_scale"
           for d in dims):
        preprocessing.append("reproject")
        disclosures.append(
            "地理坐标系下需局部度量投影：修复链已显式化（reproject），"
            "非静默换算。")
    if any(d.state == "transform" and d.dimension == "nodata_quality"
           for d in dims):
        preprocessing.append("filter_null")
        disclosures.append("部分字段空值率过高：统计前需过滤或修复。")
    if any(d.state == "transform" and d.dimension == "data_roles"
           for d in dims):
        disclosures.append("部分必需数据角色降级：结论语义弱化并披露。")

    # 报告级状态收敛（确定性）：fail → rejected；transform → degraded
    # （可修复/降级并披露）；无任何事实支撑的 pass（全部 unknown 或仅
    # 结构性满足）→ unknown（不虚标 viable）；否则 viable。
    states = {d.state for d in dims}
    fact_backed_pass = any(
        d.state == "pass" and not d.structural for d in dims)
    if "fail" in states:
        status = "rejected"
    elif "transform" in states:
        status = "degraded"
    elif not fact_backed_pass:
        status = "unknown"
    else:
        status = "viable"

    known = [d for d in dims if d.state != "unknown"]
    confidence = (sum(1 for d in known) / len(dims)) if dims else 0.0

    fam = methodology_registry.family(candidate.family_id)
    return MethodQualificationReport(
        method_id=method_id,
        family_id=candidate.family_id,
        status=status,
        dimension_states=dims,
        reason_codes=reason_codes,
        missing_requirements=missing[:6],
        recommended_preprocessing=[
            op for op in preprocessing if op in _remediation_ops()],
        disclosures=disclosures[:4],
        confidence=round(confidence, 2),
    )


def qualify_methods(
    method_ids: Sequence[str],
    facts: QualificationFacts,
) -> List[MethodQualificationReport]:
    """批量资格裁决（输入序稳定；无排序——排序归 ranking 层）。"""
    return [qualify_method(mid, facts) for mid in method_ids]


__all__ = [
    "QUAL_METHOD_STATUSES",
    "DIMENSION_STATES",
    "QUALIFICATION_DIMENSIONS",
    "DimensionState",
    "MethodQualificationReport",
    "QualificationFacts",
    "normalize_precondition_state",
    "qualify_method",
    "qualify_methods",
]
