"""AUTO_SAFE 视觉修复自动通道（C13）—— 策略分级 → 意图编译 → 既有事务。

**保守默认关闭**：``GIS_VISUAL_AUTO_REPAIR`` 未开启 = 本模块整体缺席
（F15 语义保留：visual finding 恒不自动修复；approval UI 永远可用）。
开启后边界仍是机器强制的：

- 只编译 ``auto_safe`` 分级（repair_policy：warning + 可映射 + 不触锁）；
- ``origin="system"`` —— lifecycle 锁 guard 结构性拒绝任何触碰用户锁定
  图层的 op（user-pinned 决策不可被自动修复覆盖）；
- 预算三重：per-revision ≤1 次、per-session ≤4 次、healer 收敛账本
  （attempts≥2 / no_improvement≥2 / signature 重放）硬停 —— 账本是
  engine 实例状态，本通道经 ``get_shared_lifecycle_engine`` 与 user
  批准路径共享同一实例（收敛状态互见，C13 review P1-1）；
- 全程决策账本留痕（成功 ``auto_applied`` / 失败 ``auto_failed``），
  修复后 recurrence 指纹重置（与 user 批准路径同款）。

任何失败 fail-open 返回诚实回执 —— 绝不阻断终验链。
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

BUDGET_KEY = "_visual_auto_repair_budget"
#: 每 mutation revision 最多 1 次自动修复 pass（mutation 推进 revision →
#: 新 revision 需要新观察 —— 不允许同代连改）。
MAX_AUTO_PER_REVISION = 1
#: 每会话自动修复总预算。
MAX_AUTO_PER_SESSION = 4
#: 预算窗口内保留的 revision 记录数（有界）。
_BUDGET_REVISION_WINDOW = 8


def auto_repair_enabled() -> bool:
    """env 开关（实时读 —— 与 visual_evaluator 同纪律：不快照导入期）。"""
    return os.getenv("GIS_VISUAL_AUTO_REPAIR", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _receipt(reason: str, **extra: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "ran": True, "applied": False, "reason": reason,
        "proposal_id": "", "mutation_revision": 0,
    }
    out.update(extra)
    return out


async def _load_budget(session_id: str) -> Dict[str, Any]:
    from app.services.session_data import session_data_manager

    try:
        raw = await session_data_manager.get_map_state(session_id)
        budget = (raw or {}).get(BUDGET_KEY)
        return dict(budget) if isinstance(budget, dict) else {}
    except Exception:  # noqa: BLE001 — 预算缺席 = 未消费
        return {}


async def _save_budget(session_id: str, budget: Dict[str, Any]) -> None:
    from app.services.session_data import session_data_manager

    revisions = budget.get("revisions")
    trimmed: Dict[str, int] = {}
    if isinstance(revisions, dict):
        for k, v in list(revisions.items())[-_BUDGET_REVISION_WINDOW:]:
            try:
                trimmed[str(k)[:32]] = int(v)
            except (TypeError, ValueError):
                continue
    budget["revisions"] = trimmed
    budget["session_total"] = int(budget.get("session_total") or 0)
    await session_data_manager.set_map_state(session_id, BUDGET_KEY, budget)


async def run_auto_repair_pass(
    session_id: str,
    *,
    engine: Optional[Any] = None,
) -> Dict[str, Any]:
    """一轮 AUTO_SAFE 修复（终验落块后调用；有界、fail-open）。"""
    if not session_id:
        return _receipt("invalid_session")
    if not auto_repair_enabled():
        return _receipt("disabled")

    from app.services.gis_harness.visual_observation.stored_findings import (
        current_mutation_revision,
        split_fresh_findings,
        stored_visual_findings,
    )
    from app.services.gis_harness.visual_observation.repair_bridge import (
        build_proposal_id,
        visual_findings_to_defects,
    )
    from app.services.gis_harness.visual_observation.repair_decisions import (
        DECISION_AUTO_APPLIED,
        DECISION_AUTO_FAILED,
        load_decisions,
        record_decision,
        rejected_fingerprints,
    )
    from app.services.gis_harness.visual_observation.repair_intent import (
        compile_intent,
        intent_fingerprint,
    )
    from app.services.gis_harness.visual_observation.repair_policy import (
        APPROVAL_AUTO_SAFE,
        approval_class_for,
    )
    from app.services.mapspec.lifecycle_engine import locked_layer_ids_of
    from app.services.mapspec.shared_engine import get_shared_lifecycle_engine
    from app.services.mapspec.visual_healer import VisualHealStrategyPlanner

    # P1-1：默认走共享单例 —— healer 收敛账本（实例状态）与 user 批准
    # 路径同源，收敛硬停对自动通道真实生效；测试可显式注入 engine。
    eng = engine if engine is not None else get_shared_lifecycle_engine()

    current_revision = await current_mutation_revision(session_id)
    findings = await stored_visual_findings(session_id)
    if not findings:
        return _receipt("no_visual_findings")
    findings, _ = split_fresh_findings(findings, current_revision)
    if not findings:
        return _receipt("stale_findings")

    # 拒绝记忆：用户拒绝过的 patch 不再自动执行（同一 patch 的自动版
    # 同样不受欢迎 —— rejected 是对缺陷指纹的裁决，不是对通道的裁决）。
    decisions = await load_decisions(session_id)
    rejected = rejected_fingerprints(decisions)

    mapspec = await eng.store.get_mapspec(session_id)
    if not isinstance(mapspec, dict):
        return _receipt("mapspec_unavailable")
    known_layer_ids = [
        str(ly.get("id"))[:64] for ly in (mapspec.get("layers") or [])[:64]
        if isinstance(ly, dict) and ly.get("id")
    ]
    locked_ids = set(locked_layer_ids_of(mapspec))

    from app.services.gis_harness.visual_observation.repair_policy import (
        touches_locked_layers,
    )

    auto_safe = []
    skipped: List[Dict[str, str]] = []
    for finding in findings:
        fp = str(finding.get("recurrence_fingerprint")
                 or finding.get("finding_id") or "")
        if fp and fp in rejected:
            skipped.append({"finding_id": str(finding.get("finding_id") or "")[:96],
                            "reason": "user_rejected"})
            continue
        locked = touches_locked_layers(finding, locked_ids)
        cls = approval_class_for(finding, touches_locked=locked)
        if cls == APPROVAL_AUTO_SAFE:
            auto_safe.append(finding)
        else:
            skipped.append({
                "finding_id": str(finding.get("finding_id") or "")[:96],
                "reason": cls or "unclassified"})
    if not auto_safe:
        return _receipt("no_auto_safe_findings", skipped=skipped[:12])

    translation = visual_findings_to_defects(
        auto_safe, known_layer_ids=known_layer_ids)
    defects = translation["defects"]
    skipped = skipped + list(translation["skipped"])
    if not defects:
        return _receipt("no_healable_defects", skipped=skipped[:12])

    plan = VisualHealStrategyPlanner().plan(mapspec, defects)
    ops = list(plan.ops or ())
    if not ops:
        return _receipt("no_safe_ops", skipped=skipped[:12])
    # 全有或全无：任一 op 触锁 → 整轮降级为 approval 面（自动路径绝不
    # 部分触碰用户锁定图层）。
    op_targets = {str(x) for op in ops for x in (op.layer_ids or ())}
    if op_targets & locked_ids:
        return _receipt("locked_layers_need_approval", skipped=skipped[:12])

    proposal_id = build_proposal_id(defects, current_revision)
    intent = compile_intent(
        proposal_id=proposal_id, base_revision=current_revision,
        approval_class=APPROVAL_AUTO_SAFE, origin="system",
        defects=defects, finding_ids=translation["finding_ids"],
        op_labels=[str(op.op)[:40] for op in ops[:8]],
        defect_fingerprint=str(plan.defect_fingerprint or "")[:96],
    )

    budget = await _load_budget(session_id)
    revisions = budget.get("revisions") if isinstance(
        budget.get("revisions"), dict) else {}
    session_total = int(budget.get("session_total") or 0)
    if int(revisions.get(str(current_revision), 0)) >= MAX_AUTO_PER_REVISION:
        return _receipt("revision_budget_exhausted")
    if session_total >= MAX_AUTO_PER_SESSION:
        return _receipt("session_budget_exhausted")

    async def _record(decision: str, reason: str, revision: int) -> None:
        try:
            await record_decision(
                session_id,
                decision=decision,
                proposal_id=proposal_id,
                intent_fingerprint=intent_fingerprint(intent),
                defect_fingerprint=intent.defect_fingerprint,
                recurrence_fingerprints=[
                    str(f.get("recurrence_fingerprint") or "")
                    for f in findings
                    if isinstance(f, dict)
                    and str(f.get("finding_id") or "")
                    in set(translation["finding_ids"])
                ],
                origin="system", revision=revision,
                finding_ids=intent.finding_ids, op_labels=intent.op_labels,
                reason=str(reason or "")[:120],
            )
        except Exception:  # noqa: BLE001 — 记账失败不阻断回执
            logger.debug("[VisualAutoRepair] decision record failed",
                         exc_info=True)

    try:
        result = await eng.apply_visual_heal_patch(
            session_id, defects, origin="system",
            expected_revision=current_revision,
            mutation_id=f"vauto:{intent.defect_fingerprint[:24]}:"
                        f"{current_revision}",
            on_exhausted="degrade",
        )
    except Exception as exc:  # noqa: BLE001 — 锁/引擎异常按诚实回执（自动通道绝不炸终验）
        logger.warning("[VisualAutoRepair] apply failed session=%s",
                       session_id, exc_info=True)
        await _record(DECISION_AUTO_FAILED, "apply_error", current_revision)
        return _receipt("apply_error")

    if getattr(result, "superseded", False):
        await _record(DECISION_AUTO_FAILED, "revision_conflict",
                      int(result.mutation_revision or 0))
        return _receipt("revision_conflict",
                        mutation_revision=int(result.mutation_revision or 0))

    applied = getattr(result, "is_error", True) is False
    if applied and not getattr(result, "duplicate", False):
        budget["revisions"] = dict(revisions)
        budget["revisions"][str(current_revision)] = \
            int(revisions.get(str(current_revision), 0)) + 1
        budget["session_total"] = session_total + 1
        try:
            await _save_budget(session_id, budget)
        except Exception:  # noqa: BLE001 — 预算落账失败不回滚已提交修复
            logger.warning("[VisualAutoRepair] budget save failed",
                          exc_info=True)
        # 与 user 批准路径同款：修复尝试 = 进展信号 → 重置 recurrence。
        try:
            from app.services.gis_harness.visual_observation.recurrence import (
                load_ledger, reset_fingerprints, save_ledger,
            )

            wanted_ids = set(translation["finding_ids"])
            fingerprints = [
                str(f.get("recurrence_fingerprint") or "")
                for f in findings
                if isinstance(f, dict)
                and str(f.get("finding_id") or "") in wanted_ids
            ]
            fingerprints += [intent.defect_fingerprint] \
                if intent.defect_fingerprint else []
            if fingerprints:
                ledger = await load_ledger(session_id)
                await save_ledger(
                    session_id, reset_fingerprints(ledger, fingerprints))
        except Exception:  # noqa: BLE001 — 披露面
            logger.debug("[VisualAutoRepair] recurrence reset failed",
                         exc_info=True)

    # P2-3：所有收场都入账 —— 成功（含 duplicate 世代）auto_applied；
    # 错误/硬停 auto_failed（reason 带机器可读码），账本不失真。
    await _record(
        DECISION_AUTO_APPLIED if applied else DECISION_AUTO_FAILED,
        str(getattr(result, "error_code", "") or ""),
        int(result.mutation_revision or current_revision),
    )
    return {
        "ran": True,
        "applied": applied,
        "duplicate": bool(getattr(result, "duplicate", False)),
        "hard_stop": (
            str(getattr(result, "error_code", "") or "")
            == "HEAL_CONVERGENCE_EXHAUSTED"),
        "reason": str(getattr(result, "error_code", "") or "") if not applied else "",
        "proposal_id": proposal_id,
        "mutation_revision": int(result.mutation_revision or 0),
        "skipped": skipped[:12],
    }


__all__ = [
    "BUDGET_KEY",
    "MAX_AUTO_PER_REVISION",
    "MAX_AUTO_PER_SESSION",
    "auto_repair_enabled",
    "run_auto_repair_pass",
]
