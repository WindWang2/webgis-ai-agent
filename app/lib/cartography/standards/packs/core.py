"""Builtin ``core`` standards pack v1 (ADR-0200).

Twelve declarative obligations covering the standards surface the task chart
names: required components (scale/north/source), map-type declaration,
count-vs-rate, classification, palette/CVD/print legibility,
legend title+unit, label density, and source/time/uncertainty disclosure.

Severity policy (base, before profile escalation):
- ``error``   — structural honesty obligations (components, legend, source)
- ``warning`` — quality/disclosure obligations (CVD, print, unit, time, …)
- ``info``    — metadata completeness (classification method, profile declared)

Every rule references the engine that measures it; none re-implements math.
"""
from __future__ import annotations

from app.lib.cartography.standards.pack import StandardsPack
from app.lib.cartography.standards.rule import CartographicRule, RuleApplicability

_CORE_RULES: tuple = (
    CartographicRule(
        rule_id="CORE.REQUIRED_COMPONENTS",
        kind="required_components",
        severity="error",
        message="画幅必配组件缺失（图名/比例尺/指北针/署名等 §0.5 基线）。",
        fix_hint={"route": "component_autofill", "component_type": "title"},
        references=("§0.5", "engine://component_composer/required_components_for", "ADR-0200"),
    ),
    CartographicRule(
        rule_id="CORE.SOURCE_DISCLOSURE",
        kind="source_disclosure",
        severity="error",
        message="数据署名缺失或仍为占位文本（署名不可省略）。",
        applies_when=RuleApplicability(purposes=("analysis", "publication")),
        fix_hint={"route": "component_autofill", "component_type": "attribution"},
        references=("§0.5", "ADR-0200"),
    ),
    CartographicRule(
        rule_id="CORE.LEGEND_PRESENT",
        kind="legend_present",
        severity="error",
        message="专题编码在场但没有任何图例（组件、面板或 legend_spec）。",
        fix_hint={"route": "component_autofill", "component_type": "legend"},
        references=("engine://semantic_checks/LEGEND_FIELD_CONSISTENCY", "ADR-0200"),
    ),
    CartographicRule(
        rule_id="CORE.LEGEND_UNIT_DISCLOSURE",
        kind="legend_unit_disclosure",
        severity="warning",
        message="分级/连续图例未同时声明 title 与 unit（读图者无法得知数值口径）。",
        applies_when=RuleApplicability(purposes=("analysis", "publication", "briefing")),
        requires=("CORE.LEGEND_PRESENT",),
        fix_hint={"route": "advisory"},
        references=("engine://thematic_spec/build_graduated_spec", "ADR-0200"),
    ),
    CartographicRule(
        rule_id="CORE.COUNT_VS_RATE",
        kind="count_vs_rate",
        severity="warning",
        message="面分级疑似直接分级原始计数字段（choropleth 经典错误：应归一化为比率/密度）。",
        applies_when=RuleApplicability(purposes=("analysis", "publication")),
        requires=("CORE.LEGEND_PRESENT",),
        fix_hint={"route": "advisory"},
        references=("engine://semantic_checks/CLASSIFICATION_CARDINALITY", "ADR-0200"),
    ),
    CartographicRule(
        rule_id="CORE.CVD_SAFE_PALETTE",
        kind="cvd_safe_palette",
        severity="warning",
        message="色带在色觉障碍模拟下相邻可分辨性不足（CVD 上下文 ΔE 低于阈值）。",
        requires=("CORE.LEGEND_PRESENT",),
        fix_hint={"route": "quality_loop", "operation": "change_palette"},
        references=("engine://context_matrix/evaluate_cell", "ADR-0200"),
    ),
    CartographicRule(
        rule_id="CORE.PRINT_LEGIBLE_PALETTE",
        kind="print_legible_palette",
        severity="warning",
        message="色带经印刷去饱和后灰度可分辨性不足。",
        applies_when=RuleApplicability(mediums=("print",)),
        requires=("CORE.LEGEND_PRESENT",),
        fix_hint={"route": "quality_loop", "operation": "change_palette"},
        references=("engine://context_matrix/evaluate_cell", "ADR-0200"),
    ),
    CartographicRule(
        rule_id="CORE.CLASSIFICATION_DECLARED",
        kind="classification_declared",
        severity="info",
        message="分级图例未声明分类方法（quantiles/equal_interval/…），复核不可复现。",
        applies_when=RuleApplicability(purposes=("analysis", "publication")),
        requires=("CORE.LEGEND_PRESENT",),
        fix_hint={"route": "advisory"},
        references=("engine://thematic_spec/resolve_classification_decision", "ADR-0200"),
    ),
    CartographicRule(
        rule_id="CORE.LABEL_DENSITY_DECLARED",
        kind="label_density_declared",
        severity="warning",
        message="高密度点层未声明标注预算（mode/topN/maxLabels），屏幕/mobile 可读性风险。",
        applies_when=RuleApplicability(mediums=("screen", "mobile")),
        fix_hint={"route": "advisory"},
        references=("engine://label_plan", "ADR-0200"),
    ),
    CartographicRule(
        rule_id="CORE.TIME_DISCLOSURE",
        kind="time_disclosure",
        severity="warning",
        message="数据含时间语义但 spec 未携带 time 证据块（时间口径不可追溯）。",
        applies_when=RuleApplicability(purposes=("analysis", "publication", "briefing")),
        fix_hint={"route": "advisory"},
        references=("engine://quality_loop/cartographic_projection(time)", "ADR-0200"),
    ),
    CartographicRule(
        rule_id="CORE.UNCERTAINTY_DISCLOSURE",
        kind="uncertainty_disclosure",
        severity="warning",
        message="数据含不确定性语义但缺少 uncertainty_panel/methodology_note 呈现面。",
        applies_when=RuleApplicability(purposes=("analysis", "publication")),
        fix_hint={"route": "component_autofill", "component_type": "uncertainty_panel"},
        references=("ADR-0200",),
    ),
    CartographicRule(
        rule_id="CORE.THEMATIC_PROFILE_DECLARED",
        kind="thematic_profile_declared",
        severity="info",
        message="专题层在场但 spec 未声明 cartographic_profile（规则 profile 选择退回推断）。",
        fix_hint={"route": "advisory"},
        references=("ref://semantic_checks/_review_profile", "ADR-0200"),
    ),
)


def build_core_pack() -> StandardsPack:
    return StandardsPack.build(
        pack_id="core",
        version="1.0.0",
        rules=_CORE_RULES,
        description=(
            "制图规范基线包：全用途必配组件 + 分析/出版义务（count-vs-rate、"
            "CVD/print 可辨性、图例口径、时间/不确定性披露）。"
        ),
    )


__all__ = ["build_core_pack"]
