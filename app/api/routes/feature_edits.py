"""Feature-edit REST 面（/goal 03）——预览/决策/撤销/历史/策略。

鉴权：全部挂 require_owned_session（跨租户统一 404 不泄存在）。
actor/origin 由本路由服务端裁决（user），绝不采信请求体。
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, Header, HTTPException

from app.models.db_model import Conversation
from app.schemas.feature_edit_schema import (
    FeatureEditPreviewRequest,
    PlanDecisionRequest,
    PolicyPutRequest,
)
from app.services.auth_history_bridge import require_owned_session
from app.services.data_ingest.pipeline import compute_payload_fingerprint
from app.services.distributed_lock import (
    LockContentionError,
    LockDegradedError,
    LockLostError,
)
from app.services.feature_edit import executor as fe_executor
from app.services.feature_edit import policy as fe_policy
from app.services.feature_edit.contracts import PLAN_TTL_S
from app.services.feature_edit.plan import build_plan, plan_expired, summarize_plan
from app.services.session_data import session_data_manager

router = APIRouter(prefix="/chat", tags=["AI对话"])


async def _load_payload(session_id: str, ref_id: str) -> Any:
    payload = await session_data_manager.get(session_id, ref_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return payload


async def _current_revision(session_id: str, ref_id: str) -> int:
    descriptor = await session_data_manager.get_ref_descriptor(session_id, ref_id) or {}
    return int(descriptor.get("content_revision") or 0)


@router.post("/sessions/{session_id}/feature-edits/preview")
async def preview_feature_edit(
    session_id: str,
    req: FeatureEditPreviewRequest,
    _conv: Conversation = Depends(require_owned_session),
) -> Dict[str, Any]:
    ops = [op.model_dump(by_alias=True, exclude_none=True) for op in req.ops]
    ref_id = req.target.ref_id
    selection_view = None
    if req.target.mode == "selection":
        from app.services.gis_harness.interaction_ir.projection import (
            interaction_snapshot_via_store,
        )

        snapshot = await interaction_snapshot_via_store(session_data_manager, session_id)
        selection_view = (snapshot or {}).get("selection") or {}
        sel_ref = selection_view.get("dataset_ref")
        if not ref_id and isinstance(sel_ref, str) and sel_ref.startswith("ref:"):
            ref_id = sel_ref
    if not ref_id or not str(ref_id).startswith("ref:"):
        raise HTTPException(status_code=400, detail="ref_id (ref:…) is required")
    payload = await _load_payload(session_id, ref_id)
    revision = await _current_revision(session_id, ref_id)

    # snap 目标预取（预览纯读；目标不可达出 finding 而非 500）
    target_payloads: Dict[str, Any] = {}
    for op in ops:
        target_ref = op.get("target_ref")
        if op.get("op") == "snap_to_ref" and isinstance(target_ref, str):
            target_payloads[target_ref] = await session_data_manager.get(session_id, target_ref)

    plan, findings = build_plan(
        ref_id=ref_id,
        payload=payload,
        ops=ops,
        requested_ids=req.target.feature_ids,
        selection=selection_view,
        dataset_fingerprint=compute_payload_fingerprint(payload),
        content_revision=revision,
        target_payloads=target_payloads,
    )
    if findings and not plan:
        # 不可建 plan（载荷不可编辑/无目标等）——400 带封闭 code。
        raise HTTPException(status_code=400, detail={"code": findings[0]["code"], "findings": findings})
    summary = summarize_plan(plan)
    summary["preview"] = plan.get("preview") or []
    summary["validation"] = findings
    if plan.get("committable"):
        await fe_executor.store_plan(session_id, plan)
        summary["expires_in_s"] = PLAN_TTL_S
    return summary


@router.get("/sessions/{session_id}/feature-edits/plans/{plan_id}")
async def get_feature_edit_plan(
    session_id: str,
    plan_id: str,
    _conv: Conversation = Depends(require_owned_session),
) -> Dict[str, Any]:
    plan = await fe_executor.get_plan(session_id, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    summary = summarize_plan(plan)
    summary["preview"] = plan.get("preview") or []
    summary["created_at"] = plan.get("created_at")
    if plan_expired(plan):
        summary["status"] = "expired"
    return summary


@router.post("/sessions/{session_id}/feature-edits/plans/{plan_id}/decision")
async def decide_feature_edit_plan(
    session_id: str,
    plan_id: str,
    req: PlanDecisionRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    _conv: Conversation = Depends(require_owned_session),
) -> Dict[str, Any]:
    plan = await fe_executor.get_plan(session_id, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    if plan_expired(plan):
        await fe_executor._mark_plan_status(session_id, plan_id, "expired")
        raise HTTPException(status_code=410, detail="Plan expired — request a new preview")
    if plan.get("status") == "applied":
        return {"status": "applied", "receipt": plan.get("receipt") or {"entry_seq": plan.get("entry_seq")}}
    if plan.get("status") != "pending" or not plan.get("committable"):
        raise HTTPException(status_code=409, detail={"code": "plan_state_invalid", "status": plan.get("status")})
    if req.action == "reject":
        await fe_executor._mark_plan_status(session_id, plan_id, "rejected")
        return {"status": "rejected"}

    # approve：owner 鉴权已过（require_owned_session）→ 执行（executor 内
    # CAS/幂等/入账/盖章/广播一体）。
    try:
        receipt = await fe_executor.commit_plan(
            session_id,
            plan,
            actor="user",
            origin="user",
            idempotency_key=idempotency_key,
        )
        return {"status": "applied", "receipt": receipt}
    except fe_executor.FeatureEditConflict as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code, **exc.detail})
    except fe_executor.FeatureEditBlocked as exc:
        code = exc.code if exc.code in ("dataset_not_found",) else 400
        raise HTTPException(status_code=404 if code == "dataset_not_found" else 400,
                            detail={"code": exc.code, "message": exc.message, **exc.detail})
    except (LockContentionError, LockDegradedError, LockLostError) as exc:
        raise HTTPException(status_code=503, detail=f"Session busy: {exc}")


@router.post("/sessions/{session_id}/feature-edits/{ref_id}/undo")
async def undo_feature_edit(
    session_id: str,
    ref_id: str,
    _conv: Conversation = Depends(require_owned_session),
) -> Dict[str, Any]:
    try:
        receipt = await fe_executor.apply_undo(session_id, ref_id, actor="user", origin="user")
        return {"status": "undone", "receipt": receipt}
    except fe_executor.FeatureEditConflict as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code, **exc.detail})
    except fe_executor.FeatureEditBlocked as exc:
        if exc.code == "dataset_not_found":
            raise HTTPException(status_code=404, detail=exc.message)
        raise HTTPException(status_code=409, detail={"code": exc.code, "message": exc.message, **exc.detail})
    except (LockContentionError, LockDegradedError, LockLostError) as exc:
        raise HTTPException(status_code=503, detail=f"Session busy: {exc}")


@router.post("/sessions/{session_id}/feature-edits/{ref_id}/redo")
async def redo_feature_edit(
    session_id: str,
    ref_id: str,
    _conv: Conversation = Depends(require_owned_session),
) -> Dict[str, Any]:
    try:
        receipt = await fe_executor.apply_redo(session_id, ref_id, actor="user", origin="user")
        return {"status": "redone", "receipt": receipt}
    except fe_executor.FeatureEditConflict as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code, **exc.detail})
    except fe_executor.FeatureEditBlocked as exc:
        if exc.code == "dataset_not_found":
            raise HTTPException(status_code=404, detail=exc.message)
        raise HTTPException(status_code=409, detail={"code": exc.code, "message": exc.message, **exc.detail})
    except (LockContentionError, LockDegradedError, LockLostError) as exc:
        raise HTTPException(status_code=503, detail=f"Session busy: {exc}")


@router.get("/sessions/{session_id}/feature-edits/{ref_id}/history")
async def feature_edit_history(
    session_id: str,
    ref_id: str,
    limit: int = 20,
    _conv: Conversation = Depends(require_owned_session),
) -> Dict[str, Any]:
    await _load_payload(session_id, ref_id)
    journals = await fe_executor._load_journals(session_id)
    journal = journals.get(ref_id) or {}
    entries = list(reversed(journal.get("entries") or []))[: max(1, min(limit, 50))]
    return {
        "ref_id": ref_id,
        "head_seq": journal.get("head_seq") or 0,
        "dropped": journal.get("dropped") or 0,
        "entries": [
            {
                "seq": e.get("seq"),
                "kind": e.get("kind"),
                "actor": e.get("actor"),
                "origin": e.get("origin"),
                "changed": len({d.get("wfid") for d in e.get("deltas") or [] if isinstance(d, dict)}),
                "schema_ops": e.get("schema_ops") or [],
                "content_revision_after": e.get("content_revision_after"),
                "created_at": e.get("created_at"),
            }
            for e in entries
        ],
    }


@router.get("/sessions/{session_id}/feature-edits/policy")
async def get_feature_edit_policy(
    session_id: str,
    _conv: Conversation = Depends(require_owned_session),
) -> Dict[str, Any]:
    return await fe_policy.get_policy(session_id)


@router.put("/sessions/{session_id}/feature-edits/policy")
async def put_feature_edit_policy(
    session_id: str,
    req: PolicyPutRequest,
    _conv: Conversation = Depends(require_owned_session),
) -> Dict[str, Any]:
    return await fe_policy.set_policy(session_id, req.mode)
