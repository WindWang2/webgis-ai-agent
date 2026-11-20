"""G43（本轮实际上下文回执与可控项目资料工作台）—— 会话级路由。

挂载方式与 ``session_model_views`` 相同（``chat.router.include_router``，
继承 ``/chat`` 前缀）—— 所有端点经 ``require_owned_session`` 做 owner 归
属校验（跨用户/猜 id 一律 404，不泄漏存在性）；可选资料目录与策略写入
再叠加项目级 ``get_project_with_auth``（与 memory/knowledge builder 同一
授权边界 —— 越权源 id 统一拒绝且不泄漏名称）。

端点：
- ``GET  /chat/sessions/{id}/context-sources``      授权可选资料目录（含权威版本）；
- ``GET  /chat/sessions/{id}/context-policy``       当前策略（无行 = auto）；
- ``PUT  /chat/sessions/{id}/context-policy``       CAS 更新（下一轮起生效）；
- ``POST /chat/sessions/{id}/context-preview``      无副作用预览（只读投影）；
- ``GET  /chat/sessions/{id}/context-receipts``     最近 turn 回执列表；
- ``GET  /chat/sessions/{id}/context-receipts/{turn_id}`` 单 turn 完整回执。

回执读取只提供有界投影（指纹/计数/源去向），绝不提供 prompt 全文、
秘密或图片字节；历史缺失 = coverage limited（不补造）。
"""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.auth import get_current_user_optional
from app.models.db_model import Conversation
from app.schemas.session_context_views_schema import (
    ContextPolicyResponse,
    ContextPreviewResponse,
    ContextReceiptResponse,
    ContextReceiptsResponse,
    ContextSourcesResponse,
)
from app.services.auth_history_bridge import require_owned_session
from app.services.turn_context_store import (
    PolicyConflict,
    SourceRejected,
    get_receipt,
    list_authorized_sources,
    list_receipts,
    load_policy,
    update_policy,
)

logger = logging.getLogger(__name__)
router = APIRouter()  # 前缀/tags 由挂载方（chat.router）提供


class SourcePolicyUpdate(BaseModel):
    mode: str = Field(..., description="auto | explicit")
    preferred: List[str] = Field(default_factory=list, max_length=64)
    excluded: List[str] = Field(default_factory=list, max_length=64)
    expected_version: int = Field(..., ge=0, description="CAS：当前已知 policy_version")
    project_id: str = Field(default="", max_length=255)


class PreviewRequest(BaseModel):
    project_id: str = Field(default="", max_length=255)
    message_draft: str = Field(default="", max_length=4000)
    policy_draft: Optional[SourcePolicyUpdate] = None


def _resolve_org(user: Optional[dict]) -> str:
    try:
        from app.core.tenancy import effective_org_in_thread

        return effective_org_in_thread(user)
    except Exception:  # noqa: BLE001 — 无租户 = 无可选源（fail-closed）
        return ""


def _actor(user: Optional[dict]) -> tuple:
    if isinstance(user, dict):
        return str(user.get("user_id") or ""), _resolve_org(user)
    return "", ""


@router.get("/sessions/{session_id}/context-sources", response_model=ContextSourcesResponse)
async def get_context_sources(
    session_id: str,
    project_id: str = Query(default="", max_length=255),
    _conv: Conversation = Depends(require_owned_session),
    user: Optional[dict] = Depends(get_current_user_optional),
):
    """当前用户在本会话可选择的资料目录（有界 ids + 权威版本 + 摘要）。"""
    user_id, org_id = _actor(user)
    sources = await list_authorized_sources(
        session_id=session_id, org_id=org_id,
        project_id=project_id, user_id=user_id,
    )
    return {
        "session_id": session_id,
        "project_id": project_id,
        "sources": sources,
        "counts": {
            "project_knowledge_entry": sum(
                1 for s in sources if s["kind"] == "project_knowledge_entry"),
            "carto_memory_fact": sum(
                1 for s in sources if s["kind"] == "carto_memory_fact"),
            "gis_memory_block": sum(
                1 for s in sources if s["kind"] == "gis_memory_block"),
        },
    }


@router.get("/sessions/{session_id}/context-policy", response_model=ContextPolicyResponse)
async def get_context_policy(
    session_id: str,
    _conv: Conversation = Depends(require_owned_session),
):
    policy = await load_policy(session_id)
    return {
        "session_id": session_id,
        "policy": policy.as_bounded_dict(),
        "scope_note": (
            "策略自下一轮装配起生效，仅影响本会话新装配的可选资料；"
            "不删除共享事实/资产，也不从历史会话记录中移除内容。"
        ),
    }


