"""Promotion evidence —— 自动晋升提案（永不自动改 core）。

induced → N 次有效 shadow/evaluation → PromotionCandidateReport。
Core 晋升仍走 code review / YAML PR。
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Set

from pydantic import BaseModel, Field

from app.services.gis_harness.skills.performance import (
    SkillPerformanceProfile,
    SkillPerformanceStore,
)
from app.services.gis_harness.skills.shadow import ShadowEvaluationReport

PromotionDisposition = Literal[
    "keep_shadow",
    "candidate",
    "quarantine",
    "reject",
]

#: 晋升候选最低支持次数（单次成功绝不晋升）。
MIN_SUPPORT_FOR_CANDIDATE = 5
#: 最低情境多样性（不同 situation_signature 数）。
MIN_SITUATION_DIVERSITY = 3
#: 最低成功率。
MIN_SUCCESS_RATE = 0.8
#: 最高回落率。
MAX_FALLBACK_RATE = 0.25


class PromotionEvidencePoint(BaseModel):
    situation_signature: str = ""
    success: bool = False
    goal_satisfaction: float = 0.0
    counterexample: bool = False
    notes: str = ""


class PromotionCandidateReport(BaseModel):
    skill_id: str
    skill_version: str = ""
    support_count: int = 0
    domain_diversity: int = 0
    parameter_diversity: int = 0
    geometry_diversity: int = 0
    situation_diversity: int = 0
    success_rate: float = 0.0
    goal_satisfaction_distribution: List[float] = Field(default_factory=list)
    failure_cases: List[str] = Field(default_factory=list)
    counterexamples: List[str] = Field(default_factory=list)
    resource_cost_mean: float = 0.0
    fallback_rate: float = 0.0
    quality_result_mean: float = 0.0
    security_sandbox_status: str = "unknown"
    recommended_disposition: PromotionDisposition = "keep_shadow"
    reasons: List[str] = Field(default_factory=list)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill_id[:64],
            "skill_version": self.skill_version[:16],
            "support_count": self.support_count,
            "domain_diversity": self.domain_diversity,
            "parameter_diversity": self.parameter_diversity,
            "geometry_diversity": self.geometry_diversity,
            "situation_diversity": self.situation_diversity,
            "success_rate": round(self.success_rate, 4),
            "goal_satisfaction_distribution": [
                round(x, 3) for x in self.goal_satisfaction_distribution[:16]
            ],
            "failure_cases": [f[:80] for f in self.failure_cases[:8]],
            "counterexamples": [c[:80] for c in self.counterexamples[:8]],
            "resource_cost_mean": round(self.resource_cost_mean, 3),
            "fallback_rate": round(self.fallback_rate, 4),
            "quality_result_mean": round(self.quality_result_mean, 3),
            "security_sandbox_status": self.security_sandbox_status[:32],
            "recommended_disposition": self.recommended_disposition,
            "reasons": [r[:80] for r in self.reasons[:8]],
        }


def _diversity(profiles: List[SkillPerformanceProfile]) -> Dict[str, int]:
    situations: Set[str] = {p.situation_signature for p in profiles if p.situation_signature}
    geometries: Set[str] = {p.geometry_kind for p in profiles if p.geometry_kind}
    domains: Set[str] = {p.analysis_family or p.task_family for p in profiles if (p.analysis_family or p.task_family)}
    scales: Set[str] = {p.data_scale_bucket for p in profiles if p.data_scale_bucket}
    return {
        "situation": len(situations),
        "geometry": len(geometries),
        "domain": len(domains),
        "parameter": len(scales),  # 用规模桶近似参数多样性
    }


def build_promotion_report(
    skill_id: str,
    *,
    skill_version: str = "",
    store: Optional[SkillPerformanceStore] = None,
    shadow_reports: Optional[List[ShadowEvaluationReport]] = None,
    evidence_points: Optional[List[PromotionEvidencePoint]] = None,
    security_sandbox_status: str = "unknown",
    min_support: int = MIN_SUPPORT_FOR_CANDIDATE,
    min_situation_diversity: int = MIN_SITUATION_DIVERSITY,
    min_success_rate: float = MIN_SUCCESS_RATE,
    max_fallback_rate: float = MAX_FALLBACK_RATE,
) -> PromotionCandidateReport:
    """聚合证据 → 晋升提案（fail-closed：证据不足 → keep_shadow/reject）。"""
    store = store or SkillPerformanceStore()
    profiles = store.for_skill(skill_id)
    points = list(evidence_points or [])
    shadows = list(shadow_reports or [])

    attempts = sum(p.attempts for p in profiles) + len(points)
    successes = sum(p.successes for p in profiles) + sum(1 for p in points if p.success)
    fallbacks = sum(p.fallback_count for p in profiles)
    goal_vals: List[float] = []
    for p in profiles:
        if p.attempts:
            goal_vals.append(p.mean_goal_satisfaction)
    for pt in points:
        goal_vals.append(pt.goal_satisfaction)

    counterexamples = [pt.notes or pt.situation_signature for pt in points if pt.counterexample]
    # 影子评估中 ineligible / 显著分歧也记为反例信号
    for sh in shadows:
        if not sh.eligible or "induced_ineligible" in sh.divergences:
            counterexamples.append(f"shadow:{sh.induced_skill_id}:ineligible")
        elif "capability_set_differs" in sh.divergences and sh.capability_overlap < 0.3:
            counterexamples.append(f"shadow:{sh.induced_skill_id}:cap_diverge")

    failures: List[str] = []
    for p in profiles:
        if p.attempts and p.success_rate < 0.5:
            failures.append(f"low_success@{p.situation_signature[:16]}")
        if p.methodology_failure_sum:
            failures.append(f"methodology@{p.situation_signature[:16]}")

    div = _diversity(profiles)
    # evidence_points 情境也计入
    sit_extra = {pt.situation_signature for pt in points if pt.situation_signature}
    situation_diversity = max(div["situation"], len(sit_extra)) if sit_extra else div["situation"]
    if sit_extra and div["situation"]:
        situation_diversity = len(
            {p.situation_signature for p in profiles if p.situation_signature} | sit_extra
        )

    success_rate = (successes / attempts) if attempts else 0.0
    fallback_rate = (fallbacks / attempts) if attempts else 0.0
    resource_mean = 0.0
    quality_mean = 0.0
    if profiles:
        tot_a = sum(p.attempts for p in profiles) or 1
        resource_mean = sum(p.resource_cost_sum for p in profiles) / tot_a
        quality_mean = sum(p.cartography_quality_sum for p in profiles) / tot_a

    reasons: List[str] = []
    disposition: PromotionDisposition = "keep_shadow"

    if security_sandbox_status in ("failed", "quarantined", "unsafe"):
        disposition = "quarantine"
        reasons.append("security_sandbox_failed")
    elif counterexamples:
        disposition = "reject" if len(counterexamples) >= 2 else "keep_shadow"
        reasons.append("counterexamples_present")
    elif attempts < min_support:
        disposition = "keep_shadow"
        reasons.append("insufficient_support")
    elif situation_diversity < min_situation_diversity:
        disposition = "keep_shadow"
        reasons.append("insufficient_situation_diversity")
    elif success_rate < min_success_rate:
        disposition = "keep_shadow"
        reasons.append("success_rate_below_threshold")
    elif fallback_rate > max_fallback_rate:
        disposition = "keep_shadow"
        reasons.append("fallback_rate_too_high")
    elif div["geometry"] < 2 and situation_diversity < (min_situation_diversity + 1):
        # 反过拟合：单一几何/窄情境不晋升
        disposition = "keep_shadow"
        reasons.append("risk_of_overfit")
    else:
        disposition = "candidate"
        reasons.append("evidence_sufficient_for_candidate_proposal")

    # 单次成功钉死：即使阈值被测例改小，attempts==1 也不得 candidate
    if attempts <= 1 and disposition == "candidate":
        disposition = "keep_shadow"
        reasons.append("single_success_never_promotes")

    return PromotionCandidateReport(
        skill_id=skill_id,
        skill_version=skill_version,
        support_count=attempts,
        domain_diversity=div["domain"],
        parameter_diversity=div["parameter"],
        geometry_diversity=div["geometry"],
        situation_diversity=situation_diversity,
        success_rate=round(success_rate, 4),
        goal_satisfaction_distribution=[round(x, 3) for x in goal_vals[:16]],
        failure_cases=failures[:8],
        counterexamples=counterexamples[:8],
        resource_cost_mean=round(resource_mean, 3),
        fallback_rate=round(fallback_rate, 4),
        quality_result_mean=round(quality_mean, 3),
        security_sandbox_status=security_sandbox_status,
        recommended_disposition=disposition,
        reasons=reasons,
    )


__all__ = [
    "MIN_SUPPORT_FOR_CANDIDATE",
    "MIN_SITUATION_DIVERSITY",
    "MIN_SUCCESS_RATE",
    "MAX_FALLBACK_RATE",
    "PromotionCandidateReport",
    "PromotionDisposition",
    "PromotionEvidencePoint",
    "build_promotion_report",
]
