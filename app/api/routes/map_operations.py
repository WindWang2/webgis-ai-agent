"""Map Operation 受控提交/回执端点（ADR-0278，Track A M4）。

- ``GET  /chat/sessions/{sid}/map-operations`` —— 回执环只读面（有界，
  跨刷新恢复「已完成动作不重复提交」的对账依据）。
- ``GET  /chat/sessions/{sid}/map-operations/{operation_id}`` —— 单回执。
- ``POST /chat/sessions/{sid}/map-operations/{operation_id}/commit`` ——
  owner 批准 pending 的敏感操作（confirmation_required → 真实提交）。
  镜像 visual-repairs plan→approve 先例：批准时现读 revision 作 CAS 基线
  （origin=user 由引擎强制 expected_revision），pending 条目先移除再重
  提交（同 operation_id 重提交不被幂等环误判为重放）。

鉴权与 interaction-events / mapspec mutation 同族：require_owned_session
（登录 user_id 归属或匿名 owner_token 匹配，不属主统一 404 —— 不泄漏
会话存在性）。绝不信客户端身份字段（#1841/#1843 攻击面不外溢）。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.client_ip import client_ip_from
from app.core.rate_limiter import get_rate_limiter
from app.models.db_model import Conversation
from app.schemas.map_operation_schema import (
    MapOperationCommitRequest,
    MapOperationListResponse,
    MapOperationReceiptResponse,
)
from app.services.auth_history_bridge import require_owned_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["AI对话"])

# 独立评审 A-2：批准路由触发环写 + 编译 + 引擎链 —— 与 display-ack 同款
# 限速（own-session 面的背压，非安全边界）。
_RATE_LIMIT_MAX = 60
_RATE_LIMIT_WINDOW = 60


async def _raise_if_deleted(session_id: str) -> None:
    from app.services.session_data import session_data_manager

    if await session_data_manager.get_state_field(session_id, "_cartographic_deleted") is True:
        raise HTTPException(status_code=410, detail="Session was deleted")


@router.get(
    "/sessions/{session_id}/map-operations",
    response_model=MapOperationListResponse,
)
async def list_map_operations(
    session_id: str,
    _conv: Conversation = Depends(require_owned_session),
):
    """回执环（最新在前，≤8 条；slim 形状无 mapspec 大对象）。"""
    await _raise_if_deleted(session_id)
    from app.services.gis_harness.map_operations import list_operation_receipts

    ops = await list_operation_receipts(session_id, limit=8)
    return MapOperationListResponse(session_id=session_id, operations=ops)


@router.get(
    "/sessions/{session_id}/map-operations/{operation_id}",
    response_model=MapOperationReceiptResponse,
)
async def get_map_operation(
    session_id: str,
    operation_id: str,
    _conv: Conversation = Depends(require_owned_session),
):
    await _raise_if_deleted(session_id)
    from app.services.gis_harness.map_operations import get_operation_receipt

    receipt = await get_operation_receipt(session_id, operation_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Operation not found")
    return MapOperationReceiptResponse(
        session_id=session_id,
        operation_id=operation_id,
        receipt=receipt,
    )


@router.post(
    "/sessions/{session_id}/map-operations/{operation_id}/commit",
    response_model=MapOperationReceiptResponse,
)
async def commit_pending_map_operation(
    session_id: str,
    operation_id: str,
    req: MapOperationCommitRequest,
    request: Request,
    _conv: Conversation = Depends(require_owned_session),
):
    """owner 批准 pending 敏感操作（confirmation_required → 提交）。

    批准语义：预览卡已向 owner 披露 before/after；本端点在 owner 身份
    验证通过后，以 origin=user（引擎强制 CAS）重放该操作的效果集。
    基线 revision 在批准时**现读**（不信任预览时的旧值）；并发推进则
    superseded 如实返回，绝不盲写。
    """
    await _raise_if_deleted(session_id)
    from app.services.gis_harness.map_operations import (
        MapOperationRequest,
        commit_map_operation,
        get_operation_receipt,
        read_current_revision,
    )

    client_ip = client_ip_from(request)
    limiter = await get_rate_limiter()
    if not await limiter.is_allowed(
        f"map_op_commit:{client_ip}", _RATE_LIMIT_MAX, _RATE_LIMIT_WINDOW
    ):
        raise HTTPException(status_code=429, detail="Too many map operation commits")

    pending = await get_operation_receipt(session_id, operation_id)
    if pending is None:
        raise HTTPException(status_code=404, detail="Operation not found")
    if pending.get("status") != "confirmation_required":
        raise HTTPException(
            status_code=409,
            detail={
                "error": "operation_not_pending",
                "message": "该操作不在待确认状态（可能已提交或已失效）。",
                "status": pending.get("status"),
            },
        )
    stored_request = pending.get("pending_effects_request") or {}
    raw_effects = stored_request.get("effects") or []
    if not raw_effects:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "pending_payload_missing",
                "message": "待确认操作的载荷缺失，请让 Agent 重新发起。",
            },
        )
    try:
        request = MapOperationRequest(
            operation_id=operation_id,
            mode="commit",
            effects=raw_effects,
            expected_revision=req.expected_revision
            if req.expected_revision is not None
            else await read_current_revision(session_id),
            actor="owner_commit",
            user_ack=True,
            note=(stored_request.get("note") or None),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "pending_payload_invalid", "message": str(exc)},
        ) from exc

    # 独立评审 A-5：不再「先删 pending 再提交」—— bypass_replay 放行重放
    # 门（旧回执就是本次批准的 pending 条目）；提交失败时 pending 保留，
    # owner 可重试批准。成功后 _persist_receipt 自然以最终回执覆盖同 id。
    receipt = await commit_map_operation(
        session_id, request, origin="user", user_ack=True, bypass_replay=True,
    )
    payload: Any = dict(receipt)
    return MapOperationReceiptResponse(
        session_id=session_id,
        operation_id=operation_id,
        receipt=payload,
    )
