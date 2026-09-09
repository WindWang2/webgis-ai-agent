"""Workflow Runtime V5 —— 增量重算闭环（semantic change → STALE → 重算/复用）。

差分 oracle（架构 §9.4 [R1-C2]，以修复后的 V4 纯函数为对标）：

- STALE 标记集合 == plan.recompute ∩ {apply 时 SUCCEEDED|READY|STALE}；
- 重算执行集合 == plan.recompute − 复用命中 − {BLOCKED,FAILED,PENDING,
  SKIPPED,RUNNING}；
- 复用集合 == plan.reuse ∩ {SUCCEEDED}；
- style-only：维度 == {style} → 科学节点零触碰（呈现态刷新走独立裁决）。

quiescence 门 [R1-C1]：存在 RUNNING 节点 → changes 入 pending（≤16），
完成边界由 service drain —— 杜绝「在飞节点产出陈旧结果却无 STALE 标记」。

维度映射 [R1-M2]：义务/披露变化 → ``dimension="data",
target_kind="output"``（与 diff.py 同通道；RECOMPUTE_DIMENSIONS 词表零改动）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

from app.services.gis_harness.workflow_v4.recompute import (
    RecomputePlan,
    WorkflowChange,
    compute_affected_subgraph,
)
from app.services.workflow_runtime import contracts as C
from app.services.workflow_runtime.store import InstanceStore

logger = logging.getLogger(__name__)


def pending_to_workflow_changes(
    changes: Sequence[C.PendingChange],
) -> List[WorkflowChange]:
    """PendingChange → V4 WorkflowChange（维度映射显式、不造新词表）。"""
    out: List[WorkflowChange] = []
    for ch in changes[:C.MAX_APPLY_CHANGES]:
        out.append(WorkflowChange(
            dimension=ch.dimension, target_kind=ch.target_kind,
            target=ch.target, detail=ch.detail or ch.source))
    return out


def plan_for_changes(
    dag: Dict[str, Any], changes: Sequence[C.PendingChange],
) -> RecomputePlan:
    """完整 RecomputePlan 对象（绝不用 bounded dict [R1-MINOR-6]）。"""
    return compute_affected_subgraph(
        dag, pending_to_workflow_changes(changes))


def is_style_only(plan: RecomputePlan) -> bool:
    return plan.changed_dimensions == ["style"]


class ChangeApplier:
    """把语义变更落地为节点级 CAS（STALE 批量标记）+ 决策证据。"""

    def __init__(self, store: InstanceStore, *, owner_scope: str):
        self.store = store
        self.owner_scope = owner_scope

    async def apply(
        self, instance_id: str, dag: Dict[str, Any],
        changes: Sequence[C.PendingChange], *, seq: int, source: str = "api",
    ) -> Dict[str, Any]:
        """应用变更。返回 {applied, deferred, decision}。

        - RUNNING 在飞 → deferred（入 pending_changes，完成边界 drain）；
        - style-only → 科学零触碰，决策记 style_only（呈现态归渲染层）；
        - 其余 → plan.recompute ∩ {SUCCEEDED,READY,STALE} → STALE CAS。
        """
        import asyncio

        inst = await asyncio.to_thread(
            self.store.get_instance, instance_id, self.owner_scope)
        if inst is None:
            return {"applied": False, "deferred": False,
                    "reason": "not_found"}
        states = await asyncio.to_thread(self.store.get_node_states,
                                         instance_id)
        running = [n for n, s in states.items() if s == C.NodeState.RUNNING]
        if running:
            return await self._defer(instance_id, inst, changes, source)
        if len(changes) > C.MAX_APPLY_CHANGES:
            return {"applied": False, "deferred": False,
                    "reason": "too_many_changes"}

        plan = plan_for_changes(dag, changes)
        style_only = is_style_only(plan)
        marked: List[str] = []
        reuse_resolved: List[str] = []
        if not style_only:
            terminal_ok = {C.NodeState.SUCCEEDED, C.NodeState.READY,
                           C.NodeState.STALE}
            for nid in plan.recompute:
                cur = states.get(nid)
                if cur not in terminal_ok:
                    continue
                r = await asyncio.to_thread(
                    self.store.transition_node,
                    instance_id, nid, C.NodeState.STALE,
                    reason=f"RECOMPUTE_SEED:{plan.changed_dimensions[0][:24]}",
                    event="apply_changes")
                if r.ok or r.code == "OK_IDEMPOTENT":
                    marked.append(nid)
                    states[nid] = C.NodeState.STALE
        decision = C.RecomputeDecision(
            seq=seq,
            changes_fingerprint=C.changes_fingerprint(list(changes)),
            dimensions=list(plan.changed_dimensions),
            marked_stale=marked,
            reuse_resolved=reuse_resolved,
            recomputed=[], reused=[],
            style_only=style_only,
            explanations=_explanations(plan, marked, style_only),
        )
        await self._append_decision(instance_id, inst, decision)
        # STALE 节点立即做复用裁决的入口由 driver.run 的 STALE 重入队处理。
        return {"applied": True, "deferred": False,
                "decision": decision.to_bounded_dict(),
                "plan": {"recompute": plan.recompute,
                         "reuse": plan.reuse}}

    async def _defer(
        self, instance_id: str, inst: Dict[str, Any],
        changes: Sequence[C.PendingChange], source: str,
    ) -> Dict[str, Any]:
        # R2-M3：defer CAS 失败后重读重试（≤3）—— 租约续期等高频
        # revision 写手会把单次 CAS 打成必败，变更「既未应用也未排队」
        # 违背 quiescence 设计目标。
        import asyncio

        for _attempt in range(3):
            pending = list(inst.get("pending_changes") or [])
            if len(pending) + len(changes) > C.MAX_PENDING_CHANGES:
                return {"applied": False, "deferred": False,
                        "reason": "pending_full"}
            pending.extend(
                ch.to_bounded_dict() | {"source": source} for ch in changes)
            updated = await asyncio.to_thread(
                self.store.update_instance,
                instance_id, owner_scope=self.owner_scope,
                expected_revision=inst["revision"],
                fields={"pending_changes": pending})
            if updated is not None:
                return {"applied": False, "deferred": True,
                        "pending_count": len(pending)}
            inst = await asyncio.to_thread(
                self.store.get_instance, instance_id, self.owner_scope)
            if inst is None:
                return {"applied": False, "deferred": False,
                        "reason": "not_found"}
        return {"applied": False, "deferred": False, "reason": "cas_conflict"}

    async def _append_decision(
        self, instance_id: str, inst: Dict[str, Any],
        decision: C.RecomputeDecision,
    ) -> None:
        """决策环追加（CAS：实例 revision 冲突即放弃本条 —— 有界观测
        记录的 lost update 可接受，绝不覆盖并发写入的变更标记）。"""
        import asyncio

        decisions = list(inst.get("decisions") or [])
        decisions.append(decision.to_bounded_dict())
        await asyncio.to_thread(
            self.store.update_instance,
            instance_id, owner_scope=self.owner_scope,
            expected_revision=inst["revision"],
            fields={"decisions": decisions[-C.MAX_INSTANCE_DECISIONS:]})

    async def drain_pending(
        self, instance_id: str, dag: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """完成边界 drain（quiescence 时应用挂起变更；[R1-C1]）。"""
        import asyncio

        inst = await asyncio.to_thread(
            self.store.get_instance, instance_id, self.owner_scope)
        if inst is None:
            return None
        pending = list(inst.get("pending_changes") or [])
        states = await asyncio.to_thread(self.store.get_node_states,
                                         instance_id)
        if not pending or any(
                s == C.NodeState.RUNNING for s in states.values()):
            return None
        cleared = await asyncio.to_thread(
            self.store.update_instance,
            instance_id, owner_scope=self.owner_scope,
            expected_revision=inst["revision"],
            fields={"pending_changes": []})
        if cleared is None:
            return None  # 并发 defer 写入 —— 本轮放弃，下轮重drain
        changes = [C.PendingChange(**p) for p in pending
                   if isinstance(p, dict)]
        seq = len(inst.get("decisions") or []) + 1
        # drain 复用 apply 的非 defer 分支（此时必无 RUNNING）
        return await self.apply(instance_id, dag, changes, seq=seq,
                                source="deferred")


def _explanations(plan: RecomputePlan, marked: List[str],
                  style_only: bool) -> List[str]:
    if style_only:
        return ["style 变更：呈现态刷新，科学子图零触碰"]
    out = list(plan.explanations[:4])
    if marked:
        out.append(f"标记 STALE {len(marked)} 节点: "
                   f"{','.join(marked[:6])}")
    reuse = [n for n in plan.reuse if not n.startswith("data:")]
    if reuse:
        out.append(f"未受影响节点 {len(reuse)} 个保持复用: "
                   f"{','.join(reuse[:6])}")
    return [e[:200] for e in out[:6]]
