"""requirement → task 编译（C1 / GC-01 / GC-02）—— 确定性族/表达/义务推导。

把 ``RequirementDocument``（typed 理解面）编译为 ``TaskCompilation``：

    family_candidates     IR 信号 → 七族候选排序（信号溯源，无关键词暗箱）
    expression_candidates 统计语义 × 几何期望 × 数据事实 → 表达候选
                          （用户显式点名 vs Agent 推荐增量分离）
    unit_obligations      统计/口径 → 量纲义务（missing_denominator 披露）

纪律：

- **不选族**：候选排序是**证据投影**，LLM/Pi 消费 top 候选时必须能溯源
  每个信号到 IR 字段路径；显式 override 由工具面披露（不在本层裁决）；
- **fail-closed**：无信号命中 → blocked；阻断性歧义未答 →
  needs_clarification（不伪填数据、不猜相近族）；
- 零 LLM / 零 IO：同输入逐字节同输出；``data_facts`` 显式注入。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.services.gis_harness.analysis_workflows.families import (
    FAMILY_IDS,
    role_requirements,
)
from app.services.gis_harness.execution_compiler.contracts import (
    ExpressionCandidate,
    FamilyCandidate,
    RoleNeed,
    SignalEvidence,
    TaskCompilation,
    UnitObligation,
)
from app.services.gis_harness.execution_compiler.units import (
    EXPRESSION_SEMANTICS,
    build_unit_obligations,
    statistic_semantics,
)
from app.services.gis_harness.requirement_ir.contracts import (
    RequirementDocument,
)

#: 密度表达的最低点数门槛（数据事实在场时；事实缺席 → unknown 不裁决）。
MIN_DENSITY_POINTS = 30

#: 主题类别中的危险源标记（subject.category 词面；hazard 族的信号源）。
_HAZARD_MARKERS = ("危险", " hazard", "hazard", "污染", "化学品", "灾害")

#: 任务类型 → 族强信号（task.task_type 是 intent resolver 的权威裁决）。
#: 权重分级：载荷类型权威（raster/equity/risk）> 领域任务 > 泛化聚合——
#: 「按行政区统计 DEM」是 raster→zonal 语义（矢量 admin_aggregation 无法
#: 消费栅格 ref——role_kind 门也会 typed 拒），权重必须反映科学适用性。
_TASK_TYPE_FAMILY: Dict[str, Tuple[str, float]] = {
    "administrative_statistic": ("admin_facility_distribution", 3.0),
    "accessibility_analysis": ("service_accessibility", 3.0),
    "proximity_analysis": ("network_proximity", 3.0),
    "change_detection": ("temporal_change", 3.0),
    "temporal_trend": ("temporal_change", 3.0),
    "concentration_analysis": ("statistical_zones", 3.0),
    "analytical_density": ("statistical_zones", 3.0),
    "kde_density": ("statistical_zones", 3.0),
    # 载荷/语义权威（5.0）：栅格主题（矢量聚合不适用）与率/公平语义
    # （率须分母网格身份——admin 计数族无法承载）。
    "raster_distribution": ("raster_zonal_summary", 5.0),
    "vegetation_index": ("raster_zonal_summary", 5.0),
    "spatial_equity": ("statistical_zones", 5.0),
    "risk_exposure": ("hazard_exposure", 5.0),
}

#: geometry_kind（用户显式表达词表）→ 表达候选 kind。
_GEOMETRY_KIND_EXPRESSION: Dict[str, str] = {
    "heatmap": "heatmap",
    "choropleth": "admin_choropleth",
    "proportional_symbol": "admin_choropleth",
    "point": "point_map",
    "isoline": "area_density",
    "flow": "point_map",
}


def _signals_of(doc: RequirementDocument) -> List[SignalEvidence]:
    """IR → 编译信号清单（路径+值+origin；bounded，声明序确定性）。"""
    spec = doc.intent
    core = spec.core
    out: List[SignalEvidence] = []

    def add(path: str, value: Any, weight: float = 1.0) -> None:
        text = str(value or "").strip()
        if text and len(out) < 12:
            prov = spec.field_provenance.get(path)
            out.append(SignalEvidence(
                path=path, value=text[:64],
                origin=(prov.origin if prov else "rule"),
                weight=weight))

    add("task.task_type", spec.task.task_type, 3.0)
    add("task.kind", spec.task.kind, 0.5)
    add("subject.type", spec.subject.type, 1.5)
    add("subject.category", spec.subject.category, 1.0)
    for m in spec.measures[:2]:
        add(f"measures.{m.id}.statistic", m.statistic, 1.5)
    add("statistics.dimension", spec.statistics.dimension, 2.0)
    add("statistics.group_by", spec.statistics.group_by, 0.5)
    add("statistics.normalization", spec.statistics.normalization, 1.5)
    add("spatial_relation.kind", spec.spatial_relation.kind, 2.5)
    if spec.spatial_relation.target:
        add("spatial_relation.target", spec.spatial_relation.target, 1.0)
    if spec.time.series:
        add("time.series", "true", 3.0)
    if spec.time.compare_start:
        add("time.compare_start", spec.time.compare_start, 3.0)
    add("representation.geometry_kind", spec.representation.geometry_kind, 2.0)
    add("core.geometry_expectation", core.geometry_expectation, 0.5)
    return out


def _family_score(
    family: str, signals: List[SignalEvidence], doc: RequirementDocument,
) -> Tuple[float, List[SignalEvidence]]:
    """信号 → 族得分（确定性；命中信号随行携带溯源）。"""
    spec = doc.intent
    by_path: Dict[str, str] = {s.path: s.value for s in signals}
    matched: List[SignalEvidence] = []
    score = 0.0

    def hit(path: str, predicate, weight: float) -> None:
        value = by_path.get(path) or getattr_signal(spec, path)
        if value and predicate(value):
            matched.append(SignalEvidence(
                path=path, value=str(value)[:64], origin="rule",
                weight=weight))
            nonlocal score
            score += weight

    task_type = str(spec.task.task_type or "")
    mapped = _TASK_TYPE_FAMILY.get(task_type)
    if mapped is not None and mapped[0] == family:
        hit("task.task_type", lambda _v: True, mapped[1])

    if family == "admin_facility_distribution":
        hit("statistics.dimension", lambda v: v == "administrative", 2.0)
        hit("measures.m1.statistic",
            lambda v: v in ("count", "sum", "share", "density", "none"), 1.0)
        hit("statistics.normalization",
            lambda v: v in ("per_area", "per_capita", "none"), 1.0)
        if task_type == "distribution_overview" and \
                str(spec.subject.type) == "poi":
            hit("subject.type", lambda v: v == "poi", 1.5)
    elif family == "service_accessibility":
        # 空间关系 kind 是 IR 的权威关系裁决（等时圈/邻近是科学语义
        # 承诺——权重高于任何无关系信号的泛化聚合组合）。
        hit("spatial_relation.kind", lambda v: v == "service_area", 6.0)
    elif family == "network_proximity":
        hit("spatial_relation.kind", lambda v: v == "near", 6.0)
    elif family == "hazard_exposure":
        category = str(spec.subject.category or "")
        if any(m in category for m in _HAZARD_MARKERS):
            hit("subject.category", lambda v: True, 4.0)
        # hazard 任务权威下的近邻关系 = 危险源缓冲语义（near 同时贡献
        # 网络邻近族——任务权威打破平局：暴露缓冲 ≠ 设施路由）。
        hit("spatial_relation.kind", lambda v: v == "near", 4.0)
    elif family == "temporal_change":
        hit("time.series", lambda v: v == "true", 3.0)
        hit("time.compare_start", lambda v: bool(v), 3.0)
    elif family == "statistical_zones":
        hit("statistics.dimension", lambda v: v == "grid", 3.0)
        hit("measures.m1.statistic",
            lambda v: v in ("rate", "ratio", "index", "density"), 2.0)
    elif family == "raster_zonal_summary":
        # 栅格主题是载荷类型事实：矢量族的水合路径消费不了 raster ref
        # （role_kind 门同样 typed 拒）——几何类型权威加权。
        hit("subject.type", lambda v: v == "raster", 5.0)

    # 去重（同 path 多次命中取首）并按声明序稳定
    seen: set = set()
    deduped: List[SignalEvidence] = []
    for s in matched:
        if s.path not in seen:
            seen.add(s.path)
            deduped.append(s)
    return score, deduped[:12]


def getattr_signal(spec: Any, path: str) -> Optional[str]:
    """信号路径 → 当前值（点路径只走两级；未知路径 None）。"""
    parts = path.split(".")
    cur: Any = spec
    for part in parts:
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            cur = getattr(cur, part, None)
        if cur is None:
            return None
    if isinstance(cur, bool):
        return "true" if cur else ""
    return str(cur) if cur != "" else None


def _expression_candidates(
    doc: RequirementDocument,
    data_facts: Optional[Dict[str, Dict[str, Any]]],
) -> List[ExpressionCandidate]:
    """表达候选（GC-02）：数据规模/几何/语义推导，用户点名分离。"""
    spec = doc.intent
    facts = data_facts or {}
    subject_facts = facts.get("subject") or {}
    point_count = int(subject_facts.get("feature_count") or 0)
    facts_present = bool(subject_facts)
    subject_type = str(spec.subject.type or "unknown")
    geometry_expectation = str(spec.core.geometry_expectation or "")
    normalization = str(spec.statistics.normalization or "none")
    _, _, denom_kind = statistic_semantics(
        str(spec.measures[0].statistic if spec.measures else "none"),
        normalization)

    user_kind = _GEOMETRY_KIND_EXPRESSION.get(
        str(spec.representation.geometry_kind or ""), "")

    def suitability(kind: str) -> Tuple[Optional[bool], Tuple[str, ...]]:
        """数据事实 → 候选适用性（None=事实缺席；fail-closed）。"""
        if not facts_present:
            return None, ("data_facts_absent: suitability unknown",)
        if kind in ("point_map",):
            return (point_count > 0, (f"subject feature_count={point_count}",))
        if kind in ("heatmap", "grid_density"):
            enough = point_count >= MIN_DENSITY_POINTS
            return enough, (
                f"subject feature_count={point_count} "
                f">={'MIN_DENSITY_POINTS' if enough else '>'}",)
        if kind in ("admin_choropleth", "area_density",
                    "per_capita_choropleth", "change_map"):
            boundary = facts.get("boundary") or {}
            bound_ok = bool(boundary)
            if kind == "per_capita_choropleth":
                denom = facts.get("denominator") or {}
                return (bound_ok and bool(denom),
                        ("boundary+denominator facts",))
            return bound_ok, ("boundary facts",)
        return None, ()

    out: List[ExpressionCandidate] = []
    is_point = subject_type == "poi" or geometry_expectation == "point"

    def append(kind: str, label: str, semantics_key: str, roles,
               reasons_extra: Tuple[str, ...] = ()) -> None:
        if len(out) >= 6:
            return
        suitable, reasons = suitability(kind)
        out.append(ExpressionCandidate(
            kind=kind, label_zh=label,
            user_requested=(user_kind == kind),
            suitable=suitable,
            reasons=tuple(dict.fromkeys(reasons + reasons_extra))[:4],
            requires_roles=tuple(roles[:6]),
            semantics=str(EXPRESSION_SEMANTICS.get(kind, "none")),
        ))

    if is_point:
        append("point_map", "点分布图", "point", ["subject"])
    append("admin_choropleth", "行政区分级统计图", "admin",
           ["subject", "boundary"])
    if denom_kind == "area" or normalization == "per_area" or \
            str(spec.measures[0].statistic if spec.measures else "") == "density":
        append("area_density", "面积密度图", "area", ["subject", "boundary"])
    if is_point and (normalization == "none"):
        append("heatmap", "热力密度图", "density", ["subject"])
    if normalization == "per_capita" or denom_kind == "field":
        append("per_capita_choropleth", "每万人率图", "per_capita",
               ["subject", "boundary", "denominator"],
               ("requires denominator facts — missing → count only",))

    # 用户点名表达不在推导集中 → 仍保留（suitability 照常裁决，不静默丢）
    if user_kind and user_kind not in {c.kind for c in out}:
        append(user_kind, user_kind, "none", ["subject"],
               ("user_requested kind not derivable — keep for disclosure",))

    # 排序：user_requested 优先，其次 kind 字典序（确定性）
    out.sort(key=lambda c: (not c.user_requested, c.kind))
    return out


_VECTOR_GEOMETRY_KINDS = frozenset(
    {"point", "polygon", "line", "network"})


def _subject_kind_excluded(family: str, subject_type: str) -> bool:
    """raster 主题 × 矢量 subject 角色需求 → typed 排除（role_kind 门）。"""
    if subject_type != "raster":
        return False
    for req in role_requirements(family):
        if req.role != "subject":
            continue
        kinds = set(req.geometry_kinds or ())
        if kinds and kinds <= _VECTOR_GEOMETRY_KINDS:
            return True
    return False


def compile_requirement_to_tasks(
    doc: RequirementDocument,
    *,
    data_facts: Optional[Dict[str, Dict[str, Any]]] = None,
    family_allowlist: Optional[Sequence[str]] = None,
) -> TaskCompilation:
    """RequirementDocument → TaskCompilation（纯函数；fail-closed）。"""
    spec = doc.intent
    digest = doc.requirements.derived_from or doc.document_id
    candidates: List[FamilyCandidate] = []
    allow = tuple(family_allowlist) if family_allowlist is not None else FAMILY_IDS
    signals = _signals_of(doc)

    block_reasons: List[str] = []
    disclosures: List[str] = []
    if not allow:
        block_reasons.append("family_allowlist_empty")

    for family in FAMILY_IDS:
        if family not in allow:
            continue
        # 载荷类型硬排除（ADR-0284 D3 同判）：raster 主题不能被矢量水合
        # 族的 subject 角色消费（run 期 role_kind 门同样 typed 拒）——
        # 编译期即 fail-closed 排除并披露，不留给运行期。
        if _subject_kind_excluded(family, str(spec.subject.type or "")):
            disclosures.append(
                f"family {family} excluded: subject_role_kind_mismatch"
                " (raster subject cannot hydrate as vector)")
            continue
        score, matched = _family_score(family, signals, doc)
        if score <= 0:
            continue
        candidates.append(FamilyCandidate(
            family=family,
            score=score,
            signals=matched,
            role_needs=[
                RoleNeed(role=r.role, required=r.required,
                         geometry_kinds=tuple(r.geometry_kinds or ()),
                         note=r.note or "")
                for r in role_requirements(family)[:8]],
        ))
    candidates.sort(key=lambda c: (-c.score, c.family))
    del candidates[4:]

    obligations: List[UnitObligation] = build_unit_obligations(
        spec, data_facts=data_facts)

    blocking_ambiguities = [
        a for a in spec.ambiguities
        if a.blocking and a.state == "open"]

    expressions = _expression_candidates(doc, data_facts)

    verdict = "compilable"
    clarification: Tuple[str, ...] = ()
    if blocking_ambiguities:
        verdict = "needs_clarification"
        clarification = tuple(
            f"{a.code}: {a.question_zh or a.path}"
            for a in blocking_ambiguities[:4])
    elif not candidates:
        verdict = "blocked"
        if not block_reasons:
            block_reasons.append("no_family_signal_match")

    for obligation in obligations:
        if obligation.verdict == "missing_denominator":
            disclosures.append(
                f"{obligation.measure_id}: {obligation.detail}")
    if any(c.suitable is None for c in expressions):
        disclosures.append(
            "expression suitability unknown: subject data facts not injected")
    if any(c.suitable is False and c.user_requested for c in expressions):
        disclosures.append(
            "user-requested expression not suitable from current facts — "
            "disclose instead of silently dropping")

    return TaskCompilation(
        document_id=doc.document_id[:64],
        requirement_digest=digest[:80],
        verdict=verdict,
        family_candidates=candidates,
        expression_candidates=expressions,
        unit_obligations=obligations,
        clarification_requests=clarification,
        disclosures=tuple(dict.fromkeys(disclosures))[:6],
        block_reasons=tuple(dict.fromkeys(block_reasons))[:4],
    )


__all__ = [
    "compile_requirement_to_tasks",
    "MIN_DENSITY_POINTS",
]
