"""结构化对比专报（ADR-0193 §D4）——JSON 矩阵 + 中文 markdown 双面。

单一数据源：:class:`prescriptive_advisor.ScenarioComparison`。
自洽纪律：渲染层**只做格式化，不做二次计算** —— markdown 表格中的每个
数值都可由 JSON 面的 ``metric_matrix`` / ``metric_delta_pct`` 复算。

markdown 面结构（结构化对比专报，对齐 spatial_decision/report_integration
先例）：
1. 假设清单（反事实问句）；
2. 效益对比矩阵（指标 × 方案；缺值渲染「—」；Δ% 带 +/- 号）；
3. 几何差分摘要（added/removed/modified 计数 + 覆盖增益/损失）；
4. 处方性建言（推荐方案、因果链、ROI 敏感度表、实施优先级表）；
5. 缺口与假设披露（evidence_gap_note 汇总，GIS-03 纪律）。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

_PRECISION = 2
_MISSING = "—"


def _fmt_value(value: Optional[float]) -> str:
    if value is None:
        return _MISSING
    return f"{float(value):.{_PRECISION}f}"


def _fmt_delta(value: Optional[float]) -> str:
    if value is None:
        return _MISSING
    return f"{float(value):+.{_PRECISION}f}%"


def build_comparison_report(comparison: Any) -> Dict[str, Any]:
    """ScenarioComparison → 结构化专报 JSON（供渲染与存档）。"""
    branches = list(getattr(comparison, "branches", []) or [])
    advice = comparison.advice
    gaps: List[str] = []
    metric_delta_pct: Dict[str, Dict[str, Optional[float]]] = {}
    metric_names: Dict[str, str] = {}
    for branch in branches:
        for metric in branch.metric_deltas:
            metric_delta_pct.setdefault(metric.metric_key, {})[branch.branch_id] = (
                metric.delta_pct
            )
            metric_names.setdefault(metric.metric_key, metric.metric_name)
            if metric.missing_baseline or (
                metric.baseline is None and metric.simulated is None
            ):
                note = metric.evidence_gap_note or "基线证据缺失（GIS-03）"
                gaps.append(f"{branch.branch_id}:{metric.metric_key}: {note}")

    return {
        "report_id": f"whatif-{uuid.uuid4().hex[:12]}",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schema": "whatif_comparison_report/v1",
        "parent_session_id": comparison.parent_session_id,
        "baseline_revision": comparison.baseline_revision,
        "baseline_fingerprint": comparison.baseline_fingerprint,
        "branches": [
            {
                "branch_id": b.branch_id,
                "title": b.title,
                "hypothesis": b.hypothesis,
                "cost_proxy": b.cost_proxy,
                "model": b.model,
            }
            for b in branches
        ],
        "metric_matrix": comparison.metric_matrix,
        "metric_delta_pct": metric_delta_pct,
        "metric_names": metric_names,
        "geometry_summary": {
            b.branch_id: b.geometry_summary for b in branches
        },
        "advice": advice.model_dump(),
        "gaps": gaps,
    }


def render_comparison_markdown(report: Dict[str, Any]) -> str:
    """专报 JSON → 中文 markdown（只格式化，不复算）。"""
    branches: List[Dict[str, Any]] = report.get("branches") or []
    branch_ids = [b["branch_id"] for b in branches]
    title_by_id = {b["branch_id"]: (b.get("title") or f"方案 {b['branch_id']}") for b in branches}
    matrix: Dict[str, Dict[str, Any]] = report.get("metric_matrix") or {}
    delta_pct: Dict[str, Dict[str, Any]] = report.get("metric_delta_pct") or {}
    metric_names: Dict[str, str] = report.get("metric_names") or {}
    advice: Dict[str, Any] = report.get("advice") or {}
    gaps: List[str] = report.get("gaps") or []

    lines: List[str] = ["# What-If 多方案对比专报", ""]

    # 一、假设清单
    lines.append("## 一、假设清单")
    lines.append("")
    for branch in branches:
        hypothesis = branch.get("hypothesis") or "（未填写反事实问句）"
        lines.append(f"- {title_by_id[branch['branch_id']]}：{hypothesis}")
    lines.append("")

    # 二、效益对比矩阵
    lines.append("## 二、效益对比矩阵")
    lines.append("")
    header = "| 指标 | Baseline | " + " | ".join(
        title_by_id[bid] for bid in branch_ids
    ) + " | " + " | ".join(f"{bid} Δ%" for bid in branch_ids) + " |"
    lines.append(header)
    columns = len(branch_ids) * 2 + 2
    lines.append("|" + "---|" * columns)
    for metric_key, row in matrix.items():
        display_name = metric_names.get(metric_key, metric_key)
        cells = [_fmt_value(row.get("baseline"))]
        cells += [_fmt_value(row.get(bid)) for bid in branch_ids]
        delta_row = delta_pct.get(metric_key, {})
        cells += [_fmt_delta(delta_row.get(bid)) for bid in branch_ids]
        lines.append(f"| {display_name} | " + " | ".join(cells) + " |")
    lines.append("")

    # 三、几何差分摘要
    lines.append("## 三、几何差分摘要")
    lines.append("")
    geometry_summary: Dict[str, Any] = report.get("geometry_summary") or {}
    for branch in branches:
        bid = branch["branch_id"]
        summary = geometry_summary.get(bid) or {}
        layer_parts: List[str] = []
        for layer_id, layer_summary in summary.items():
            if layer_id.startswith("_") or not isinstance(layer_summary, dict):
                continue
            layer_parts.append(
                f"{layer_id}(+{layer_summary.get('added', 0)}/"
                f"-{layer_summary.get('removed', 0)}/"
                f"~{layer_summary.get('modified', 0)})"
            )
        coverage = summary.get("_coverage") or {}
        coverage_txt = ""
        if coverage:
            coverage_txt = (
                f"；覆盖增益 {coverage.get('gained_area_m2', 0.0):,.2f} m²，"
                f"覆盖损失 {coverage.get('lost_area_m2', 0.0):,.2f} m²"
            )
        layer_txt = "、".join(layer_parts) if layer_parts else "无几何变更图层"
        lines.append(f"- {title_by_id[bid]}：{layer_txt}{coverage_txt}")
    lines.append("")

    # 四、处方性建言
    lines.append("## 四、处方性建言")
    lines.append("")
    recommended = advice.get("recommended_branch_id")
    if recommended:
        rec_title = title_by_id.get(recommended, f"方案 {recommended}")
        lines.append(
            f"**推荐方案：{rec_title}**（置信度 {advice.get('confidence', 0.0):.2f}，"
            f"模式 {advice.get('mode', 'deterministic')}）"
        )
    else:
        lines.append("**无推荐方案**（缺少可评价指标）")
    lines.append("")
    chain = advice.get("rationale_causal_chain") or []
    if chain:
        lines.append("因果链：")
        for step in chain:
            lines.append(f"1. {step}")
        lines.append("")

    roi_rows = advice.get("roi_sensitivity") or []
    if roi_rows:
        lines.append("### ROI 敏感度")
        lines.append("")
        lines.append("| 方案 | 指标 | Δ% | 成本代理 | ROI |")
        lines.append("|---|---|---|---|---|")
        for row in roi_rows:
            lines.append(
                f"| {row.get('branch_id', '')} | {row.get('metric_key', '')} "
                f"| {_fmt_delta(row.get('delta_pct'))} "
                f"| {row.get('cost_proxy', 0.0):.1f} "
                f"| {row.get('roi_index', 0.0):+.4f} |"
            )
        lines.append("")

    priority_rows = advice.get("implementation_priority") or []
    if priority_rows:
        lines.append("### 实施优先级")
        lines.append("")
        for row in priority_rows:
            bid = row.get("branch_id", "")
            lines.append(
                f"{row.get('priority', 0)}. {title_by_id.get(bid, bid)} —— "
                f"{row.get('rationale', '')}"
            )
        lines.append("")

    narrative = advice.get("narrative") or ""
    if narrative:
        lines.append("建议叙述：")
        lines.append("")
        for para in narrative.splitlines():
            lines.append(f"> {para}")
        lines.append("")

    # 五、缺口与假设披露
    lines.append("## 五、缺口与假设披露")
    lines.append("")
    if gaps:
        for gap in gaps:
            lines.append(f"- {gap}")
    else:
        lines.append("- 无缺口：全部指标具备基线证据。")
    lines.append("")
    fingerprint = report.get("baseline_fingerprint") or ""
    if fingerprint:
        lines.append(f"基线指纹：`{fingerprint}`")
    return "\n".join(lines)
