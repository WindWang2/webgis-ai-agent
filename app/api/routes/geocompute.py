"""GeoCompute 执行平面 REST（ADR-0096，additive）。

计划校验/指纹、执行、run 状态查询。执行是同步核心，统一 to_thread
卸载（与 project workflow 路由同一模式），绝不阻塞事件循环。
计划执行证据是**有界摘要**；节点载荷只经 ref/产物通道出平面。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.auth import get_current_user_optional
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


@router.post("/plans/validate", tags=["GeoCompute / 执行平面"])
async def validate_execution_plan(
    plan_in: ExecutionPlanIn,
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    """校验执行图并返回确定性指纹（不执行）。"""
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
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    """执行执行图（同步卸载到工作线程；预算/取消/deadline 全程生效）。"""
    from app.services.geocompute.executor import engine
    from app.services.geocompute import graph

    plan = _plan_from_request(body.plan)
    try:
        graph.validate_plan(plan)
    except GeoComputeError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict())

    def _run():
        return engine.execute_plan(plan, session_id=body.session_id)

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
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    from app.services.geocompute.executor import engine

    run = engine.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail={"code": "RUN_NOT_FOUND"})
    return run.model_dump()


@router.get("/runs/{run_id}/summary", tags=["GeoCompute / 执行平面"])
async def get_execution_run_summary(
    run_id: str,
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    from app.services.geocompute.executor import engine

    run = engine.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail={"code": "RUN_NOT_FOUND"})
    return {"lines": run.summary_lines()}
