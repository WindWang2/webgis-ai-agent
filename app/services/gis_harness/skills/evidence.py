"""Skill Evidence —— 技能选择与过程证据记录（ADR-0182 §2.7；goal S21）。

执行时记录：selected_skill / skill_version / selection_reason /
procedure_step 状态 / skipped_step（带披露）/ fallback / completion。
**不记录模型隐式 CoT** —— 证据是结构化事实，供 Goal Evaluator 与
replay 消费。有界环形缓冲，防止长会话膨胀。
"""
from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, Field

#: 证据事件词表。
SKILL_EVIDENCE_EVENTS = (
    "skill_selected",     # 选择完成（带 reason/confidence）
    "step_started",       # 过程步骤开始
    "step_completed",     # 过程步骤完成（带 evidence kinds）
    "step_skipped",       # 步骤跳过（必须带披露与原因）
    "fallback_triggered", # 回退触发（带 trigger/action/disclosure）
    "composition_planned",# 组合计划（带执行顺序）
    "skill_completed",    # 技能完成（带 completion evidence）
    "policy_decided",     # SkillPolicy 裁决（mode/trust/confidence）
    "shadow_evaluated",   # induced 影子评估（零写生产）
    "promotion_proposed", # 晋升提案（非自动改 core）
)

#: 缓冲上限（环形）。
MAX_RECORDS = 256


class SkillEvidenceRecord(BaseModel):
    """一条技能证据（结构化事实；禁 CoT —— 字段闭合，无自由文本大字段）。"""
    event: str                          # ⊆ SKILL_EVIDENCE_EVENTS
    skill_id: str = ""
    skill_version: str = ""
    selection_reason: List[str] = Field(default_factory=list)  # matched signals
    confidence: float = 0.0
    step_id: str = ""
    evidence_kinds: List[str] = Field(default_factory=list)
    skip_disclosure: str = ""
    fallback_trigger: str = ""
    fallback_action: str = ""
    disclosure: str = ""
    composition_id: str = ""
    execution_order: List[str] = Field(default_factory=list)
    notes: str = ""                     # ≤200 字有界备注

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "event": self.event,
            "skill_id": self.skill_id[:64],
            "skill_version": self.skill_version[:16],
            "selection_reason": [r[:48] for r in self.selection_reason[:6]],
            "confidence": round(self.confidence, 2),
            "step_id": self.step_id[:48],
            "evidence_kinds": [k[:48] for k in self.evidence_kinds[:8]],
            "skip_disclosure": self.skip_disclosure[:160],
            "fallback_trigger": self.fallback_trigger[:32],
            "fallback_action": self.fallback_action[:32],
            "disclosure": self.disclosure[:200],
            "composition_id": self.composition_id[:64],
            "execution_order": [s[:64] for s in self.execution_order[:8]],
            "notes": self.notes[:200],
        }


class SkillEvidenceRecorder:
    """会话级证据记录器（有界环形；纯内存，无 I/O）。"""

    def __init__(self, *, capacity: int = MAX_RECORDS) -> None:
        self._records: List[SkillEvidenceRecord] = []
        self._capacity = capacity

    def record(self, rec: SkillEvidenceRecord) -> None:
        if rec.event not in SKILL_EVIDENCE_EVENTS:
            raise ValueError(f"unknown skill evidence event {rec.event}")
        self._records.append(rec)
        if len(self._records) > self._capacity:
            del self._records[: len(self._records) - self._capacity]

    # ── 便捷工厂 ─────────────────────────────────────────────────────
    def record_selection(
        self, skill_id: str, skill_version: str,
        selection_reason: List[str], confidence: float,
    ) -> None:
        self.record(SkillEvidenceRecord(
            event="skill_selected", skill_id=skill_id,
            skill_version=skill_version,
            selection_reason=selection_reason, confidence=confidence,
        ))

    def record_step(
        self, skill_id: str, step_id: str, evidence_kinds: List[str],
    ) -> None:
        self.record(SkillEvidenceRecord(
            event="step_completed", skill_id=skill_id, step_id=step_id,
            evidence_kinds=evidence_kinds,
        ))

    def record_skip(
        self, skill_id: str, step_id: str, disclosure: str,
    ) -> None:
        self.record(SkillEvidenceRecord(
            event="step_skipped", skill_id=skill_id, step_id=step_id,
            skip_disclosure=disclosure,
        ))

    def record_fallback(
        self, skill_id: str, trigger: str, action: str, disclosure: str,
    ) -> None:
        self.record(SkillEvidenceRecord(
            event="fallback_triggered", skill_id=skill_id,
            fallback_trigger=trigger, fallback_action=action,
            disclosure=disclosure,
        ))

    # ── 读取面 ───────────────────────────────────────────────────────
    @property
    def records(self) -> List[SkillEvidenceRecord]:
        return list(self._records)

    def by_skill(self, skill_id: str) -> List[SkillEvidenceRecord]:
        return [r for r in self._records if r.skill_id == skill_id]

    def evidence_kinds_for(self, skill_id: str) -> List[str]:
        """该技能已产出的全部证据种类（replay 的对账输入）。"""
        kinds: List[str] = []
        for r in self._records:
            if r.skill_id == skill_id:
                for k in r.evidence_kinds:
                    if k not in kinds:
                        kinds.append(k)
        return kinds

    def to_bounded_list(self, *, limit: int = 64) -> List[Dict[str, Any]]:
        return [r.to_bounded_dict() for r in self._records[-limit:]]


    def record_policy(
        self, *, skill_id: str, skill_version: str, mode: str,
        trust_tier: str, confidence: float, reasons: list,
    ) -> None:
        self.record(SkillEvidenceRecord(
            event="policy_decided", skill_id=skill_id,
            skill_version=skill_version, confidence=confidence,
            selection_reason=[mode, trust_tier, *[str(r) for r in reasons[:4]]],
            notes=f"mode={mode};tier={trust_tier}"[:200],
        ))

    def record_shadow(
        self, *, skill_id: str, skill_version: str, notes: str = "",
    ) -> None:
        self.record(SkillEvidenceRecord(
            event="shadow_evaluated", skill_id=skill_id,
            skill_version=skill_version,
            disclosure="shadow_evaluation_no_mutation",
            notes=notes[:200],
        ))

    def record_promotion(
        self, *, skill_id: str, skill_version: str, disposition: str,
    ) -> None:
        self.record(SkillEvidenceRecord(
            event="promotion_proposed", skill_id=skill_id,
            skill_version=skill_version,
            notes=f"disposition={disposition}"[:200],
        ))

    def clear(self) -> None:
        self._records.clear()


__all__ = [
    "SKILL_EVIDENCE_EVENTS",
    "MAX_RECORDS",
    "SkillEvidenceRecord",
    "SkillEvidenceRecorder",
]
