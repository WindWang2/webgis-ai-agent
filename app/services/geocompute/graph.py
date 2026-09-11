"""执行图纯函数（ADR-0096 D2；ADR-0101 D2 扩展）：校验、拓扑、后代失效、复用键。

全部是纯函数 —— 图语义与执行策略解耦，执行器与测试共用同一份真值。
"""
from __future__ import annotations

from collections import deque

from app.services.geocompute.errors import GeoComputeError
from app.services.geocompute.normalization import canonicalize_strict
from app.services.geocompute.plan import (
    ExecutionNode,
    ExecutionPlan,
    ExecutionPolicyKind,
    NodeCategory,
    NodeReusePolicy,
    PayloadKind,
)


class PlanValidationError(GeoComputeError):
    code = "PLAN_INVALID"


#: 物化类类别：无输入的物化节点是无效序列（没有可物化的上游结果）。
_MATERIALIZING_CATEGORIES = {
    NodeCategory.MATERIALIZE,
    NodeCategory.ARTIFACT_REGISTER,
    NodeCategory.EXPORT,
}

#: 副作用类类别：重试会产生重复副作用（重复 artifact / 重复 ref），
#: 显式声明 ``parameters.idempotent``（执行层按指纹幂等）前禁止自动重试。
_SIDE_EFFECT_CATEGORIES = {
    NodeCategory.MATERIALIZE,
    NodeCategory.ARTIFACT_REGISTER,
    NodeCategory.EXPORT,
}

#: durable 交接只承载 features/rows 载荷（session ref）；raster_path 型
#: 输出 / 需在进程内解析输入的类别不支持（与 api.build_plan_from_json 同规）。
_DURABLE_UNSUPPORTED_CATEGORIES = {
    NodeCategory.RASTER_WINDOW_OPERATION,
    NodeCategory.ARTIFACT_REGISTER,
}

#: 跨平面语义禁区（ADR-0101 D1）：Data Plane 的节点参数绝不携带
#: Agent/Product Plane 的会话语义。只做顶层键检查（浅、廉价、诚实）。
_FORBIDDEN_PARAM_KEYS = {
    "pi_turn", "chat", "prompt", "messages", "conversation",
    "map_spec", "mapspec", "session_plan", "frontend_state",
    "ui_state", "tool_call_id",
}


def validate_plan(plan: ExecutionPlan) -> None:
    """结构 + 契约校验（V4 §5）：结构、引用、环、物化序列、重试声明、
    载荷类型兼容、CRS 期望、跨平面语义、指纹可归一性、预算上限。"""
    if not plan.nodes:
        raise PlanValidationError("execution plan has no nodes")
    ids = [n.node_id for n in plan.nodes]
    if len(ids) != len(set(ids)):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise PlanValidationError(f"duplicate node_id: {dupes}")
    node_map = plan.node_map()
    for node in plan.nodes:
        _validate_node_contract(node)
        for src in node.inputs:
            if src not in node_map:
                raise PlanValidationError(
                    f"node '{node.node_id}' references unknown input '{src}'"
                )
        if src_cycle_via(node, node_map):
            raise PlanValidationError(f"cycle detected through node '{node.node_id}'")
    for node in plan.nodes:
        _validate_edge_contracts(node, node_map)
    if len(plan.nodes) > plan.budget.max_nodes:
        raise PlanValidationError(
            f"plan has {len(plan.nodes)} nodes, budget allows {plan.budget.max_nodes}"
        )


