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
    )


def plan_federation_v6(req: Any) -> EnumeratedPlan:
    """V6 计划入口：request → 成本最优连通树 + 下推决策（纯函数）。

    - fetch 窗口按角色写回 scan 节点（build 侧放宽，probe 侧 V5 链语义）；
    - 聚合下推裁决（rejected alternative）附入 EXPLAIN 披露。
    """
    ctx = build_enumeration_context(req)
    plan = enumerate_federation(ctx)
    budget = getattr(req, "budget", None)
    if budget is not None:
        plan.tree = apply_fetch_windows(plan.tree, derive_fetch_windows(ctx, budget))
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
    """聚合下推的语义安全裁决（EXPLAIN 诚实披露「为何不下推」）。

    V5/V6 的 aggregate_join 语义 = **先连接后聚合**：聚合值按「左（事实表）
    优先、右回退」解析，分组键来自右行 —— 把 GROUP BY 下推到右源会统计
    **未被连接命中的右行**，语义不等价。因此通用聚合下推被拒绝；只有
    「聚合字段全部来自右侧且右侧行先按组去重不影响连接」的受限形态才可能
    安全（当前不存在该契约）—— 如实披露为 rejected alternative。
    """
    if not any(e.kind == "aggregate_join" for e in ctx.joins):
        return None
    return {
        "name": "aggregate_pushdown_to_source",
        "description": (
            "push GROUP BY/aggregates into the right source before joining"
        ),
        "feasible": False,
        "rejected_reason": (
            "aggregate_join aggregates over JOINED rows (left-priority field "
            "resolution); source-side GROUP BY would count unmatched right "
            "rows — semantically unsafe, stays local (bounded by budget)"
        ),
    }


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
