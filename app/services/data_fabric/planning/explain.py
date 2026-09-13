"""Deterministic plan explanation (DS3, ADR-0173).

``explain_plan`` renders the human-readable plan description used in logs and
the debug surface. Pure function of the plan — same plan, same text (the
explain snapshot test locks the format).
"""
from __future__ import annotations

from typing import List

from app.services.data_fabric.contracts import AcquisitionPlan

_STEP_LABELS = {
    "source_select": "选择源",
    "version_pin": "版本锁定",
    "bbox_clip": "空间裁剪",
    "time_filter": "时间过滤",
    "field_projection": "字段投影",
    "aggregate_pushdown": "聚合下推",
    "pagination": "分页拉取",
    "sampling": "抽样",
}


def explain_plan(plan: AcquisitionPlan) -> List[str]:
    """Deterministic line-by-line plan explanation (no timestamps)."""
    lines: List[str] = [
        f"计划 {plan.plan_id} · 数据集 {plan.dataset_key} · 版本 {plan.version}",
    ]
    for i, step in enumerate(plan.steps, 1):
        label = _STEP_LABELS.get(step.step_type, step.step_type)
        detail = []
        if step.params.get("bbox"):
            detail.append(f"bbox={step.params['bbox']}")
        if step.params.get("range"):
            detail.append(f"range={step.params['range']}")
        if step.params.get("columns"):
            detail.append(f"columns={','.join(step.params['columns'])}")
        if step.params.get("aggregate"):
            detail.append(f"agg={step.params['aggregate']}")
        if "pushed_down" in step.params:
            detail.append("下推" if step.params["pushed_down"] else "本地")
        if step.params.get("rate") is not None:
            detail.append(f"rate={step.params['rate']}")
        if step.params.get("pin"):
            detail.append(f"pin={step.params['pin']}")
        lines.append(f"  {i}. {label}" + (f"（{'；'.join(detail)}）" if detail else ""))
    c = plan.cost_estimate
    lines.append(
        f"  代价估算：{c.rows if c.rows is not None else '?'} 行 / "
        f"{c.bytes if c.bytes is not None else '?'} 字节 / "
        f"{c.latency_ms if c.latency_ms is not None else '?'} ms"
        + (f" / {c.quota} 请求" if c.quota is not None else "")
        + "（provisional，DS8 校准）"
    )
    if plan.budget is not None:
        lines.append(
            f"  预算：rows≤{plan.budget.max_rows} bytes≤{plan.budget.max_bytes} "
            f"ms≤{plan.budget.max_ms} quota≤{plan.budget.max_quota}"
        )
    return lines


__all__ = ["explain_plan"]