@router.put("/sessions/{session_id}/context-policy", response_model=ContextPolicyResponse)
async def put_context_policy(
    session_id: str,
    req: SourcePolicyUpdate,
    _conv: Conversation = Depends(require_owned_session),
    user: Optional[dict] = Depends(get_current_user_optional),
):
    """CAS 更新会话可选资料策略（server-minted 源 id；越权/未知 id 拒绝）。"""
    user_id, org_id = _actor(user)
    catalog = await list_authorized_sources(
        session_id=session_id, org_id=org_id,
        project_id=req.project_id, user_id=user_id,
    )
    authorized_ids = [s["source_id"] for s in catalog]
    try:
        policy = await update_policy(
            session_id=session_id, org_id=org_id,
            project_id=req.project_id, user_id=user_id,
            mode=req.mode, preferred=req.preferred,
            excluded=req.excluded, expected_version=req.expected_version,
            authorized_source_ids=authorized_ids,
        )
    except SourceRejected as exc:
        # 统一拒绝（结构错误 vs 越权不区分 —— 不泄漏源存在性）
        raise HTTPException(status_code=400, detail=f"invalid source policy: {exc.reason}") from exc
    except PolicyConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "POLICY_VERSION_CONFLICT",
                "message": "policy was updated concurrently; refresh and retry",
                "current": exc.current.as_bounded_dict(),
            },
        ) from exc
    return {
        "session_id": session_id,
        "policy": policy.as_bounded_dict(),
        "effective_when": "next_turn",
        "scope_note": (
            "已生效于下一轮装配；当前在飞 turn（若有）按其装配时刻冻结的"
            "策略执行，不受本次更新影响。"
        ),
    }


@router.post("/sessions/{session_id}/context-preview", response_model=ContextPreviewResponse)
async def post_context_preview(
    session_id: str,
    req: PreviewRequest,
    _conv: Conversation = Depends(require_owned_session),
    user: Optional[dict] = Depends(get_current_user_optional),
):
    """无副作用预览：只读投影下一轮可选资料（不推进任何世界状态）。"""
    from app.services.context_assembly.preview import preview_turn_context
    from app.services.context_assembly.source_policy import SourcePolicy

    user_id, org_id = _actor(user)
    policy = None
    if req.policy_draft is not None:
        # review P3：草稿策略与 PUT 同规则结构校验（mode/命名空间/冲突）。
        from app.services.context_assembly.source_policy import (
            validate_policy_payload,
        )

        _, _, draft_err = validate_policy_payload(
            mode=req.policy_draft.mode,
            preferred=req.policy_draft.preferred,
            excluded=req.policy_draft.excluded,
        )
        if draft_err is not None:
            raise HTTPException(
                status_code=400, detail=f"invalid source policy: {draft_err}")
        if req.policy_draft.mode == "auto" and (
            req.policy_draft.preferred or req.policy_draft.excluded
        ):
            raise HTTPException(
                status_code=400, detail="invalid source policy: auto_with_selection")
        catalog = await list_authorized_sources(
            session_id=session_id, org_id=org_id,
            project_id=req.policy_draft.project_id or req.project_id,
            user_id=user_id,
        )
        authorized_ids = [s["source_id"] for s in catalog]
        unknown = [
            s for s in (*req.policy_draft.preferred, *req.policy_draft.excluded)
            if s not in set(authorized_ids)
        ]
        if unknown:
            raise HTTPException(
                status_code=400, detail="invalid source policy: source_not_authorized")
        policy = SourcePolicy(
            session_id=session_id,
            mode=req.policy_draft.mode,
            preferred=tuple(req.policy_draft.preferred),
            excluded=tuple(req.policy_draft.excluded),
            policy_version=-1,  # 草稿：未落库
        )
    preview = await preview_turn_context(
        session_id=session_id, org_id=org_id,
        project_id=req.project_id or (
            req.policy_draft.project_id if req.policy_draft else ""),
        user_id=user_id,
        message_draft=req.message_draft,
        policy=policy,
    )
    return preview


@router.get("/sessions/{session_id}/context-receipts", response_model=ContextReceiptsResponse)
async def get_context_receipts(
    session_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    _conv: Conversation = Depends(require_owned_session),
):
    """最近 turn 的回执列表（有界摘要，含提交状态与源计数）。"""
    receipts = await list_receipts(session_id, limit=limit)
    return {
        "session_id": session_id,
        "receipts": receipts,
        "coverage_note": (
            "回执记录宿主每轮实际装配与提交状态；早于本功能的 turn 或 "
            "legacy 路径显示 coverage limited/unknown，不补造历史。"
        ),
    }


@router.get("/sessions/{session_id}/context-receipts/{turn_id}", response_model=ContextReceiptResponse)
async def get_context_receipt(
    session_id: str,
    turn_id: str,
    _conv: Conversation = Depends(require_owned_session),
):
    """单 turn 完整回执（有界投影；无记录 = 404，UI 显示 limited）。"""
    receipt = await get_receipt(session_id, turn_id)
    if receipt is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "RECEIPT_NOT_FOUND",
                "message": (
                    "no durable receipt for this turn (legacy session, "
                    "pre-G43 turn, or persistence failure)"
                ),
            },
        )
    return receipt


__all__ = ["router"]
