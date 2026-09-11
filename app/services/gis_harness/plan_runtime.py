"""PlanRuntime —— 计划版本化 / 重规划驱动 / 失败种子最小重算（V7 ADR-0130 D2）。

V6 基线缺口（harness-v6 PR follow-ups + 审计 W2）：

- 计划事实（行/goal/contract）变化只能靠 ``rows_fingerprint`` 感知 ——
  没有**版本号**、没有历史、没有回滚点；supersede 直接覆盖。
- ``decide_continuation`` 有 ``replan`` 裁决词，``failure_taxonomy`` 有
  ``replan`` 动作，但 **LOOP_BUDGETS 不含 replan、无生产驱动点**
  （durable_context.py 旧注释自认「入而无驱动 = 预算耗尽永不可达」）。
- runtime_bridge 的 recompute 种子只来自「曾 satisfied 后漂移」的节点；
  **行失败**（row failed）不产生最小重算清单 —— Pi 只看到 blocked 投影，
  拿不到「重算谁、复用谁」的直接指令。

V7 契约（预算与驱动点**同一 commit** 落地 —— V6 R1 M4 教训）：

- **计划版本**：``compute_plan_fingerprint``（goal + rows + contract 核心）
  变化 → version+1；历史环形（≤8）；回滚点（≤4）保留前一指纹的完整
  行集哈希证据 —— 回滚语义 = 披露 + 由锚点/会话事实支撑，不复制载荷。
- **重规划回路**：``request_replan``（finalizer 出口生产驱动点）——
  修复不可达（不可修复 error / 修复预算尽）且 replan 预算有余 →
  ``plan_runtime.replan_pending`` 置位 + durable 记账；计划事实一变
  （版本推进）即视为重规划已消费 → 清 pending（``replan_committed``）。
  预算耗尽 → 诚实 abort 披露（复用 REMEDIATION_POLICY 语义，不无限对抗）。
- **失败种子最小重算**：``seed_recompute_from_failures``（纯函数）——
  failed 行 → 重算该 capability + 下游闭包；satisfied 且非下游 → 复用。
  消费形态与 V6 一致：投影给 Pi（[GIS PlanRuntime] / recompute 行），
  **Harness 不执行工具**（行状态单写者纪律不变）。

持久化：``gis_chapter["plan_runtime"]`` 单键 additive；写路径复用
session lock + goal/rows 漂移守卫（runtime_state_machine 同款）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.services.gis_harness.workflow_instance import (
    canonical_fingerprint,
    rows_fingerprint,
)

logger = logging.getLogger(__name__)

#: gis_chapter 单键（additive；旧读者忽略）。
PLAN_RUNTIME_KEY = "plan_runtime"

#: 有界预算。
MAX_HISTORY = 8
MAX_ROLLBACK_POINTS = 4
MAX_RECOMPUTE_ITEMS = 32
MAX_STEP_ATTEMPTS = 24

#: replan 回路预算（durable_context.LOOP_BUDGETS 的 replan 条目镜像；
#: 本模块是它的**生产驱动点** —— 预算与驱动同一 commit，不可分开部署）。
REPLAN_LOOP = "replan"


# ── 指纹 ─────────────────────────────────────────────────────────────────


def compute_plan_fingerprint(chapter: Dict[str, Any]) -> str:
    """计划身份指纹：goal + 行（V2 签名）+ contract 语义核心。

    任一变化 → 新版本（supersede / 行编辑 / 参数编辑 / 契约重算）。
    """
    if not isinstance(chapter, dict):
        return ""
    contract = chapter.get("workflow_contract")
    contract_core = ""
    if isinstance(contract, dict):
        contract_core = canonical_fingerprint({
            "roles": contract.get("roles") or [],
            "obligations": contract.get("obligations") or [],
            "method_blockers": contract.get("method_blockers") or [],
            "data_blockers": contract.get("data_blockers") or [],
        })
    return canonical_fingerprint({
        "goal": str(chapter.get("query") or "")[:200],
        "rows": rows_fingerprint(chapter)[:2048],
        "contract": contract_core,
    })


# ── 失败种子最小重算（纯函数）────────────────────────────────────────────


def _downstream_closure(graph: Any, seeds: set[str]) -> set[str]:
    """PlanGraph 上的下游闭包（failed 节点的污染面；node_id == capability）。"""
    dependents: Dict[str, List[str]] = {}
    for node in graph.nodes:
        for dep in node.depends_on:
            dependents.setdefault(dep, []).append(node.node_id)
    seen: set[str] = set()
    frontier = list(seeds)
    while frontier:
        cur = frontier.pop()
        for nxt in dependents.get(cur, []):
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    return seen


def seed_recompute_from_failures(chapter: Dict[str, Any]) -> Dict[str, Any]:
    """行失败 → 最小重算清单（纯函数；RecomputePlan 同形投影）。

    - recompute：failed 行的 capability + 其下游闭包（拓扑近似序）；
    - reuse：satisfied 且不在污染面的 capability（复用证据明确）；
    - explanations：有界（≤6）。
    无失败行 → 空 plan（``recompute=[]``）。
    """
    empty = {"recompute": [], "reuse": [], "explanations": [],
             "changed_dimensions": ["output"]}
    if not isinstance(chapter, dict):
        return empty
    failed = {
        str(r.get("capability") or "")
        for r in list(chapter.get("analysis_steps") or [])
        + list(chapter.get("data_requirements") or [])
        if isinstance(r, dict)
        and str(r.get("status") or "") == "failed" and r.get("capability")
    }
    if not failed:
        return empty
    try:
        from app.services.gis_harness.plan_graph import build_plan_graph

        graph = build_plan_graph(chapter)
    except Exception:  # noqa: BLE001 — 图缺席退化为只列失败行
        graph = None
    if graph is not None:
        seeds = {c for c in failed}
        closure = _downstream_closure(graph, seeds) | seeds
        recompute = sorted(closure)
        satisfied = [
            n.capability for n in graph.nodes
            if str(getattr(n.status, "value", n.status)) == "complete"
            and n.capability and n.node_id not in closure
            and n.capability not in failed
        ]
    else:
        recompute = sorted(failed)
        satisfied = []
    explanations = [
        f"failed:{cap} → 最小重算（下游闭包 {len(recompute)} 节点）"
        for cap in sorted(failed)
    ]
    return {
        "recompute": [c[:64] for c in recompute[:MAX_RECOMPUTE_ITEMS]],
        "reuse": [c[:64] for c in satisfied[:MAX_RECOMPUTE_ITEMS]],
        "explanations": [e[:200] for e in explanations[:6]],
        "changed_dimensions": ["output"],
    }


# ── 状态块 ───────────────────────────────────────────────────────────────


class PlanVersionRecord(BaseModel):
    """一次版本推进（环形；无时间戳 —— version 即序）。"""

    version: int
    fingerprint: str = ""
    reason: str = ""

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "version": int(self.version),
            "fingerprint": self.fingerprint[:32],
            "reason": self.reason[:96],
        }


class PlanRuntimeBlock(BaseModel):
    """计划运行时块（gis_chapter[PLAN_RUNTIME_KEY] 的 schema）。"""

    schema_version: str = "plan_runtime.v1"
    version: int = 1
    fingerprint: str = ""                # 当前计划身份指纹
    history: List[PlanVersionRecord] = Field(default_factory=list)
    rollback_points: List[PlanVersionRecord] = Field(default_factory=list)
    replan_pending: bool = False
    replan_reason: str = ""
    replan_from_verdict: str = ""
    recompute: Dict[str, Any] = Field(default_factory=dict)  # seed_recompute 投影

    def to_bounded_dict(self) -> Dict[str, Any]:
        recompute = self.recompute if isinstance(self.recompute, dict) else {}
        return {
            "schema": self.schema_version,
            "version": int(self.version),
            "fingerprint": self.fingerprint[:32],
            "history": [h.to_bounded_dict() for h in self.history[:MAX_HISTORY]],
            "rollback_points": [
                r.to_bounded_dict() for r in self.rollback_points[:MAX_ROLLBACK_POINTS]],
            "replan_pending": self.replan_pending,
            "replan_reason": self.replan_reason[:160],
            "replan_from_verdict": self.replan_from_verdict[:32],
            "recompute": {
                "recompute": [str(x)[:64]
                              for x in (recompute.get("recompute") or [])[:MAX_RECOMPUTE_ITEMS]],
                "reuse": [str(x)[:64]
                          for x in (recompute.get("reuse") or [])[:MAX_RECOMPUTE_ITEMS]],
                "explanations": [str(x)[:200]
                                 for x in (recompute.get("explanations") or [])[:6]],
            },
        }


def _parse_stored(stored: Optional[Dict[str, Any]]) -> PlanRuntimeBlock:
    if not isinstance(stored, dict) or stored.get("schema") != "plan_runtime.v1":
        return PlanRuntimeBlock()
    def _records(key: str, limit: int) -> List[PlanVersionRecord]:
        return [
            PlanVersionRecord(
                version=int(r.get("version") or 0),
                fingerprint=str(r.get("fingerprint") or "")[:32],
                reason=str(r.get("reason") or "")[:96],
            )
            for r in (stored.get(key) or [])[:limit] if isinstance(r, dict)
        ]
    return PlanRuntimeBlock(
        version=int(stored.get("version") or 1),
        fingerprint=str(stored.get("fingerprint") or "")[:32],
        history=_records("history", MAX_HISTORY),
        rollback_points=_records("rollback_points", MAX_ROLLBACK_POINTS),
        replan_pending=bool(stored.get("replan_pending")),
        replan_reason=str(stored.get("replan_reason") or "")[:160],
        replan_from_verdict=str(stored.get("replan_from_verdict") or "")[:32],
        recompute=stored.get("recompute") if isinstance(
            stored.get("recompute"), dict) else {},
    )


def derive_plan_runtime(
    chapter: Dict[str, Any],
    *,
    reason: str = "",
    stored: Optional[Dict[str, Any]] = None,
) -> Optional[PlanRuntimeBlock]:
    """权威事实 → 计划运行时块（指纹不变 → None = 无事可写）。

    指纹变化 → version+1 + 历史/回滚点推进 + replan_pending 清除
    （计划事实已变 = 重规划已消费）。"""
    block = _parse_stored(stored)
    fp = compute_plan_fingerprint(chapter)
    if not fp:
        return None
    if block.fingerprint and fp == block.fingerprint:
        # 指纹不变：仅当 recompute 需要刷新（失败面变化）才重写
        recompute = seed_recompute_from_failures(chapter)
        if recompute.get("recompute") == (block.recompute or {}).get("recompute"):
            return None
        block.recompute = recompute
        return block
    # 版本推进（首次创建：无被替换版本 → 不写历史/回滚点）
    had_previous = bool(block.fingerprint)
    new_block = PlanRuntimeBlock(
        version=block.version + 1 if had_previous else 1,
        fingerprint=fp,
        history=(
            list(block.history) + [PlanVersionRecord(
                version=block.version,
                fingerprint=block.fingerprint,
                reason=str(reason or "plan_changed")[:96],
            )]
        )[-MAX_HISTORY:] if had_previous else [],
        rollback_points=(
            list(block.rollback_points) + [PlanVersionRecord(
                version=block.version,
                fingerprint=block.fingerprint,
                reason="rollback_point",
            )]
        )[-MAX_ROLLBACK_POINTS:] if had_previous else [],
        # 计划事实已变 → 重规划已消费
        replan_pending=False,
        replan_reason=block.replan_reason if not block.replan_pending else "",
        replan_from_verdict=block.replan_from_verdict if not block.replan_pending else "",
        recompute=seed_recompute_from_failures(chapter),
    )
    return new_block


# ── 有界投影（LLM 面）────────────────────────────────────────────────────


def format_plan_runtime_line(chapter: Optional[Dict[str, Any]]) -> str:
    """[GIS PlanRuntime] 单行投影（SessionPlan projection 的 additive 行）。"""
    if not isinstance(chapter, dict):
        return ""
    stored = chapter.get(PLAN_RUNTIME_KEY)
    if not isinstance(stored, dict) or stored.get("schema") != "plan_runtime.v1":
        return ""
    parts = [f"[GIS PlanRuntime] v={stored.get('version')}"]
    if stored.get("replan_pending"):
        parts.append(
            "replan=pending（修复不可达 → 重规划剩余步骤；预算已记账）")
    rc = (stored.get("recompute") or {})
    recompute = [str(x) for x in (rc.get("recompute") or [])]
    if recompute:
        parts.append("min-rerun=" + ",".join(recompute[:4]))
    reuse = [str(x) for x in (rc.get("reuse") or [])]
    if reuse:
        parts.append(f"reuse={len(reuse)}")
    rb = stored.get("rollback_points") or []
    if rb:
        parts.append(f"rollback_pts={len(rb)}")
    return " ".join(parts)[:480]


# ── 服务入口 ─────────────────────────────────────────────────────────────


async def maybe_advance_plan_version(
    session_id: str,
    *,
    reason: str = "plan_changed",
    force: bool = False,
) -> Optional[Dict[str, Any]]:
    """触发点入口：指纹门 + 纯派生 + 锁内单键持久化（幂等、有界）。

    生产驱动点：与 maybe_update_runtime_state 同面（tool_result /
    turn_settled / 观察到达）；由状态机模块外的调用方按需触发。
    """
    if not session_id:
        return None
    from app.services.session_plan import goal_key, load_session_plan, save_session_plan

    plan = await load_session_plan(session_id)
    if plan is None or not isinstance(plan.gis_chapter, dict):
        return None
    chapter = plan.gis_chapter
    if not chapter.get("plan_id"):
        return None
    stored = chapter.get(PLAN_RUNTIME_KEY)
    new_block = derive_plan_runtime(
        chapter, reason=reason, stored=stored if isinstance(stored, dict) else None)
    if new_block is None and not force:
        return None
    if new_block is None:
        new_block = _parse_stored(stored)
        if new_block.fingerprint == compute_plan_fingerprint(chapter):
            return None

    validated_goal = goal_key(chapter, plan.user_goal)
    validated_rows = rows_fingerprint(chapter)
    try:
        from app.services.distributed_lock import session_lock_registry

        async with session_lock_registry.lock(session_id, fail_on_degraded=True) as lock:
            fresh = await load_session_plan(session_id)
            if fresh is None or not isinstance(fresh.gis_chapter, dict) or lock.lost:
                return None
            if goal_key(fresh.gis_chapter, fresh.user_goal) != validated_goal:
                return None
            if rows_fingerprint(fresh.gis_chapter)[:2048] != validated_rows[:2048]:
                return None
            fresh_stored = fresh.gis_chapter.get(PLAN_RUNTIME_KEY)
            fresh_block = derive_plan_runtime(
                fresh.gis_chapter, reason=reason,
                stored=fresh_stored if isinstance(fresh_stored, dict) else None)
            if fresh_block is None:
                return None
            block = fresh_block.to_bounded_dict()
            fresh.gis_chapter[PLAN_RUNTIME_KEY] = block
            await save_session_plan(fresh)
            return block
    except Exception:  # noqa: BLE001 — 投影失败不阻断 turn
        logger.warning(
            "[PlanRuntime] persist failed session=%s (retry on next trigger)",
            session_id, exc_info=True,
        )
        return None


async def request_replan(
    session_id: str,
    *,
    reason: str = "",
    from_verdict: str = "",
) -> Dict[str, Any]:
    """重规划生产驱动点（finalizer 出口调用；ADR-0130 D2）。

    - replan 预算有余 → ``plan_runtime.replan_pending`` 置位 + durable
      记账（update_recovery_state(loop="replan")），返回 verdict=replan；
    - 预算耗尽 → verdict=abort_with_disclosure（诚实部分完成，不置位）。

    返回有界 payload（供 finalizer 披露面与 continuation 裁决消费）。
    """
    payload: Dict[str, Any] = {
        "verdict": "replan", "loop": REPLAN_LOOP,
        "replan_pending": False, "reason": str(reason)[:160],
    }
    if not session_id:
        payload["verdict"] = "abort_with_disclosure"
        payload["reason"] = "missing session"
        return payload
    from app.services.gis_harness.durable_context import (
        LOOP_BUDGETS,
        load_recovery_state,
        update_recovery_state,
    )

    recovery = await load_recovery_state(session_id)
    used = int((recovery.get("loops") or {}).get(REPLAN_LOOP) or 0)
    budget = int(LOOP_BUDGETS.get(REPLAN_LOOP, 0))
    if used >= budget:
        payload["verdict"] = "abort_with_disclosure"
        payload["reason"] = (
            f"replan budget exhausted ({used}/{budget}) — 诚实部分完成")
        payload["disclosure"] = (
            "重规划预算耗尽：不自动重规划；恢复/新证据后按事实重判。")
        return payload

    from app.services.session_plan import (
        goal_key,
        load_session_plan,
        save_session_plan,
    )

    plan = await load_session_plan(session_id)
    if plan is None or not isinstance(plan.gis_chapter, dict):
        payload["verdict"] = "abort_with_disclosure"
        payload["reason"] = "no gis chapter"
        return payload
    chapter = plan.gis_chapter
    validated_goal = goal_key(chapter, plan.user_goal)
    validated_rows = rows_fingerprint(chapter)
    try:
        from app.services.distributed_lock import session_lock_registry

        async with session_lock_registry.lock(session_id, fail_on_degraded=True) as lock:
            fresh = await load_session_plan(session_id)
            if fresh is None or not isinstance(fresh.gis_chapter, dict) or lock.lost:
                payload["verdict"] = "abort_with_disclosure"
                return payload
            if goal_key(fresh.gis_chapter, fresh.user_goal) != validated_goal:
                payload["verdict"] = "abort_with_disclosure"
                return payload
            if rows_fingerprint(fresh.gis_chapter)[:2048] != validated_rows[:2048]:
                payload["verdict"] = "abort_with_disclosure"
                payload["reason"] = "rows changed mid-run — retry next trigger"
                return payload
            stored = _parse_stored(fresh.gis_chapter.get(PLAN_RUNTIME_KEY))
            stored.replan_pending = True
            stored.replan_reason = str(reason or "repair unreachable")[:160]
            stored.replan_from_verdict = str(from_verdict)[:32]
            fresh.gis_chapter[PLAN_RUNTIME_KEY] = stored.to_bounded_dict()
            await save_session_plan(fresh)
    except Exception:  # noqa: BLE001 — 置位失败按 abort（不假装已请求）
        logger.warning(
            "[PlanRuntime] replan flag persist failed session=%s", session_id,
            exc_info=True)
        payload["verdict"] = "abort_with_disclosure"
        payload["reason"] = "replan flag persist failed"
        return payload
    # durable 记账（锁外 —— update_recovery_state 自带读改写纪律）
    await update_recovery_state(
        session_id, loop=REPLAN_LOOP,
        detail=str(reason or "repair unreachable")[:160])
    payload["replan_pending"] = True
    payload["replan_remaining"] = max(0, budget - used - 1)
    return payload


__all__ = [
    "PLAN_RUNTIME_KEY",
    "REPLAN_LOOP",
    "PlanVersionRecord",
    "PlanRuntimeBlock",
    "compute_plan_fingerprint",
    "seed_recompute_from_failures",
    "derive_plan_runtime",
    "format_plan_runtime_line",
    "maybe_advance_plan_version",
    "request_replan",
]
