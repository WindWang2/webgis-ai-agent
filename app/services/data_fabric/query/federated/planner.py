"""V6 联邦计划入口（ADR-0118 W7）：request → EnumerationContext → 计划树。

纯函数解析（无 adapter IO）：统计/能力事实来自请求自带的提示
（``estimated_rows`` / ``stats_hints``）；adapter 探测升级由调用方
（``federation.execute_chain`` 的 engine 分派，W10）注入 SourceFacts。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.data_fabric.query.federated.enumerator import (
    EnumerationContext,
    EnumeratedPlan,
    JoinEdge,
    MAX_ALTERNATIVES as MAX_ALTS,
    SourceFacts,
    enumerate_federation,
)
from app.services.data_fabric.query.federated.logical import (
    LogicalNode,
    LogicalScan,
    _coerce_where as _parse_where,
)


def build_enumeration_context(req: Any) -> EnumerationContext:
    """FederatedChainRequest（duck-typed）→ 纯事实枚举上下文。"""
    sources = []
    for s in req.sources:
        where, where_raw = _parse_where(s.where)
        ndv: Dict[str, int] = {}
        hints = getattr(req, "stats_hints", None) or {}
        hint = hints.get(s.source_id)
        if hint is not None and getattr(hint, "column_ndv", None):
            ndv = dict(hint.column_ndv)
        unique_keys: List[str] = (
            list(hint.unique_keys)
            if hint is not None and getattr(hint, "unique_keys", None)
            else []
        )
        # V7：声明了 source_type → 注入静态默认能力矩阵（纯函数、无 IO；
        # 聚合下推证明与下推边界披露消费）。缺省 = 能力未知（保守不下推）。
        # V8：stats_hints.caps（探测后覆盖，probed 才注入）优先于静态矩阵。
        caps = None
        if hint is not None and getattr(hint, "caps", None) is not None:
            caps = hint.caps
        if caps is None:
            st = getattr(s, "source_type", None)
            if st:
                from app.services.data_fabric.query.capabilities import get_capabilities

                caps = get_capabilities(st)
        sources.append(
            SourceFacts(
                source_id=s.source_id,
                dataset_id=s.dataset_id,
                estimated_rows=s.estimated_rows,
                row_count=s.estimated_rows,
                column_ndv=ndv,
                crs=getattr(s, "srs", None),
                where=where,
                where_raw=where_raw,
                fields=list(s.fields) if s.fields else None,
                unique_keys=unique_keys,
                caps=caps,
            )
        )
    joins = []
    for idx, j in enumerate(req.joins):
        joins.append(
            JoinEdge(
                left_source_id=j.left_source_id,
                right_source_id=j.right_source_id,
                kind=j.kind,
                join_field_left=j.join_field_left,
                join_field_right=j.join_field_right,
                spatial_op=j.spatial_op,
                group_by_right=list(j.group_by_right) if j.group_by_right else None,
                aggregates=list(j.aggregates) if j.aggregates else None,
                positional_index=None
                if (j.left_source_id and j.right_source_id)
                else idx,
            )
        )
    return EnumerationContext(
        sources=sources,
        joins=joins,
        limit=req.limit,
        bbox=list(req.bbox) if getattr(req, "bbox", None) else None,
        order_strategy=getattr(req, "order_strategy", "cost"),
        estimate_basis=getattr(req, "estimate_basis", None),
    )


def plan_federation_v6(req: Any) -> EnumeratedPlan:
    """V6 计划入口：request → 成本最优连通树 + 下推决策（纯函数）。

    - fetch 窗口按角色写回 scan 节点（build 侧放宽，probe 侧 V5 链语义）；
    - 聚合下推裁决（rejected alternative）附入 EXPLAIN 披露；
    - V7（ADR-0119 W9）：R-C1 证明通过的 aggregate_join 改写为安全下推
      形态（右 scan 携带 aggregate_request；输出与本地内核逐位一致）。
    """
    ctx = build_enumeration_context(req)
    plan = enumerate_federation(ctx)
    budget = getattr(req, "budget", None)
    if budget is not None:
        plan.tree = apply_fetch_windows(plan.tree, derive_fetch_windows(ctx, budget))
    plan.tree = apply_safe_aggregate_pushdown(plan.tree, ctx)
    verdict = aggregate_pushdown_verdict(ctx)
    if verdict is not None and len(plan.alternatives) < MAX_ALTS:
        plan.alternatives.append(verdict)
    return plan


__all__ = [
    "build_enumeration_context",
    "plan_federation_v6",
    "derive_fetch_windows",
    "apply_fetch_windows",
    "aggregate_pushdown_verdict",
    "pushdown_boundary_lines",
]


# ── 下推 V6（W7）：fetch 窗口 + 下推边界解释 ────────────────────────────────


def derive_fetch_windows(ctx: EnumerationContext, budget: Any) -> Dict[str, int]:
    """每源 fetch 窗口（V5 奇偶默认：恒等映射 → 全部源 = ``req.limit``）。

    曾经的 build 侧放宽设计（窗口 → ``min(budget.max_rows, MAX_JOIN_CANDIDATES)``）
    在差分语料中造成**结果分歧**：V5 的窗口欠取会漏掉窗口外的匹配行，V6 放宽
    后会找回它们 —— 「优化器不改变结果语义」红线胜过覆盖改进。窗口放宽属于
    显式契约变更（跟随 ADR-0118 Known Limitations 的 follow-up），当前不启用。

    返回空 dict（``apply_fetch_windows`` 对空窗口为恒等）。
    """
    return {}


def apply_fetch_windows(plan_tree: LogicalNode, windows: Dict[str, int]) -> LogicalNode:
    """把窗口写回计划树的 scan 节点（返回新树；计划即执行）。"""
    from app.services.data_fabric.query.federated.logical import logical_from_dict

    if isinstance(plan_tree, LogicalScan):
        w = windows.get(plan_tree.source_id)
        if w is not None and w != plan_tree.fetch_limit:
            return plan_tree.model_copy(update={"fetch_limit": w})
        return plan_tree
    data = plan_tree.model_dump()
    for side in ("input", "left", "right"):
        child = data.get(side)
        if isinstance(child, dict) and "kind" in child:
            data[side] = apply_fetch_windows(
                logical_from_dict(child), windows
            ).model_dump()
    return logical_from_dict(data)


def aggregate_pushdown_verdict(ctx: EnumerationContext) -> Optional[Dict[str, Any]]:
    """聚合下推的语义安全裁决（EXPLAIN 诚实披露「为何不下推/为何安全」）。

    V5/V6 的 aggregate_join 语义 = **先连接后聚合**：聚合值按「左（事实表）
    优先、右回退」解析，分组键来自右行 —— 把 GROUP BY 下推到右源会统计
    **未被连接命中的右行**，语义不等价。

    V7（ADR-0119 W9）：R-C1 五条件证明通过（``aggregate_pushdown_proof``）
    的边允许安全下推 —— 右源 GROUP BY 的组值与 join-后聚合**逐位等价**
    （唯一左键 ⇒ 每右行至多一匹配；组含 join 键 ⇒ 存活组=命中组）。
    未证明的 aggregate join 维持诚实拒绝。
    """
    if not any(e.kind == "aggregate_join" for e in ctx.joins):
        return None
    from app.services.data_fabric.query.federated.enumerator import (
        aggregate_pushdown_proof,
    )

    sources_in = tuple(s.source_id for s in ctx.sources)
    proven: list = []
    for e in ctx.joins:
        if e.kind != "aggregate_join":
            continue
        proof = aggregate_pushdown_proof(e, sources_in, {s.source_id: s for s in ctx.sources})
        if proof is not None:
            proven.append(proof)
    if proven:
        return {
            "name": "aggregate_pushdown_to_source",
            "description": (
                "push GROUP BY/aggregates into the right source "
                "(safe: unique left key + group key covers join field"
                " + decomposable aggregates + source aggregation cap)"
            ),
            "feasible": True,
            "applied": True,
            "proof": proven,
            "rejected_reason": None,
        }
    return {
        "name": "aggregate_pushdown_to_source",
        "description": (
            "push GROUP BY/aggregates into the right source before joining"
        ),
        "feasible": False,
        "rejected_reason": (
            "aggregate_join aggregates over JOINED rows (left-priority field "
            "resolution); source-side GROUP BY would count unmatched right "
            "rows — semantically unsafe (no measured unique-key proof), "
            "stays local (bounded by budget)"
        ),
    }


def apply_safe_aggregate_pushdown(plan_tree: LogicalNode, ctx: EnumerationContext) -> LogicalNode:
    """把证明通过的 aggregate_join 改写为下推形态（返回新树；计划即执行）。

    改写内容：``LogicalJoin.aggregate_pushdown=True``；右 scan（scan-like
    守卫）携带 ``aggregate_request={"group_by", "aggregates"}`` —— 执行器
    拉组行后精确投影，输出形状与本地内核逐位一致。守卫失败（右非
    scan-like 等）→ 保持原节点（本地路径，诚实回退）。
    """
    from app.services.data_fabric.query.federated.enumerator import (
        aggregate_pushdown_proof,
    )
    from app.services.data_fabric.query.federated.logical import (
        LogicalJoin as LJ,
        LogicalScan as LS,
    )

    by_id = {s.source_id: s for s in ctx.sources}
    sources_in = tuple(s.source_id for s in ctx.sources)

    def _rewrite(node: LogicalNode) -> LogicalNode:
        if isinstance(node, LJ):
            new_left = _rewrite(node.left)
            new_right = _rewrite(node.right)
            updates: Dict[str, Any] = {"left": new_left, "right": new_right}
            if (
                node.join_kind == "aggregate_join"
                and isinstance(new_right, LS)
                and isinstance(new_left, LS)
                and not node.aggregate_pushdown
            ):
                # R1-C3/M4：左子树必须是**单扫描**（嵌套 join 的扇出会破坏
                # 唯一键证明）；边匹配必须逐字段一致（防同右源多边选错）。
                edge = _matching_edge(node, new_left, new_right, ctx.joins)
                if edge is not None:
                    proof = aggregate_pushdown_proof(edge, sources_in, by_id)
                    if proof is not None:
                        updates["aggregate_pushdown"] = True
                        updates["right"] = new_right.model_copy(
                            update={
                                "aggregate_request": {
                                    "group_by": list(edge.group_by_right or []),
                                    "aggregates": list(edge.aggregates or []),
                                }
                            }
                        )
            return node.model_copy(update=updates)
        for attr in ("input",):
            child = getattr(node, attr, None)
            if child is not None and hasattr(child, "canonical_dict"):
                return node.model_copy(update={attr: _rewrite(child)})
        return node

    return _rewrite(plan_tree)


def _matching_edge(node: LogicalNode, left_scan: LogicalNode, right_scan: LogicalNode, joins):
    """join 节点 → 唯一匹配的 aggregate_join 边（R1-M4：join 语义逐项相等）。

    匹配条件：左右 source_id 一致 ∧ join 字段一致 ∧ group_by（排序后）一致
    ∧ aggregates（规范 JSON 排序后）一致 —— 同右源多条边绝不猜。
    """
    import json as _json

    for e in joins:
        if e.kind != "aggregate_join":
            continue
        if e.left_source_id != left_scan.source_id:
            continue
        if e.right_source_id != right_scan.source_id:
            continue
        if e.join_field_left != node.join_field_left or e.join_field_right != node.join_field_right:
            continue
        if sorted(e.group_by_right or []) != sorted(node.group_by_right or []):
            continue
        ea = sorted(e.aggregates or [], key=lambda a: _json.dumps(a, sort_keys=True))
        na = sorted(node.aggregates or [], key=lambda a: _json.dumps(a, sort_keys=True))
        if ea != na:
            continue
        return e
    return None


def pushdown_boundary_lines(ctx: EnumerationContext) -> List[str]:
    """每源下推边界解释（声明即契约：capability → 哪些族能推/为何本地）。"""
    lines: List[str] = []
    for s in ctx.sources:
        caps = getattr(s, "caps", None)
        if caps is None:
            lines.append(
                f"source {s.source_id}: capabilities not probed; where/bbox "
                "compile per adapter contract at fetch time"
            )
            continue
        pushed = []
        local = []
        if caps.bbox_pushdown:
            pushed.append("bbox")
        else:
            local.append("bbox")
        if caps.filter_pushdown and not caps.filter_ops_local:
            pushed.append("filter")
        elif caps.filter_pushdown:
            pushed.append(
                f"filter(partial; local ops: {','.join(caps.filter_ops_local)})"
            )
        else:
            local.append("filter")
        if caps.projection_pushdown:
            pushed.append("projection")
        if caps.temporal_filter:
            pushed.append("temporal")
        else:
            local.append("temporal")
        if caps.aggregation:
            pushed.append("aggregation")
        parts = [f"source {s.source_id} ({caps.source_type}):"]
        if pushed:
            parts.append("pushdown " + ", ".join(pushed))
        if local:
            parts.append("local " + ", ".join(local))
        lines.append(" ".join(parts))
    return lines
