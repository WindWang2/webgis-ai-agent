"""G33 专题表达工作台 HTTP 面（UI 与 Pi 工具共用 thematic_authoring.service）。

新文件优先（对齐 raster_authoring.py 先例）：会话归属由
``require_owned_session`` 守卫；提交走与用户/agent 完全相同的
MapSpec 生命周期引擎（CAS + checkpoint + durable undo 栈）。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.models.db_model import Conversation
from app.services.auth_history_bridge import require_owned_session
from app.services.thematic_authoring import (
    commit_expression,
    inspect_layer,
    preview_expression,
)
from app.services.thematic_authoring.contract import parse_plan
from app.services.thematic_authoring.errors import (
    ThematicAuthoringError,
    error_payload,
    http_status,
)

router = APIRouter(prefix="/thematic-authoring", tags=["专题表达工作台"])


def _fail(err: ThematicAuthoringError) -> HTTPException:
    return HTTPException(status_code=http_status(err), detail=error_payload(err))


async def _owned(session_id: str, owner_token: Optional[str], conv: Conversation) -> Optional[str]:
    """会话归属由 require_owned_session 守卫；这里取 ref 读取用的 token。"""
    return owner_token or getattr(conv, "owner_token", None)


# ─── 请求体 ────────────────────────────────────────────────────────────────

class PreviewBody(BaseModel):
    plan: Dict[str, Any]


class CommitBody(PreviewBody):
    expected_revision: int = Field(ge=0)
    client_mutation_id: Optional[str] = Field(default=None, max_length=128)


# ─── 作者检查（当前表达 + 同版本分布） ────────────────────────────────────

@router.get("/sessions/{session_id}/layers/{layer_id}/inspect")
async def inspect(
    session_id: str,
    layer_id: str,
    field: Optional[str] = Query(None, max_length=128),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await inspect_layer(
            session_id, layer_id, field=field,
            owner_token=await _owned(session_id, owner_token, conv))
    except ThematicAuthoringError as err:
        raise _fail(err) from err


# ─── 预览（零 mutation） ───────────────────────────────────────────────────

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
            owner_token=await _owned(session_id, owner_token, conv))
    except ThematicAuthoringError as err:
        raise _fail(err) from err


# ─── 单笔提交（同 id 整层 upsert；一次 CAS / 一个 checkpoint） ─────────────

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
            origin="user", actor="thematic_authoring_panel",
            mutation_id=f"thematic:{body.client_mutation_id}" if body.client_mutation_id else None,
            owner_token=await _owned(session_id, owner_token, conv),
        )
    except ThematicAuthoringError as err:
        raise _fail(err) from err
