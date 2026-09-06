"""Composition Packs V3 — 组合模板域包契约测试（ADR-0101 D1）。

锁定：
- pack 加载确定性、seed 不变、id 唯一；
- fallback 链可解析且无环（图遍历）；
- compatible_map_models 全部可解析（无虚构模型契约）；
- resolver 对新模型选中正确的域包模板；对既有模型的选型不漂移；
- PDF 目标模板不得 require 仅 interactive 的组件（披露族/表格）；
- resolver+composer+validate 端到端：域包模板产出的组合通过组合校验。
"""
from __future__ import annotations

import pytest

from app.lib.cartography.component_registry import get_component_registry
from app.lib.cartography.composition_packs import COMPOSITION_PACK_TEMPLATES
from app.lib.cartography.composition_templates import (
    SEED_COMPOSITION_TEMPLATES,
    get_composition_template_registry,
)
from app.lib.cartography.model_library import get_map_model_registry
from app.services.gis_harness.component_composer import ComponentComposer
from app.services.gis_harness.component_resolver import ComponentResolver

pytestmark = pytest.mark.cartography

# V4 起 table_panel 也落地 canvas 导出（drawChromeTable 有界快照），
# interactive-only 集合清空；守卫保留 —— 未来新增仅 interactive 组件时
# 在此登记，防止 pdf 模板 required 槽引用不可导出组件。
PDF_EXPORT_ONLY_TYPES: set[str] = set()


def test_pack_loads_and_seed_intact() -> None:
    reg = get_composition_template_registry()
    for tpl in SEED_COMPOSITION_TEMPLATES:
        assert reg.get(tpl.id) is not None, f"seed template {tpl.id} 丢失"
    assert reg.count == len(SEED_COMPOSITION_TEMPLATES) + len(COMPOSITION_PACK_TEMPLATES)
    assert len(COMPOSITION_PACK_TEMPLATES) >= 18


def test_pack_ids_unique() -> None:
    ids = [t.id for t in COMPOSITION_PACK_TEMPLATES]
    assert len(ids) == len(set(ids))
    seed_ids = {t.id for t in SEED_COMPOSITION_TEMPLATES}
    assert not seed_ids & set(ids)


def test_fallback_chains_resolve_and_acyclic_for_packs() -> None:
    """pack 模板 fallback 可解析，且不参与任何环。

    seed 的 minimal↔standard 互为 fallback 是既有设计（resolver 不沿链
    行走，无死循环风险）—— seed 内部的环豁免；pack 模板若落入环
    （无论环里是否有其他 pack 模板）即失败：pack 必须以 seed 环外模板
    或明确出口收尾。
    """
    reg = get_composition_template_registry()
    pack_ids = {t.id for t in COMPOSITION_PACK_TEMPLATES}
    for tpl in reg.all_templates():
        if tpl.id not in pack_ids:
            continue
        order = [tpl.id]
        cur = tpl
        while cur.fallback_template:
            nxt = reg.get(cur.fallback_template)
            assert nxt is not None, f"{cur.id}: fallback '{cur.fallback_template}' 不存在"
            if nxt.id in order:
                # 真环 = 从首次访问处到当前路径尾部；只有环内含 pack 模板
                # 才算 pack 引入的新环（走入 seed 内部环允许 —— 出口存在）。
                start = order.index(nxt.id)
                cycle = order[start:]
                assert not (pack_ids & set(cycle)), (
                    f"{tpl.id}: pack 模板参与 fallback 环 {cycle}"
                )
                break
            order.append(nxt.id)
            cur = nxt


def test_compatible_models_resolve() -> None:
    model_reg = get_map_model_registry()
    for tpl in COMPOSITION_PACK_TEMPLATES:
        for mid in tpl.compatible_map_models:
            assert model_reg.resolve(mid) is not None, (
                f"{tpl.id}: compatible_map_model '{mid}' 未注册")


def test_pdf_templates_never_require_interactive_only_components() -> None:
    for tpl in COMPOSITION_PACK_TEMPLATES:
        if "pdf" not in tpl.output_targets:
            continue
        for slot in tpl.component_slots:
            if slot.cardinality != "required":
                continue
            interactive_only = [t for t in slot.allowed_component_types
                                if t in PDF_EXPORT_ONLY_TYPES]
            assert not interactive_only, (
                f"{tpl.id} slot {slot.id}: required 槽含仅 interactive 组件 "
                f"{interactive_only}（pdf 目标下必然 required_slot_missing）"
            )


