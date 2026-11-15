"""G38 数据关联工作台 HTTP 面（UI 与 Pi 工具共用 data_join_authoring.service）。

会话归属由 ``require_owned_session`` 守卫（G32/G33 同款）；源解析的逐源
授权在 service 内经 G18 resolve_source 复核。错误统一 typed payload
（stable machine code + correction_hint）。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.models.db_model import Conversation
from app.services.auth_history_bridge import require_owned_session
from app.services.data_join_authoring import service
from app.services.data_join_authoring.errors import DataJoinError, http_status

router = APIRouter(prefix="/data-join-authoring", tags=["数据关联工作台 G38"])


def _fail(err: DataJoinError) -> HTTPException:
    return HTTPException(status_code=http_status(err), detail=err.payload())


def _actor(conv: Conversation, owner_token: Optional[str],
           session_id: str = "") -> Dict[str, Any]:
    # trusted_session_id：require_owned_session 已证明会话归属 —— 工具/匿名
    # 调用方对同会话源经 service 的受信回退解析；跨会话源仍拒绝。
    return {
        "user_id": str(getattr(conv, "user_id", "") or "") or None,
        "org_id": getattr(conv, "org_id", None),
        "owner_token": owner_token or getattr(conv, "owner_token", None),
        "trusted_session_id": session_id,
    }


# ─── 请求体 ────────────────────────────────────────────────────────────────

class DescribeBody(BaseModel):
    source: Dict[str, Any]


class PlanBody(BaseModel):
    plan: Dict[str, Any]


class UpdatePlanBody(PlanBody):
    expected_revision: int = Field(ge=1)


class PreviewBody(BaseModel):
    expected_revision: int = Field(ge=1)
    sample_rows: int = Field(default=50, ge=1, le=50)


class ExecuteBody(BaseModel):
    expected_revision: int = Field(ge=1)
    background: bool = True


class PageQuery(BaseModel):
    cursor: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=200)


# ─── 源卡 ──────────────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/sources/describe")
async def describe_source(
    session_id: str,
    body: DescribeBody,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.describe_source(
            session_id, body.source, _actor(conv, owner_token, session_id))
    except DataJoinError as err:
        raise _fail(err) from err


# ─── 计划 CRUD（CAS） ─────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/plans")
async def create_plan(
    session_id: str,
    body: PlanBody,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.create_plan(
            session_id, body.plan, actor=_actor(conv, owner_token, session_id))
    except DataJoinError as err:
        raise _fail(err) from err


@router.get("/sessions/{session_id}/plans")
async def list_plans(
    session_id: str,
    limit: int = Query(50, ge=1, le=100),
    include_archived: bool = Query(False),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.list_plans(
            session_id, limit=limit, include_archived=include_archived)
    except DataJoinError as err:
        raise _fail(err) from err


@router.get("/sessions/{session_id}/plans/{plan_id}")
async def get_plan(
    session_id: str,
    plan_id: str,
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.get_plan(session_id, plan_id)
    except DataJoinError as err:
        raise _fail(err) from err


@router.patch("/sessions/{session_id}/plans/{plan_id}")
async def update_plan(
    session_id: str,
    plan_id: str,
    body: UpdatePlanBody,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.update_plan(
            session_id, plan_id, body.plan,
            expected_revision=body.expected_revision,
            actor=_actor(conv, owner_token, session_id))
    except DataJoinError as err:
        raise _fail(err) from err


@router.delete("/sessions/{session_id}/plans/{plan_id}")
async def archive_plan(
    session_id: str,
    plan_id: str,
    expected_revision: int = Query(..., ge=1),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.archive_plan(
            session_id, plan_id, expected_revision=expected_revision)
    except DataJoinError as err:
        raise _fail(err) from err


# ─── 预览（全量诊断；零 mutation） ────────────────────────────────────────

@router.post("/sessions/{session_id}/plans/{plan_id}/preview")
async def preview_plan(
    session_id: str,
    plan_id: str,
    body: PreviewBody,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.preview_plan(
            session_id, plan_id,
            expected_revision=body.expected_revision,
            actor=_actor(conv, owner_token, session_id),
            sample_rows=body.sample_rows)
    except DataJoinError as err:
        raise _fail(err) from err


# ─── 执行 / 检查 / 结果 / 取消 ────────────────────────────────────────────

@router.post("/sessions/{session_id}/plans/{plan_id}/execute")
async def execute_plan(
    session_id: str,
    plan_id: str,
    body: ExecuteBody,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.execute_plan(
            session_id, plan_id,
            expected_revision=body.expected_revision,
            actor=_actor(conv, owner_token, session_id),
            background=body.background)
    except DataJoinError as err:
        raise _fail(err) from err


@router.get("/sessions/{session_id}/runs")
async def list_runs(
    session_id: str,
    plan_id: Optional[str] = Query(None, max_length=64),
    limit: int = Query(50, ge=1, le=100),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.list_runs(
            session_id, plan_id=plan_id, limit=limit)
    except DataJoinError as err:
        raise _fail(err) from err


@router.get("/sessions/{session_id}/runs/{run_id}")
async def inspect_run(
    session_id: str,
    run_id: str,
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.inspect_run(session_id, run_id)
    except DataJoinError as err:
        raise _fail(err) from err


@router.get("/sessions/{session_id}/runs/{run_id}/results")
async def run_results(
    session_id: str,
    run_id: str,
    cursor: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.run_result_page(session_id, run_id, cursor, limit)
    except DataJoinError as err:
        raise _fail(err) from err


@router.post("/sessions/{session_id}/runs/{run_id}/cancel")
async def cancel_run(
    session_id: str,
    run_id: str,
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.cancel_run(session_id, run_id)
    except DataJoinError as err:
        raise _fail(err) from err


@router.post("/sessions/{session_id}/runs/{run_id}/render-ref")
async def render_ref(
    session_id: str,
    run_id: str,
    conv: Conversation = Depends(require_owned_session),
):
    """上图渲染投影（≤10k 行；超过 → typed render_ref_over_budget）。"""
    try:
        return await service.render_ref_for_run(session_id, run_id)
    except DataJoinError as err:
        raise _fail(err) from err


@router.post("/sessions/{session_id}/recover")
async def recover(
    session_id: str,
    conv: Conversation = Depends(require_owned_session),
):
    """孤儿收据收敛（J21；也可由服务启动钩子触发）。"""
    from app.services.data_join_authoring import recovery

    return recovery.recover_runs(session_id)
