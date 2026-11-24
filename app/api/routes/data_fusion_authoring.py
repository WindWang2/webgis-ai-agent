"""融合工作台 HTTP 面（UI 与 Pi 工具共用 data_fusion_authoring.service）。

会话归属由 ``require_owned_session`` 守卫（G32/G33/G38 同款）；源解析
的逐源授权在 service 内经 G18 resolve_source 复核。错误统一 typed
payload（stable machine code + correction_hint）。普通 UI 与 Agent 同
一 service/同一权限/同一收据/同一幂等键。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.models.db_model import Conversation
from app.schemas.data_fusion_authoring_schema import (
    FusionDecisionListResponse,
    FusionDecisionView,
    FusionDescribeResponse,
    FusionPlanListResponse,
    FusionPlanPreviewResponse,
    FusionPlanView,
    FusionRecoverResponse,
    FusionRefreshResponse,
    FusionRenderRefResponse,
    FusionReviewQueueResponse,
    FusionRunCancelResponse,
    FusionRunListResponse,
    FusionRunResultsResponse,
    FusionRunView,
)
from app.services.auth_history_bridge import require_owned_session
from app.services.data_fusion_authoring import service
from app.services.data_fusion_authoring.errors import DataFusionError, http_status

router = APIRouter(prefix="/data-fusion-authoring", tags=["多源实体融合工作台"])


def _fail(err: DataFusionError) -> HTTPException:
    return HTTPException(status_code=http_status(err), detail=err.payload())


def _actor(conv: Conversation, owner_token: Optional[str],
           session_id: str = "") -> Dict[str, Any]:
    # trusted_session_id：require_owned_session 已证明会话归属 —— 同会话
    # 源经 service 的受信回退解析；跨会话源仍拒绝。
    return {
        "user_id": str(getattr(conv, "user_id", "") or "") or None,
        "org_id": getattr(conv, "org_id", None),
        "owner_token": owner_token or getattr(conv, "owner_token", None),
        "trusted_session_id": session_id,
    }


# ─── 请求体 ────────────────────────────────────────────────────────────────

class DescribeBody(BaseModel):
    sources: List[Dict[str, Any]] = Field(min_length=1, max_length=2)


class PlanBody(BaseModel):
    plan: Dict[str, Any]


class UpdatePlanBody(PlanBody):
    expected_revision: int = Field(ge=1)


class PreviewBody(BaseModel):
    expected_revision: int = Field(ge=1)


class ExecuteBody(BaseModel):
    expected_revision: int = Field(ge=1)
    background: bool = True


class DecisionBody(BaseModel):
    pair_key: str = Field(min_length=4, max_length=160)
    action: str = Field(pattern="^(accept|reject)$")
    decision_run_id: Optional[str] = None
    idx_a: Optional[int] = Field(default=None, ge=0)
    idx_b: Optional[int] = Field(default=None, ge=0)
    field_values: Optional[Dict[str, Any]] = None
    geometry_side: Optional[str] = Field(default=None, pattern="^[ab]$")
    reviewer: Optional[str] = Field(default=None, max_length=255)
    note: Optional[str] = Field(default=None, max_length=500)
    expected_revision: int = Field(default=0, ge=0)


class DeleteDecisionBody(BaseModel):
    pair_key: str = Field(min_length=4, max_length=160)
    expected_revision: int = Field(ge=1)


class CommitBody(BaseModel):
    project_id: Optional[str] = None


# ─── 来源资格卡（WP2） ─────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/sources/describe",
             response_model=FusionDescribeResponse)
async def describe_sources(
    session_id: str,
    body: DescribeBody,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.describe_sources(
            session_id, body.sources, _actor(conv, owner_token, session_id))
    except DataFusionError as err:
        raise _fail(err) from err


# ─── 计划 CRUD（CAS） ─────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/plans", response_model=FusionPlanView)
async def create_plan(
    session_id: str,
    body: PlanBody,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.create_plan(
            session_id, body.plan,
            actor=_actor(conv, owner_token, session_id))
    except DataFusionError as err:
        raise _fail(err) from err


@router.get("/sessions/{session_id}/plans",
            response_model=FusionPlanListResponse)
async def list_plans(
    session_id: str,
    limit: int = Query(50, ge=1, le=200),
    cursor: int = Query(0, ge=0),
    include_archived: bool = Query(True),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.list_plans(
            session_id, limit=limit, cursor=cursor,
            include_archived=include_archived)
    except DataFusionError as err:
        raise _fail(err) from err


@router.get("/sessions/{session_id}/plans/{plan_id}",
            response_model=FusionPlanView)
async def get_plan(
    session_id: str,
    plan_id: str,
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.get_plan(session_id, plan_id)
    except DataFusionError as err:
        raise _fail(err) from err


@router.put("/sessions/{session_id}/plans/{plan_id}",
            response_model=FusionPlanView)
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
    except DataFusionError as err:
        raise _fail(err) from err


@router.post("/sessions/{session_id}/plans/{plan_id}/archive",
             response_model=FusionPlanView)
async def archive_plan(
    session_id: str,
    plan_id: str,
    body: UpdatePlanBody,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.archive_plan(
            session_id, plan_id,
            expected_revision=body.expected_revision)
    except DataFusionError as err:
        raise _fail(err) from err


# ─── 预览（只读；不合并） ──────────────────────────────────────────────────

@router.post("/sessions/{session_id}/plans/{plan_id}/preview",
             response_model=FusionPlanPreviewResponse)
async def preview_plan(
    session_id: str,
    plan_id: str,
    body: PreviewBody,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.preview_plan(
            session_id, plan_id, expected_revision=body.expected_revision,
            actor=_actor(conv, owner_token, session_id))
    except DataFusionError as err:
        raise _fail(err) from err


# ─── 执行 / 运行查询 / 取消 ────────────────────────────────────────────────

@router.post("/sessions/{session_id}/plans/{plan_id}/execute",
             response_model=FusionRunView)
async def execute_plan(
    session_id: str,
    plan_id: str,
    body: ExecuteBody,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.execute_plan(
            session_id, plan_id, expected_revision=body.expected_revision,
            actor=_actor(conv, owner_token, session_id),
            background=body.background)
    except DataFusionError as err:
        raise _fail(err) from err


@router.get("/sessions/{session_id}/plans/{plan_id}/runs",
            response_model=FusionRunListResponse)
async def list_runs(
    session_id: str,
    plan_id: str,
    limit: int = Query(50, ge=1, le=200),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.list_runs(session_id, plan_id, limit=limit)
    except DataFusionError as err:
        raise _fail(err) from err


@router.get("/sessions/{session_id}/runs/{run_id}",
            response_model=FusionRunView)
async def inspect_run(
    session_id: str,
    run_id: str,
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.inspect_run(session_id, run_id)
    except DataFusionError as err:
        raise _fail(err) from err


@router.post("/sessions/{session_id}/runs/{run_id}/cancel",
             response_model=FusionRunCancelResponse)
async def cancel_run(
    session_id: str,
    run_id: str,
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.cancel_run(session_id, run_id)
    except DataFusionError as err:
        raise _fail(err) from err


@router.get("/sessions/{session_id}/runs/{run_id}/results",
            response_model=FusionRunResultsResponse)
async def run_results(
    session_id: str,
    run_id: str,
    cursor: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.run_result_page(
            session_id, run_id, cursor=cursor, limit=limit)
    except DataFusionError as err:
        raise _fail(err) from err


# ─── 审阅队列与决定（CAS） ─────────────────────────────────────────────────

@router.get("/sessions/{session_id}/runs/{run_id}/review-queue",
            response_model=FusionReviewQueueResponse)
async def review_queue(
    session_id: str,
    run_id: str,
    cursor: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    status: Optional[str] = Query(None),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return service.review_queue(
            session_id, run_id, cursor=cursor, limit=limit,
            status_filter=status)
    except DataFusionError as err:
        raise _fail(err) from err


@router.get("/sessions/{session_id}/plans/{plan_id}/decisions",
            response_model=FusionDecisionListResponse)
async def list_decisions(
    session_id: str,
    plan_id: str,
    limit: int = Query(200, ge=1, le=200),
    cursor: int = Query(0, ge=0),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.list_decisions(
            session_id, plan_id, limit=limit, cursor=cursor)
    except DataFusionError as err:
        raise _fail(err) from err


@router.post("/sessions/{session_id}/plans/{plan_id}/decisions",
             response_model=FusionDecisionView)
async def upsert_decision(
    session_id: str,
    plan_id: str,
    body: DecisionBody,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.upsert_decision(
            session_id, plan_id,
            pair_key=body.pair_key, action=body.action,
            decision_run_id=body.decision_run_id,
            idx_a=body.idx_a, idx_b=body.idx_b,
            field_values=body.field_values,
            geometry_side=body.geometry_side,
            reviewer=body.reviewer or str(
                getattr(conv, "user_id", "") or "") or None,
            note=body.note, expected_revision=body.expected_revision)
    except DataFusionError as err:
        raise _fail(err) from err


@router.delete("/sessions/{session_id}/plans/{plan_id}/decisions",
               response_model=FusionDecisionListResponse)
async def delete_decision(
    session_id: str,
    plan_id: str,
    body: DeleteDecisionBody,
    conv: Conversation = Depends(require_owned_session),
):
    try:
        out = await service.delete_decision(
            session_id, plan_id, body.pair_key,
            expected_revision=body.expected_revision)
        return {
            "plan_id": plan_id, "decisions": [out.get("decision") or {}],
            "has_more": False,
        }
    except DataFusionError as err:
        raise _fail(err) from err


# ─── 受权 commit（不可变融合数据集版本） ───────────────────────────────────

@router.post("/sessions/{session_id}/runs/{run_id}/commit",
             response_model=FusionRunView)
async def commit_run(
    session_id: str,
    run_id: str,
    body: CommitBody,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.commit_run(
            session_id, run_id,
            actor=_actor(conv, owner_token, session_id),
            project_id=body.project_id)
    except DataFusionError as err:
        raise _fail(err) from err


# ─── 渲染投影 / 增量刷新 / 恢复 ────────────────────────────────────────────

@router.post("/sessions/{session_id}/runs/{run_id}/render-ref",
             response_model=FusionRenderRefResponse)
async def render_ref(
    session_id: str,
    run_id: str,
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.render_ref_for_run(session_id, run_id)
    except DataFusionError as err:
        raise _fail(err) from err


class RefreshBody(BaseModel):
    expected_revision: int = Field(ge=1)
    dry_run: bool = False


@router.post("/sessions/{session_id}/plans/{plan_id}/refresh",
             response_model=FusionRefreshResponse)
async def refresh_plan(
    session_id: str,
    plan_id: str,
    body: RefreshBody,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await service.refresh_plan(
            session_id, plan_id, expected_revision=body.expected_revision,
            actor=_actor(conv, owner_token, session_id),
            dry_run=body.dry_run)
    except DataFusionError as err:
        raise _fail(err) from err


@router.post("/sessions/{session_id}/recover",
             response_model=FusionRecoverResponse)
async def recover(
    session_id: str,
    conv: Conversation = Depends(require_owned_session),
):
    """孤儿 run 收敛（进程重启恢复；typed 记录，不伪造完成）。"""
    n = service.recover_pending_runs(session_id)
    return FusionRecoverResponse(recovered=n)
