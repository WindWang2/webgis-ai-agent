"""Typed Data Qualification Contract —— per-step 数据资格判定（Goal V3）。

对 workflow 的每个数据角色（→ 执行期的每个 step），基于数据画像事实
（Spatial Meta Profile / resolver camelCase 形态）给出机器可读的四态裁决：

    eligible            事实满足，可直接执行；
    transform_required  不满足但存在自动可修复变换（reproject / 派生字段 /
                        空值过滤…）—— 生成显式 transform step；
    degraded            可降级执行但必须披露（近似/部分/代理语义）；
    blocked             不能科学执行 —— 声明缺什么（不虚构、不静默）；
    unknown             画像无事实 —— 未知 ≠ 不满足（与义务评估同红线）。

红线：

- 结构性检查（几何类别 / 字段在场 / 空值率 / 样本量）在本模块做事实比对；
  **科学性检查（投影合规 / 数值字段 / 最小样本）委托算法层
  scientific_preconditions（单一事实源），不重复实现科学语义**；
- 输出全部确定性：同 profile 同裁决；remediation 只引用既有修复词表
  （SpatialRepairPipeline ops / geocompute 算子同源），不发明新变换；
- 本模块不执行任何变换 —— 只产出显式 transform step 供 plan 具象化。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from pydantic import BaseModel, Field

#: 资格裁决状态（五态；unknown 与 ObligationEvaluation.status 的 unknown
#: 语义一致：画像缺事实 ≠ 数据不合格）。
QUALIFICATION_STATES = (
    "eligible", "transform_required", "degraded", "blocked", "unknown",
)

#: 显式修复操作词表。每个操作的**实现背书**（单一事实源引用，非平行
#: 词表——经 test_data_qualification 对 CapabilityRegistry 对账）：
#: - reproject      → spatial_repair_pipeline.crs_transform（pyproj）
#: - repair_geometry → make_valid / snap_within_tolerance / deduplicate
#: - normalize      → attribute_type_normalization（str→num / NaN→None）
#: - derive_field   → capability `geometry_centroid` + geocompute 派生算子
#: - aggregate      → capability `grid_binning` / `admin_aggregation`
#: - filter_null    → geocompute FILTER 算子（SQL 三值谓词）
#: - filter_nodata  → raster_calculator nodata 掩膜（tools/advanced_spatial）
#: - resample       → capability `raster_resample`
REMEDIATION_OPS = (
    "reproject",
    "repair_geometry",
    "derive_field",
    "aggregate",
    "normalize",
    "filter_null",
    "filter_nodata",
    "resample",
)

#: 操作 → 实现背书（``capability:<id>`` = CapabilityRegistry 引用，经
#: registry 对账；``fn:<module>:<symbol>`` = 确定性修复函数）。执行仍由
#: CapabilityRegistry / AlgorithmResolver 解析，本模块只声明意图。
REMEDIATION_OP_BACKING = {
    "reproject": ("fn:app.services.spatial_repair_pipeline:crs_transform",),
    "repair_geometry": (
        "fn:app.services.spatial_repair_pipeline:make_valid",
        "fn:app.services.spatial_repair_pipeline:snap_within_tolerance",
        "fn:app.services.spatial_repair_pipeline:deduplicate",
    ),
    "derive_field": ("capability:geometry_centroid",),
    "aggregate": ("capability:grid_binning", "capability:admin_aggregation"),
    "normalize": (
        "fn:app.services.spatial_repair_pipeline:attribute_type_normalization",),
    "filter_null": ("op:geocompute.FILTER",),
    "filter_nodata": ("fn:app.tools.advanced_spatial:raster_calculator",),
    "resample": ("capability:raster_resample",),
}

#: 无需 profile 事实即可给出的修复操作（有确定性实现）。
_AUTO_FIXABLE_OPS = frozenset({
    "reproject", "repair_geometry", "derive_field", "normalize",
    "filter_null", "filter_nodata", "resample",
})

#: 高空值率阈值：超过则该字段不可靠（degraded 证据）。
_HIGH_NULL_RATIO = 0.5

#: 分母/时间字段提示词表 —— 引用 workflow_schema 公有词表（单一事实源；
#: 此前副本曾漂移丢失 "day"/"时期"，review A3）。
from app.services.gis_harness.workflow_schema import (  # noqa: E402
    DENOMINATOR_FIELD_HINTS,
    TIME_FIELD_HINTS,
)

#: 剖面字段名 → 检查维度的事实键（resolver camelCase 约定，与
#: spatial_meta_profiler / DatasetProfile.to_resolver_profile 同源）。
_GEOMETRY_TYPES_KEY = "geometryTypes"
_FEATURE_COUNT_KEY = "featureCount"
_CRS_KEY = "crs"
_ROW_COUNT_KEY = "rowCount"


def _geometry_category(geometry_types: Sequence[str]) -> str:
    """geometryTypes → 主导类别（与 recipes.check_eligibility 同语义）。"""
    point = {"Point", "MultiPoint"}
    line = {"LineString", "MultiLineString"}
    polygon = {"Polygon", "MultiPolygon"}
    counts = {"point": 0, "line": 0, "polygon": 0}
    for gt in geometry_types or []:
        if gt in point:
            counts["point"] += 1
        elif gt in line:
            counts["line"] += 1
        elif gt in polygon:
            counts["polygon"] += 1
    active = [k for k, v in counts.items() if v > 0]
    return max(counts, key=lambda c: counts[c]) if active else "unknown"


#: 公共别名（防副本漂移，同 plan_candidates.DATA_FIT_SCORE 前例）：
#: 方法资格引擎（workflow_v4.methodology）消费同一几何归约。
geometry_category = _geometry_category


def _profile_fields(profile: Dict[str, Any]) -> Dict[str, Any]:
    fields = profile.get("fields")
    return fields if isinstance(fields, dict) else {}


def _field_names(profile: Dict[str, Any]) -> List[str]:
    return [str(k) for k in _profile_fields(profile).keys()]


def _numeric_field_names(profile: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for name, spec in _profile_fields(profile).items():
        ftype = str((spec or {}).get("type", "")).lower()
        if ftype in ("number", "integer", "int", "float", "double"):
            out.append(str(name))
    return out


def _has_time_fact(profile: Dict[str, Any]) -> Optional[bool]:
    """hasTimeField / temporalObservationCount / 时间字段名 → 时间维度事实。

    None = 画像无事实（unknown）；True/False = 有事实。
    """
    if "hasTimeField" in profile:
        return bool(profile.get("hasTimeField"))
    obs = profile.get("temporalObservationCount")
    if isinstance(obs, (int, float)):
        return obs >= 2
    for name in _field_names(profile):
        low = name.lower()
        if any(h in low for h in TIME_FIELD_HINTS):
            return True
    return None


class RemediationStep(BaseModel):
    """一个显式 transform step（数据资格不满足时的修复声明，不执行）。"""
    operation: str                 # ⊆ REMEDIATION_OPS
    target: str = ""               # 目标角色/字段/图层
    params: Dict[str, Any] = Field(default_factory=dict)
    reason_code: str = ""          # 触发原因（稳定机器可读码）
    disclosure: str = ""           # 用户可见披露（material 时必须展示）
    auto_applicable: bool = False  # True = 有确定性实现，可自动生成执行 step
    confidence: float = Field(0.5, ge=0.0, le=1.0)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "operation": self.operation[:32],
            "target": self.target[:64],
            "params": dict(list(self.params.items())[:6]),
            "reason_code": self.reason_code[:64],
            "disclosure": self.disclosure[:200],
            "auto_applicable": self.auto_applicable,
            "confidence": round(self.confidence, 2),
        }


class DataQualification(BaseModel):
    """一个数据角色（step 数据需求）的资格裁决（compiler qualify_data 输出）。"""
    role: str
    state: str = "unknown"         # ⊆ QUALIFICATION_STATES
    reason_code: str = ""
    detail: str = ""
    remediation: List[RemediationStep] = Field(default_factory=list)
    checks: List[Dict[str, Any]] = Field(default_factory=list)
    # 置信度 = 有事实的检查占比（确定性启发式；1.0 = 全部检查有事实支撑）
    confidence: float = Field(0.0, ge=0.0, le=1.0)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role[:32],
            "state": self.state,
            "reason_code": self.reason_code[:64],
            "detail": self.detail[:200],
            "remediation": [r.to_bounded_dict() for r in self.remediation[:4]],
            "checks": [dict(list(c.items())[:5]) for c in self.checks[:6]],
            "confidence": round(self.confidence, 2),
        }


def _check(name: str, passed: bool, **evidence: Any) -> Dict[str, Any]:
    ev: Dict[str, Any] = {"check": name, "passed": passed}
    ev.update(evidence)
    return ev


def _remediation(
    operation: str, target: str, *, reason_code: str, disclosure: str = "",
    params: Optional[Dict[str, Any]] = None, confidence: float = 0.8,
    auto_applicable: Optional[bool] = None,
) -> RemediationStep:
    return RemediationStep(
        operation=operation, target=target,
        params=params or {},
        reason_code=reason_code,
        disclosure=disclosure,
        auto_applicable=(
            operation in _AUTO_FIXABLE_OPS
            if auto_applicable is None else auto_applicable
        ),
        confidence=confidence,
    )


# ── 角色语义 → 数据需求（结构性部分；科学性由义务/算法层负责）────────────

#: 需要 measure 类数值字段的角色。
_MEASURE_ROLES = frozenset({"measure", "denominator", "criteria"})


def _derived_numeric_fields(profile: Dict[str, Any]) -> List[str]:
    """从 fields 类型事实派生数值字段名（与 DatasetProfile 同归约；
    resolver profile 未携带 numericFields 键时的补全）。"""
    present = profile.get("numericFields")
    if isinstance(present, list) and present:
        return [str(x) for x in present]
    return _numeric_field_names(profile)


def _evaluate_precondition_facts(
    precondition_id: str, profile: Dict[str, Any],
) -> tuple[Optional[bool], Any]:
    """委托算法层 precondition 做科学性检查（单一事实源）。

    返回 (passed, result)：
    - passed=None：算法层未用到任何事实（PASS-on-no-facts 的诚实缺省）
      → 调用方按 unknown 处理，不得计为满足；
    - passed=True/False：事实在手，PASS/PASS_WITH_WARNINGS=True，
      其余（REQUIRES_TRANSFORM/INSUFFICIENT_DATA/INVALID_METHOD）=False。
    """
    from app.lib.gis.scientific_preconditions import evaluate_precondition

    result = evaluate_precondition(precondition_id, profile)
    passed: Optional[bool] = None
    if result.facts_used:
        passed = result.verdict in ("PASS", "PASS_WITH_WARNINGS")
    elif result.verdict in ("INSUFFICIENT_DATA", "INVALID_METHOD"):
        # 字段已知但事实不满足（如「画像字段已知但无数值字段」）
        passed = False
    return passed, result


def qualify_data_role(
    req: Any,
    role_status: str,
    *,
    resolver_profile: Optional[Dict[str, Any]] = None,
    crs_projection_obligation: bool = False,
) -> DataQualification:
    """对单个数据角色做资格裁决（确定性纯函数）。

    ``req`` 是 workflow_schema.DataRoleRequirement（duck-typed 以避免
    import 环）；``role_status`` 是 resolve_data_roles 的解析状态。
    """
    profile = resolver_profile if isinstance(resolver_profile, dict) else {}
    has_profile = bool(profile)
    geom_types = list(profile.get(_GEOMETRY_TYPES_KEY) or [])
    geom_cat = _geometry_category(geom_types)
    feature_count = profile.get(_FEATURE_COUNT_KEY)
    if isinstance(feature_count, float) and feature_count.is_integer():
        feature_count = int(feature_count)
    fields = _profile_fields(profile)

    checks: List[Dict[str, Any]] = []
    remediation: List[RemediationStep] = []
    facts_total = 0      # 有事实可判的检查数（置信度分母）
    facts_fail = 0       # 其中不满足的

    def _record(passed: Optional[bool], check: Dict[str, Any]) -> None:
        nonlocal facts_total, facts_fail
        checks.append(check)
        if passed is not None:
            facts_total += 1
            if not passed:
                facts_fail += 1

    # ── 解析层状态先行（角色缺失语义与 workflow_schema 一致）─────────
    if role_status == "unresolved" and req.required:
        if req.missing_policy == "block":
            return DataQualification(
                role=req.role, state="blocked",
                reason_code=req.reason_code or f"DATA_ROLE_MISSING_{req.role.upper()}",
                detail="必选角色无数据且策略为 block（与义务评估同源）",
                confidence=1.0,
            )
        return DataQualification(
            role=req.role, state="degraded",
            reason_code=req.reason_code or f"DATA_ROLE_MISSING_{req.role.upper()}",
            detail=req.degrade_disclosure or f"缺少 {req.role} 数据：已降级并披露",
            confidence=1.0,
        )
    if role_status == "degraded":
        return DataQualification(
            role=req.role, state="degraded",
            reason_code=req.reason_code or f"DATA_ROLE_MISSING_{req.role.upper()}",
            detail=req.degrade_disclosure or f"缺少 {req.role} 数据：已降级并披露",
            confidence=1.0,
        )
    if not req.required and role_status in ("unresolved", "external"):
        return DataQualification(
            role=req.role, state="unknown",
            reason_code="OPTIONAL_ROLE_UNRESOLVED",
            detail="可选角色未解析：不影响科学结论",
            confidence=1.0,
        )
    if role_status == "external":
        # 外部获取通道（data_fabric/user_upload）：规划期不可证伪
        return DataQualification(
            role=req.role, state="unknown",
            reason_code="EXTERNAL_ACQUISITION_UNVERIFIED",
            detail="依赖外部获取：执行期按实际数据复评",
            confidence=1.0,
        )
    if not has_profile:
        return DataQualification(
            role=req.role, state="unknown",
            reason_code="PROFILE_FACTS_UNAVAILABLE",
            detail="无数据画像事实：未知 ≠ 不满足（不虚构资格）",
            confidence=0.0,
        )

    # ── 结构性事实检查（bound 且有 profile）──────────────────────────
    # 1) 几何类别匹配
    if req.geometry_kinds:
        need = set(req.geometry_kinds)
        passed: Optional[bool] = None
        if geom_cat != "unknown" and need != {"unknown"}:
            passed = geom_cat in need
        _record(passed, _check(
            "geometry_kind", bool(passed), dominant=geom_cat,
            required=sorted(need),
        ))
        if passed is False:
            # 点↔面可派生（质心/聚合）→ transform_required；其余不可自动
            # 修复（auto_applicable=False，裁决走 degraded，需人工介入）
            if "point" in need and geom_cat == "polygon":
                remediation.append(_remediation(
                    "derive_field", req.role,
                    reason_code="GEOMETRY_DERIVE_CENTROID",
                    disclosure="面要素转质心点以满足点支持需求。",
                    params={"method": "centroid"},
                ))
            elif "polygon" in need and geom_cat == "point":
                remediation.append(_remediation(
                    "aggregate", req.role,
                    reason_code="GEOMETRY_AGGREGATE_REQUIRED",
                    disclosure="点要素需聚合到面单元（行政/格网）后再执行。",
                    params={"to": "polygon"},
                ))
            else:
                remediation.append(RemediationStep(
                    operation="repair_geometry", target=req.role,
                    reason_code="GEOMETRY_NOT_SUPPORTED",
                    disclosure="几何类别与需求不符且无确定性派生路径。",
                    auto_applicable=False, confidence=0.3,
                ))

    # 2) 数值字段（measure 族角色）——科学性委托算法层 precondition。
    # review R2 MAJOR-1：字段补全只在**显式 schema**（fields_status ==
    # "explicit"）下注入 —— 截断/未知 schema 的空列表不是「无数值字段」
    # 的证据，注入会把 unknown 洗成 authoritative-empty → false-REJECT。
    # 事实缺席时 precondition 自行走 deferred-PASS（unknown ≠ unsatisfied）。
    if req.role in _MEASURE_ROLES:
        enriched = dict(profile)
        if str(profile.get("fields_status") or "") == "explicit":
            enriched["numericFields"] = _derived_numeric_fields(profile)
        passed, result = _evaluate_precondition_facts(
            "numeric_field_required", enriched)
        _record(passed, _check(
            "numeric_field", bool(passed) if passed is not None else False,
            verdict=result.verdict, message=result.message[:120],
        ))
        if passed is False and _derived_numeric_fields(profile):
            # 有数值字段但角色要求特定字段 → derive_field 可尝试派生
            remediation.append(_remediation(
                "derive_field", req.role,
                reason_code="MEASURE_FIELD_DERIVATION_POSSIBLE",
                disclosure="缺少角色要求的度量字段：可由现有数值字段显式派生。",
                params={"candidates": _derived_numeric_fields(profile)[:4]},
                confidence=0.4,
            ))

    # 3) 分母字段的强证据（与 workflow_schema._DENOMINATOR_FIELD_HINTS 同规）
    if req.role == "denominator":
        fields_known = bool(fields)
        if fields_known:
            names = [n.lower() for n in _field_names(profile)]
            has_strong = any(
                any(h in n for h in DENOMINATOR_FIELD_HINTS) for n in names
            )
            _record(has_strong, _check(
                "denominator_field", has_strong,
                hint_fields=[n for n in names[:8]],
            ))
            if not has_strong:
                remediation.append(_remediation(
                    "derive_field", "denominator",
                    reason_code="DENOMINATOR_FIELD_REQUIRED",
                    disclosure="缺少人口/面积等分母字段：不得下人均/率/公平性结论。",
                    auto_applicable=False, confidence=0.2,
                ))
        else:
            # 字段事实缺席（栅格/降级画像）：unknown ≠ 不满足 —— 不计失败、
            # 不降档（review S3 红线）。
            _record(None, _check(
                "denominator_field", False, facts="fields_unknown"))

    # 4) 空值率（字段级事实）
    if req.role in _MEASURE_ROLES or req.role == "subject":
        null_heavy: List[str] = []
        for name, spec in fields.items():
            ratio = (spec or {}).get("null_ratio")
            if isinstance(ratio, (int, float)) and ratio > _HIGH_NULL_RATIO:
                null_heavy.append(str(name))
        if fields:
            # 实际通过性进入 facts 统计（review S6：恒 True 会让 confidence
            # 与失败计数失真）
            _record(not null_heavy, _check(
                "null_ratio", not null_heavy, null_heavy=null_heavy[:6],
            ))
            if null_heavy:
                remediation.append(_remediation(
                    "filter_null", req.role,
                    reason_code="HIGH_NULL_RATIO_FIELDS",
                    disclosure=f"字段空值率过高（{', '.join(null_heavy[:3])}）："
                               "统计前需过滤或修复。",
                    params={"fields": null_heavy[:6],
                            "threshold": _HIGH_NULL_RATIO},
                ))

    # 5) 样本量（结构性下限：≥1 有要素；科学下限由 precondition 管）
    if isinstance(feature_count, (int, float)):
        passed = feature_count >= 1
        _record(passed, _check("sample_size", passed, count=feature_count))
        if not passed:
            return DataQualification(
                role=req.role, state="blocked",
                reason_code="EMPTY_DATASET",
                detail="数据集为空：不能执行任何分析",
                checks=checks, remediation=remediation, confidence=1.0,
            )

    # 6) 投影合规（科学性委托算法层；仅当需求方声明投影义务时检查）
    if crs_projection_obligation:
        passed, result = _evaluate_precondition_facts(
            "projected_crs_required", profile)
        _record(passed, _check(
            "projected_crs", bool(passed) if passed is not None else False,
            verdict=result.verdict, crs=profile.get(_CRS_KEY),
        ))
        if passed is False:
            remediation.append(_remediation(
                "reproject", req.role,
                reason_code="PROJECTED_CRS_REQUIRED",
                disclosure=(result.transform_hint
                            or "地理坐标系：需先重投影到投影坐标系。"),
                params={"crs": profile.get(_CRS_KEY)},
            ))

    # 7) 时间维度（事实有无；不判科学充分性）
    time_fact = _has_time_fact(profile)
    if req.role in ("target_time", "baseline", "comparison_time"):
        _record(time_fact, _check("temporal_dimension", bool(time_fact)))

    # ── 状态收敛（确定性）────────────────────────────────────────────
    # 优先级：存在不可自动修复项 → degraded（近似/需人工）；全部修复项
    # 可自动应用 → transform_required（生成显式 transform step）；无修复
    # 项且事实全满足 → eligible；无修复项但有事实失败 → degraded。
    non_auto = [r for r in remediation if not r.auto_applicable]
    if non_auto:
        state = "degraded"
        reason = non_auto[0].reason_code
    elif remediation:
        state = "transform_required"
        reason = remediation[0].reason_code
    elif facts_total and facts_fail == 0:
        state = "eligible"
        reason = "PROFILE_FACTS_SATISFIED"
    elif facts_total and facts_fail:
        state = "degraded"
        reason = "PROFILE_FACTS_PARTIAL"
    else:
        state = "unknown"
        reason = "NO_FACT_CHECKS_APPLICABLE"

    confidence = round((facts_total - facts_fail) / facts_total, 2) if facts_total else 0.0
    return DataQualification(
        role=req.role, state=state, reason_code=reason,
        detail="；".join(
            c.get("check", "") for c in checks if not c.get("passed")) or "结构检查通过",
        remediation=remediation, checks=checks, confidence=confidence,
    )


def qualify_workflow_data_roles(
    data_roles: Sequence[Any],
    role_resolutions: Sequence[Any],
    *,
    resolver_profile: Optional[Dict[str, Any]] = None,
    crs_projection_obligation: bool = False,
) -> List[DataQualification]:
    """workflow 全部数据角色的资格裁决（compiler qualify_data 阶段）。"""
    status_by_role = {r.role: r.status for r in role_resolutions or []}
    return [
        qualify_data_role(
            req, status_by_role.get(req.role, "unresolved"),
            resolver_profile=resolver_profile,
            crs_projection_obligation=crs_projection_obligation,
        )
        for req in data_roles
    ]


__all__ = [
    "QUALIFICATION_STATES",
    "REMEDIATION_OPS",
    "RemediationStep",
    "DataQualification",
    "qualify_data_role",
    "qualify_workflow_data_roles",
]
