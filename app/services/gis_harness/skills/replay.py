"""Procedure Replay —— 过程重放验证（ADR-0182 §2.7；goal S25）。

对选中的 Skill 做 **deterministic procedure projection**：不是让 LLM
判断"看起来差不多"，而是逐一验证 required procedure obligations 是否
都进入了 plan/evidence：

- 每个步骤 → covered（计划含该步能力+证据）/ missing（required 步骤
  无踪迹）/ skipped_declared（带披露跳过）/ unknown（投影缺事实）；
- 每条义务 → satisfied / missing / unknown；
- 分母义务（rate/density/percentage）有专门核查。

纯函数：输入 = 契约 + plan/evidence 投影 dict；零 LLM、零 I/O。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.services.gis_harness.skills.contract import SkillContract
from app.services.gis_harness.skills.semantics import DENOMINATOR_REQUIRED_MEASURES

#: 步骤覆盖状态词表。
STEP_REPLAY_STATES = ("covered", "missing", "skipped_declared", "unknown")

#: 义务满足状态词表。
OBLIGATION_REPLAY_STATES = ("satisfied", "missing", "unknown")


class StepReplay(BaseModel):
    step_id: str
    state: str                         # ⊆ STEP_REPLAY_STATES
    detail: str = ""
    matched_evidence: List[str] = Field(default_factory=list)


class ObligationReplay(BaseModel):
    obligation_id: str
    state: str                         # ⊆ OBLIGATION_REPLAY_STATES
    detail: str = ""


class ProcedureReplayReport(BaseModel):
    skill_id: str
    skill_version: str = ""
    steps: List[StepReplay] = Field(default_factory=list)
    obligations: List[ObligationReplay] = Field(default_factory=list)
    complete: bool = False             # 全部 required 步骤 covered/skipped_declared 且无 missing 义务

    @property
    def missing_steps(self) -> List[str]:
        return [s.step_id for s in self.steps if s.state == "missing"]

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill_id[:64],
            "skill_version": self.skill_version[:16],
            "complete": self.complete,
            "missing_steps": self.missing_steps[:8],
            "steps": [
                {"step_id": s.step_id[:48], "state": s.state,
                 "detail": s.detail[:120],
                 "matched_evidence": [e[:48] for e in s.matched_evidence[:6]]}
                for s in self.steps[:32]
            ],
            "obligations": [
                {"obligation_id": o.obligation_id[:64], "state": o.state,
                 "detail": o.detail[:120]}
                for o in self.obligations[:16]
            ],
        }


def replay_procedure(
    skill: SkillContract,
    plan_facts: Optional[Dict[str, Any]] = None,
    evidence_facts: Optional[Dict[str, Any]] = None,
) -> ProcedureReplayReport:
    """过程重放：契约 × (plan, evidence) 投影 → 逐步覆盖报告。

    ``plan_facts`` 期望形态（宽松读取，缺键=unknown）::

        {
          "capabilities": [...],          # 计划绑定的 capability id
          "step_ids": [...],              # 计划声明的步骤 id（可选）
          "skipped_steps": [{"step_id": .., "disclosure": ..}],
          "evidence_kinds": [...],        # 计划/执行已产出的证据种类
          "has_denominator_evidence": bool,   # 分母证据在场（可选显式声明）
        }

    ``evidence_facts`` 同形态（EvidenceRecorder 投影），两者合并对账。
    """
    plan = plan_facts if isinstance(plan_facts, dict) else {}
    evd = evidence_facts if isinstance(evidence_facts, dict) else {}

    capabilities = set(plan.get("capabilities") or []) | set(evd.get("capabilities") or [])
    declared_steps = set(plan.get("step_ids") or []) | set(evd.get("step_ids") or [])
    produced_evidence = set(plan.get("evidence_kinds") or []) | set(evd.get("evidence_kinds") or [])
    skipped: Dict[str, str] = {}
    for item in list(plan.get("skipped_steps") or []) + list(evd.get("skipped_steps") or []):
        if isinstance(item, dict) and item.get("step_id"):
            skipped[str(item["step_id"])] = str(item.get("disclosure") or "")

    report = ProcedureReplayReport(
        skill_id=skill.id, skill_version=skill.version)

    for step in skill.procedure.steps:
        matched: List[str] = []
        # 能力踪迹：步骤声明的 capability 与计划能力面有交集
        cap_traced = (not step.capability_refs) or (
            set(step.capability_refs) & capabilities)
        # 证据踪迹：步骤要求的证据种类已在场
        ev_hits = [ev.evidence_kind for ev in step.evidence_requirements
                   if ev.evidence_kind in produced_evidence]
        ev_needed = [ev.evidence_kind for ev in step.evidence_requirements]
        step_declared = step.step_id in declared_steps

        if step.step_id in skipped:
            # S25 红线：跳过声明只对**允许跳过**的步骤有效。required 且
            # skip_policy=never 的步骤被声明跳过 = 违规，判 missing（带
            # 说明）—— 否则 plan_facts 可绕过验收门（LLM 自己喂投影）。
            if step.required and step.skip_policy == "never":
                state = "missing"
                detail = "required 步骤（skip_policy=never）被声明跳过：跳过无效"
            else:
                state = "skipped_declared"
                detail = skipped[step.step_id] or "已声明的跳过（无披露文本）"
        elif ev_needed:
            if len(ev_hits) == len(ev_needed):
                state, detail = "covered", ""
            elif ev_hits:
                state = "unknown"
                detail = f"证据部分在场：{ev_hits}（缺 {sorted(set(ev_needed) - set(ev_hits))}）"
            elif step_declared and cap_traced:
                state = "unknown"
                detail = "步骤已入计划，证据待产出"
            else:
                state = "missing" if step.required else "unknown"
                detail = "required 步骤无计划与证据踪迹" if step.required else \
                    "可选步骤无踪迹"
        else:
            if cap_traced:
                state = "covered"
                detail = ""
            elif step_declared:
                state = "unknown"
                detail = "步骤已入计划，能力绑定未投影"
            else:
                state = "missing" if step.required else "unknown"
                detail = "required 步骤无计划与证据踪迹" if step.required else \
                    "可选步骤无踪迹"
        if state == "covered":
            matched = ev_hits
        report.steps.append(StepReplay(
            step_id=step.step_id, state=state, detail=detail,
            matched_evidence=matched))

    # 义务重放
    ss = skill.statistical_semantics
    needs_denominator = bool(ss and ss.denominator_required)
    for obl in skill.quality_obligations:
        if obl.evidence_kind and obl.evidence_kind in produced_evidence:
            state, detail = "satisfied", ""
        elif obl.evidence_kind:
            state, detail = "missing", f"缺证据种类 {obl.evidence_kind}"
        elif obl.precondition_id:
            state, detail = "unknown", (
                f"precondition {obl.precondition_id} 由算法层运行期裁决")
        else:
            state, detail = "unknown", "无证据键映射（声明型义务）"
        report.obligations.append(ObligationReplay(
            obligation_id=obl.obligation_id, state=state, detail=detail))

    # 分母专项（S10：rate/density/percentage 结论必须有分母证据）
    if needs_denominator:
        denom_ok = bool(
            plan.get("has_denominator_evidence")
            or evd.get("has_denominator_evidence")
            or "denominator_evidence" in produced_evidence)
        state = "satisfied" if denom_ok else "missing"
        report.obligations.append(ObligationReplay(
            obligation_id="statistical.denominator_required",
            state=state,
            detail="" if denom_ok else
            "度量语义含 " + "/".join(
                m for m in (ss.measure_semantics if ss else []
                            ) if m in DENOMINATOR_REQUIRED_MEASURES)
            + "，但无分母证据：不得下率/密度/占比结论"))

    # complete 判定：required 步骤无 missing（covered/skipped_declared/unknown
    # 之外不许有 missing）且无 missing 义务。unknown 不阻断 complete（诚实
    # 未知 ≠ 违规），由 Evaluator 结合运行期证据裁决。
    report.complete = (
        not report.missing_steps
        and all(o.state != "missing" for o in report.obligations)
    )
    return report


__all__ = [
    "STEP_REPLAY_STATES",
    "OBLIGATION_REPLAY_STATES",
    "StepReplay",
    "ObligationReplay",
    "ProcedureReplayReport",
    "replay_procedure",
]
