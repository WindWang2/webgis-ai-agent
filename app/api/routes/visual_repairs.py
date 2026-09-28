"""User-approved visual repair 路由（F15，ADR-0214 决策四 + C13 闭环）。

四个端点（全部 ``require_owned_session``，scope ``session:write``）：

- ``POST .../visual-repairs/plan``：存储态 visual findings → 证据新鲜度门
  （C13：``observed_revision`` ≠ 当前 revision = stale，不可修复）→ 拒绝
  记忆过滤（C13：user 拒绝过的缺陷不再索要）→ healer 缺陷翻译（单一映射
  点 ``normalize_visual_report``）→ 闭包预览（⊆ 四类呈现面微变异）→
  typed intent 提案存储。**零突变**；
- ``POST .../visual-repairs/apply``：显式 ``approved=true`` + CAS + 拒绝
  记忆拦截 → ``apply_visual_heal_patch(origin="user")``（CAS/checkpoint/
  revision 单调/幂等回放复用 ADR-0186 事务）→ 批准决策入账 → 成功后
  后台 ``visual_repair`` 触发复验。收敛耗尽 = 200 ``{applied:false,
  hard_stop:true}``（诚实硬停，非 5xx）。**user-wins**（lifecycle guard
  既有裁决）：user origin 是用户自有锁的唯一 override —— 批准经 plan 的
  ``touches_locked`` 披露后知情执行；agent/system 自动修复路径仍被 guard
  一律拒绝；
- ``POST .../visual-repairs/reject``：用户否决提案（拒绝记忆入账）；
- ``POST .../visual-snapshots``：有界截图入库（PNG 魔数 + ≤4 MiB 门 +
  revision/指纹归属），响应 ref+sha 摘要——字节的唯一生产入口，
  trace/journal 只见 ref。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, UploadFile

from app.services.auth_history_bridge import require_owned_session
from app.models.db_model import Conversation
from app.schemas.visual_repair_schema import (
    VisualRepairApplyRequest,
    VisualRepairApplyResponse,
    VisualRepairPlanRequest,
    VisualRepairPlanResponse,
    VisualRepairRejectRequest,
    VisualRepairRejectResponse,
    VisualScreenshotUploadResponse,
)
from app.services.distributed_lock import (
    LockContentionError,
    LockDegradedError,
    LockLostError,
)
from app.services.mapspec.lifecycle_engine import MapSpecLifecycleEngine

router = APIRouter(prefix="/chat", tags=["AI对话"])
_engine = MapSpecLifecycleEngine()
logger = logging.getLogger(__name__)

_HEAL_OP_LABELS = {
    "MUTATION_HEAL_LABEL_COLLISION": "label_layout",
    "MUTATION_HEAL_CONTRAST": "contrast_palette",
    "MUTATION_HEAL_LAYER_ORDER": "layer_order",
    "MUTATION_HEAL_OPACITY": "opacity",
}


async def _stored_visual_findings(session_id: str) -> List[Dict[str, Any]]:
    """map_product.visual_findings（持久化披露面；缺失 = 空）。"""
    from app.services.session_plan import load_session_plan

    plan = await load_session_plan(session_id)
    chapter = plan.gis_chapter if plan is not None else None
    block = (chapter or {}).get("map_product") if isinstance(chapter, dict) else None
    raw = (block or {}).get("visual_findings") if isinstance(block, dict) else None
    if not isinstance(raw, list):
        return []
    return [f for f in raw if isinstance(f, dict)][:12]


def _split_fresh_findings(
    findings: List[Dict[str, Any]], current_revision: int,
) -> tuple:
    """证据新鲜度门（C13）：``observed_revision`` == 当前 revision 的
    finding 才可进入修复面。旧观测不得驱动新地图 —— 终验之后任何
    mutation（用户手改/agent/修复）都会推进 revision，存储态证据随即
    过期；缺省/未知（旧数据 0）保守按过期处理。返回 (fresh, stale_count)。
    """
    fresh: List[Dict[str, Any]] = []
    stale = 0
    for f in findings:
        try:
            observed = int(f.get("observed_revision") or 0)
        except (TypeError, ValueError):
            observed = 0
        if observed == int(current_revision):
            fresh.append(f)
        else:
            stale += 1
    return fresh, stale


async def _known_layer_ids(session_id: str) -> List[str]:
    loaded = await _engine.store.get_mapspec(session_id)
    layers = (loaded or {}).get("layers") if isinstance(loaded, dict) else None
    if not isinstance(layers, list):
        return []
    return [
        str(ly.get("id"))[:64] for ly in layers[:64]
        if isinstance(ly, dict) and ly.get("id")
    ]


async def _current_revision(session_id: str) -> int:
    from app.services.session_data import session_data_manager

    try:
        state = await session_data_manager.get_map_state(session_id)
        return int((state or {}).get("_cartographic_mutation_revision") or 0)
    except (TypeError, ValueError):
        return 0


def _op_preview(op: Any, locked_ids: set) -> Dict[str, Any]:
    """HealOp → 有界预览（载荷按 op 类型择要；锁交集如实披露）。"""
    targets = {str(x) for x in (op.layer_ids or ())}
    return {
        "op": _HEAL_OP_LABELS.get(str(op.op), str(op.op)[:40]),
        "layer_ids": [str(x)[:64] for x in (op.layer_ids or ())][:4],
        "severity": str(op.severity)[:16],
        "rationale": str(op.rationale)[:120],
        # 知情批准面：op 触达用户锁定的图层 —— user-wins 语义下 user
        # origin 是锁的唯一 override，但批准必须发生在披露之后。
        "touches_locked": bool(targets & locked_ids),
    }


@router.post(
    "/sessions/{session_id}/visual-repairs/plan",
    response_model=VisualRepairPlanResponse,
)
async def plan_visual_repair(
    session_id: str,
    req: VisualRepairPlanRequest,
    _conv: Conversation = Depends(require_owned_session),
) -> Dict[str, Any]:
    from app.services.gis_harness.visual_observation.repair_bridge import (
        build_proposal_id,
        defect_payload,
        save_proposal,
        visual_findings_to_defects,
    )
    from app.services.mapspec.visual_healer import VisualHealStrategyPlanner

    findings = await _stored_visual_findings(session_id)
    if not findings:
        return VisualRepairPlanResponse(
            session_id=session_id, proposable=False,
            reason="no_visual_findings").model_dump()

    base_revision = await _current_revision(session_id)
    findings, stale_count = _split_fresh_findings(findings, base_revision)
    if req.finding_ids:
        # 定向修复的 id 全部真实存在但无一新鲜 → 原因是 stale（而非
        # 不可自愈）；id 不存在的 miss 仍交翻译面诚实回 no_healable_defects。
        wanted = {str(x) for x in req.finding_ids}
        known_ids = {str(f.get("finding_id") or "")
                     for f in await _stored_visual_findings(session_id)}
        fresh_ids = {str(f.get("finding_id") or "") for f in findings}
        if wanted and wanted <= known_ids and not (wanted & fresh_ids):
            return VisualRepairPlanResponse(
                session_id=session_id, proposable=False,
                reason="stale_findings", stale_count=stale_count).model_dump()
    if not findings:
        # 全部证据过期：诚实地要求重观察，而不是拿旧 finding 编译新提案。
        return VisualRepairPlanResponse(
            session_id=session_id, proposable=False,
            reason="stale_findings", stale_count=stale_count).model_dump()

    # C13 拒绝记忆：用户拒绝过的缺陷指纹不再反复索要同一 patch。
    skipped: List[Dict[str, Any]] = []
    from app.services.gis_harness.visual_observation.repair_decisions import (
        load_decisions,
        rejected_fingerprints,
    )

    rejected = rejected_fingerprints(await load_decisions(session_id))
    if rejected:
        kept: List[Dict[str, Any]] = []
        for f in findings:
            fp = str(f.get("recurrence_fingerprint") or "")
            if fp and fp in rejected:
                skipped.append({
                    "finding_id": str(f.get("finding_id") or "")[:96],
                    "reason": "user_rejected"})
                continue
            kept.append(f)
        findings = kept
        if not findings:
            return VisualRepairPlanResponse(
                session_id=session_id, proposable=False,
                reason="all_rejected_by_user", skipped=skipped[:12],
                stale_count=stale_count).model_dump()

    translation = visual_findings_to_defects(
        findings,
        known_layer_ids=await _known_layer_ids(session_id),
        finding_ids=req.finding_ids,
    )
    defects = translation["defects"]
    skipped = list(translation["skipped"])
    if not defects:
        return VisualRepairPlanResponse(
            session_id=session_id, proposable=False,
            reason="no_healable_defects", skipped=skipped,
            stale_count=stale_count).model_dump()

    mapspec = await _engine.store.get_mapspec(session_id)
    if not isinstance(mapspec, dict):
        return VisualRepairPlanResponse(
            session_id=session_id, proposable=False,
            reason="mapspec_unavailable", skipped=skipped,
            stale_count=stale_count).model_dump()

    plan = VisualHealStrategyPlanner().plan(mapspec, defects)
    ops = list(plan.ops or ())
    skipped = skipped + [
        {k: str(v)[:64] for k, v in s.items()}
        for s in (plan.skipped or ())
    ]
    if not ops:
        # 诚实无操作优先于形式提案（镜像 healer HEAL_PLAN_EMPTY 纪律）。
        return VisualRepairPlanResponse(
            session_id=session_id, proposable=False,
            reason="no_safe_ops", skipped=skipped,
            stale_count=stale_count).model_dump()

    from app.services.mapspec.lifecycle_engine import locked_layer_ids_of

    locked_ids = set(locked_layer_ids_of(mapspec))
    proposal_id = build_proposal_id(defects, base_revision)
    # C13：提案携带 typed 意图（codec round-trip 面账本/复验共用）。
    from app.services.gis_harness.visual_observation.repair_intent import (
        compile_intent,
        intent_to_dict,
    )

    intent = compile_intent(
        proposal_id=proposal_id, base_revision=int(base_revision),
        approval_class="needs_approval", origin="user",
        defects=defects, finding_ids=translation["finding_ids"],
        op_labels=[str(op.op)[:40] for op in ops[:8]],
        defect_fingerprint=str(plan.defect_fingerprint or "")[:96],
    )
    proposal = {
        "proposal_id": proposal_id,
        "intent": intent_to_dict(intent),
        "defects": defect_payload(defects),
        "ops_signature": str(plan.ops_signature or "")[:96],
        "defect_fingerprint": str(plan.defect_fingerprint or "")[:96],
        "base_revision": int(base_revision),
        "ops": [_op_preview(op, locked_ids) for op in ops[:8]],
        "skipped": skipped[:12],
        "finding_ids": [str(x)[:96] for x in translation["finding_ids"]],
    }
    stored = await save_proposal(session_id, proposal)
    if not stored:
        raise HTTPException(
            status_code=503,
            detail={"error": "proposal_store_unavailable",
                    "message": "提案存储暂不可用，请稍后重试。"},
        )
    return VisualRepairPlanResponse(
        session_id=session_id,
        proposable=True,
        proposal_id=proposal["proposal_id"],
        base_revision=int(base_revision),
        ops=proposal["ops"],
        skipped=skipped,
        finding_ids=proposal["finding_ids"],
        stale_count=stale_count,
    ).model_dump()


@router.post(
    "/sessions/{session_id}/visual-repairs/apply",
    response_model=VisualRepairApplyResponse,
)
async def apply_visual_repair(
    session_id: str,
    req: VisualRepairApplyRequest,
    background_tasks: BackgroundTasks,
    _conv: Conversation = Depends(require_owned_session),
) -> Dict[str, Any]:
    from app.services.gis_harness.visual_observation.recurrence import (
        load_ledger,
        reset_fingerprints,
        save_ledger,
    )
    from app.services.gis_harness.visual_observation.repair_bridge import (
        defects_from_proposal,
        load_proposal,
    )

    # 批准是结构门槛：缺省/False 一律拒绝（无「默认同意」语义）。
    if req.approved is not True:
        raise HTTPException(
            status_code=400,
            detail={"error": "approval_required",
                    "message": "visual repair 需要显式 approved=true。"},
        )
    proposal = await load_proposal(session_id, req.proposal_id)
    if proposal is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "proposal_not_found",
                    "message": "提案不存在或已淘汰（≤4 FIFO），请重新 plan。"},
        )
    # C13 拒绝记忆：用户否决过的提案不可再执行（结构门槛，非 UI 礼仪）。
    if proposal.get("rejected") is True:
        raise HTTPException(
            status_code=409,
            detail={"error": "proposal_rejected",
                    "message": "提案已被用户拒绝，如需修复请重新 plan。"},
        )
    from app.services.gis_harness.visual_observation.repair_decisions import (
        DECISION_APPROVED,
        DECISION_REJECTED,
        decision_for_proposal,
        load_decisions,
    )

    prior = decision_for_proposal(
        await load_decisions(session_id), req.proposal_id)
    if prior is not None and str(prior.get("decision") or "") == DECISION_REJECTED:
        raise HTTPException(
            status_code=409,
            detail={"error": "proposal_rejected",
                    "message": "提案已被用户拒绝，如需修复请重新 plan。"},
        )
    base_revision = int(proposal.get("base_revision") or 0)
    current_revision = await _current_revision(session_id)
    if current_revision != base_revision:
        # plan→apply 之间状态漂移：提案可能基于陈旧 spec，拒绝并要求重提。
        raise HTTPException(
            status_code=409,
            detail={"error": "proposal_stale",
                    "message": "提案基于旧 revision，请重新 plan。",
                    "base_revision": base_revision,
                    "current_revision": current_revision},
        )
    defects = defects_from_proposal(proposal)
    if not defects:
        raise HTTPException(
            status_code=400,
            detail={"error": "proposal_corrupt",
                    "message": "提案缺陷载荷无法还原，请重新 plan。"},
        )
    try:
        # 注：显式 mutation_id 的幂等重放语义有一处可辩边界 —— healer 收敛
        # 账本预检（attempts≥2 → HEAL_CONVERGENCE_EXHAUSTED）先于引擎
        # dedup（lifecycle_engine.apply_visual_heal_patch 预检顺序），故
        # 预算耗尽后的重放收到 hard_stop 回执而非 duplicate 世代。
        result = await _engine.apply_visual_heal_patch(
            session_id,
            defects,
            origin="user",
            expected_revision=req.expected_revision,
            mutation_id=f"vrepair:{req.proposal_id}",
            on_exhausted="degrade",
        )
    except (TimeoutError, LockContentionError):
        raise HTTPException(
            status_code=503,
            detail={"error": "session_busy",
                    "message": "会话正被 Agent 操作占用，请稍后重试。"},
        )
    except (LockDegradedError, LockLostError):
        raise HTTPException(
            status_code=503,
            detail={"error": "session_lock_unavailable",
                    "message": "会话锁暂不可用，状态未修改，请稍后重试。"},
        )
    # CAS 漂移（stale expected_revision）先于错误/成功判定 —— superseded
    # 结果 is_error=False 但**未提交**，漏判会把未提交误报为 applied。
    if result.superseded:
        raise HTTPException(
            status_code=409,
            detail={"error": "revision_conflict",
                    "message": "expected_revision 已漂移，未提交；请重读后重试。",
                    "mutation_revision": int(result.mutation_revision or 0)},
        )

    applied = result.is_error is False
    reason = ""
    if result.is_error:
        if result.error_code == "HEAL_CONVERGENCE_EXHAUSTED":
            # 收敛硬停：同一缺陷指纹的自愈预算耗尽 —— 诚实回执（非 5xx），
            # 用户须人工研判或改变上游策略后再试（correction_hint 已携带）。
            return VisualRepairApplyResponse(
                session_id=session_id,
                proposal_id=req.proposal_id,
                applied=False,
                reason="convergence_exhausted",
                hard_stop=True,
                mutation_revision=int(result.mutation_revision or 0),
                correction_hint=str(result.correction_hint or "")[:200],
            ).model_dump()
        reason = str(result.error_code or "apply_failed")[:48]
    if applied and not result.duplicate:
        # 修复尝试即进展信号：重置相关指纹的 recurrence 计数（fail-open）。
        try:
            wanted_ids = {str(x) for x in (proposal.get("finding_ids") or [])}
            stored_findings = await _stored_visual_findings(session_id)
            fingerprints = [
                str(f.get("recurrence_fingerprint") or "")
                for f in stored_findings
                if isinstance(f, dict)
                and str(f.get("finding_id") or "") in wanted_ids
            ]
            if fingerprints:
                ledger = await load_ledger(session_id)
                await save_ledger(
                    session_id, reset_fingerprints(ledger, fingerprints))
            # C13：批准决策入账（因果链：批准 → mutation_revision）。
            from app.services.gis_harness.visual_observation.repair_decisions import (
                record_decision,
            )
            from app.services.gis_harness.visual_observation.repair_intent import (
                intent_fingerprint,
                intent_from_dict,
            )

            stored_intent = intent_from_dict(proposal.get("intent"))
            await record_decision(
                session_id,
                decision=DECISION_APPROVED,
                proposal_id=req.proposal_id,
                intent_fingerprint=(
                    intent_fingerprint(stored_intent)
                    if stored_intent is not None else ""),
                defect_fingerprint=str(
                    proposal.get("defect_fingerprint") or "")[:96],
                recurrence_fingerprints=fingerprints,
                origin="user", revision=int(result.mutation_revision or 0),
                finding_ids=proposal.get("finding_ids") or (),
            )
        except Exception:  # noqa: BLE001 — 披露面，绝不阻断回执
            logger.debug("[VisualRepair] recurrence reset failed",
                         exc_info=True)

    if applied:
        # visual_repair 触发复验（seam 白名单既有词的生产消费方）；heal 已
        # 推进 mutation revision，去重门自然打开。后台执行，绝不阻断回执。
        background_tasks.add_task(_reverify_after_repair, session_id)

    return VisualRepairApplyResponse(
        session_id=session_id,
        proposal_id=req.proposal_id,
        applied=applied,
        duplicate=bool(result.duplicate),
        reason=reason,
        hard_stop=False,
        mutation_revision=int(result.mutation_revision or 0),
        correction_hint=str(result.correction_hint or "")[:200],
        reverify="visual_repair" if applied else "",
    ).model_dump()


async def _reverify_after_repair(session_id: str) -> None:
    """visual_repair 触发的复验（有界 ≤2 passes；失败不影响已提交修复）。"""
    try:
        from app.services.gis_harness.completion.pipeline import (
            maybe_finalize_map_product,
        )

        await maybe_finalize_map_product(session_id, reason="visual_repair")
    except Exception:  # noqa: BLE001 — 复验是增值面
        logger.warning("[VisualRepair] reverify failed session=%s",
                       session_id, exc_info=True)


@router.post(
    "/sessions/{session_id}/visual-repairs/reject",
    response_model=VisualRepairRejectResponse,
)
async def reject_visual_repair(
    session_id: str,
    req: VisualRepairRejectRequest,
    _conv: Conversation = Depends(require_owned_session),
) -> Dict[str, Any]:
    """用户否决提案（C13 拒绝记忆输入面）。

    拒绝按 finding 的 recurrence 指纹持久（会话内）：plan/auto 面不再为
    同一缺陷编译同一 patch；缺陷证据变化（观察重盖章、指纹更新）后自然
    放行。幂等：重复拒绝返回相同回执（决策按 proposal_id 原位替换）。
    """
    from app.services.gis_harness.visual_observation.repair_bridge import (
        load_proposal,
        save_proposal,
    )
    from app.services.gis_harness.visual_observation.repair_decisions import (
        DECISION_REJECTED,
        record_decision,
    )

    proposal = await load_proposal(session_id, req.proposal_id)
    if proposal is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "proposal_not_found",
                    "message": "提案不存在或已淘汰（≤4 FIFO）。"},
        )
    proposal["rejected"] = True
    proposal["reject_reason"] = str(req.reason or "")[:200]
    await save_proposal(session_id, proposal)

    wanted_ids = {str(x) for x in (proposal.get("finding_ids") or [])}
    recurrence = [
        str(f.get("recurrence_fingerprint") or "")
        for f in await _stored_visual_findings(session_id)
        if isinstance(f, dict)
        and str(f.get("finding_id") or "") in wanted_ids
        and f.get("recurrence_fingerprint")
    ]
    await record_decision(
        session_id,
        decision=DECISION_REJECTED,
        proposal_id=req.proposal_id,
        defect_fingerprint=str(proposal.get("defect_fingerprint") or "")[:96],
        recurrence_fingerprints=recurrence,
        origin="user",
        revision=int(proposal.get("base_revision") or 0),
        finding_ids=proposal.get("finding_ids") or (),
        reason=req.reason,
    )
    return VisualRepairRejectResponse(
        session_id=session_id,
        proposal_id=req.proposal_id,
        rejected=True,
        defect_fingerprint=str(proposal.get("defect_fingerprint") or "")[:96],
    ).model_dump()


@router.post(
    "/sessions/{session_id}/visual-snapshots",
    response_model=VisualScreenshotUploadResponse,
)
async def upload_visual_snapshot(
    session_id: str,
    screenshot: UploadFile,
    mapspec_revision: int = Query(default=0, ge=0),
    mapspec_fingerprint: str = Query(default="", max_length=96),
    width: int = Query(default=0, ge=0),
    height: int = Query(default=0, ge=0),
    _conv: Conversation = Depends(require_owned_session),
) -> Dict[str, Any]:
    from app.services.gis_harness.visual_observation.store import (
        MAX_SCREENSHOT_BYTES,
        ScreenshotRejected,
        register_visual_screenshot,
    )

    # 有界读取：至多 MAX+1 字节 —— 上传通道的内存/磁盘面在 read 处即封顶
    # （超限载荷根本不进入进程堆的完整物化）。
    data = await screenshot.read(MAX_SCREENSHOT_BYTES + 1)
    if len(data) > MAX_SCREENSHOT_BYTES:
        raise HTTPException(
            status_code=400,
            detail={"error": "screenshot_oversized",
                    "message": "截图超过 4MiB 上限。"},
        )
    try:
        entry = await register_visual_screenshot(
            session_id, data,
            mapspec_revision=mapspec_revision,
            mapspec_fingerprint=mapspec_fingerprint,
            width=width, height=height)
    except ScreenshotRejected as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": exc.reason, "message": "截图未通过确定性初筛。"},
        )
    return VisualScreenshotUploadResponse(
        session_id=session_id,
        ref=entry.ref,
        sha256=entry.sha256,
        size=entry.size,
        mapspec_revision=entry.mapspec_revision,
        mapspec_fingerprint=entry.mapspec_fingerprint,
    ).model_dump()


__all__ = ["router"]
