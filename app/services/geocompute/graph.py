"""执行图纯函数（ADR-0096 D2）：校验、拓扑、后代失效、复用键。

全部是纯函数 —— 图语义与执行策略解耦，执行器与测试共用同一份真值。
"""
from __future__ import annotations

from collections import deque

from app.services.geocompute.errors import GeoComputeError
from app.services.geocompute.plan import ExecutionNode, ExecutionPlan


class PlanValidationError(GeoComputeError):
    code = "PLAN_INVALID"


def validate_plan(plan: ExecutionPlan) -> None:
    """结构校验：空图拒绝、节点 id 唯一、输入存在、无环、预算上限。"""
    if not plan.nodes:
        raise PlanValidationError("execution plan has no nodes")
    ids = [n.node_id for n in plan.nodes]
    if len(ids) != len(set(ids)):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise PlanValidationError(f"duplicate node_id: {dupes}")
    node_map = plan.node_map()
    for node in plan.nodes:
        for src in node.inputs:
            if src not in node_map:
                raise PlanValidationError(
                    f"node '{node.node_id}' references unknown input '{src}'"
                )
        if src_cycle_via(node, node_map):
            raise PlanValidationError(f"cycle detected through node '{node.node_id}'")
    if len(plan.nodes) > plan.budget.max_nodes:
        raise PlanValidationError(
            f"plan has {len(plan.nodes)} nodes, budget allows {plan.budget.max_nodes}"
        )


def src_cycle_via(node: ExecutionNode, node_map: dict[str, ExecutionNode]) -> bool:
    """从 node 沿输入边出发能否回到 node（DFS，有界由 DAG 结构保证）。"""
    stack = list(node.inputs)
    seen: set[str] = set()
    while stack:
        cur = stack.pop()
        if cur == node.node_id:
            return True
        if cur in seen:
            continue
        seen.add(cur)
        parent = node_map.get(cur)
        if parent is not None:
            stack.extend(parent.inputs)
    return False


def topo_wave_order(plan: ExecutionPlan) -> list[list[str]]:
    """按依赖分波（同波节点可并行）。校验后的计划必然无环。"""
    node_map = plan.node_map()
    indegree = {nid: 0 for nid in node_map}
    dependents: dict[str, list[str]] = {nid: [] for nid in node_map}
    for node in plan.nodes:
        for src in node.inputs:
            indegree[node.node_id] += 1
            dependents[src].append(node.node_id)
    waves: list[list[str]] = []
    frontier = sorted(nid for nid, deg in indegree.items() if deg == 0)
    done = 0
    while frontier:
        waves.append(frontier)
        done += len(frontier)
        nxt: list[str] = []
        for nid in frontier:
            for dep in dependents[nid]:
                indegree[dep] -= 1
                if indegree[dep] == 0:
                    nxt.append(dep)
        frontier = sorted(nxt)
    if done != len(node_map):  # pragma: no cover - validate_plan 先行拦截
        raise PlanValidationError("cycle detected after validation (invariant bug)")
    return waves


def descendants_of(plan: ExecutionPlan, node_id: str) -> set[str]:
    """node_id 的全部传递后代（反向 BFS；不含自身）。"""
    dependents: dict[str, list[str]] = {n.node_id: [] for n in plan.nodes}
    for node in plan.nodes:
        for src in node.inputs:
            dependents[src].append(node.node_id)
    out: set[str] = set()
    queue = deque(dependents.get(node_id, []))
    while queue:
        cur = queue.popleft()
        if cur in out:
            continue
        out.add(cur)
        queue.extend(dependents.get(cur, []))
    return out


def invalidation_set(plan: ExecutionPlan, changed_fingerprints: set[str]) -> set[str]:
    """给定语义指纹发生变化的节点集合，返回必须重算的节点集合（含自身）。

    上游指纹变化 → 该节点指纹变化 → 其全部后代失效。这是「部分重跑」
    与「后代失效」语义的核心（目标 §3）。
    """
    changed_nodes = {
        n.node_id for n in plan.nodes if n.semantic_fingerprint() in changed_fingerprints
    }
    out: set[str] = set(changed_nodes)
    for nid in changed_nodes:
        out |= descendants_of(plan, nid)
    return out


def node_reuse_key(plan_fingerprint: str, node: ExecutionNode, owner_scope: str) -> str:
    """复用存储键：计划指纹域内按节点指纹寻址，并按 owner 域隔离。

    同一节点的语义指纹在不同计划间是可比的（纯语义），但复用键仍包含
    计划指纹 —— 避免跨计划的意外命中；跨计划复用留待显式产物链（M6）。

    SEC（评审 MAJOR）：``owner_scope`` 由 executor 从调用者身份派生
    （user id 优先，回退 session id；匿名固定 "anonymous"，见
    ``executor.owner_scope_for``）。不同 owner 即使语义指纹完全相同也
    绝不共享缓存条目 —— 堵住跨用户结果复用泄漏（A 用户的 QUERY 结果
    曾可被 B 用户的同指纹节点直接命中）。
    """
    return f"{owner_scope}:{plan_fingerprint}:{node.semantic_fingerprint()}"
