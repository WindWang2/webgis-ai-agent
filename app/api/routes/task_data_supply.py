"""Task Data Supply Routes — /api/v1/task-data-supply（GOAL-20261126-02 WP4）。

与 agent 工具面（``app/tools/task_data_supply_tools``）共用同一 service 层
（``app/services/task_data_supply``）。路由只做验证与装配；候选发现走
会话联邦目录（owner 作用域），计划存储 owner 隔离，执行走既有
MaterializationService 生产数据面。响应全部有界（覆盖矩阵 ≤7 行/角色、
候选 ≤12 条），绝不返回海量 GeoJSON。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.auth import get_current_user
from app.services.task_data_supply.coverage import evaluate_task_coverage
from app.services.task_data_supply.discovery import (
    build_requirement_from_args,
    discover_task_candidates,
    get_task_supply_store,
)
from app.services.task_data_supply.supply_plan import (
    SupplyPlanner,
    build_supply_plan,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/task-data-supply")

_DATA_ROLES = (
    "subject", "measure", "boundary", "denominator", "network",
    "elevation", "hazard", "receptor", "population", "facility",
    "criteria", "constraint", "reference", "baseline", "target_time",
    "comparison_time",
)


class ScopeParams(BaseModel):
    """任务范围 + 角色需求（工具面同参数语义）。"""
    session_id: str = Field(..., min_length=8, max_length=128)
    place: str = Field(default="", max_length=128)
    bbox: Optional[List[float]] = None
    time_range: Optional[List[str]] = None
    roles: List[str] = Field(default_factory=list)
    keyword: str = Field(default="", max_length=64)
    budget_max_bytes: Optional[int] = None
    choices: Dict[str, str] = Field(default_factory=dict)

    def _roles_pairs(self) -> Tuple[Tuple[str, Tuple[str, ...]], ...]:
        pairs: Tuple[Tuple[str, Tuple[str, ...]], ...] = ()
        for role in self.roles[:16]:
            if role not in _DATA_ROLES:
                raise HTTPException(
                    status_code=400,
                    detail=(f"REQUIREMENT_ROLE_UNKNOWN: {role!r} ∉ "
                            f"DATA_ROLES（合法值：{','.join(_DATA_ROLES)}）"))
            pairs = (*pairs, (role, ("numeric_stats",)))
        return pairs


def _planner() -> SupplyPlanner:
    return SupplyPlanner()


def _require_session_owner(
    session_id: str,
    user: Optional[Dict[str, Any]],
    owner_token: Optional[str],
) -> None:
    """会话归属校验（与 data_fabric._require_existing_session_owner 同语义）：
    必须存在且调用方可写（读路径同样校验——计划 store 会话私有）。"""
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")

    def _real_user_id(u: Optional[Dict[str, Any]]) -> Optional[str]:
        # 与 data_fabric._real_user_id 同语义：anonymous 哨兵归一为 None
        if not u:
            return None
        v = str(u.get("user_id") or "").strip()
        return None if (not v or v == "anonymous") else v

    from app.core.auth import authorize_session_write
    from app.core.database import SessionLocal
    from app.models.db_model import Conversation

    with SessionLocal() as db:  # type: Session
        conv = db.query(Conversation).filter(
            Conversation.id == session_id).first()
        if conv is None or not authorize_session_write(
                conv, _real_user_id(user), owner_token):
            raise HTTPException(status_code=404, detail="Session not found")


async def _requirement_and_candidates(params: ScopeParams):
    try:
        requirement = build_requirement_from_args(
            place=params.place, bbox=params.bbox,
            time_range=params.time_range,
            roles=params._roles_pairs(), provenance="rest")
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)[:300])
    try:
        candidates, meta = await discover_task_candidates(
            session_id=params.session_id,
            task_scope=requirement.task_scope,
            roles=tuple(it.role for it in requirement.items),
            keyword=params.keyword, limit=24)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail=f"TASK_SUPPLY_DISCOVERY_FAILED: {e}"[:300])
    return requirement, candidates, meta


@router.post("/coverage")
async def compare_task_coverage(
    params: ScopeParams,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """任务需求 × 候选 → 覆盖矩阵（四层分离 + 缺口原因码，有界）。"""
    _require_session_owner(params.session_id, user, owner_token)
    requirement, candidates, meta = await _requirement_and_candidates(params)
    coverage = evaluate_task_coverage(requirement, candidates)
    return {
        "success": True,
        "requirement_id": requirement.requirement_id,
        "missing_obligations": requirement.missing_obligations(),
        "coverage": coverage.to_bounded_dict(),
        "discovery_meta": meta,
    }


@router.post("/plans")
async def create_supply_plan(
    params: ScopeParams,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """覆盖结论 → 受预算供给计划（存会话 store；requires_confirmation
    状态与替代方案显式可见）。"""
    _require_session_owner(params.session_id, user, owner_token)
    requirement, candidates, meta = await _requirement_and_candidates(params)
    coverage = evaluate_task_coverage(requirement, candidates)
    try:
        plan = build_supply_plan(
            requirement, coverage, candidates,
            budget_max_bytes=params.budget_max_bytes,
            choices=params.choices)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)[:300])
    get_task_supply_store().save(params.session_id, plan)
    return {"success": True, "plan": plan.to_bounded_dict(),
            "discovery_meta": meta}


class ExecuteParams(BaseModel):
    """已批准执行（显式 approved；可选只执行部分角色）。"""
    approved: bool = False
    roles: List[str] = Field(default_factory=list)


@router.post("/plans/{plan_id}/execute")
async def execute_supply_plan(
    plan_id: str,
    params: ExecuteParams,
    session_id: str = Query(..., min_length=8, max_length=128),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """执行已批准步骤：acquire 走既有 MaterializationService（ref 铸造）；
    未批准/需确认/跨会话 → 类型化拒绝。"""
    _require_session_owner(session_id, user, owner_token)
    plan = get_task_supply_store().get(session_id, plan_id)
    if plan is None:
        raise HTTPException(
            status_code=404,
            detail=(f"TASK_SUPPLY_PLAN_NOT_FOUND: 计划 {plan_id!r} 不存在"
                    "或不属于当前会话（跨会话不可见）"))
    if not params.approved:
        raise HTTPException(status_code=400,
                            detail="TASK_SUPPLY_NOT_APPROVED: 需显式批准")
    if plan.state == "requires_confirmation":
        pending = [s.role for s in plan.steps
                   if s.alternatives and not s.user_choice]
        raise HTTPException(
            status_code=409,
            detail=("TASK_SUPPLY_PLAN_NEEDS_CONFIRMATION: 存在未决策事项"
                    f" {pending[:8]}"))
    from app.services.task_data_supply.execution import (
        execute_plan_steps)

    wanted = set(params.roles)
    results, skipped = await execute_plan_steps(session_id, plan,
                                                roles=wanted)
    return {"success": True, "plan_id": plan.plan_id,
            "plan_state": plan.state, "results": results[:16],
            "skipped": skipped[:16],
            "plan": plan.to_bounded_dict()}


@router.get("/plans/{plan_id}")
async def get_supply_plan(
    plan_id: str,
    session_id: str = Query(..., min_length=8, max_length=128),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """计划状态查询（owner 严格隔离：跨会话 404，不泄漏存在性）。"""
    _require_session_owner(session_id, user, owner_token)
    plan = get_task_supply_store().get(session_id, plan_id)
    if plan is None:
        raise HTTPException(
            status_code=404,
            detail=(f"TASK_SUPPLY_PLAN_NOT_FOUND: 计划 {plan_id!r} 不存在"
                    "或不属于当前会话"))
    return {"success": True, "plan": plan.to_bounded_dict()}


__all__ = ["router"]
