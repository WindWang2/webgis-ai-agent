"""SkillPolicy —— 生产规划何时信任 GIS Skill（Direction 02 / ADR-0182 之上）。

权威缝（最小）：

    SelectionFacts → SkillPolicy.resolve → SkillPolicyDecision
                                           ├─ guide / execute_guided → projection
                                           ├─ shadow → induced 旁路评估（零写生产）
                                           └─ none / fallback / blocked → 既有 planner

红线：

- 不替代 SkillResolver（选择仍走 resolver）；本层裁决 **信任与模式**；
- 不绕过 capability / security / spatial guardrail / governor / schema；
- induced 永不直接控制生产执行（最多 shadow）；
- 运行期不修改 core 技能资产；
- 同输入同决策（确定性）；``GIS_SKILL_POLICY=0`` 强制 none。
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

from app.services.gis_harness.skills.contract import SkillContract
from app.services.gis_harness.skills.resolver import (
    CONFIDENCE_HIGH,
    CONFIDENCE_MEDIUM,
    SkillCandidate,
    SkillResolver,
    SkillSelectionResult,
)
from app.services.gis_harness.skills.situation import SelectionFacts

#: 策略模式词表（与 GOAL §4 对齐；闭合）。
SkillPolicyMode = Literal[
    "none",
    "guide",
    "execute_guided",
    "shadow",
    "blocked",
    "fallback",
]

#: 信任档（映射既有 pack/deprecated/quarantine；不新造 pack registry）。
SkillTrustTier = Literal[
    "core",
    "candidate",
    "experimental",
    "quarantined",
    "deprecated",
]

#: 生产信任路径允许的 pack（审定库）。
TRUSTED_PACKS: Tuple[str, ...] = ("core",)

#: 环境开关：关闭后策略恒为 none（干净回落既有 harness planning）。
SKILL_POLICY_ENV = "GIS_SKILL_POLICY"

#: execute_guided 额外要求：高置信且无澄清歧义。
_EXECUTE_MIN_CONFIDENCE = CONFIDENCE_HIGH


class SkillPolicyDecision(BaseModel):
    """生产技能策略裁决（有界、可审计、可进 Pi context）。"""

    mode: SkillPolicyMode = "none"
    selected_skill: Optional[str] = None
    skill_version: str = ""
    trust_tier: SkillTrustTier = "core"
    confidence: float = 0.0
    confidence_band: str = ""
    eligibility_ok: bool = True
    situation_signature: str = ""
    reasons: List[str] = Field(default_factory=list)
    fallback_reason: str = ""
    shadow_candidate: Optional[str] = None
    shadow_candidate_version: str = ""
    rejected_codes: List[str] = Field(default_factory=list)
    matched_signals: List[str] = Field(default_factory=list)
    clarification_code: str = ""

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "selected_skill": (self.selected_skill or "")[:64] or None,
            "skill_version": self.skill_version[:16],
            "trust_tier": self.trust_tier,
            "confidence": round(self.confidence, 3),
            "confidence_band": self.confidence_band[:16],
            "eligibility_ok": self.eligibility_ok,
            "situation_signature": self.situation_signature[:32],
            "reasons": [r[:64] for r in self.reasons[:8]],
            "fallback_reason": self.fallback_reason[:120],
            "shadow_candidate": (self.shadow_candidate or "")[:64] or None,
            "shadow_candidate_version": self.shadow_candidate_version[:16],
            "rejected_codes": [c[:48] for c in self.rejected_codes[:8]],
            "matched_signals": [s[:48] for s in self.matched_signals[:8]],
            "clarification_code": self.clarification_code[:48],
        }

    def pi_context_card(self) -> Dict[str, Any]:
        """Pi 渐进披露：仅决策卡片，禁止注入全文库。"""
        if self.mode in ("none", "fallback", "blocked") and not self.selected_skill:
            return {
                "selected_procedure": None,
                "mode": self.mode,
                "fallback": self.fallback_reason[:160] or "use_existing_planner",
            }
        return {
            "selected_procedure": {
                "id": (self.selected_skill or "")[:64],
                "version": self.skill_version[:16],
                "trust_tier": self.trust_tier,
                "mode": self.mode,
                "confidence": round(self.confidence, 3),
                "critical_obligations": self.reasons[:4],
                "next_recommended_step": (
                    self.matched_signals[0][:48] if self.matched_signals else ""
                ),
                "fallback": self.fallback_reason[:120] or "existing_harness_planning",
            },
            "shadow_candidate": self.shadow_candidate,
        }


def situation_signature(facts: SelectionFacts) -> str:
    """有界 situation 指纹（性能剖面 / 影子对照用；非加密）。"""
    payload = {
        "task": (facts.task_type or "")[:48],
        "geom": sorted(g[:16] for g in facts.geometry_kinds[:6]),
        "roles": sorted(r[:24] for r in facts.data_roles[:8]),
        "scope_unit": (facts.scope_unit or "")[:24],
        "delivery": (facts.delivery_target or "")[:24],
        "measure": (facts.measure_semantics or "")[:24],
        "temporal": (facts.temporal_mode or "")[:24],
        "onto": sorted(o[:48] for o in facts.ontology_matches[:6]),
        "online": "unknown",  # reserved; facts 面暂无 online 字段
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8"), usedforsecurity=False).hexdigest()[:24]


def trust_tier_for(skill: Optional[SkillContract], *, quarantined: bool = False) -> SkillTrustTier:
    """pack/deprecated/quarantine → trust tier（纯函数）。"""
    if skill is None:
        return "experimental"
    if quarantined:
        return "quarantined"
    if skill.deprecated:
        return "deprecated"
    if skill.pack == "core":
        return "core"
    if skill.pack == "induced":
        return "experimental"
    return "experimental"


def policy_enabled() -> bool:
    """Kill-switch：``GIS_SKILL_POLICY=0/false/off`` → 关闭。"""
    raw = (os.environ.get(SKILL_POLICY_ENV) or "1").strip().lower()
    return raw not in ("0", "false", "off", "no")


class SkillPolicy:
    """生产技能策略：包装 SkillResolver，裁决信任模式与回落。

    ``trusted_resolver``：仅 core（或显式注入的审定集合）。
    ``shadow_resolver``：可选 induced / experimental 集合（旁路）。
    ``quarantine_ids``：显式隔离集合（blocked）。
    """

    def __init__(
        self,
        trusted_resolver: SkillResolver,
        *,
        shadow_resolver: Optional[SkillResolver] = None,
        quarantine_ids: Optional[Tuple[str, ...]] = None,
        execute_min_confidence: float = _EXECUTE_MIN_CONFIDENCE,
    ) -> None:
        self._trusted = trusted_resolver
        self._shadow = shadow_resolver
        self._quarantine = set(quarantine_ids or ())
        self._execute_min = execute_min_confidence

    @classmethod
    def from_library(cls, library, *, shadow_resolver: Optional[SkillResolver] = None) -> "SkillPolicy":
        """从 SkillLibrary 构造（trusted = library.resolver）。"""
        return cls(library.resolver, shadow_resolver=shadow_resolver)

    def resolve(
        self,
        facts: SelectionFacts,
        *,
        prefer_execute: bool = True,
        allow_shadow: bool = True,
    ) -> SkillPolicyDecision:
        """事实 → 策略裁决（确定性）。"""
        sig = situation_signature(facts)
        if not policy_enabled():
            return SkillPolicyDecision(
                mode="none",
                situation_signature=sig,
                reasons=["policy_disabled"],
                fallback_reason="GIS_SKILL_POLICY disabled; use existing planner",
            )

        selection = self._trusted.resolve(facts, packs=TRUSTED_PACKS)
        decision = self._decide_trusted(selection, facts, sig, prefer_execute=prefer_execute)

        if allow_shadow and self._shadow is not None:
            shadow = self._pick_shadow(facts, exclude=decision.selected_skill)
            if shadow is not None:
                decision.shadow_candidate = shadow.skill_id
                decision.shadow_candidate_version = shadow.skill_version
                if "shadow_candidate" not in decision.reasons:
                    decision.reasons.append("shadow_candidate")

        return decision

    def _decide_trusted(
        self,
        selection: SkillSelectionResult,
        facts: SelectionFacts,
        sig: str,
        *,
        prefer_execute: bool,
    ) -> SkillPolicyDecision:
        rejected_codes: List[str] = []
        for rej in selection.rejected[:12]:
            rejected_codes.extend(rej.reason_codes[:4])

        clarification = (
            selection.clarification.reason_code if selection.clarification else ""
        )

        if selection.selected is None or selection.top is None:
            mode: SkillPolicyMode = "none"
            fallback = "no_suitable_skill"
            if clarification == "EMPTY_GOAL":
                fallback = "empty_goal"
            elif clarification == "NO_MATCH":
                fallback = "no_match"
            elif rejected_codes:
                mode = "fallback"
                fallback = "all_candidates_ineligible"
            return SkillPolicyDecision(
                mode=mode,
                situation_signature=sig,
                reasons=["no_trusted_selection"],
                fallback_reason=fallback,
                rejected_codes=rejected_codes[:8],
                clarification_code=clarification,
                eligibility_ok=not bool(rejected_codes),
            )

        top = selection.top
        skill = self._trusted.get(top.skill_id)
        if skill is not None and skill.id in self._quarantine:
            return SkillPolicyDecision(
                mode="blocked",
                selected_skill=skill.id,
                skill_version=skill.version,
                trust_tier="quarantined",
                confidence=top.confidence,
                confidence_band=top.confidence_band,
                eligibility_ok=False,
                situation_signature=sig,
                reasons=["quarantined"],
                fallback_reason="skill_quarantined",
                rejected_codes=["QUARANTINED"],
                matched_signals=list(top.matched_signals)[:8],
                clarification_code=clarification,
            )

        tier = trust_tier_for(skill)
        if skill is not None and skill.deprecated:
            return SkillPolicyDecision(
                mode="fallback",
                selected_skill=skill.id,
                skill_version=skill.version,
                trust_tier="deprecated",
                confidence=top.confidence,
                confidence_band=top.confidence_band,
                eligibility_ok=True,
                situation_signature=sig,
                reasons=["deprecated_skill"],
                fallback_reason=f"deprecated_by:{skill.deprecated_by}" if skill.deprecated_by else "deprecated",
                matched_signals=list(top.matched_signals)[:8],
                clarification_code=clarification,
            )

        # 低置信 / 歧义 → 不强制技能，干净回落
        if top.confidence < CONFIDENCE_MEDIUM:
            return SkillPolicyDecision(
                mode="fallback",
                selected_skill=top.skill_id,
                skill_version=top.skill_version,
                trust_tier=tier,
                confidence=top.confidence,
                confidence_band=top.confidence_band,
                eligibility_ok=True,
                situation_signature=sig,
                reasons=["low_confidence"],
                fallback_reason="confidence_below_medium",
                matched_signals=list(top.matched_signals)[:8],
                clarification_code=clarification,
            )

        if clarification == "AMBIGUOUS_TOP_CANDIDATES":
            return SkillPolicyDecision(
                mode="guide",
                selected_skill=top.skill_id,
                skill_version=top.skill_version,
                trust_tier=tier,
                confidence=top.confidence,
                confidence_band=top.confidence_band,
                eligibility_ok=True,
                situation_signature=sig,
                reasons=["ambiguous_top_candidates", "guide_only"],
                fallback_reason="ambiguous; prefer clarification then existing planner",
                matched_signals=list(top.matched_signals)[:8],
                clarification_code=clarification,
            )

        if prefer_execute and top.confidence >= self._execute_min and tier == "core":
            mode = "execute_guided"
            reasons = ["high_confidence_core", "eligible"]
        else:
            mode = "guide"
            reasons = ["medium_or_guide_preference", "eligible"]

        return SkillPolicyDecision(
            mode=mode,
            selected_skill=top.skill_id,
            skill_version=top.skill_version,
            trust_tier=tier,
            confidence=top.confidence,
            confidence_band=top.confidence_band,
            eligibility_ok=True,
            situation_signature=sig,
            reasons=reasons,
            fallback_reason="existing_harness_planning_on_step_failure",
            rejected_codes=rejected_codes[:8],
            matched_signals=list(top.matched_signals)[:8],
            clarification_code=clarification,
        )

    def _pick_shadow(
        self, facts: SelectionFacts, *, exclude: Optional[str],
    ) -> Optional[SkillCandidate]:
        assert self._shadow is not None
        result = self._shadow.resolve(facts, packs=("induced",), limit=3)
        for cand in result.ranked:
            if cand.skill_id == exclude:
                continue
            if cand.skill_id in self._quarantine:
                continue
            if cand.confidence < CONFIDENCE_MEDIUM:
                continue
            skill = self._shadow.get(cand.skill_id)
            if skill is None or skill.pack != "induced":
                continue
            return cand
        return None


def resolve_for_planning(
    facts: SelectionFacts,
    *,
    library=None,
    shadow_resolver: Optional[SkillResolver] = None,
    prefer_execute: bool = True,
    allow_shadow: bool = True,
) -> SkillPolicyDecision:
    """热路径入口：Situation/Intent 事实 → 策略裁决。

    ``library`` 缺省取 ``get_skill_library()``。不抛则恒有决策对象。
    """
    if library is None:
        from app.services.gis_harness.skills.loader import get_skill_library
        library = get_skill_library()
    policy = SkillPolicy.from_library(library, shadow_resolver=shadow_resolver)
    return policy.resolve(
        facts, prefer_execute=prefer_execute, allow_shadow=allow_shadow,
    )


__all__ = [
    "CONFIDENCE_HIGH",
    "CONFIDENCE_MEDIUM",
    "SKILL_POLICY_ENV",
    "TRUSTED_PACKS",
    "SkillPolicy",
    "SkillPolicyDecision",
    "SkillPolicyMode",
    "SkillTrustTier",
    "policy_enabled",
    "resolve_for_planning",
    "situation_signature",
    "trust_tier_for",
]
