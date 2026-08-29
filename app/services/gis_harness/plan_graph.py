"""Plan Graph / Analysis DAG —— MapProductPlan 的依赖感知投影（Runtime v3）。

v2 现状：``data_requirements`` 与 ``analysis_steps`` 是同一 capability 列表的
两个平行扁平投影（``_resolve_capabilities`` 对每个 capability 各发一行），
无边、无 ``depends_on``、无 artifact 关联 —— 执行顺序只存在于 recipe 的
声明顺序里，且没有任何东西强制它。producer→consumer 语义只存在于 registry
descriptor（``input_artifact_types``/``output_artifact_types``），plan 从不携带。

本模块把扁平行升级为 dependency-aware graph（Phase D）与确定性推进的
analysis DAG（Phase E）：

    MapProductPlan（唯一事实源：planner 编制，SessionPlan 持久化）
          ↓ build_plan_graph（纯投影，无第二事实源）
    PlanGraph
          ├── 依赖边：registry artifact 类型推断（A.output ∩ B.input ⇒ A→B）
          ├── 节点状态：merge(requirement.status, step.status)
          ├── ready：deps 全满足（complete/skipped/fallback-unlocked）
          ├── unavailable 传播：mandatory dep 缺失阻塞下游；optional 缺失
          │   自身 skipped（不阻塞 mandatory 图）
          └── fallback：capability 级 fallback 裁决（resolver 记录的
              capability_fallback_available:<cap>）——fallback 节点完成即
              视为原节点满足，解锁下游

推进语义（E）：**LLM 不维护 DAG 状态**。状态由 SessionPlan + tool result
binding 确定性推进（``_mark_progress`` 写行状态 → 本模块纯函数评估投影）。
本模块不写任何持久状态。

兼容（§18）：``data_requirements``/``analysis_steps`` 保留为 canonical 行
（新增 additive 字段 ``depends_on``/``optional``，由 planner 在编制时填充）；
graph 是这些行的**派生视图**——legacy projection = graph projection，单一
计算源。旧持久计划（无 depends_on 字段）同样可建图（推断在读取侧重放）。

环处理：registry artifact 推断可能产生环（两个 capability 互相消费对方的
输出类型，如 terrain_slope/terrain_aspect 都消费并产出 terrain_surface）。
按计划声明序增量插边，跳过成环边（确定性）；显式构造的 depends_on 环由
``build_plan_graph(strict=True)`` 拒绝（``PlanGraphError``）。
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class PlanNodeStatus(str, Enum):
    pending = "pending"
    ready = "ready"
    running = "running"
    complete = "complete"
    skipped = "skipped"
    unavailable = "unavailable"
    failed = "failed"


# 扁平行状态 → DAG 节点状态（requirement/step 两行的同一 capability 合并）。
_ROW_STATUS_TO_NODE: Dict[str, PlanNodeStatus] = {
    "available": PlanNodeStatus.complete,
    "done": PlanNodeStatus.complete,
    "complete": PlanNodeStatus.complete,
    "pending": PlanNodeStatus.pending,
    "unavailable": PlanNodeStatus.unavailable,
    "skipped": PlanNodeStatus.skipped,
}

# 节点满足态：依赖处于这些状态即解锁下游（skipped 含 fallback-unlock）。
_SATISFIED = (PlanNodeStatus.complete, PlanNodeStatus.skipped)


class PlanGraphError(ValueError):
    """非法图（显式依赖成环 / 引用不存在的依赖）。"""


class PlanNode(BaseModel):
    """图中一个 capability 节点（requirement 行与 step 行的合并投影）。"""

    node_id: str
    capability: str
    kind: str = "analysis"  # requirement | analysis（capability category 派生）
    purpose: str = ""
    depends_on: List[str] = []
    status: PlanNodeStatus = PlanNodeStatus.pending
    resolved_algorithm: str = ""
    resolved_tool: str = ""
    bound_ref: str = ""
    output_ref: str = ""
    input_refs: List[str] = []
    optional: bool = False
    cost_class: str = ""
    fallback_to: str = ""
    blocked_by: List[str] = []
    notes: List[str] = []


class PlanGraph(BaseModel):
    """整个计划的 DAG 投影（纯派生，不持久化）。"""

    plan_id: str = ""
    manifest_fingerprint: str = ""
    nodes: List[PlanNode] = []
    dropped_cycle_edges: List[List[str]] = []
    unresolved_dependency_refs: List[str] = []

    def node(self, capability: str) -> Optional[PlanNode]:
        for n in self.nodes:
            if n.capability == capability:
                return n
        return None

    def ready_nodes(self) -> List[str]:
        return [n.capability for n in self.nodes if n.status == PlanNodeStatus.ready]

    def waiting_nodes(self) -> List[PlanNode]:
        return [n for n in self.nodes if n.status == PlanNodeStatus.pending]

    def completed_nodes(self) -> List[str]:
        return [n.capability for n in self.nodes if n.status == PlanNodeStatus.complete]

    def unavailable_nodes(self) -> List[PlanNode]:
        return [n for n in self.nodes if n.status == PlanNodeStatus.unavailable]


def _row(rows: Any, capability: str) -> Dict[str, Any]:
    """从扁平行列表取 capability 行（无则空 dict）。"""
    for item in rows or []:
        if isinstance(item, dict) and item.get("capability") == capability:
            return item
    return {}


def _merge_row_status(req: Dict[str, Any], step: Dict[str, Any]) -> PlanNodeStatus:
    """requirement/step 两行状态合并（终态优先；冲突取更保守者）。"""
    s_req = _ROW_STATUS_TO_NODE.get(str(req.get("status") or ""), PlanNodeStatus.pending)
    s_step = _ROW_STATUS_TO_NODE.get(str(step.get("status") or ""), PlanNodeStatus.pending)
    if PlanNodeStatus.complete in (s_req, s_step):
        # 一行 complete 一行 pending：产品阶段只回填了 requirement（数据到
        # 手）而 step 未判 done —— complete 优先（artifact 已绑定）。
        return PlanNodeStatus.complete
    if s_req == s_step:
        return s_req
    if PlanNodeStatus.unavailable in (s_req, s_step):
        return PlanNodeStatus.unavailable
    return PlanNodeStatus.pending


def _has_path(adjacency: Dict[str, List[str]], src: str, dst: str) -> bool:
    """DFS：src 是否可达 dst（增量插边的成环检测）。"""
    stack, seen = [src], set()
    while stack:
        cur = stack.pop()
        if cur == dst:
            return True
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(adjacency.get(cur, []))
    return False


def infer_dependency_edges(capabilities: List[str]) -> Dict[str, List[str]]:
    """registry artifact 类型推断：A.output ∩ B.input ⇒ A→B（计划声明序，
    增量插边，跳过成环边——terrain_slope/terrain_aspect 等互相消费同类型
    的 capability 不会把图打死）。"""
    from app.lib.gis.capability_registry import get_capability_registry

    caps = get_capability_registry()
    by_id: Dict[str, List[str]] = {cap: [] for cap in capabilities}
    present = set(capabilities)
    descriptors = {}
    for cap in capabilities:
        d = caps.get(cap)
        if d is not None:
            descriptors[cap] = (
                set(getattr(d, "output_artifact_types", None) or []),
                set(getattr(d, "input_artifact_types", None) or []),
            )
    for consumer in capabilities:
        c_in = descriptors.get(consumer, (set(), set()))[1]
        if not c_in:
            continue
        for producer in capabilities:
            if producer == consumer:
                continue
            p_out = descriptors.get(producer, (set(), set()))[0]
            if p_out and (p_out & c_in) and producer in present:
                # 增量插边：producer 已可达 consumer（经既有依赖边）时
                # 跳过 —— 再插 consumer→producer 会闭合成环。
                if not _has_path(by_id, producer, consumer):
                    by_id[consumer].append(producer)
    return by_id


def _capability_meta(capability: str) -> tuple[str, str]:
    """(kind, cost_class)：capability category + 算法复杂度（描述符有界投影）。"""
    from app.lib.gis.capability_registry import get_capability_registry

    caps = get_capability_registry()
    d = caps.get(capability)
    kind = "requirement" if getattr(d, "category", "") == "data_access" else "analysis"
    cost = str(getattr(d, "preferred_execution", "") or "")
    return kind, cost


def _algorithm_cost_class(selections: List[Dict[str, Any]], capability: str) -> str:
    """从 plan.algorithm_selections（dict 形式）取算法复杂度档位。"""
    for record in selections or []:
        if isinstance(record, dict) and record.get("capability") == capability:
            algo_id = str(record.get("algorithm") or "")
            if not algo_id:
                return ""
            from app.lib.gis.algorithm_registry import get_algorithm_registry

            algo = get_algorithm_registry().get(algo_id)
            if algo is not None:
                complexity = str(getattr(algo, "complexity", "") or "")
                return complexity
    return ""


def _fallback_from_selection(selections: List[Dict[str, Any]], capability: str) -> str:
    """resolver 的 capability 级 fallback 裁决（reason 携带
    capability_fallback_available:<cap>）——preferred 不可用但 fallback
    capability 可解析时记录，评估期 fallback 完成即解锁下游。"""
    for record in selections or []:
        if not (isinstance(record, dict) and record.get("capability") == capability):
            continue
        reason = str(record.get("reason") or "")
        marker = "capability_fallback_available:"
        if record.get("status") == "unavailable" and marker in reason:
            return reason.split(marker, 1)[1].strip()
        for trail in record.get("fallback_trail") or []:
            if isinstance(trail, dict) and trail.get("to_element"):
                # 算法级 fallback 已 resolved：记录实际裁决的替代元素。
                if record.get("status") == "resolved":
                    return str(trail.get("to_element"))
    return ""


def build_plan_graph(
    plan: Any,
    *,
    evaluate: bool = True,
    strict: bool = False,
) -> PlanGraph:
    """从 MapProductPlan（model 或持久 chapter dict）构建 DAG 投影。

    ``strict=True`` 时显式 ``depends_on`` 引用不存在节点或成环 →
    ``PlanGraphError``（对抗校验入口；生产路径容错记录）。
    """
    if isinstance(plan, dict):
        requirements = plan.get("data_requirements") or []
        steps = plan.get("analysis_steps") or []
        selections = plan.get("algorithm_selections") or []
        plan_id = str(plan.get("plan_id") or "")
        fingerprint = str(plan.get("manifest_fingerprint") or "")
    else:
        requirements = [r.model_dump() for r in plan.data_requirements]
        steps = [s.model_dump() for s in plan.analysis_steps]
        selections = [s.model_dump() for s in plan.algorithm_selections]
        plan_id = plan.plan_id
        fingerprint = plan.manifest_fingerprint

    capabilities: List[str] = []
    for row in list(requirements) + list(steps):
        cap = str((row or {}).get("capability") or "")
        if cap and cap not in capabilities:
            capabilities.append(cap)

    # 依赖边：planner 编制时填充的 depends_on 优先（持久计划）；
    # 缺失（旧计划 / 简化视图）读取侧重放 registry 推断。
    declared: Dict[str, List[str]] = {}
    for cap in capabilities:
        req = _row(requirements, cap)
        deps = req.get("depends_on")
        if deps is None:
            step = _row(steps, cap)
            deps = step.get("depends_on")
        declared[cap] = [str(d) for d in deps] if deps else None

    inferred = infer_dependency_edges(capabilities)

    graph = PlanGraph(plan_id=plan_id, manifest_fingerprint=fingerprint)
    by_id: Dict[str, List[str]] = {cap: [] for cap in capabilities}
    for cap in capabilities:
        deps = declared.get(cap)
        if deps is None:
            deps = inferred.get(cap, [])
        for dep in deps:
            if dep not in by_id:
                # 依赖声明引用不存在的 capability（旧数据/手写）。
                graph.unresolved_dependency_refs.append(f"{cap}->{dep}")
                if strict:
                    raise PlanGraphError(
                        f"node {cap!r} depends on unknown capability {dep!r}"
                    )
                continue
            if not _has_path(by_id, dep, cap):
                by_id[cap].append(dep)
            elif strict:
                raise PlanGraphError(
                    f"dependency cycle via edge {cap!r} -> {dep!r}"
                )
            else:
                graph.dropped_cycle_edges.append([cap, dep])

    for cap in capabilities:
        req = _row(requirements, cap)
        step = _row(steps, cap)
        kind, _exec = _capability_meta(cap)
        node = PlanNode(
            node_id=cap,
            capability=cap,
            kind=kind,
            purpose=str(req.get("purpose") or step.get("purpose") or ""),
            depends_on=list(by_id[cap]),
            status=_merge_row_status(req, step),
            resolved_algorithm=str(req.get("resolved_algorithm") or step.get("resolved_algorithm") or ""),
            resolved_tool=str(req.get("resolved_tool") or step.get("resolved_tool") or ""),
            bound_ref=str(req.get("bound_ref") or step.get("bound_ref") or ""),
            output_ref=str(req.get("bound_ref") or step.get("bound_ref") or ""),
            optional=bool(req.get("optional") or step.get("optional") or False),
            cost_class=_algorithm_cost_class(selections, cap),
            fallback_to=_fallback_from_selection(selections, cap),
        )
        graph.nodes.append(node)

    if evaluate:
        _evaluate(graph)
    return graph


def _dep_satisfied(graph: PlanGraph, dep_capability: str) -> bool:
    dep = graph.node(dep_capability)
    if dep is None:
        return False
    if dep.status in _SATISFIED:
        return True
    # fallback unlock：preferred 节点 unavailable，但其 fallback 节点完成。
    if (
        dep.status == PlanNodeStatus.unavailable
        and dep.fallback_to
    ):
        fb = graph.node(dep.fallback_to)
        if fb is not None and fb.status in _SATISFIED:
            return True
    return False


def _evaluate(graph: PlanGraph) -> None:
    """确定性评估：ready 派生 + unavailable 传播 + optional skipped 级联。

    幂等纯函数——只读行状态与依赖边，不写回扁平行（那由 SessionPlan
    ``_mark_progress`` 负责；本投影下次读取时重算）。
    """
    # 1) unavailable 传播（固定点：blocked 判定依赖其它节点的不可用态）。
    for _round in range(len(graph.nodes) + 1):
        changed = False
        for node in graph.nodes:
            if node.status in (PlanNodeStatus.unavailable, PlanNodeStatus.skipped):
                continue
            unsatisfied = [
                d for d in node.depends_on
                if not _dep_satisfied(graph, d)
            ]
            if not unsatisfied:
                continue
            blocking = []
            for d in unsatisfied:
                dep = graph.node(d)
                if dep is not None and dep.status == PlanNodeStatus.unavailable and not _dep_satisfied(graph, d):
                    blocking.append(d)
            if blocking:
                if node.optional:
                    node.status = PlanNodeStatus.skipped
                    node.notes.append(
                        "skipped: unavailable optional deps " + ",".join(blocking)
                    )
                else:
                    node.status = PlanNodeStatus.unavailable
                    node.blocked_by = blocking
                    node.notes.append(
                        "unavailable: blocked_by " + ",".join(blocking)
                    )
                changed = True
        if not changed:
            break

    # 2) optional 且自身 unavailable → skipped（不阻塞 mandatory 图；下游把
    # skipped 视为满足，报告里仍可见）。
    for node in graph.nodes:
        if node.status == PlanNodeStatus.unavailable and node.optional and not node.blocked_by:
            node.status = PlanNodeStatus.skipped
            node.notes.append("skipped: optional capability unavailable")

    # 3) ready 派生：pending 且依赖全满足。
    for node in graph.nodes:
        if node.status != PlanNodeStatus.pending:
            continue
        if all(_dep_satisfied(graph, d) for d in node.depends_on):
            node.status = PlanNodeStatus.ready
        else:
            node.input_refs = [
                graph.node(d).bound_ref
                for d in node.depends_on
                if graph.node(d) is not None and graph.node(d).bound_ref  # type: ignore[union-attr]
            ]


def recommended_next(graph: PlanGraph) -> str:
    """确定性推荐：首个 ready 节点（依赖最少的优先，同数取声明序）。"""
    ready = [n for n in graph.nodes if n.status == PlanNodeStatus.ready]
    if not ready:
        return ""
    ready.sort(key=lambda n: (len(n.depends_on), n.node_id))
    return ready[0].capability


def project_graph_block(graph: PlanGraph, *, budget: int = 10) -> str:
    """有界 [GIS Plan] 投影（Phase F：给 Pi 的下一轮上下文，≤budget 行）。"""
    if not graph.nodes:
        return ""
    lines: List[str] = []
    ready = graph.ready_nodes()
    waiting = graph.waiting_nodes()
    done = graph.completed_nodes()
    unavailable = graph.unavailable_nodes()
    skipped = [n for n in graph.nodes if n.status == PlanNodeStatus.skipped]

    def _cap(items: List[str], limit: int) -> str:
        shown = ",".join(items[:limit]) or "none"
        extra = f" (+{len(items) - limit} more)" if len(items) > limit else ""
        return shown + extra

    lines.append(f"[GIS Plan] nodes={len(graph.nodes)}")
    lines.append(f"Ready: {_cap(ready, 6)}")
    wait_desc = [
        f"{n.capability} <- {','.join(n.depends_on)}" if n.depends_on else n.capability
        for n in waiting[:4]
    ]
    suffix = f" (+{len(waiting) - 4} more)" if len(waiting) > 4 else ""
    lines.append(f"Waiting: {','.join(wait_desc) or 'none'}{suffix}")
    lines.append(f"Completed: {_cap(done, 6)}")
    if unavailable:
        un = [
            f"{n.capability}" + (f" (fallback: {n.fallback_to})" if n.fallback_to else "")
            for n in unavailable[:4]
        ]
        lines.append("Unavailable: " + ",".join(un))
    if skipped:
        lines.append("Skipped: " + _cap([n.capability for n in skipped], 4))
    nxt = recommended_next(graph)
    if nxt:
        lines.append(f"Recommended next: {nxt}")
    if graph.dropped_cycle_edges:
        lines.append(f"Note: {len(graph.dropped_cycle_edges)} cycle edge(s) dropped")
    return "\n".join(lines[:budget])


__all__ = [
    "PlanNode",
    "PlanGraph",
    "PlanNodeStatus",
    "PlanGraphError",
    "build_plan_graph",
    "infer_dependency_edges",
    "recommended_next",
    "project_graph_block",
]
