"""Agent Ops Cockpit read projections (cockpit.v1) — additive, read-only.

单一只读投影面：把 Mission Runtime、Swarm durable ledger、热路径 SkillPolicy
bundle、进程内 Evidence/Claim store 与 runtime trace 聚合成前端可观察的
bounded DTO。绝不在这里重建第二个状态机 —— 真相仍各归其主：

- Mission/Swarm/checkpoints → ``MissionStore``（DB，org-scoped）
- Skill/Evidence/trace      → 进程内 session-scoped（诚实披露：单进程、
  重启即清；绝不伪装持久化）
- Operator 动作             → 沿用 ``/mission-runtime/missions/{id}/…``，
  这里**不提供**任何写端点。

租户纪律与既有路由对齐：
- mission 读路径 ``tenancy.effective_org_in_thread``（fail-closed，同
  mission_runtime.py）；
- session 读路径 ``require_owned_session``（同 jobs.py 的归属证明链，
  审计 S33 漏洞类：session_id 是查询参数，不验证就等于跨租户读）。

所有载荷有界：沿用各契约自身的 ``to_bounded_dict`` / validator 上限，
列表端点 ``limit`` 服务端 clamp；前端轮询节奏由服务端 ``poll_after_ms``
建议（jobs.ts 协议先例），前端不得自造节奏。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth import get_current_user, require_owned_session
from app.services.gis_harness.hotpath_convergence.session_ctx import (
    get_turn_context,
)
from app.services.mission_runtime.service import (
    get_mission_runtime,
    mission_runtime_enabled,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cockpit", tags=["Agent Ops Cockpit"])

SCHEMA = "cockpit.v1"

#: Bounded list caps (Zero Big Data projection discipline).
MAX_LIST_LIMIT = 200
MAX_EVIDENCE_ITEMS = 500
MAX_TRACE_EVENTS = 64
_POLL_ACTIVE_MS = 2000
_POLL_IDLE_MS = 15000


def _effective_org(user: Dict[str, Any]) -> str:
    """Indirection kept so tests can pin org without touching tenancy."""
    from app.core import tenancy
    return tenancy.effective_org_in_thread(user)


def _uid(user: Dict[str, Any]) -> str:
    from app.core.auth import actor_ids

    uid, _org_id = actor_ids(user)
    return uid or ""


def _is_admin(user: Dict[str, Any]) -> bool:
    return isinstance(user, dict) and user.get("role") == "admin"


def _scope_user(user: Dict[str, Any]) -> Any:
    """SEC-04：非 admin 只投影 own missions；admin 保持 org 全量运维视图。"""
    return None if _is_admin(user) else _uid(user)


def _require_runtime():
    if not mission_runtime_enabled():
        raise HTTPException(status_code=503, detail="cockpit_disabled")
    return get_mission_runtime()


def _envelope(**payload: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"schema": SCHEMA, "generated_at": time.time()}
    out.update(payload)
    return out


def _mission_summary(rec: Any) -> Dict[str, Any]:
    frontier = rec.frontier
    budget = rec.resource_budget
    return {
        "mission_id": rec.mission_id,
        "org_id": rec.org_id,
        "state": rec.state.value,
        "revision": rec.revision,
        "goal_revision": rec.goal_revision,
        "root_goal": rec.root_goal[:200],
        "project_id": rec.project_id,
        "created_at": rec.created_at,
        "updated_at": rec.updated_at,
        "lease_owner": rec.lease_owner,
        "blocked_reason": (rec.recovery.blocked_reason or "")[:200],
        "frontier_counts": {
            "completed": len(frontier.completed),
            "running": len(frontier.running),
            "pending": len(frontier.pending),
            "failed": len(frontier.failed),
            "blocked": len(frontier.blocked),
        },
        "resource": {
            "quota": dict(budget.quota),
            "consumed": dict(budget.consumed),
            "reserved": dict(budget.reserved),
        },
    }


@router.get("/health")
def cockpit_health() -> dict:
    """匿名可达（与 /mission-runtime/health 同纪律）：只披露开关与 schema。"""
    return {"enabled": mission_runtime_enabled(), "schema": SCHEMA}


@router.get("/missions")
def list_missions(
    limit: int = Query(default=50, ge=1, le=MAX_LIST_LIMIT),
    user: Dict[str, Any] = Depends(get_current_user),
) -> dict:
    """Org-scoped 未终结 Mission 列表（操作员“现在在跑什么”视图）。

    v1 只投影 ACTIVE 状态（``list_unfinished``）—— 终态历史归档是后续
    additive 能力，不在本 PR 扩 ``MissionStore`` 查询面（该文件是 #1335
    并行热区）。
    """
    svc = _require_runtime()
    org = _effective_org(user)
    # has_active 必须基于全量归属范围而非截断页（jobs.py 同款纪律），
    # 否则活跃 mission 比当前页更旧时前端会误停轮询。
    records = svc.store.list_unfinished(
        org_id=org, limit=500, user_id=_scope_user(user)
    )
    missions = [_mission_summary(r) for r in records[: max(1, min(limit, MAX_LIST_LIMIT))]]
    has_active = len(records) > 0
    return _envelope(
        missions=missions,
        has_active=has_active,
        poll_after_ms=_POLL_ACTIVE_MS if has_active else _POLL_IDLE_MS,
    )


def _get_mission_or_404(svc, mission_id: str, org: str, user: Any):
    rec = svc.store.get_mission(
        mission_id, org_id=org, user_id=_scope_user(user)
    )
    if rec is None:
        raise HTTPException(status_code=404, detail="mission_not_found")
    return rec


@router.get("/missions/{mission_id}")
def mission_detail(
    mission_id: str,
    user: Dict[str, Any] = Depends(get_current_user),
) -> dict:
    """Mission 全记录 + 有界诊断（goal / frontier / blocked reason / 预算）。"""
    svc = _require_runtime()
    rec = _get_mission_or_404(svc, mission_id, _effective_org(user), user)
    return _envelope(
        mission=rec.model_dump(mode="json"),
        diagnostics=svc.diagnostics(mission_id, org_id=rec.org_id).model_dump(),
    )


@router.get("/missions/{mission_id}/timeline")
def mission_timeline(
    mission_id: str,
    limit: int = Query(default=32, ge=1, le=32),
    user: Dict[str, Any] = Depends(get_current_user),
) -> dict:
    """Checkpoints 时间线（goal_revision / state / refs 锚点，新→旧）。

    状态机转换本身没有逐条 journal —— checkpoint 是唯一被持久化的时间线
    事实；本投影不推导、不补插中间态（诚实投影纪律）。
    """
    svc = _require_runtime()
    rec = _get_mission_or_404(svc, mission_id, _effective_org(user), user)
    checkpoints = svc.store.list_checkpoints(mission_id, limit=limit)
    return _envelope(
        mission_id=mission_id,
        state=rec.state.value,
        revision=rec.revision,
        goal_revision=rec.goal_revision,
        root_goal=rec.root_goal[:200],
        updated_at=rec.updated_at,
        blocked_reason=rec.recovery.blocked_reason,
        refs=rec.refs.model_dump(),
        checkpoints=checkpoints,
    )


@router.get("/missions/{mission_id}/swarm")
def mission_swarm(
    mission_id: str,
    user: Dict[str, Any] = Depends(get_current_user),
) -> dict:
    """Swarm durable ledger 投影（任务态 / operation class / produced refs）。"""
    svc = _require_runtime()
    _get_mission_or_404(svc, mission_id, _effective_org(user), user)
    runs = svc.store.list_swarm_runs_for_mission(mission_id)
    return _envelope(
        mission_id=mission_id,
        runs=[r.model_dump(mode="json") for r in runs],
    )


@router.get("/sessions/{session_id}/skill")
def session_skill(
    session_id: str,
    _conv: Any = Depends(require_owned_session),
    _user: Dict[str, Any] = Depends(get_current_user),
) -> dict:
    """热路径 SkillPolicy bundle 投影（decision / projection / shadow / pi_context）。

    进程内 session-scoped：无 bundle 返回 ``present=false``，绝不伪造。
    Mission runtime 关停时本端点仍可用 —— hotpath 技能绑定独立于开关。
    """
    ctx = get_turn_context(session_id)
    # 生产写序（tools.py）：skill_guidance = bundle.to_bounded_dict()。
    # 防御性回退：guidance 为空但 bundle 在场时直接向 bundle 要有界视图，
    # 兼容未来只存 bundle 的调用方；两者皆缺 ⇒ present=false，绝不伪造。
    raw = ctx.skill_guidance if isinstance(ctx.skill_guidance, dict) else None
    if not raw:
        to_bounded = getattr(getattr(ctx, "skill_bundle", None), "to_bounded_dict", None)
        raw = to_bounded() if callable(to_bounded) else None
    present = bool(raw)
    return _envelope(
        session_id=session_id,
        present=present,
        guidance=raw if present else None,
        mission_id=ctx.mission_id,
    )


@router.get("/sessions/{session_id}/evidence")
def session_evidence(
    session_id: str,
    limit: int = Query(default=100, ge=1, le=MAX_EVIDENCE_ITEMS),
    _conv: Any = Depends(require_owned_session),
    _user: Dict[str, Any] = Depends(get_current_user),
) -> dict:
    """Evidence/Claim 投影：claims + edges + stats（全部走有界 dict）。

    只读：绝不 ``get_or_create`` —— 只读路径不得改变进程内状态。
    """
    ctx = get_turn_context(session_id)
    store = getattr(ctx, "claim_store", None)
    if store is None:
        return _envelope(
            session_id=session_id,
            present=False,
            claims=[],
            edges=[],
            stats={},
            truncated=False,
        )
    claims = store.all_claims()[: max(1, min(limit, MAX_EVIDENCE_ITEMS))]
    edges = store.all_edges()[: max(1, min(limit, MAX_EVIDENCE_ITEMS))]
    stats = store.stats()
    total = int(stats.get("claims", 0)) + int(stats.get("edges", 0))
    return _envelope(
        session_id=session_id,
        present=True,
        claims=[c.to_bounded_dict() for c in claims],
        edges=[e.to_bounded_dict() for e in edges],
        stats=stats,
        truncated=total > len(claims) + len(edges),
    )


@router.get("/sessions/{session_id}/trace")
def session_trace(
    session_id: str,
    limit: int = Query(default=32, ge=1, le=MAX_TRACE_EVENTS),
    _conv: Any = Depends(require_owned_session),
    _user: Dict[str, Any] = Depends(get_current_user),
) -> dict:
    """Runtime trace / replay 投影（封闭 stage 词表 + 有界 detail）。

    结构化决策证据 only：TraceEvent.to_dict 在源头截断 detail（≤8 键、
    ≤96 字符、无 CoT / secret 面）。``GIS_TRACE_PERSIST`` 等开关与本
    投影无关 —— 这里读的是进程内 ring，不触盘。
    """
    from app.services.gis_harness.trace import get_runtime_trace

    tr = get_runtime_trace()
    return _envelope(
        session_id=session_id,
        events=tr.events(session_id, limit=max(1, min(limit, MAX_TRACE_EVENTS))),
        summary=tr.summary(session_id),
        counters=tr.counters(),
    )


__all__ = ["router"]
