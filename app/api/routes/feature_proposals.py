"""feature-proposals REST —— 持久要素变更提案审阅（G32）。

全部端点挂 ``require_owned_session``（跨租户统一 404 不泄存在）；
actor 由服务端 auth 上下文裁决（HTTP 面恒 ``user``），**绝不采信请求体** ——
agent 工具面（feature_proposal_tools）没有 decision/merge 函数（P23）。
错误映射：FeatureProposalNotFound→404 / Conflict→409 / Blocked→400·404·422。
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from app.schemas.feature_proposal_schema import (
    CommentRequest,
    ProposalCreateRequest,
    ProposalDecisionRequest,
    ProposalUpdateRequest,
    RebaseRequest,
    ResolutionSubmitRequest,
)
from app.services.auth_history_bridge import get_current_user_optional, require_owned_session
from app.services.feature_proposals import service as fp_service
from app.services.feature_proposals.contracts import (
    FeatureProposalBlocked,
    FeatureProposalConflict,
    FeatureProposalError,
    FeatureProposalNotFound,
    SnapshotUnavailable,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["AI对话"])

_BLOCK_STATUS = {
    "dataset_not_found": 404,
    "proposal_not_found": 404,
    "dataset_read_only_source": 422,
    "invalid_ops": 422,
    "invalid_target": 422,
    "invalid_action": 422,
    "state_invalid": 409,
    "resolution_rejected": 422,
    "resolution_emptied": 422,
    "draft_limit_reached": 429,
    "snapshot_unavailable": 410,
    "snapshot_too_large": 422,
    "snapshot_not_serializable": 422,
    "snapshot_corrupt": 410,
}


def _actor(user: Optional[dict]) -> tuple[str, str]:
    """HTTP 面 actor 恒 user；actor_id 取 auth 上下文（缺省 owner 标识）。"""
    if isinstance(user, dict):
        uid = user.get("user_id") or user.get("sub") or user.get("id")
        if isinstance(uid, str) and uid:
            return "user", uid
    return "user", "session-owner"


def _error_response(exc: FeatureProposalError) -> JSONResponse:
    payload = exc.to_payload()
    if isinstance(exc, FeatureProposalNotFound):
        status = 404
    elif isinstance(exc, FeatureProposalConflict):
        status = 409
    elif isinstance(exc, SnapshotUnavailable):
        status = _BLOCK_STATUS.get(exc.code, 410)
    elif isinstance(exc, FeatureProposalBlocked):
        status = _BLOCK_STATUS.get(exc.code, 400)
    else:
        status = 500
    return JSONResponse(status_code=status, content=payload)


@router.post("/sessions/{session_id}/feature-proposals")
async def create_feature_proposal(
    session_id: str,
    body: ProposalCreateRequest,
    _conv=Depends(require_owned_session),
    user: Optional[dict] = Depends(get_current_user_optional),
) -> JSONResponse:
    actor_kind, actor_id = _actor(user)
    try:
        record = await fp_service.create_proposal(
            session_id,
            ref_id=body.ref_id,
            ops=[op.model_dump(by_alias=False) for op in body.ops],
            feature_ids=body.feature_ids,
            title=body.title,
            author_kind=actor_kind,
            author_id=actor_id,
            submit=body.submit,
        )
        return JSONResponse(status_code=201, content=fp_service.proposal_summary(record))
    except FeatureProposalError as exc:
        return _error_response(exc)


@router.get("/sessions/{session_id}/feature-proposals")
async def list_feature_proposals(
    session_id: str,
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _conv=Depends(require_owned_session),
) -> JSONResponse:
    try:
        return JSONResponse(content=await fp_service.list_proposals(
            session_id, status=status, limit=limit, offset=offset,
        ))
    except FeatureProposalError as exc:
        return _error_response(exc)


@router.get("/sessions/{session_id}/feature-proposals/{proposal_id}")
async def get_feature_proposal(
    session_id: str, proposal_id: str,
    _conv=Depends(require_owned_session),
) -> JSONResponse:
    try:
        return JSONResponse(content=await fp_service.get_proposal_detail(session_id, proposal_id))
    except FeatureProposalError as exc:
        return _error_response(exc)


@router.get("/sessions/{session_id}/feature-proposals/{proposal_id}/diff")
async def get_feature_proposal_diff(
    session_id: str, proposal_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _conv=Depends(require_owned_session),
) -> JSONResponse:
    try:
        return JSONResponse(content=await fp_service.get_diff(
            session_id, proposal_id, page=page, page_size=page_size,
        ))
    except FeatureProposalError as exc:
        return _error_response(exc)


@router.get("/sessions/{session_id}/feature-proposals/{proposal_id}/conflicts")
async def get_feature_proposal_conflicts(
    session_id: str, proposal_id: str,
    _conv=Depends(require_owned_session),
) -> JSONResponse:
    try:
        return JSONResponse(content=await fp_service.get_conflicts(session_id, proposal_id))
    except FeatureProposalError as exc:
        return _error_response(exc)


@router.patch("/sessions/{session_id}/feature-proposals/{proposal_id}")
async def update_feature_proposal(
    session_id: str, proposal_id: str,
    body: ProposalUpdateRequest,
    _conv=Depends(require_owned_session),
    user: Optional[dict] = Depends(get_current_user_optional),
) -> JSONResponse:
    actor_kind, actor_id = _actor(user)
    try:
        record = await fp_service.update_proposal(
            session_id, proposal_id,
            expected_revision=body.expected_revision,
            ops=[op.model_dump(by_alias=False) for op in body.ops] if body.ops is not None else None,
            feature_ids=body.feature_ids,
            title=body.title,
            actor_kind=actor_kind, actor_id=actor_id,
        )
        return JSONResponse(content=fp_service.proposal_summary(record))
    except FeatureProposalError as exc:
        return _error_response(exc)


@router.post("/sessions/{session_id}/feature-proposals/{proposal_id}/resolutions")
async def submit_feature_proposal_resolutions(
    session_id: str, proposal_id: str,
    body: ResolutionSubmitRequest,
    _conv=Depends(require_owned_session),
    user: Optional[dict] = Depends(get_current_user_optional),
) -> JSONResponse:
    actor_kind, actor_id = _actor(user)
    try:
        return JSONResponse(content=await fp_service.submit_resolutions(
            session_id, proposal_id,
            expected_revision=body.expected_revision,
            resolutions=[r.model_dump(by_alias=False) for r in body.resolutions],
            actor_kind=actor_kind, actor_id=actor_id,
        ))
    except FeatureProposalError as exc:
        return _error_response(exc)


@router.post("/sessions/{session_id}/feature-proposals/{proposal_id}/rebase")
async def rebase_feature_proposal(
    session_id: str, proposal_id: str,
    body: RebaseRequest,
    _conv=Depends(require_owned_session),
    user: Optional[dict] = Depends(get_current_user_optional),
) -> JSONResponse:
    actor_kind, actor_id = _actor(user)
    try:
        return JSONResponse(content=await fp_service.rebase_proposal(
            session_id, proposal_id,
            expected_revision=body.expected_revision, apply=body.apply,
            actor_kind=actor_kind, actor_id=actor_id,
        ))
    except FeatureProposalError as exc:
        return _error_response(exc)


@router.post("/sessions/{session_id}/feature-proposals/{proposal_id}/decisions")
async def decide_feature_proposal(
    session_id: str, proposal_id: str,
    body: ProposalDecisionRequest,
    request: Request,
    _conv=Depends(require_owned_session),
    user: Optional[dict] = Depends(get_current_user_optional),
) -> JSONResponse:
    actor_kind, actor_id = _actor(user)
    # P23：decision 入口的 actor_kind 恒为 user（服务端裁决）；请求体没有
    # actor/role 字段（schema extra=forbid），伪造无从谈起。
    try:
        return JSONResponse(content=await fp_service.decide(
            session_id, proposal_id,
            action=body.action, note=body.note,
            actor_kind=actor_kind, actor_id=actor_id,
        ))
    except FeatureProposalError as exc:
        return _error_response(exc)


@router.post("/sessions/{session_id}/feature-proposals/{proposal_id}/merge")
async def merge_feature_proposal(
    session_id: str, proposal_id: str,
    _conv=Depends(require_owned_session),
    user: Optional[dict] = Depends(get_current_user_optional),
) -> JSONResponse:
    actor_kind, actor_id = _actor(user)
    try:
        return JSONResponse(content=await fp_service.merge(
            session_id, proposal_id, actor_kind=actor_kind, actor_id=actor_id,
        ))
    except FeatureProposalError as exc:
        return _error_response(exc)


@router.get("/sessions/{session_id}/feature-proposals/{proposal_id}/recovery")
async def feature_proposal_recovery_view(
    session_id: str, proposal_id: str,
    _conv=Depends(require_owned_session),
) -> JSONResponse:
    try:
        return JSONResponse(content=await fp_service.recovery_view(session_id, proposal_id))
    except FeatureProposalError as exc:
        return _error_response(exc)


@router.post("/sessions/{session_id}/feature-proposals/{proposal_id}/recovery/resolve")
async def resolve_feature_proposal_recovery(
    session_id: str, proposal_id: str,
    _conv=Depends(require_owned_session),
    user: Optional[dict] = Depends(get_current_user_optional),
) -> JSONResponse:
    actor_kind, actor_id = _actor(user)
    try:
        return JSONResponse(content=await fp_service.resolve_recovery(
            session_id, proposal_id, actor_kind=actor_kind, actor_id=actor_id,
        ))
    except FeatureProposalError as exc:
        return _error_response(exc)


@router.post("/sessions/{session_id}/feature-proposals/{proposal_id}/comments")
async def comment_feature_proposal(
    session_id: str, proposal_id: str,
    body: CommentRequest,
    _conv=Depends(require_owned_session),
    user: Optional[dict] = Depends(get_current_user_optional),
) -> JSONResponse:
    actor_kind, actor_id = _actor(user)
    try:
        return JSONResponse(content=await fp_service.add_comment(
            session_id, proposal_id, body=body.body,
            author_kind=actor_kind, author_id=actor_id,
        ))
    except FeatureProposalError as exc:
        return _error_response(exc)


@router.get("/sessions/{session_id}/feature-proposals/{proposal_id}/audit")
async def export_feature_proposal_audit(
    session_id: str, proposal_id: str,
    _conv=Depends(require_owned_session),
) -> JSONResponse:
    try:
        return JSONResponse(content=await fp_service.export_audit(session_id, proposal_id))
    except FeatureProposalError as exc:
        return _error_response(exc)


@router.post("/sessions/{session_id}/feature-proposals/{proposal_id}/withdraw")
async def withdraw_feature_proposal(
    session_id: str, proposal_id: str,
    body: ProposalDecisionRequest,
    _conv=Depends(require_owned_session),
    user: Optional[dict] = Depends(get_current_user_optional),
) -> JSONResponse:
    actor_kind, actor_id = _actor(user)
    try:
        return JSONResponse(content=await fp_service.withdraw(
            session_id, proposal_id, actor_kind=actor_kind, actor_id=actor_id,
            note=body.note,
        ))
    except FeatureProposalError as exc:
        return _error_response(exc)