def test_resolver_picks_domain_template_for_new_models() -> None:
    reg = get_composition_template_registry()
    resolver = ComponentResolver()
    expectations = {
        "risk_exposure_classes": "composition.risk_exposure_report",
        "equity_assessment": "composition.equity_assessment_report",
        "zoning_planning": "composition.zoning_plan_map",
        "change_comparison_map": "composition.temporal_change_report",
        "site_selection_result": "composition.site_selection_decision",
        # mcda 同时被 site_selection_decision(52)/mcda_score_report(53) 兼容，
        # priority 优先 → site_selection_decision（但兼容性成立即可）
        "mcda_score_map": "composition.site_selection_decision",
        "flow_od_arc": "composition.flow_map_report",
        "service_area_overlay": "composition.service_area_report",
        "terrain_analytical_surface": "composition.terrain_analysis",
        "spectral_index_surface": "composition.rs_index_report",
        "graduated_line": "composition.flow_map_report",
        # 无 specific 模板的新模型：pdf 目标下落到首个 generic 兼容模板
        # （minimal_interactive 无 pdf、presentation 无 pdf → report_map）
        "graduated_point": "composition.report_map",
        "categorized_point": "composition.report_map",
    }
    for model, expected in expectations.items():
        sel = resolver.resolve(map_model_id=model, output_target="pdf")
        chosen = sel.composition_template_id
        tpl = reg.get(chosen)
        assert tpl is not None and (
            chosen == expected or model in tpl.compatible_map_models
        ), f"{model}: 选中 {chosen}，期望 {expected}"


def test_resolver_selection_unchanged_for_legacy_models() -> None:
    resolver = ComponentResolver()
    # legacy 密度模型仍优先 density_map（density_pref 规则不漂移）
    sel = resolver.resolve(map_model_id="visual_heatmap", output_target="interactive")
    assert sel.composition_template_id == "composition.density_map"
    # administrative_choropleth + interactive：specific 中 priority 最小者
    # （standard_analysis 20 < statistical_map 28 < statistical_report 36）
    sel = resolver.resolve(map_model_id="administrative_choropleth", output_target="interactive")
    assert sel.composition_template_id == "composition.standard_analysis"
    # 新增统计报告模板不得抢占既有 interactive 选型
    sel = resolver.resolve(map_model_id="aggregate_grid", output_target="interactive")
    assert sel.composition_template_id == "composition.standard_analysis"


def test_end_to_end_composition_passes_validation() -> None:
    """域包模板 × 新模型：resolve → compose → validate 端到端无 error。"""
    from app.lib.cartography.composition_validation import validate_component_composition

    resolver = ComponentResolver()
    composer = ComponentComposer()
    comp_reg = get_component_registry()

    cases = [
        ("risk_exposure_classes", "composition.risk_exposure_report"),
        ("equity_assessment", "composition.equity_assessment_report"),
        ("zoning_planning", "composition.zoning_plan_map"),
        ("site_selection_result", "composition.site_selection_decision"),
        ("terrain_analytical_surface", "composition.terrain_analysis"),
        ("zoning_planning", "composition.government_report_map"),
    ]
    for model, template_id in cases:
        sel = resolver.resolve(
            composition_template_id=template_id, map_model_id=model,
            output_target="pdf",
        )
        assert sel.composition_template_id == template_id
        components = composer.compose(
            sel,
            title_text="域包端到端用例",
            subtitle_text="V3 composition packs",
            layer_bindings={"primary": "layer-main"},
            layer_model_ids={"layer-main": model},
        )
        # 无 planned 组件实例
        for c in components:
            desc = comp_reg.get_by_type(c.type)
            assert desc is None or desc.runtime_status == "native"
        result = validate_component_composition(
            components, composition_template_id=template_id,
            map_model_id=model, layer_ids=["layer-main"],
            output_target="pdf", layer_model_ids={"layer-main": model},
        )
        assert result.ok, (
            f"{model} × {template_id}: {[(v.code, v.detail) for v in result.errors]}"
        )


def test_template_variant_preferences_resolve() -> None:
    """域包模板引用的 V3 变体模板（government/neatline/kpi 等）被正确选中。"""
    resolver = ComponentResolver()
    sel = resolver.resolve(
        composition_template_id="composition.government_report_map",
        map_model_id="administrative_choropleth", output_target="pdf",
    )
    assert sel.component_templates.get("title") == "title/government"
    assert sel.component_templates.get("map_border") == "frame/neatline"
    assert sel.component_templates.get("scale_bar") == "scale-bar/dual-unit"
    assert sel.component_templates.get("north_arrow") == "north-arrow/monochrome"
