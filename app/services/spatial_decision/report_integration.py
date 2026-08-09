"""
Spatial Decision Intelligence Report Integration.
Extends report pipeline to compile structured SpatialDecisionResult and ScenarioComparisonResult
into publication-quality decision reports with high-resolution vector maps and audit lineage.
"""
import logging
from datetime import datetime, timezone

from app.services.spatial_decision.models import (
    SpatialDecisionResult,
    ScenarioComparisonResult,
)

logger = logging.getLogger(__name__)


def generate_decision_report_markdown(result: SpatialDecisionResult) -> str:
    """Generate comprehensive Markdown text for a Spatial Decision Result."""
    lines = []
    scen = result.scenario
    area = result.target_area

    lines.append(f"# 空间决策模拟评估报告：{scen.name}")
    lines.append(f"**生成时间**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    lines.append(f"**决策编号**: `{result.decision_id}` | **总体置信度**: {result.confidence * 100:.1f}%\n")

    lines.append("## 1. 决策背景与目标区域")
    lines.append(f"- **场景描述**: {scen.description}")
    lines.append(f"- **目标区域**: {area.resolved_name} (来源: `{area.source}`, 解析置信度: {area.confidence * 100:.1f}%)")
    if area.center:
        lines.append(f"- **中心坐标**: [{area.center[0]:.4f}, {area.center[1]:.4f}] (WGS84)")
    if area.correction_hint:
        lines.append(f"- **区域提示**: {area.correction_hint}")
    lines.append("")

    lines.append("## 2. 空间影响区域评估")
    lines.append("| 影响层级 | 覆盖半径 (m) | 影响面积 (km²) | 强度评级 |")
    lines.append("| :--- | :---: | :---: | :---: |")
    for zone in result.spatial_impacts:
        r_str = f"{zone.radius_m:.0f}" if zone.radius_m else "全域"
        lines.append(f"| {zone.zone_type.title()} Zone | {r_str} | {zone.area_km2:.2f} | {zone.impact_level.upper()} |")
    lines.append("")

    lines.append("## 3. 关键指标推演与不确定性区间")
    lines.append("| 指标名称 | 基线值 | 模拟期望值 | 不确定性区间 [Min, Max] | 变化幅度 (Δ%) | 基线状态 |")
    lines.append("| :--- | :---: | :---: | :---: | :---: | :---: |")
    for m_key, m in result.metrics.items():
        rng_str = f"[{m.range.min_val:.1f}, {m.range.max_val:.1f}]" if m.range else "-"
        status_str = "⚠️ 数据缺失" if m.missing_baseline else "✅ 真实基线"
        # GIS-03: metrics without a real baseline are unsimulated (None).
        # Render "—" for the numeric cells instead of crashing on f"{None:.2f}".
        if m.missing_baseline or m.baseline is None:
            base_str = "—" if m.baseline is None else f"{m.baseline:.2f}"
            sim_str = "—" if m.simulated is None else f"{m.simulated:.2f}"
            delta_str = "—" if m.delta_pct is None else f"{m.delta_pct:+.2f}%"
            lines.append(f"| {m.metric_name} ({m.unit}) | {base_str} | {sim_str} | {rng_str} | {delta_str} | {status_str} |")
        else:
            lines.append(f"| {m.metric_name} ({m.unit}) | {m.baseline:.2f} | {m.simulated:.2f} | {rng_str} | {m.delta_pct:+.2f}% | {status_str} |")
    lines.append("")

    lines.append("## 4. 规则应用与证据链 (Evidence Chain)")
    lines.append("### 应用的领域规则:")
    for rule in result.rules_applied:
        lines.append(f"- **[{rule.domain.upper()}] {rule.name}** (`{rule.id}`): {rule.statement} (置信度: {rule.confidence})")
    
    lines.append("\n### 证据链条 (Evidence Audit Chain):")
    for ev in result.evidence_chain:
        lines.append(f"1. **[{ev.type.upper()}]** ({ev.domain}): {ev.statement} — *来源: {ev.source}*")
    lines.append("")

    lines.append("## 5. 假设与不确定性分析")
    if result.assumptions:
        lines.append("### 显式假设:")
        for asm in result.assumptions:
            lines.append(f"- {asm}")
    lines.append(f"\n{result.uncertainty_description}\n")

    lines.append("## 6. 决策建议 (Recommendations)")
    for rec in result.recommendations:
        lines.append(f"- {rec}")
    lines.append("")

    lines.append("## 7. 数据溯源 (Provenance)")
    for k, v in result.provenance.items():
        lines.append(f"- **{k}**: `{v}`")

    return "\n".join(lines)


def generate_comparison_report_markdown(comparison: ScenarioComparisonResult) -> str:
    """Generate comprehensive Markdown text for a Scenario Comparison Result."""
    lines = []
    lines.append("# 多方案空间情景对比评估报告")
    lines.append(f"**对比编号**: `{comparison.comparison_id}` | **评估方案数**: {len(comparison.scenarios)}\n")

    lines.append("## 1. 推荐方案与决策结论")
    lines.append(f"**推荐选址/方案 ID**: `{comparison.recommended_scenario_id}`")
    lines.append(f"\n{comparison.recommendation_rationale}\n")

    lines.append("## 2. 多方案指标对比矩阵 (Metric Matrix)")
    
    # Build header
    scen_ids = [s.scenario.scenario_id for s in comparison.scenarios]
    scen_names = [s.scenario.name for s in comparison.scenarios]
    header = "| 指标名称 | " + " | ".join(scen_names) + " |"
    sep = "| :--- | " + " | ".join([":---:" for _ in scen_names]) + " |"
    lines.append(header)
    lines.append(sep)

    for m_key, scen_dict in comparison.metric_matrix.items():
        vals = [f"{scen_dict.get(sid, 0.0):.2f}" for sid in scen_ids]
        lines.append(f"| {m_key} | " + " | ".join(vals) + " |")
    lines.append("")

    lines.append("## 3. 空间影响覆盖面积对比")
    for sid, area_km2 in comparison.affected_area_comparison.items():
        scen_obj = next(s for s in comparison.scenarios if s.scenario.scenario_id == sid)
        lines.append(f"- **{scen_obj.scenario.name}** (`{sid}`): 影响总面积 **{area_km2:.2f} km²**")
    lines.append("")

    lines.append("## 4. 权衡分析 (Trade-Off Analysis)")
    for to in comparison.trade_offs:
        lines.append(f"- {to}")
    lines.append(f"\n- **Pareto 非劣方案集合**: `{', '.join(comparison.pareto_optimal_scenarios)}`")

    return "\n".join(lines)