def _validate_node_contract(node: ExecutionNode) -> None:
    """单节点契约（V4 §5）：物化序列、重试安全、确定性/复用、CRS、跨平面。"""
    if node.category in _MATERIALIZING_CATEGORIES and not node.inputs:
        raise PlanValidationError(
            f"node '{node.node_id}' ({node.category.value}) materializes "
            "without any input (invalid materialization sequence)"
        )
    if (
        node.category in _SIDE_EFFECT_CATEGORIES
        and node.retry.max_attempts > 1
        and not node.parameters.get("idempotent")
    ):
        raise PlanValidationError(
            f"node '{node.node_id}' ({node.category.value}) declares retry "
            f"max_attempts={node.retry.max_attempts} without parameters.idempotent "
            "(unsafe retry: side effects would duplicate)"
        )
    if not node.deterministic and node.reuse is not NodeReusePolicy.DISALLOW:
        raise PlanValidationError(
            f"node '{node.node_id}' declares deterministic=false; "
            "its result must not be reused (set reuse=disallow)"
        )
    if (
        node.policy is ExecutionPolicyKind.DURABLE_JOB
        and node.category in _DURABLE_UNSUPPORTED_CATEGORIES
        and node.partition is None
    ):
        # V8 例外：声明 partition 的 raster_window_operation 走分区
        # fan-out（tile job 结果经 job result_summary 回传路径，合并
        # 在 coordinator 进程内完成）—— tile 载荷不再依赖 session-ref
        # features/rows 交接，该 V4 限制对分区路径不成立。
        raise PlanValidationError(
            f"node '{node.node_id}' ({node.category.value}) does not support "
            "durable_job policy (payload handoff is session-ref features/rows only)"
        )
    if node.crs is not None and node.crs.output_crs:
        from app.services.geocompute.normalization import normalize_crs_ref

        if not normalize_crs_ref(node.crs.output_crs):
            raise PlanValidationError(
                f"node '{node.node_id}' has an empty CRS expectation"
            )
    if node.category is NodeCategory.REPROJECT and node.crs is not None \
            and not node.crs.allow_reproject:
        raise PlanValidationError(
            f"node '{node.node_id}' is a reproject node with allow_reproject=false "
            "(impossible CRS expectation)"
        )
    forbidden = sorted(_FORBIDDEN_PARAM_KEYS & set(node.parameters))
    if forbidden:
        raise PlanValidationError(
            f"node '{node.node_id}' carries cross-plane semantics in parameters: "
            f"{forbidden} (data plane must not import agent/product plane state)"
        )
    unknown_upstream = sorted(set(node.upstream_fingerprints) - set(node.inputs))
    if unknown_upstream:
        raise PlanValidationError(
            f"node '{node.node_id}' declares upstream_fingerprints for "
            f"non-input nodes: {unknown_upstream}"
        )
    try:
        canonicalize_strict(node.parameters)
    except ValueError as exc:
        raise PlanValidationError(f"node '{node.node_id}': {exc}") from exc


def _validate_edge_contracts(
    node: ExecutionNode, node_map: dict[str, ExecutionNode]
) -> None:
    """边级契约：载荷类型兼容（incompatible artifacts 的最小诚实形式）。"""
    if not node.accepts or PayloadKind.ANY in node.accepts:
        return
    for src in node.inputs:
        upstream = node_map.get(src)
        if upstream is None or upstream.produces is None:
            continue
        if upstream.produces is PayloadKind.NONE:
            raise PlanValidationError(
                f"node '{node.node_id}' consumes input '{src}' which produces nothing"
            )
        if upstream.produces not in node.accepts:
            raise PlanValidationError(
                f"node '{node.node_id}' accepts {sorted(a.value for a in node.accepts)} "
                f"but input '{src}' produces '{upstream.produces.value}' "
                "(incompatible artifact contract)"
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
    """计划域复用键（V3 形状，保留兼容）：``owner:plan_fp:node_fp``。

    SEC（评审 MAJOR）：``owner_scope`` 由 executor 从调用者身份派生
    （user id 优先，回退 session id；匿名固定 "anonymous"，见
    ``executor.owner_scope_for``）。不同 owner 即使语义指纹完全相同也
    绝不共享缓存条目。
    """
    return f"{owner_scope}:{plan_fingerprint}:{node.semantic_fingerprint()}"


def checkpoint_reuse_key(node: ExecutionNode, owner_scope: str) -> str:
    """checkpoint 复用键（ADR-0101 D4）：``owner:node_fp`` —— 跨计划安全复用。

    V3 把复用限制在计划指纹域内（缺上游验证，跨计划命中不安全）。
    V4 的缓存条目带有上游输出指纹（``__upstream_fps__``）且确定性节点
    才允许复用（deterministic=false ⇒ validation 强制 DISALLOW），跨计划
    命中可以安全成立 —— 「仅 D 参数变化 → A/B/C 仍可复用」的前提。
    owner 域隔离不变：绝不跨用户共享。
    """
    return f"{owner_scope}:{node.semantic_fingerprint()}"
