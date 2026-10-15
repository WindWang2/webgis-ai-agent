"""ProcedureCoverage —— 运行期证据绑定与过程覆盖（ADR-0219 C4/C5/C6）。

把既有运行期事实（SessionPlan 行状态 + kernel ``PlanStep.evidence`` +
决策结构）映射回 ProcedureBinding，产出确定性覆盖报告：

- **capability 级**（对全部技能可用）：skill required/optional 能力是否
  进计划、运行期是否完成 —— covered / missing / unknown / blocked / stale；
- **step 级**：covered / missing / unknown / skipped_declared / blocked /
  stale（capability_refs 精确绑定；obligation-only 步骤按 honest-unknown）；
- **义务级**：satisfied / missing / unknown（分母义务保持 S10 红线）；
- **drift**：绑定 skill version/fingerprint 与当前 library 对账
  （skill_version_drift / skill_fingerprint_drift / skill_deprecated /
  skill_missing）；
- ``complete`` = 无 missing（unknown 不阻断 —— honest-unknown ≠ 违规，
  与 skills/replay.py 一致）。

红线：

- **读时派生**：不建第二状态库、不写任何持久状态；
- **只信运行期事实**：绑定块只提供 join key；覆盖状态一律从行状态/
  kernel 证据派生，不接受调用方自喂投影（对抗面：LLM 文本不能伪造覆盖）；
- **stale generation**：行 voided（replace/supersede 语义）→ 该能力/步骤
  证据不计入新 procedure（stale）；envelope supersede 后旧章节不被读取；
- **不循环论证**：能力完成只证明"能力面执行过"（covered，附 kernel
  evidence refs）；evidence-kind 无独立运行期生产者时保持 unknown ——
  唯一例外是分母义务（rate/density/percentage 无分母证据 = missing）；
- skill coverage ≠ goal satisfaction：本报告只描述"方法步骤是否完成"，
  不替代 science/cartography/final_map 事实。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.services.gis_harness.skills.procedure_binding import (
    ProcedureBinding,
)

#: 覆盖结构版本。
PROCEDURE_COVERAGE_SCHEMA = "procedure_coverage.v1"

#: capability 覆盖状态词表（skipped_declared = 只服务「分支未走/缺省
#: 分支」步骤的能力，合法未走）。
CAPABILITY_COVERAGE_STATES = (
    "covered", "missing", "unknown", "blocked", "stale", "skipped_declared",
)

#: 步骤覆盖状态词表。
STEP_COVERAGE_STATES = (
    "covered",
    "missing",
    "unknown",
    "skipped_declared",
    "blocked",
    "stale",
)

#: 义务覆盖状态词表。
OBLIGATION_COVERAGE_STATES = ("satisfied", "missing", "unknown")

#: drift 码词表（closed）。
PROCEDURE_DRIFT_CODES = (
    "skill_version_drift",
    "skill_fingerprint_drift",
    "skill_deprecated",
    "skill_missing",
)

#: 行状态 → 覆盖事实（归一；plan_graph 同词表）。
_ROW_TERMINAL = {
    "complete": "complete",
    "done": "complete",
    "available": "complete",
    "voided": "voided",
    "failed": "failed",
    "unavailable": "unavailable",
}

_MAX_STEPS = 32
_MAX_CAPS = 16
_MAX_EVIDENCE_PER_STEP = 4


class CapabilityCoverage(BaseModel):
    capability_id: str
    criticality: str = "required"
    planned: bool = False
    state: str = "unknown"          # ⊆ CAPABILITY_COVERAGE_STATES
    detail: str = ""
    matched_ref: str = ""           # 行 bound_ref（artifact 指针）

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id[:64],
            "criticality": self.criticality[:16],
            "planned": self.planned,
            "state": self.state,
            "detail": self.detail[:160],
            "matched_ref": self.matched_ref[:96],
        }


class StepCoverage(BaseModel):
    step_id: str
    state: str = "unknown"          # ⊆ STEP_COVERAGE_STATES
    detail: str = ""
    bound_capabilities: List[str] = Field(default_factory=list)
    #: kernel evidence 引用（tool/tool_call_id/ref；same-capability 重复以
    #: tool_call_id 区分；FIFO ≤4 —— 与 kernel MAX_EVIDENCE_PER_STEP 同界）
    matched_evidence: List[Dict[str, str]] = Field(default_factory=list)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id[:64],
            "state": self.state,
            "detail": self.detail[:160],
            "bound_capabilities": self.bound_capabilities[:4],
            "matched_evidence": [
                {
                    "step_ref": e.get("step_ref", "")[:64],
                    "tool": e.get("tool", "")[:48],
                    "tool_call_id": e.get("tool_call_id", "")[:64],
                    "ref": e.get("ref", "")[:96],
                }
                for e in self.matched_evidence[:_MAX_EVIDENCE_PER_STEP]
            ],
        }


class ObligationCoverage(BaseModel):
    obligation_id: str
    state: str = "unknown"          # ⊆ OBLIGATION_COVERAGE_STATES
    detail: str = ""

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "obligation_id": self.obligation_id[:64],
            "state": self.state,
            "detail": self.detail[:160],
        }


class ProcedureCoverageReport(BaseModel):
    """过程覆盖报告（读时派生、确定性、可序列化）。"""

    schema_version: str = PROCEDURE_COVERAGE_SCHEMA
    skill_id: str
    skill_version: str = ""
    skill_fingerprint: str = ""
    binding_fingerprint: str = ""
    mode: str = ""
    drift: List[str] = Field(default_factory=list)   # ⊆ PROCEDURE_DRIFT_CODES
    capabilities: List[CapabilityCoverage] = Field(default_factory=list)
    steps: List[StepCoverage] = Field(default_factory=list)
    obligations: List[ObligationCoverage] = Field(default_factory=list)
    counts: Dict[str, int] = Field(default_factory=dict)   # state → n（steps）
    #: complete（replay 兼容）：无 missing 面（unknown 不阻断 —— 与
    #: skills/replay.py 同规；计划期即可为 True）。
    complete: bool = False
    #: fully_covered（强语义）：complete 且全部 required 步骤 covered/
    #: skipped_declared、required 能力 covered —— 「方法按声明完成」的
    #: 诚实判定（obligation-only 步骤在 evidence-kind 运行期生产者
    #: 缺席时保持 unknown → 不冒充完成）。
    fully_covered: bool = False
    missing_required: List[str] = Field(default_factory=list)
    #: 可执行的缺失面（capability + 步骤 + 义务合并，≤8）
    actionable_missing: List[str] = Field(default_factory=list)

    def next_expected(self) -> str:
        """下一个预期步骤（声明序上首个未完成步骤；空=无）。

        skipped_declared/stale 视为已处置；missing/unknown/blocked 都算
        "未完成" —— missing 优先暴露。
        """
        for s in self.steps:
            if s.state in ("covered", "skipped_declared", "stale"):
                continue
            return s.step_id
        return ""

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "skill_id": self.skill_id[:64],
            "skill_version": self.skill_version[:16],
            "skill_fingerprint": self.skill_fingerprint[:16],
            "binding_fingerprint": self.binding_fingerprint[:16],
            "mode": self.mode[:24],
            "drift": self.drift[:4],
            "capabilities": [
                c.to_bounded_dict() for c in self.capabilities[:_MAX_CAPS]],
            "steps": [s.to_bounded_dict() for s in self.steps[:_MAX_STEPS]],
            "obligations": [
                o.to_bounded_dict() for o in self.obligations[:16]],
            "counts": dict(list(self.counts.items())[:8]),
            "complete": self.complete,
            "fully_covered": self.fully_covered,
            "missing_required": self.missing_required[:8],
            "actionable_missing": [
                m[:120] for m in self.actionable_missing[:8]],
        }

    def summary_line(self) -> str:
        """有界单行投影（SessionPlan 下一轮上下文 / SSE 面）。"""
        total = len(self.steps)
        covered = self.counts.get("covered", 0)
        parts = [
            f"Procedure: skill={self.skill_id[:48]}@{self.skill_version[:12]}",
            f"covered={covered}/{total}",
        ]
        if self.missing_required:
            parts.append(
                "missing=[" + ",".join(self.missing_required[:4]) + "]")
        nxt = self.next_expected()
        if nxt:
            parts.append(f"next={nxt[:48]}")
        if self.drift:
            parts.append("drift=" + ",".join(self.drift[:3]))
        if self.fully_covered:
            parts.append("complete=true")
        elif self.complete:
            parts.append("no_missing=true")
        return " ".join(parts)


# ── 运行期事实抽取（只读；chapter/envelope 是既有真相）──────────────────────


def _chapter_rows(chapter: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """capability → 行（requirement/step 合并；complete 优先）。"""
    rows: Dict[str, Dict[str, Any]] = {}
    for row in list(chapter.get("data_requirements") or []) + list(
            chapter.get("analysis_steps") or []):
        if isinstance(row, dict) and row.get("capability"):
            cap = str(row["capability"])
            existing = rows.get(cap)
            if existing is None or str(row.get("status") or "") in (
                    "complete", "done", "available"):
                rows[cap] = row
    return rows


def _kernel_evidence_by_capability(
    envelope: Any,
) -> Dict[str, List[Dict[str, str]]]:
    """kernel PlanStep.evidence → per-capability 证据条目（exactly-once）。

    same-capability repeated（多 PlanStep / 多次 attach）以
    ``(tool_call_id, ref)`` 去重 —— 重复回调/锁重试不重复计数。
    """
    out: Dict[str, List[Dict[str, str]]] = {}
    if envelope is None:
        return out
    steps: List[Any] = list(envelope.get("steps") or []) if isinstance(
        envelope, dict) else list(getattr(envelope, "steps", None) or [])
    seen: set = set()
    for step in steps:
        step_dict = step if isinstance(step, dict) else None
        cap = str(getattr(step, "capability", "") or "") or str(
            (step_dict or {}).get("capability") or "")
        step_id = str(getattr(step, "id", "") or "") or str(
            (step_dict or {}).get("id") or "")
        entries = list(getattr(step, "evidence", None) or []) or list(
            (step_dict or {}).get("evidence") or [])
        for ev in entries:
            if isinstance(ev, dict):
                tool = str(ev.get("tool") or "")
                call_id = str(ev.get("tool_call_id") or "")
                ref = str(ev.get("ref") or "")
            else:
                tool = str(getattr(ev, "tool", "") or "")
                call_id = str(getattr(ev, "tool_call_id", "") or "")
                ref = str(getattr(ev, "ref", "") or "")
            if not (tool or ref or call_id):
                continue
            key = (cap, call_id or ref or tool)
            if key in seen:
                continue
            seen.add(key)
            out.setdefault(cap, []).append({
                "step_ref": step_id,
                "tool": tool,
                "tool_call_id": call_id,
                "ref": ref,
            })
    return out


def _progress_rows(envelope: Any) -> Dict[str, str]:
    """envelope.progress（CapabilityProgress）→ capability → status。

    replace 语义：同 goal 重规划把**progress 行**置 voided（章节行被新
    计划整体替换）—— 带完成产物（bound_ref）的 voided 是「旧 generation
    证据失效」的权威信号；无 bound_ref 的 voided（原本就 pending，被
    批量 void）没有损失任何证据，按 pending 对待，不冒充 stale。
    """
    out: Dict[str, str] = {}
    if envelope is None:
        return out
    rows: List[Any] = list(envelope.get("progress") or []) if isinstance(
        envelope, dict) else list(getattr(envelope, "progress", None) or [])
    for row in rows:
        if isinstance(row, dict):
            cap = str(row.get("capability") or "")
            status = str(row.get("status") or "")
            ref = str(row.get("bound_ref") or "")
        else:
            cap = str(getattr(row, "capability", "") or "")
            status = str(getattr(row, "status", "") or "")
            ref = str(getattr(row, "bound_ref", "") or "")
        if not cap or not status:
            continue
        # 无 bound_ref 的 voided（原本 pending，被批量 void）没有损失任何
        # 证据 → 按 pending 对待，不冒充 stale。
        if status == "voided" and not ref:
            status = "pending"
        out[cap] = status
    return out


def _current_skill(skill_id: str, library: Any) -> Optional[Any]:
    """当前库中的技能（library 缺省 → 进程默认库；失败 → None）。"""
    lib = library
    if lib is None:
        try:
            from app.services.gis_harness.skills.loader import get_skill_library
            lib = get_skill_library()
        except Exception:  # noqa: BLE001 — 库缺席 = 无法对账（不阻断覆盖）
            return None
    try:
        return lib.get(skill_id)
    except Exception:  # noqa: BLE001
        return None


def _drift_codes(binding: ProcedureBinding, current_skill: Any) -> List[str]:
    """绑定技能资产 vs 当前库的漂移对账（C6）。"""
    drift: List[str] = []
    if current_skill is None:
        return ["skill_missing"]
    if str(getattr(current_skill, "version", "") or "") != binding.skill_version:
        drift.append("skill_version_drift")
    if str(getattr(current_skill, "fingerprint", lambda: "")() or "")[
            :16] != binding.skill_fingerprint:
        drift.append("skill_fingerprint_drift")
    if bool(getattr(current_skill, "deprecated", False)):
        drift.append("skill_deprecated")
    return drift


# ── 状态机（纯函数）──────────────────────────────────────────────────────────


def _norm_rows(
    caps: List[str], rows: Dict[str, Dict[str, Any]],
) -> List[str]:
    """capability 列表 → 归一化行状态（无行 = unplanned）。"""
    out: List[str] = []
    for cap in caps:
        raw = str(rows.get(cap, {}).get("status") or "")
        out.append(_ROW_TERMINAL.get(raw, "pending"))
    return out


def _aggregate_states(norm: List[str]) -> str:
    """行状态聚合：voided > failed/unavailable > complete > pending。"""
    if "voided" in norm:
        return "voided"
    if "failed" in norm:
        return "failed"
    if "unavailable" in norm:
        return "unavailable"
    if norm and all(s == "complete" for s in norm):
        return "complete"
    return "pending"


def _eval_capability(
    cap: Any,
    rows: Dict[str, Dict[str, Any]],
    progress: Dict[str, str],
    *,
    excused: Optional[set] = None,
) -> CapabilityCoverage:
    """capability 级覆盖（skill 能力面；对全部技能可用）。

    以**当前** chapter 行 + envelope progress 行为准（绑定快照可能已与
    计划分叉 —— replan 后行消失的 required 能力 = 方法能力面重新出现
    缺口，判 missing；progress voided = 旧 generation 证据失效，stale）。
    """
    if progress.get(cap.plan_node_id or cap.capability_id) == "voided":
        return CapabilityCoverage(
            capability_id=cap.capability_id,
            criticality=cap.criticality,
            planned=True,
            state="stale",
            detail="progress 行 void（replace 语义：旧 generation 证据失效）",
        )
    row_present = bool(cap.plan_node_id) and cap.plan_node_id in rows
    if not row_present and cap.capability_id in (excused or set()):
        return CapabilityCoverage(
            capability_id=cap.capability_id,
            criticality=cap.criticality,
            planned=False,
            state="skipped_declared",
            detail="能力只服务分支未走/缺省分支的步骤（结构裁决）",
        )
    if not row_present:
        # 行不在当前计划：required = missing；optional = unknown。
        state = "missing" if cap.criticality == "required" else "unknown"
        detail = (
            "技能 required 能力行不在当前计划（绑定后计划分叉）"
            if cap.status == "bound" and state == "missing"
            else ("技能 required 能力未进计划" if state == "missing"
                  else "optional 能力未进计划"))
        return CapabilityCoverage(
            capability_id=cap.capability_id,
            criticality=cap.criticality,
            planned=False,
            state=state,
            detail=detail,
        )
    norm = _norm_rows([cap.plan_node_id], rows)
    agg = norm[0]
    bound_ref = str(rows.get(cap.plan_node_id, {}).get("bound_ref") or "")
    if agg == "voided":
        state, detail = "stale", "能力行 void（旧 generation 证据失效）"
    elif agg == "complete":
        state, detail = "covered", "能力行 complete"
    elif agg == "failed":
        state, detail = "blocked", "能力行 failed（可重试）"
    elif agg == "unavailable":
        state, detail = "blocked", "能力行 unavailable（按 fallback 声明处置）"
    else:
        state, detail = "unknown", "待执行/执行中"
    return CapabilityCoverage(
        capability_id=cap.capability_id,
        criticality=cap.criticality,
        planned=True,
        state=state,
        detail=detail,
        matched_ref=bound_ref if agg == "complete" else "",
    )


def _denominator_fact(chapter: Dict[str, Any]) -> Optional[bool]:
    """运行期分母事实（map_product 块的显式键；生产缝未写 → None）。

    与 skills/replay.py 的 ``has_denominator_evidence`` 同名同义；当前
    无生产者写该键（诚实 unknown），未来 science/终验缝落地后即三态。
    """
    mp = chapter.get("map_product")
    if isinstance(mp, dict) and "has_denominator_evidence" in mp:
        return bool(mp.get("has_denominator_evidence"))
    return None


def _eval_step(
    step: Any,
    rows: Dict[str, Dict[str, Any]],
    kernel_ev: Dict[str, List[Dict[str, str]]],
    decision_defaulted: set,
    decision_ambiguous: set,
    progress: Optional[Dict[str, str]] = None,
    decisions: Optional[List[Any]] = None,
) -> StepCoverage:
    """step 级覆盖状态机（纯函数；只读行状态与 kernel 证据）。"""
    caps = list(step.plan_node_ids)
    coverage = StepCoverage(
        step_id=step.step_id, bound_capabilities=caps[:4])
    progress = progress or {}
    from app.services.gis_harness.skills.procedure_binding import (
        branch_not_taken as _branch_not_taken,
    )
    branch_not_taken = _branch_not_taken(step, decisions or [])

    if not caps:
        # 无计划节点：区分「未进计划」「义务型步骤」「分支未走」。
        if branch_not_taken is not None:
            coverage.state = "skipped_declared"
            coverage.detail = (
                f"decision {branch_not_taken[0]} 结构选中 "
                f"{branch_not_taken[1]}，该步骤所属分支未走")
        elif bool(set(step.conditional_on) & decision_defaulted):
            # 步骤只在非缺省分支、缺省分支被结构选中 = 按方法缺省跳过
            # （决策点是技能声明的方法路径，非 LLM 选择）。
            coverage.state = "skipped_declared"
            coverage.detail = "decision default 分支不含该步骤（结构裁决）"
        elif bool(set(step.conditional_on) & decision_ambiguous):
            coverage.state = "unknown"
            coverage.detail = "decision 分支结构歧义（多选项命中/无信号）"
        elif step.status == "capability_absent" and step.required:
            coverage.state = "missing"
            coverage.detail = (
                "required 步骤的 capability "
                + ",".join(step.capability_ids[:3])
                + " 未进入计划且无跳过声明")
        else:
            coverage.state = "unknown"
            coverage.detail = (
                "无 capability 绑定（obligation-only）或可选步骤无运行期踪迹")
        return coverage

    if any(progress.get(c) == "voided" for c in caps):
        coverage.state = "stale"
        coverage.detail = "progress 行 void（replace 语义：旧 generation 证据失效）"
        return coverage
    agg = _aggregate_states(_norm_rows(caps, rows))
    if agg == "voided":
        coverage.state = "stale"
        coverage.detail = "绑定能力行已 void（旧 generation 证据失效）"
        return coverage
    if agg == "complete":
        coverage.state = "covered"
        matched: List[Dict[str, str]] = []
        seen_keys: set = set()
        for cap in caps:
            for entry in kernel_ev.get(cap, []):
                key = (entry.get("tool_call_id") or entry.get("ref")
                       or entry.get("tool"))
                if key in seen_keys:
                    continue  # exactly-once
                seen_keys.add(key)
                matched.append({
                    "step_ref": entry.get("step_ref", ""),
                    "tool": entry.get("tool", ""),
                    "tool_call_id": entry.get("tool_call_id", ""),
                    "ref": entry.get("ref", ""),
                })
                if len(matched) >= _MAX_EVIDENCE_PER_STEP:
                    break
            if len(matched) >= _MAX_EVIDENCE_PER_STEP:
                break
        coverage.matched_evidence = matched
        coverage.detail = (
            f"capability {'/'.join(caps[:3])} 完成"
            + (f"；kernel evidence ×{len(matched)}" if matched
               else "；行 complete（kernel evidence 缺席=行事实足够）"))
        return coverage
    if agg == "failed":
        coverage.state = "blocked"
        coverage.detail = "绑定能力行 failed（执行过无 artifact；可重试）"
        return coverage
    if agg == "unavailable":
        coverage.state = "blocked"
        coverage.detail = (
            "绑定能力行 unavailable（能力不可用；按技能 fallback 声明处置）")
        return coverage
    coverage.state = "unknown"
    coverage.detail = "绑定能力待执行/执行中"
    return coverage


def _eval_obligations(
    binding: ProcedureBinding,
    step_coverages: List[StepCoverage],
    capability_coverages: List[CapabilityCoverage],
    denom_fact: Optional[bool] = None,
) -> List[ObligationCoverage]:
    """义务对账：evidence kind 有运行期事实（对应步骤/能力完成）才 satisfied。

    结构接地（structural grounding）：步骤声明 kind 且该步骤绑定的能力
    面执行完成（行 complete —— 真实运行期事实）→ 该 kind 视为已产出，
    detail 携带追溯链；无对应踪迹的保持 unknown（不虚构）。分母义务保持
    replay.py 的 S10 红线：无分母证据 = missing（阻断 complete）。
    """
    covered_kinds: Dict[str, List[str]] = {}
    for sc in step_coverages:
        if sc.state != "covered":
            continue
        step = binding.step(sc.step_id)
        if step is None:
            continue
        for kind in step.evidence_kinds:
            covered_kinds.setdefault(kind, []).append(step.step_id)
    # capability 级接地：技能完成证据 kinds 若与已覆盖能力的行绑定事实
    # 关联…… 不虚构 —— capability 级不产 kind（kind 属步骤/义务声明）。

    out: List[ObligationCoverage] = []
    for obl in binding.obligations:
        if obl.evidence_kind and obl.evidence_kind in covered_kinds:
            out.append(ObligationCoverage(
                obligation_id=obl.obligation_id,
                state="satisfied",
                detail=(
                    f"evidence kind {obl.evidence_kind} 结构接地：步骤 "
                    + ",".join(covered_kinds[obl.evidence_kind][:3])
                    + " 的能力面完成（kind 级独立生产者缺席，能力完成"
                    "是当前唯一运行期事实）"),
            ))
        elif obl.evidence_kind:
            out.append(ObligationCoverage(
                obligation_id=obl.obligation_id,
                state="unknown",
                detail=f"evidence kind {obl.evidence_kind} 无运行期踪迹",
            ))
        elif obl.precondition_id:
            out.append(ObligationCoverage(
                obligation_id=obl.obligation_id,
                state="unknown",
                detail=f"precondition {obl.precondition_id} 由算法层运行期裁决",
            ))
        else:
            out.append(ObligationCoverage(
                obligation_id=obl.obligation_id,
                state="unknown",
                detail="声明型义务（无 evidence kind 映射）",
            ))

    # 分母专项（S10）：三态接线 ——
    # - 结构接地（技能步骤声明 denominator 类 evidence kind 且能力完成）
    #   → satisfied；
    # - 运行期显式事实（map_product 块 has_denominator_evidence，生产缝
    #   未写键时缺席）→ True=satisfied / False=missing（S10 红线）；
    # - 无任何独立事实 → unknown（诚实：procedure 层无法证实也无法证伪，
    #   由 science/goal_satisfaction 层裁决 —— 不得恒 missing 冒充已核查）。
    if binding.denominator_required:
        denom_kinds = sorted(
            k for k in covered_kinds if "denominator" in k.lower())
        if denom_kinds:
            out.append(ObligationCoverage(
                obligation_id="statistical.denominator_required",
                state="satisfied",
                detail="分母证据由步骤能力完成接地: "
                       + ",".join(denom_kinds[:3]),
            ))
        elif denom_fact is True:
            out.append(ObligationCoverage(
                obligation_id="statistical.denominator_required",
                state="satisfied",
                detail="运行期事实 has_denominator_evidence=true",
            ))
        elif denom_fact is False:
            out.append(ObligationCoverage(
                obligation_id="statistical.denominator_required",
                state="missing",
                detail="度量语义含 rate/density/percentage，但无分母证据："
                       "不得下率/密度/占比结论",
            ))
        else:
            out.append(ObligationCoverage(
                obligation_id="statistical.denominator_required",
                state="unknown",
                detail="无独立分母证据运行期事实（procedure 层不裁决；"
                       "由 science/goal_satisfaction 层核查）",
            ))
    return out


# ── 主入口 ───────────────────────────────────────────────────────────────────


def compute_procedure_coverage(
    chapter: Optional[Dict[str, Any]],
    *,
    envelope: Any = None,
    library: Any = None,
) -> Optional[ProcedureCoverageReport]:
    """chapter（含 procedure_binding 块）→ ProcedureCoverageReport。

    返回 None = 无绑定块 / 块损坏（fail-closed：无覆盖面，调用方零漂移）。
    纯读派生，不写任何状态；同输入同输出（重放稳定）。
    """
    if not isinstance(chapter, dict):
        return None
    block = chapter.get("procedure_binding")
    if not isinstance(block, dict):
        return None
    try:
        binding = ProcedureBinding.model_validate(block)
    except Exception:  # noqa: BLE001 — 块损坏按无绑定处理（不虚构覆盖）
        return None

    rows = _chapter_rows(chapter)
    kernel_ev = _kernel_evidence_by_capability(envelope)
    progress = _progress_rows(envelope)
    current_skill = _current_skill(binding.skill_id, library)
    drift = _drift_codes(binding, current_skill)

    report = ProcedureCoverageReport(
        skill_id=binding.skill_id,
        skill_version=binding.skill_version,
        skill_fingerprint=binding.skill_fingerprint,
        binding_fingerprint=binding.fingerprint(),
        mode=binding.mode,
        drift=drift,
    )

    from app.services.gis_harness.skills.procedure_binding import (
        branch_not_taken as _bnt,
    )
    steps_not_taken = {
        s.step_id for s in binding.steps
        if _bnt(s, binding.decisions) is not None
    }
    excused_caps = {
        c for s in binding.steps if s.step_id in steps_not_taken
        for c in s.capability_ids
    }
    for cap in binding.capabilities[:_MAX_CAPS]:
        report.capabilities.append(_eval_capability(
            cap, rows, progress, excused=excused_caps))

    decision_defaulted = {
        d.node_id for d in binding.decisions if d.resolved_by == "default"}
    decision_ambiguous = {
        d.node_id for d in binding.decisions if d.resolved_by == ""}
    for step in binding.steps[:_MAX_STEPS]:
        report.steps.append(_eval_step(
            step, rows, kernel_ev,
            decision_defaulted, decision_ambiguous, progress,
            decisions=binding.decisions,
        ))

    report.obligations = _eval_obligations(
        binding, report.steps, report.capabilities,
        denom_fact=_denominator_fact(chapter),
    )

    counts: Dict[str, int] = {}
    for s in report.steps:
        counts[s.state] = counts.get(s.state, 0) + 1
    report.counts = dict(sorted(counts.items()))
    report.missing_required = [
        s.step_id for s in report.steps if s.state == "missing"
    ] + [
        f"cap:{c.capability_id}" for c in report.capabilities
        if c.state == "missing"
    ]
    missing_obligations = [
        o.obligation_id for o in report.obligations if o.state == "missing"]
    # complete（replay 兼容）：无 missing 面（unknown 不阻断 —— 与
    # skills/replay.py 同规；review P1-2：强语义单独由 fully_covered 承载，
    # 否则 complete 成为常量 False，信号退化）。
    report.complete = (
        not report.missing_required and not missing_obligations)
    # fully_covered（强语义）：「方法按声明完成」—— complete 且全部
    # required 步骤 covered/skipped_declared、required 能力 covered。
    # （未执行的 unknown/stale/blocked 不冒充完成；counts 提供 honest 视图。）
    step_by_id = {s.step_id: s for s in binding.steps}
    required_steps_settled = all(
        sc.state in ("covered", "skipped_declared")
        for sc, sb in (
            (sc, step_by_id.get(sc.step_id)) for sc in report.steps)
        if sb is not None and sb.required
    ) and bool(report.steps)
    required_caps_covered = all(
        c.state == "covered" for c in report.capabilities
        if c.criticality == "required"
    ) and bool(report.capabilities)
    report.fully_covered = (
        report.complete
        and required_steps_settled
        and required_caps_covered
    )
    report.actionable_missing = (
        [
            f"capability {c.capability_id}: {c.detail[:96]}"
            for c in report.capabilities if c.state == "missing"
        ] + [
            f"step {s.step_id}: {s.detail[:96]}"
            for s in report.steps if s.state == "missing"
        ] + [
            f"obligation {o.obligation_id}: {o.detail[:96]}"
            for o in report.obligations if o.state == "missing"
        ]
    )[:8]
    return report


def procedure_pi_card(
    coverage: Optional[ProcedureCoverageReport],
    *,
    check: Any = None,
) -> Dict[str, Any]:
    """C7：有界 procedure 卡片（Pi 渐进披露；禁整份 YAML procedure 注入）。

    只含：当前 skill、procedure phase（声明序第几步/共几步）、缺失义务
    （≤3）、计划违例（≤3）、drift。任何字段失败按缺席投影。
    """
    if coverage is None:
        return {}
    current = coverage.next_expected()
    phase_index = 0
    for i, s in enumerate(coverage.steps):
        if s.step_id == current:
            phase_index = i + 1
            break
    else:
        phase_index = len(coverage.steps)
    card: Dict[str, Any] = {
        "schema": "procedure_pi_card.v1",
        "skill": f"{coverage.skill_id[:48]}@{coverage.skill_version[:12]}",
        "mode": coverage.mode[:24],
        "phase": {
            "index": phase_index,
            "total": len(coverage.steps),
            "current": current[:48],
        },
        "missing_obligations": [
            o.obligation_id[:64]
            for o in coverage.obligations if o.state == "missing"
        ][:3],
        "drift": list(coverage.drift[:3]),
    }
    if check is not None:
        try:
            violations = list(getattr(check, "violations", None) or [])
            card["violations"] = [
                f"{v.code}:{v.subject}"[:80] for v in violations[:3]
            ]
        except Exception:  # noqa: BLE001 — 卡片是增值披露
            card["violations"] = []
    return card


__all__ = [
    "PROCEDURE_COVERAGE_SCHEMA",
    "CAPABILITY_COVERAGE_STATES",
    "STEP_COVERAGE_STATES",
    "OBLIGATION_COVERAGE_STATES",
    "PROCEDURE_DRIFT_CODES",
    "CapabilityCoverage",
    "StepCoverage",
    "ObligationCoverage",
    "ProcedureCoverageReport",
    "compute_procedure_coverage",
    "procedure_pi_card",
]
