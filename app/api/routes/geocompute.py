"""GeoCompute 执行平面 REST（ADR-0096，additive）。

计划校验/指纹、执行、run 状态查询。执行是同步核心，统一 to_thread
卸载（与 project workflow 路由同一模式），绝不阻塞事件循环。
计划执行证据是**有界摘要**；节点载荷只经 ref/产物通道出平面。

安全语义（SEC 评审）：
- ``/plans/execute``、``/runs/*``、``/plans/drift-check`` 强制认证
  （无/坏 Bearer → 401）；``/plans/validate`` 保持可选认证 —— 纯 CPU
  校验，只暴露指纹/波次等派生信息，不触达数据与目录。
- 执行请求的 ``session_id`` 若已存在 Conversation 行，必须属于当前
  调用者（与 data_fabric 物化路由同一 ``authorize_session_write`` 判定；
  陌生会话 → 404，不泄漏存在性）。
- caller 身份贯穿执行器：目录项准入（与 data_fabric 同一租户谓词）、
  节点复用键 owner 域、run 读隔离（他人 run → 404）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.auth import (
    get_current_user,
    get_current_user_optional,
    get_owner_token,
)
from app.services.geocompute import BudgetExceededError, GeoComputeError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/geocompute", tags=["GeoCompute / 执行平面"])


class ExecutionNodeIn(BaseModel):
    node_id: str
    category: str
    operation: str = ""
    inputs: list[str] = Field(default_factory=list)
    dataset_fingerprints: Dict[str, str] = Field(default_factory=dict)
    parameters: Dict[str, Any] = Field(default_factory=dict)
    crs: Optional[Dict[str, Any]] = None
    estimate: Optional[Dict[str, Any]] = None
    policy: str = "in_process"
    reuse: str = "allow"
    retry: Dict[str, Any] = Field(default_factory=dict)
    deadline_s: Optional[float] = None
    cancellable: bool = True
    locality_hint: Optional[str] = None
    description: Optional[str] = None


class ExecutionPlanIn(BaseModel):
    plan_id: str
    nodes: list[ExecutionNodeIn] = Field(default_factory=list)
    budget: Dict[str, Any] = Field(default_factory=dict)
    description: Optional[str] = None


class ExecutePlanRequest(BaseModel):
    plan: ExecutionPlanIn
    session_id: Optional[str] = None


def _plan_from_request(data: ExecutionPlanIn):
    from app.services.geocompute.plan import (
        CrsExpectation,
        ExecutionNode,
        ExecutionPlan,
        NodeCategory,
        ExecutionPolicyKind,
        NodeReusePolicy,
        ResourceBudget,
        ResourceEstimate,
        RetryPolicy,
    )

    nodes = []
    for n in data.nodes:
        nodes.append(
            ExecutionNode(
                node_id=n.node_id,
                category=NodeCategory(n.category),
                operation=n.operation,
                inputs=n.inputs,
                dataset_fingerprints=n.dataset_fingerprints,
                parameters=n.parameters,
                crs=CrsExpectation(**n.crs) if n.crs else None,
                estimate=ResourceEstimate(**n.estimate) if n.estimate else None,
                policy=ExecutionPolicyKind(n.policy),
                reuse=NodeReusePolicy(n.reuse),
                retry=RetryPolicy(**n.retry) if n.retry else RetryPolicy(),
                deadline_s=n.deadline_s,
                cancellable=n.cancellable,
                locality_hint=n.locality_hint,
                description=n.description,
            )
        )
    return ExecutionPlan(
        plan_id=data.plan_id,
        nodes=nodes,
        budget=ResourceBudget(**data.budget) if data.budget else ResourceBudget(),
        description=data.description,
    )


def _plan_fingerprint(data: ExecutionPlanIn) -> str:
    return _plan_from_request(data).graph_fingerprint()


def _authorize_session_write_sync(
    session_id: str,
    user: Dict[str, Any],
    owner_token: Optional[str],
) -> None:
    """执行前校验 session_id 的写归属（data_fabric._require_existing_session_owner
    的镜像；在 to_thread 工作线程内跑同步 ORM）。

    Conversation 行不存在 → 允许（首写创建，与首条聊天消息同一语义）；
    存在则必须 user_id 匹配（匿名行须 X-Session-Token 匹配，legacy
    NULL/NULL fail closed）→ 否则 404（不区分「不存在」与「他人会话」）。
    """
    from app.core.auth import actor_ids, authorize_session_write
    from app.core.database import SessionLocal
    from app.models.db_model import Conversation

    uid, _ = actor_ids(user)
    with SessionLocal() as db:
        conv = db.query(Conversation).filter(Conversation.id == session_id).first()
        if not authorize_session_write(conv, uid, owner_token):
            raise HTTPException(status_code=404, detail="Session not found")


@router.post("/plans/validate", tags=["GeoCompute / 执行平面"])
async def validate_execution_plan(
    plan_in: ExecutionPlanIn,
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    """校验执行图并返回确定性指纹（不执行）。

    安全说明（SEC 评审）：保持**可选认证** —— 纯 CPU 校验，无目录/数据
    访问，响应只含指纹、波次与已接线类别等派生信息；未认证调用不构成
    信息泄漏面。
    """
    from app.services.geocompute import graph

    plan = _plan_from_request(plan_in)
    try:
        graph.validate_plan(plan)
    except GeoComputeError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict())
    return {
        "plan_id": plan.plan_id,
        "graph_fingerprint": plan.graph_fingerprint(),
        "node_fingerprints": {n.node_id: n.semantic_fingerprint() for n in plan.nodes},
        "waves": graph.topo_wave_order(plan),
        "wired_categories": __import__(
            "app.services.geocompute.ops", fromlist=["wired_categories"]
        ).wired_categories(),
    }


@router.post("/plans/execute", tags=["GeoCompute / 执行平面"])
async def execute_execution_plan(
    body: ExecutePlanRequest,
    user: Dict[str, Any] = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
):
    """执行执行图（同步卸载到工作线程；预算/取消/deadline 全程生效）。

    强制认证（无/坏 Bearer → 401）。``session_id`` 归属校验与执行同一
    工作线程顺序执行（校验先于任何节点运行）。
    """
    from app.services.geocompute.executor import engine
    from app.services.geocompute import graph

    plan = _plan_from_request(body.plan)
    try:
        graph.validate_plan(plan)
    except GeoComputeError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict())

    def _run():
        if body.session_id:
            _authorize_session_write_sync(body.session_id, user, owner_token)
        return engine.execute_plan(
            plan, session_id=body.session_id, caller=dict(user)
        )

    try:
        run = await asyncio.to_thread(_run)
    except BudgetExceededError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict())
    except GeoComputeError as exc:
        raise HTTPException(status_code=500, detail=exc.to_dict())
    return run.model_dump()


@router.get("/runs/{run_id}", tags=["GeoCompute / 执行平面"])
async def get_execution_run(
    run_id: str,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """查询 run（强制认证 + 读隔离：他人 run 一律 404，避免存在性预言机）。"""
    from app.services.geocompute.executor import engine, owner_scope_for

    run = engine.get_run(run_id, owner_scope=owner_scope_for(user))
    if run is None:
        raise HTTPException(status_code=404, detail={"code": "RUN_NOT_FOUND"})
    return run.model_dump()


@router.get("/runs/{run_id}/summary", tags=["GeoCompute / 执行平面"])
async def get_execution_run_summary(
    run_id: str,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """查询 run 摘要（强制认证 + 与 GET /runs 相同的读隔离）。"""
    from app.services.geocompute.executor import engine, owner_scope_for

    run = engine.get_run(run_id, owner_scope=owner_scope_for(user))
    if run is None:
        raise HTTPException(status_code=404, detail={"code": "RUN_NOT_FOUND"})
    return {"lines": run.summary_lines()}


@router.post("/plans/drift-check", tags=["GeoCompute / 执行平面"])
async def drift_check(
    body: Dict[str, Any],
    user: Dict[str, Any] = Depends(get_current_user),
):
    """对持久化计划记录做语义漂移判定（ADR-0096 D7）。

    body: {"stored": {...持久记录...}, "plan": {ExecutionPlanIn（可选）}}
    返回 DriftVerdict（current/stale_runtime/degraded_plan/unknown）。
    强制认证：stored 记录可能携带计划元数据，不向匿名暴露。
    """
    from app.services.geocompute.drift import check_plan_drift

    stored = body.get("stored")
    plan_in = body.get("plan")
    plan = _plan_from_request(ExecutionPlanIn(**plan_in)) if plan_in else None
    verdict = check_plan_drift(stored, plan=plan)
    return verdict.to_dict()
