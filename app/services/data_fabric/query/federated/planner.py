"""V6 联邦计划入口（ADR-0118 W7）：request → EnumerationContext → 计划树。

纯函数解析（无 adapter IO）：统计/能力事实来自请求自带的提示
（``estimated_rows`` / ``stats_hints``）；adapter 探测升级由调用方
（``federation.execute_chain`` 的 engine 分派，W10）注入 SourceFacts。
"""

from __future__ import annotations

from typing import Any, Dict

from app.services.data_fabric.query.federated.enumerator import (
    EnumerationContext,
    EnumeratedPlan,
    JoinEdge,
    SourceFacts,
    enumerate_federation,
)
from app.services.data_fabric.query.federated.logical import (
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
    """V6 计划入口：request → 成本最优连通树（纯函数；typed 错误同 V5）。"""
    return enumerate_federation(build_enumeration_context(req))


__all__ = [
    "build_enumeration_context",
    "plan_federation_v6",
]
