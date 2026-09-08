"""V6 Explain（ADR-0118 W9）：计划树 + est vs actual + 边界/物化/变换披露。

确定性文本（同输入同行文）；不含 secret/连接信息。``exec_result`` 缺省时
只渲染计划侧（dry-run/EXPLAIN 形态）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.data_fabric.query.federated.enumerator import (
    EnumerationContext,
    EnumeratedPlan,
)
from app.services.data_fabric.query.federated.logical import (
    LogicalJoin,
    LogicalLimit,
    LogicalNode,
    LogicalReproject,
    LogicalScan,
)


def _scan_line(node: LogicalScan, est: Optional[int]) -> str:
    parts = [f"scan {node.source_id}", f"dataset={node.dataset_id}"]
    if node.fetch_limit:
        parts.append(f"fetch_window={node.fetch_limit}")
    if node.fields:
        parts.append(f"projection={len(node.fields)} cols")
    if node.where is not None or node.where_raw:
        parts.append("where=pushed_or_local(per caps)")
    if node.crs:
        parts.append(f"crs={node.crs}")
    if est is not None:
        parts.append(f"est_rows={est}")
    return "  ".join(parts)


def render_tree(
    node: LogicalNode,
    *,
    est_by_source: Optional[Dict[str, int]] = None,
    depth: int = 0,
) -> List[str]:
    """先序渲染计划树（缩进表层级）。"""
    pad = "  " * depth
    est = (est_by_source or {}).get(getattr(node, "source_id", None))
    if isinstance(node, LogicalScan):
        return [f"{pad}- {_scan_line(node, est)}"]
    if isinstance(node, LogicalJoin):
        lines = [f"{pad}- join {node.join_kind}"]
        if node.join_field_left and node.join_field_right:
            lines[-1] += f"  keys={node.join_field_left}={node.join_field_right}"
        if node.spatial_op:
            lines[-1] += f"  op={node.spatial_op}"
        if node.group_by_right:
            lines[-1] += f"  group_by={','.join(node.group_by_right)}"
        lines += render_tree(node.left, est_by_source=est_by_source, depth=depth + 1)
        lines += render_tree(node.right, est_by_source=est_by_source, depth=depth + 1)
        return lines
    if isinstance(node, LogicalReproject):
        lines = [
            f"{pad}- reproject {node.from_crs}→{node.to_crs} "
            f"(placement={node.placement}; once into cache)"
        ]
        return lines + render_tree(
            node.input, est_by_source=est_by_source, depth=depth + 1
        )
    if isinstance(node, LogicalLimit):
        lines = [f"{pad}- limit {node.limit}"]
        return lines + render_tree(
            node.input, est_by_source=est_by_source, depth=depth + 1
        )
    return [f"{pad}- node kind={getattr(node, 'kind', '?')}"]


def explain_v6_lines(
    plan: EnumeratedPlan,
    ctx: Optional[EnumerationContext] = None,
    *,
    exec_result: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """V6 EXPLAIN 文本（确定性；计划侧 + 可选 actual 侧）。"""
    lines: List[str] = ["Federated Query Plan V6:"]
    lines.append(f"  plan_hash: {plan.plan_hash}")
    lines.append(f"  estimated_cost: {plan.cost:.0f}")
    lines.append("  plan_tree:")
    lines += [
        f"    {l}" if not l.startswith("  ") else l for l in render_tree(plan.tree)
    ]
    lines.append("  cost_components:")
    for k, v in plan.components.items():
        lines.append(f"    {k}: {v}")
    if plan.crs_transforms:
        lines.append("  crs_transforms:")
        for t in plan.crs_transforms:
            lines.append(
                f"    {t.get('placement')} side={t.get('transform_side')}"
                f" {t.get('from_crs')}→{t.get('to_crs')}: {t.get('reason')}"
            )
    lines.append("  pushdown_boundary:")
    if ctx is not None:
        from app.services.data_fabric.query.federated.planner import (
            pushdown_boundary_lines,
        )

        lines += [f"    {l}" for l in pushdown_boundary_lines(ctx)]
    else:
        lines.append("    (context unavailable)")
    lines.append("  network_fetch:")
    if ctx is not None:
        for s in ctx.sources:
            w = "unknown"
            lines.append(
                f"    source {s.source_id}: estimated_rows={s.estimated_rows}"
                f" fetch_window={w}"
            )
    lines.append("  materialization: build sides are materialized under the")
    lines.append("    MAX_JOIN_CANDIDATES hard cap; probe sides stream page-wise")
    if plan.alternatives:
        lines.append("  alternatives:")
        for a in plan.alternatives[:6]:
            reason = f" ({a['rejected_reason']})" if a.get("rejected_reason") else ""
            lines.append(f"    [{a.get('feasible', False)}] {a['name']}{reason}")
    if plan.warnings:
        lines.append("  warnings:")
        for w in plan.warnings:
            lines.append(f"    {w}")
    if exec_result is not None:
        lines.append("  actual:")
        lines.append(f"    rows_returned: {exec_result.get('row_count')}")
        lines.append(f"    joined_rows: {exec_result.get('joined_row_count')}")
        lines.append(f"    pages_fetched: {exec_result.get('pages_fetched')}")
        per_source = exec_result.get("per_source_rows") or {}
        for sid, n in per_source.items():
            lines.append(f"    source {sid}: fetched={n}")
        for h in exec_result.get("hop_stats") or []:
            lines.append(f"    hop {h.get('hop')}: {h}")
        for t in exec_result.get("crs_transforms_applied") or []:
            lines.append(f"    crs_applied: {t}")
        for b in exec_result.get("bloom_stats") or []:
            lines.append(f"    bloom: {b}")
        for note in exec_result.get("adaptive_observations") or []:
            lines.append(f"    adaptive: {note}")
        if exec_result.get("replans_used") is not None:
            lines.append(f"    replans_used: {exec_result.get('replans_used')}")
    return lines


__all__ = ["explain_v6_lines", "render_tree"]
