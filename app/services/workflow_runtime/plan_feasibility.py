"""Workflow DAG → 计划级资源 feasibility（ADR-0214 D3）。

把 #1484 的 ``governor.plan_aggregation``（critical path / parallel live
peak / retry 乘数 / cache 折扣 / unknown 保守地板）接到 workflow DAG：

- 波次结构：拓扑层（level = 1+max(上游 level)）内 PARALLEL、层间
  SEQUENTIAL —— 与 driver 波次 ``max_concurrency`` 调度语义同构；
- ``aggregate_plan`` 消费 PlanNode 序列 → PlanAggregate；
- ``budget_violations`` 对 manifest scope=``workflow`` 的显式上限裁决；
- **provisional 纪律**：manifest 未登记 workflow 上限 → 零违规 → 行为
  完全不变（先观测后拦截）；enforce 需显式 env + 登记 limits。

资源裁决**绝不越过** capability/permission/data qualification 硬门 ——
本模块只在全部资格语义之后回答"这个计划值多少资源、超不超申报上限"。
纯函数 + 确定性；manifest 读取 fail-open。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.services.governor.contract import Dimension
from app.services.governor.plan_aggregation import (
    MAX_PLAN_NODES,
    PlanAggregate,
    PlanNode,
    PlanNodeKind,
    aggregate_plan,
    budget_violations,
)
from app.services.workflow_runtime.estimate import (
    resource_estimate_for_workflow_node,
)

logger = logging.getLogger(__name__)

#: manifest 中 workflow 计划级预算的 scope 键（config/governor_budgets.json）
WORKFLOW_BUDGET_SCOPE = "workflow"

#: 准入模式词表：off（跳过）/ observe（默认，披露不拦截）/ enforce
PLAN_ADMISSION_ENV = "GIS_WORKFLOW_PLAN_ADMISSION"


def plan_admission_mode() -> str:
    raw = os.getenv(PLAN_ADMISSION_ENV, "observe").strip().lower()
    return raw if raw in ("off", "observe", "enforce") else "observe"


@dataclass
class PlanFeasibility:
    """计划级裁决产物（可序列化披露；journal payload 直接消费）。"""

    mode: str = "observe"
    violations: List[str] = field(default_factory=list)
    limits: Dict[str, float] = field(default_factory=dict)
    node_count: int = 0
    parallel_waves: int = 0
    truncated: bool = False
    aggregate_summary: Dict[str, Any] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        """enforce 下是否应拒绝实例启动（observe/off 恒 False）。"""
        return bool(self.violations) and self.mode == "enforce"

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "violations": [v[:96] for v in self.violations[:12]],
            "limits": {k[:48]: float(v) for k, v in
                       sorted(self.limits.items())[:12]},
            "node_count": self.node_count,
            "parallel_waves": self.parallel_waves,
            "truncated": self.truncated,
            "critical_path": [
                k[:64] for k in
                self.aggregate_summary.get("critical_path", [])[:32]],
            "wall_exp_s": self.aggregate_summary.get("wall_exp_s"),
            "peak_mem_exp_bytes": self.aggregate_summary.get(
                "peak_mem_exp_bytes"),
        }


def dag_waves(dag: Dict[str, Any]) -> List[List[str]]:
    """DAG → 拓扑波次（层内声明序；确定性；环内节点收敛进末波）。

    与 driver ``ready_set`` 的可派发语义同构：同一波的节点可并行派发
    （受 max_concurrency 截断），跨波串行。边真相单一来源 =
    ``machine.upstream_of``（数据流边 ∪ 结构依赖，端口后缀归一）。
    """
    from app.services.workflow_runtime.machine import upstream_of

    nodes = [n for n in (dag.get("nodes") or [])
             if str(n.get("node_id", "") or "")]
    ids = [str(n.get("node_id")) for n in nodes]
    upstream = upstream_of(dag)
    levels: Dict[str, int] = {}
    remaining = list(ids)
    guard = 0
    while remaining and guard <= len(ids) + 1:
        guard += 1
        progressed = False
        nxt: List[str] = []
        for nid in remaining:
            ups = [u for u in (upstream.get(nid) or []) if u in set(ids)]
            if all(u in levels for u in ups):
                levels[nid] = (max((levels[u] for u in ups), default=-1)
                               + 1)
                progressed = True
            else:
                nxt.append(nid)
        if not progressed:
            # 环：剩余节点全部收敛进当前最深层的下一层（诚实收敛，
            # 绝不死循环 —— 环在编译面已拒绝，这里是防御）
            base = max(levels.values(), default=0) + 1
            for nid in nxt:
                levels[nid] = base
            break
        remaining = nxt
    order = {nid: idx for idx, nid in enumerate(ids)}
    waves: Dict[int, List[str]] = {}
    for nid, lv in levels.items():
        waves.setdefault(lv, []).append(nid)
    return [sorted(waves[lv], key=lambda n: order[n])
            for lv in sorted(waves)]


def dag_plan_nodes(
    dag: Dict[str, Any],
    *,
    max_parallel: int = 1,
    expected_attempts: int = 1,
    estimates: Optional[Dict[str, Any]] = None,
) -> List[PlanNode]:
    """DAG → PlanNode 序列（波次形状投影；纯函数）。

    ``estimates`` 允许调用方注入预计算估算（driver 复用节点级结果）；
    缺省逐节点现算（纯函数，无 IO）。optional 节点进披露池（不进主
    聚合，aggregate_plan 既有语义）。
    """
    estimates = estimates or {}
    optional_map = {
        str(n.get("node_id", "")): bool(n.get("optional", False))
        for n in dag.get("nodes") or []
    }
    by_id = {str(n.get("node_id", "")): n for n in dag.get("nodes") or []}
    plan: List[PlanNode] = []
    for wave_idx, wave in enumerate(dag_waves(dag)):
        parallel = len(wave) > 1 and max_parallel > 1
        for node_idx, nid in enumerate(wave):
            node = by_id.get(nid) or {}
            est = estimates.get(nid)
            if est is None:
                est = resource_estimate_for_workflow_node(node)
            kind = (PlanNodeKind.OPTIONAL
                    if optional_map.get(nid, False)
                    else (PlanNodeKind.PARALLEL if parallel
                          else PlanNodeKind.SEQUENTIAL))
            # 波边界（非首波的第一个节点）强制分段：连续同 kind 波不得
            # 被 aggregate_plan 的 run 合并抹平（wall max 语义只属波内）
            boundary = wave_idx > 0 and node_idx == 0
            plan.append(PlanNode(
                key=nid,
                label=str(node.get("kind", ""))[:128],
                kind=kind,
                estimate=est,
                expected_attempts=max(1, int(expected_attempts)),
                boundary=boundary,
            ))
    return plan


def evaluate_plan(
    dag: Dict[str, Any],
    *,
    max_parallel: int = 1,
    expected_attempts: int = 1,
    limits: Optional[Dict[Dimension, float]] = None,
    estimates: Optional[Dict[str, Any]] = None,
) -> PlanFeasibility:
    """计划聚合 + 上限裁决（确定性；limits 空 → 零违规）。"""
    mode = plan_admission_mode()
    limits = dict(limits or {})
    if mode == "off" or not limits:
        return PlanFeasibility(mode=mode, limits={
            k.value: float(v) for k, v in limits.items()})
    plan = dag_plan_nodes(
        dag, max_parallel=max_parallel,
        expected_attempts=expected_attempts, estimates=estimates)
    aggregate: PlanAggregate = aggregate_plan(
        plan, subsystem=_workflow_subsystem())
    violations = budget_violations(aggregate.estimate, limits)
    wall_dim = aggregate.estimate.dim(Dimension.WALL_TIME_S)
    mem_dim = aggregate.estimate.dim(Dimension.MEMORY_BYTES)
    return PlanFeasibility(
        mode=mode,
        violations=violations,
        limits={k.value: float(v) for k, v in limits.items()},
        node_count=len(plan),
        parallel_waves=sum(
            1 for w in dag_waves(dag) if len(w) > 1 and max_parallel > 1),
        truncated=len(plan) > MAX_PLAN_NODES,
        aggregate_summary={
            "critical_path": aggregate.critical_path,
            "wall_exp_s": (round(wall_dim.expected, 3)
                           if wall_dim.expected is not None else None),
            "peak_mem_exp_bytes": (round(mem_dim.expected, 1)
                                   if mem_dim.expected is not None else None),
            "floor_charged_dims": aggregate.floor_charged_dims,
            "retry_tail_s": aggregate.retry_tail_s,
            "optional_pool": len(aggregate.optional_pool),
            "fallback_pool": len(aggregate.fallback_pool),
        },
    )


def workflow_plan_limits() -> Dict[Dimension, float]:
    """manifest scope=workflow 的显式上限（fail-open → 空 = 不设限）。

    单一真相：与 governor 预算同一 manifest 文件、同一词表校验；
    未登记 scope → 空（provisional 观测纪律，行为不变）。
    """
    try:
        from app.services.governor.config import load_manifest

        budget = load_manifest().get(WORKFLOW_BUDGET_SCOPE)
        if budget is None:
            return {}
        return dict(budget.limits)
    except FileNotFoundError:
        return {}
    except Exception:  # noqa: BLE001 — 预算面故障绝不阻断执行
        logger.warning("[workflow-plan] budget manifest unreadable",
                       exc_info=True)
        return {}


def _workflow_subsystem():
    from app.services.governor.contract import Subsystem

    return Subsystem.WORKFLOW


__all__ = [
    "WORKFLOW_BUDGET_SCOPE",
    "PLAN_ADMISSION_ENV",
    "PlanFeasibility",
    "dag_waves",
    "dag_plan_nodes",
    "evaluate_plan",
    "workflow_plan_limits",
    "plan_admission_mode",
]
