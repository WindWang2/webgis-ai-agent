"""SkillPerformanceProfile —— 情境条件化的技能表现剖面。

不做第二套通用遥测平台：有界内存环 + 可选从 GoalSatisfaction /
replay 摘要更新。禁止把技能质量压成单一全局分。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.services.gis_harness.skills.policy import situation_signature
from app.services.gis_harness.skills.situation import SelectionFacts

MAX_PROFILES = 512
MAX_SAMPLES_PER_PROFILE = 64


class SkillPerformanceSample(BaseModel):
    """单次执行样本（有界）。"""
    success: bool = False
    goal_satisfaction: float = 0.0
    partial_completion: bool = False
    fallback_used: bool = False
    repair_count: int = 0
    latency_ms: int = 0
    resource_cost: float = 0.0
    cartography_quality: float = 0.0
    methodology_failures: int = 0


class SkillPerformanceProfile(BaseModel):
    """情境条件化剖面键 = skill_id + situation_signature 前缀。"""

    skill_id: str
    skill_version: str = ""
    situation_signature: str = ""
    task_family: str = ""
    geometry_kind: str = ""
    data_scale_bucket: str = ""  # unknown|tiny|small|medium|large
    aoi_scale: str = ""
    crs_class: str = ""
    online_offline: str = "unknown"
    desired_product: str = ""
    analysis_family: str = ""

    attempts: int = 0
    successes: int = 0
    goal_satisfaction_sum: float = 0.0
    partial_completions: int = 0
    fallback_count: int = 0
    repair_count_sum: int = 0
    latency_ms_sum: int = 0
    resource_cost_sum: float = 0.0
    cartography_quality_sum: float = 0.0
    methodology_failure_sum: int = 0

    def record(self, sample: SkillPerformanceSample) -> None:
        self.attempts += 1
        if sample.success:
            self.successes += 1
        self.goal_satisfaction_sum += float(sample.goal_satisfaction)
        if sample.partial_completion:
            self.partial_completions += 1
        if sample.fallback_used:
            self.fallback_count += 1
        self.repair_count_sum += int(sample.repair_count)
        self.latency_ms_sum += int(sample.latency_ms)
        self.resource_cost_sum += float(sample.resource_cost)
        self.cartography_quality_sum += float(sample.cartography_quality)
        self.methodology_failure_sum += int(sample.methodology_failures)

    @property
    def success_rate(self) -> float:
        if self.attempts <= 0:
            return 0.0
        return round(self.successes / self.attempts, 4)

    @property
    def mean_goal_satisfaction(self) -> float:
        if self.attempts <= 0:
            return 0.0
        return round(self.goal_satisfaction_sum / self.attempts, 4)

    @property
    def fallback_rate(self) -> float:
        if self.attempts <= 0:
            return 0.0
        return round(self.fallback_count / self.attempts, 4)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill_id[:64],
            "skill_version": self.skill_version[:16],
            "situation_signature": self.situation_signature[:32],
            "task_family": self.task_family[:48],
            "geometry_kind": self.geometry_kind[:24],
            "attempts": self.attempts,
            "success_rate": self.success_rate,
            "mean_goal_satisfaction": self.mean_goal_satisfaction,
            "fallback_rate": self.fallback_rate,
            "partial_completions": self.partial_completions,
            "repair_count_sum": self.repair_count_sum,
            "methodology_failure_sum": self.methodology_failure_sum,
        }


def _scale_bucket(feature_count: Optional[int]) -> str:
    if feature_count is None:
        return "unknown"
    if feature_count < 20:
        return "tiny"
    if feature_count < 200:
        return "small"
    if feature_count < 5000:
        return "medium"
    return "large"


def profile_key(skill_id: str, facts: SelectionFacts) -> str:
    return f"{skill_id}|{situation_signature(facts)}"


def profile_from_facts(
    skill_id: str,
    facts: SelectionFacts,
    *,
    skill_version: str = "",
) -> SkillPerformanceProfile:
    geom = facts.geometry_kinds[0] if facts.geometry_kinds else ""
    return SkillPerformanceProfile(
        skill_id=skill_id,
        skill_version=skill_version,
        situation_signature=situation_signature(facts),
        task_family=facts.task_type or "",
        geometry_kind=geom,
        data_scale_bucket=_scale_bucket(facts.feature_count),
        aoi_scale=facts.scope_unit or "",
        desired_product=facts.delivery_target or "",
        analysis_family=(facts.ontology_matches[0] if facts.ontology_matches else ""),
    )


class SkillPerformanceStore:
    """进程内有界剖面库（测试可 reset；非第二遥测平台）。"""

    def __init__(self, *, capacity: int = MAX_PROFILES) -> None:
        self._profiles: Dict[str, SkillPerformanceProfile] = {}
        self._capacity = capacity

    def get_or_create(
        self, skill_id: str, facts: SelectionFacts, *, skill_version: str = "",
    ) -> SkillPerformanceProfile:
        key = profile_key(skill_id, facts)
        prof = self._profiles.get(key)
        if prof is None:
            prof = profile_from_facts(skill_id, facts, skill_version=skill_version)
            self._profiles[key] = prof
            self._evict_if_needed()
        return prof

    def record(
        self,
        skill_id: str,
        facts: SelectionFacts,
        sample: SkillPerformanceSample,
        *,
        skill_version: str = "",
    ) -> SkillPerformanceProfile:
        prof = self.get_or_create(skill_id, facts, skill_version=skill_version)
        if skill_version:
            prof.skill_version = skill_version
        prof.record(sample)
        return prof

    def get(self, skill_id: str, facts: SelectionFacts) -> Optional[SkillPerformanceProfile]:
        return self._profiles.get(profile_key(skill_id, facts))

    def for_skill(self, skill_id: str) -> List[SkillPerformanceProfile]:
        return [p for p in self._profiles.values() if p.skill_id == skill_id]

    def _evict_if_needed(self) -> None:
        if len(self._profiles) <= self._capacity:
            return
        # 确定性淘汰：attempts 少、签名字典序靠前
        ordered = sorted(
            self._profiles.items(),
            key=lambda kv: (kv[1].attempts, kv[0]),
        )
        for key, _ in ordered[: len(self._profiles) - self._capacity]:
            del self._profiles[key]

    def clear(self) -> None:
        self._profiles.clear()

    def to_bounded_list(self, *, limit: int = 64) -> List[Dict[str, Any]]:
        items = sorted(self._profiles.values(), key=lambda p: (p.skill_id, p.situation_signature))
        return [p.to_bounded_dict() for p in items[:limit]]


_store: Optional[SkillPerformanceStore] = None


def get_skill_performance_store() -> SkillPerformanceStore:
    global _store
    if _store is None:
        _store = SkillPerformanceStore()
    return _store


def reset_skill_performance_store() -> None:
    global _store
    _store = None


__all__ = [
    "MAX_PROFILES",
    "SkillPerformanceProfile",
    "SkillPerformanceSample",
    "SkillPerformanceStore",
    "get_skill_performance_store",
    "profile_from_facts",
    "profile_key",
    "reset_skill_performance_store",
]
