"""GoalVerification 对账层（GOAL 05 / M4，ADR-0279）。

定位（防重复施工红线）：本层**不是第二 oracle**——ADR-0183
GoalSatisfactionReport 仍是任务语义裁决的单一评估点；本层只回答一个
此前无人回答的对账问题：**「完成回执（task_complete / goal satisfied）
与真实证据（渲染观察 / 数据资格 / 科学评审）是否互相一致」**——

- 地图观察缺失 / 渲染未验证 → actual_rendering 永不 pass（即便 goal
  verdict == satisfied，也必须记 contradiction 披露）；
- 科学质量证据不足（需求 not_evaluated / cartographic review 缺席）→
  scientific_quality 不 pass（L4/L5 未评估按 not_evaluated 诚实报告）；
- 数据角色 unresolved / qualification blocked → data_coverage fail；
- user-wins：用户显式隐藏的层 → warning + user_wins_notes，绝不擅自
  判 fail 或代用户显现。

纪律：纯函数（零 IO / 零时钟）、全字段有界、缺证据 → not_evaluated
绝不升级 pass。词表封闭（slot 8 值 / status 4 值，测试钉死）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

SCHEMA_VERSION = "goal_verification.v1"

#: slot 词表（封闭）。
SLOT_REQUIRED_OUTPUTS = "required_outputs"
SLOT_DATA_COVERAGE = "data_coverage"
SLOT_SCIENTIFIC_QUALITY = "scientific_quality"
SLOT_LAYER_VISIBILITY = "layer_visibility"
SLOT_ACTUAL_RENDERING = "actual_rendering"
SLOT_LEGEND_STATISTICS = "legend_statistics"
SLOT_EXPORT_DELIVERY = "export_delivery"
SLOT_UNFINISHED_WORK = "unfinished_work"

_ALL_SLOTS = (
    SLOT_REQUIRED_OUTPUTS, SLOT_DATA_COVERAGE, SLOT_SCIENTIFIC_QUALITY,
    SLOT_LAYER_VISIBILITY, SLOT_ACTUAL_RENDERING, SLOT_LEGEND_STATISTICS,
    SLOT_EXPORT_DELIVERY, SLOT_UNFINISHED_WORK,
)

#: 观察阶梯（与 observation_states.LADDER 同词表；本地只读快照）。
_RENDER_PASS_STATES = frozenset({"rendered", "data_present",
                                 "semantically_correct"})

#: overall 折叠序（弱 → 强）：fail > not_evaluated > warning > pass。
_SEVERITY_ORDER = {"pass": 0, "warning": 1, "not_evaluated": 2, "fail": 3}


class VerificationSlot(BaseModel):
    slot: str
    status: str = "not_evaluated"          # pass/fail/warning/not_evaluated
    evidence_refs: List[str] = Field(default_factory=list)
    detail: str = ""
    #: 不适用（该需求族不存在/无角色）→ 不参与 overall 折叠（诚实缺席
    #: ≠ 拖低裁决）；状态仍如实报告 not_evaluated。
    counts_toward_overall: bool = True

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "slot": str(self.slot)[:32],
            "status": str(self.status)[:16],
            "evidence_refs": [str(e)[:64]
                              for e in self.evidence_refs[:6]],
            "detail": str(self.detail)[:160],
            "counts_toward_overall": bool(self.counts_toward_overall),
        }


class GoalVerificationReport(BaseModel):
    schema_version: str = SCHEMA_VERSION
    goal_verdict: str = ""                  # ADR-0183 verdict（只读引用）
    overall: str = "not_evaluated"
    slots: List[VerificationSlot] = Field(default_factory=list)
    contradictions: List[str] = Field(default_factory=list)
    user_wins_notes: List[str] = Field(default_factory=list)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "goal_verdict": str(self.goal_verdict)[:24],
            "overall": str(self.overall)[:16],
            "slots": [s.to_bounded_dict() for s in self.slots[:10]],
            "contradictions": [str(c)[:160]
                               for c in self.contradictions[:4]],
            "user_wins_notes": [str(n)[:160]
                                for n in self.user_wins_notes[:4]],
        }


def _d(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _requirements(goal: Dict[str, Any]) -> List[Dict[str, Any]]:
    reqs = goal.get("requirements")
    if not isinstance(reqs, list):
        return []
    return [r for r in reqs if isinstance(r, dict)]


def _kinds(goal: Dict[str, Any], *kinds: str) -> List[Dict[str, Any]]:
    return [r for r in _requirements(goal)
            if str(r.get("kind") or "") in kinds]


def _verdict_status(reqs: List[Dict[str, Any]]) -> str:
    if not reqs:
        return "not_evaluated"
    verdicts = {str(r.get("verdict") or "not_evaluated")
                for r in reqs}
    if "failed" in verdicts or "blocked" in verdicts:
        return "fail"
    if "not_evaluated" in verdicts:
        return "not_evaluated"
    if "partial" in verdicts:
        return "warning"
    return "pass"


def reconcile_goal_verification(
    *,
    goal_report: Optional[Dict[str, Any]],
    observation_health: Optional[Dict[str, Any]] = None,
    chapter: Optional[Dict[str, Any]] = None,
    task_complete: Optional[bool] = None,
    quality_review_present: Optional[bool] = None,
    user_hidden_layers: Optional[List[str]] = None,
) -> Optional[GoalVerificationReport]:
    """对账入口（纯函数；goal_report 缺席 → None = 无可对账契约）。"""
    goal = _d(goal_report)
    if not goal or not goal.get("verdict"):
        return None
    obs = _d(observation_health)
    chap = _d(chapter)
    verdict = str(goal.get("verdict") or "")[:24]
    report = GoalVerificationReport(goal_verdict=verdict)
    contradictions: List[str] = []
    user_wins: List[str] = []

    # ── 1. required_outputs：整体需求面 ──────────────────────────────
    reqs = _requirements(goal)
    if not reqs:
        outputs_status = "not_evaluated"
    elif verdict == "satisfied":
        outputs_status = "pass"
    elif verdict in ("failed", "blocked"):
        outputs_status = "fail"
    else:
        outputs_status = "warning"
    report.slots.append(VerificationSlot(
        slot=SLOT_REQUIRED_OUTPUTS, status=outputs_status,
        evidence_refs=[f"goal:{verdict}"] if verdict else [],
        detail=f"{len(reqs)} requirements, verdict={verdict}"))

    # ── 2. data_coverage：角色绑定 + 资格 receipts ───────────────────
    roles = []
    wf = _d(chap.get("workflow_contract"))
    if isinstance(wf.get("roles"), list):
        roles = [r for r in wf["roles"] if isinstance(r, dict)]
    quals = []
    compilation = _d(chap.get("compilation"))
    if isinstance(compilation.get("data_qualifications"), list):
        quals = [q for q in compilation["data_qualifications"]
                 if isinstance(q, dict)]
    if not roles:
        coverage_status, detail = "not_evaluated", "no data roles"
    else:
        statuses = {str(r.get("status") or "") for r in roles}
        blocked_q = [q for q in quals
                     if str(q.get("state") or "") == "blocked"]
        if "unresolved" in statuses or blocked_q:
            coverage_status = "fail"
            reasons = [
                f"role:{r.get('role')}:{r.get('status')}"
                for r in roles
                if str(r.get("status")) == "unresolved"][:3]
            reasons += [
                f"qual:{q.get('role')}:{q.get('reason_code')}"
                for q in blocked_q[:3]]
            detail = "blocked: " + ", ".join(reasons)
        elif "degraded" in statuses:
            coverage_status, detail = "warning", "degraded data roles"
        else:
            coverage_status, detail = "pass", \
                f"{len(roles)} roles bound/external"
    report.slots.append(VerificationSlot(
        slot=SLOT_DATA_COVERAGE, status=coverage_status,
        evidence_refs=[f"role:{r.get('role')}" for r in roles[:4]],
        detail=detail,
        counts_toward_overall=bool(roles)))

    # ── 3. scientific_quality：科学需求 + L4/L5 评审在场 ────────────
    science_reqs = _kinds(goal, "analysis", "comparison", "statistics")
    science_status = _verdict_status(science_reqs)
    science_detail = f"{len(science_reqs)} science requirements"
    science_refs = [
        f"req:{r.get('requirement_id') or r.get('id')}"
        for r in science_reqs[:4]]
    if quality_review_present is False:
        # L4（cartographic quality）/L5（visual judge）未评估 —— 证据
        # 缺席按 not_evaluated 报告，不得升级 pass。
        science_status = "not_evaluated"
        science_detail += "; quality review absent (L4/L5 not evaluated)"
        science_refs.append("quality_review:absent")
    elif quality_review_present is True and science_status == "pass":
        science_refs.append("quality_review:present")
    if science_status == "fail":
        science_detail += "; science evidence insufficient"
    report.slots.append(VerificationSlot(
        slot=SLOT_SCIENTIFIC_QUALITY, status=science_status,
        evidence_refs=science_refs, detail=science_detail,
        # R1-D：无科学需求族（纯地图目标）不拖低 overall —— 与
        # legend/export 同款 N/A 纪律；有科学需求才计数（评审缺席的
        # 封顶只在需求存在时有意义）。
        counts_toward_overall=bool(science_reqs)))

    # ── 4/5. layer_visibility 与 actual_rendering ────────────────────
    hidden = {str(h) for h in (user_hidden_layers or [])}
    aggregate = str(obs.get("aggregate") or "unknown")
    health = str(obs.get("health") or "")
    # R1-C：生产者 observation_states.build_observation_summary 的
    # layers 是 {layer_id: state} 字典；旧/合成形状（list[dict]）兼容。
    _raw_layers = obs.get("layers")
    layer_entries: List[Dict[str, Any]] = []
    if isinstance(_raw_layers, dict):
        layer_entries = [
            {"id": str(k), "state": v} for k, v in _raw_layers.items()]
    elif isinstance(_raw_layers, list):
        layer_entries = [e for e in _raw_layers if isinstance(e, dict)]
    has_map_req = bool(_kinds(goal, "map"))

    if not obs:
        render_status = "not_evaluated"
        render_detail = "render observation absent"
        layer_status = "not_evaluated"
    else:
        if aggregate in _RENDER_PASS_STATES:
            render_status = "pass"
        elif aggregate in ("loaded", "mounted", "pending"):
            render_status = "not_evaluated"      # 未渲染完成 ≠ pass
        elif hidden:
            # 用户显式隐藏导致观察缺席 → 尊重 user-wins：至多 warning
            # （仍绝不 pass —— 未展示就是未验证）。
            render_status = "warning"
        else:
            render_status = "fail" if has_map_req else "not_evaluated"
        render_detail = f"observation aggregate={aggregate}, health={health}"
        # 层可见性：health 阶梯 + user-wins 豁免
        if health == "ok":
            layer_status = "pass"
        elif health in ("partial", "degraded"):
            layer_status = "warning"
        elif health == "blocked":
            layer_status = "fail"
        else:
            layer_status = "not_evaluated"
        hidden_observed = [
            str(e.get("id") or "") for e in layer_entries
            if str(e.get("id") or "") in hidden]
        if hidden:
            # 用户显式隐藏 → 尊重 user-wins：不算系统失败（至多 warning），
            # 诚实说明未展示，绝不代用户显现。
            if layer_status in ("fail", "not_evaluated"):
                layer_status = "warning"
            user_wins.append(
                "user-hidden layers respected (not shown by design): "
                + ",".join(sorted(hidden_observed or hidden)[:4]))
    if has_map_req and not obs:
        contradictions.append(
            "goal requires map but render observation absent "
            f"(verdict={verdict})")
    report.slots.append(VerificationSlot(
        slot=SLOT_LAYER_VISIBILITY, status=layer_status,
        evidence_refs=[f"obs_layer:{e.get('id')}"
                       for e in layer_entries[:4]],
        detail=f"aggregate={aggregate} health={health or 'absent'}",
        counts_toward_overall=has_map_req))
    report.slots.append(VerificationSlot(
        slot=SLOT_ACTUAL_RENDERING, status=render_status,
        evidence_refs=[f"obs_rev:{obs.get('observed_revision')}"]
        if obs.get("observed_revision") is not None else [],
        detail=render_detail,
        counts_toward_overall=has_map_req))

    # ── 6. legend_statistics ─────────────────────────────────────────
    stat_reqs = _kinds(goal, "chart", "statistics")
    stat_status = _verdict_status(stat_reqs)
    report.slots.append(VerificationSlot(
        slot=SLOT_LEGEND_STATISTICS, status=stat_status,
        evidence_refs=[
            f"req:{r.get('requirement_id') or r.get('id')}"
            for r in stat_reqs[:4]],
        detail=f"{len(stat_reqs)} chart/statistics requirements",
        counts_toward_overall=bool(stat_reqs)))

    # ── 7. export_delivery：显式导出需求不兑现即 fail ───────────────
    export_reqs = _kinds(goal, "export")
    export_status = _verdict_status(export_reqs)
    if export_reqs and export_status == "warning":
        export_status = "fail"   # 用户显式要求导出：partial 即未兑现
    report.slots.append(VerificationSlot(
        slot=SLOT_EXPORT_DELIVERY, status=export_status,
        evidence_refs=[
            f"req:{r.get('requirement_id') or r.get('id')}"
            for r in export_reqs[:4]],
        detail=f"{len(export_reqs)} export requirements",
        counts_toward_overall=bool(export_reqs)))

    # ── 8. unfinished_work ───────────────────────────────────────────
    missing = [str(m) for m in (goal.get("missing_summary") or [])
               if isinstance(m, str)]
    if missing:
        report.slots.append(VerificationSlot(
            slot=SLOT_UNFINISHED_WORK, status="warning",
            evidence_refs=[f"missing:{m}" for m in missing[:4]],
            detail="outstanding: " + ", ".join(missing[:4])))
    else:
        report.slots.append(VerificationSlot(
            slot=SLOT_UNFINISHED_WORK, status="pass",
            detail="no outstanding slots"))

    # ── 对账矛盾（完成回执 vs 证据面） ───────────────────────────────
    slot_by_name = {s.slot: s.status for s in report.slots}
    if task_complete and any(
            slot_by_name.get(s) == "fail" for s in _ALL_SLOTS):
        contradictions.append(
            "task_complete=true but verification has failing slots")
    _render = slot_by_name.get(SLOT_ACTUAL_RENDERING)
    _user_explained = bool(user_wins) and _render == "warning"
    if (verdict == "satisfied" and _render != "pass" and has_map_req
            and not _user_explained):
        contradictions.append(
            "goal satisfied without verified rendering evidence")
    report.contradictions = contradictions[:4]
    report.user_wins_notes = user_wins[:4]
    severities = [_SEVERITY_ORDER.get(s.status, 2)
                  for s in report.slots if s.counts_toward_overall]
    worst = max(severities) if severities else 2
    report.overall = next(
        k for k, v in _SEVERITY_ORDER.items() if v == worst)
    return report


__all__ = [
    "SCHEMA_VERSION",
    "GoalVerificationReport",
    "VerificationSlot",
    "reconcile_goal_verification",
]
