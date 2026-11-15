"""G41 双变量/可靠性作者工作台 HTTP 面（UI 与 Pi 工具共用同一领域服务）。

会话归属由 ``require_owned_session`` 守卫；提交走与用户/agent 完全相同的
MapSpec 生命周期引擎（CAS + checkpoint + durable undo 栈）。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.models.db_model import Conversation
from app.services.auth_history_bridge import require_owned_session
from app.services.bivariate_reliability_authoring import (
    AuthoringError,
    commit_expression,
    error_payload,
    http_status,
    inspect_layer,
    parse_plan,
    preview_expression,
)

router = APIRouter(prefix="/bivariate-reliability", tags=["双变量与可靠性作者工作台"])


def _fail(err: AuthoringError) -> HTTPException:
    return HTTPException(status_code=http_status(err), detail=error_payload(err))


async def _owned(owner_token: Optional[str], conv: Conversation) -> Optional[str]:
    return owner_token or getattr(conv, "owner_token", None)


class PreviewBody(BaseModel):
    plan: Dict[str, Any]


class CommitBody(PreviewBody):
    expected_revision: int = Field(ge=0)
    preview_digest: str = Field(min_length=1, max_length=128)
    client_mutation_id: Optional[str] = Field(default=None, max_length=128)


@router.get("/sessions/{session_id}/layers/{layer_id}/inspect")
async def inspect(
    session_id: str,
    layer_id: str,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await inspect_layer(
            session_id, layer_id,
            owner_token=await _owned(owner_token, conv))
    except AuthoringError as err:
        raise _fail(err) from err


@router.post("/sessions/{session_id}/layers/{layer_id}/preview")
async def preview(
    session_id: str,
    layer_id: str,
    body: PreviewBody,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        plan = parse_plan({**body.plan, "layer_id": layer_id})
        return await preview_expression(
            session_id, layer_id, plan,
            owner_token=await _owned(owner_token, conv))
    except AuthoringError as err:
        raise _fail(err) from err


@router.post("/sessions/{session_id}/layers/{layer_id}/commit")
async def commit(
    session_id: str,
    layer_id: str,
    body: CommitBody,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        plan = parse_plan({**body.plan, "layer_id": layer_id})
        return await commit_expression(
            session_id, layer_id, plan,
            expected_revision=body.expected_revision,
            preview_digest=body.preview_digest,
            origin="user", actor="bivariate_reliability_panel",
            mutation_id=(f"g41:{body.client_mutation_id}"
                         if body.client_mutation_id else None),
            owner_token=await _owned(owner_token, conv))
    except AuthoringError as err:
        raise _fail(err) from err
