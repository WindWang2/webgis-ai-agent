"""Component Variant Library V3 — 变体一等公民契约测试（ADR-0101 D3）。

锁定：
- descriptor.variants ↔ ComponentTemplate 双向覆盖（native 模板变体全部
  在 descriptor 词表内；descriptor 词表的每个变体都有模板条目或为模板
  前瞻 planned）；
- planned 模板：目录可见、resolver 不可选；
- 缺省选型稳定：扩容不改变既有类型的首选模板（golden 兼容）；
- 模板校验：native variant 越界 / options.variant 双写漂移会被抓出。
"""
from __future__ import annotations

import pytest

from app.lib.cartography.component_registry import get_component_registry
from app.lib.cartography.component_templates import (
    SEED_COMPONENT_TEMPLATES,
    get_component_template_registry,
)
from app.services.gis_harness.component_resolver import ComponentResolver

pytestmark = pytest.mark.cartography

# V3 新增的 native 变体（渲染器已落地，前端测试锁定渲染行为）
V3_NATIVE_VARIANTS = {
    "title": ["minimal", "government"],
    "subtitle": ["report"],
    "north_arrow": ["monochrome"],
    "scale_bar": ["dual_unit"],
    "continuous_colorbar": ["scientific", "stepped"],
    "legend": ["horizontal"],
    "categorical_legend": ["horizontal"],
    "map_border": ["neatline"],
    "statistics_panel": ["kpi"],
}

V3_PLANNED_TEMPLATES = ["legend/bivariate", "legend/uncertainty", "inset-map/hierarchy-locator"]


def test_v3_native_variants_registered_in_descriptors() -> None:
    reg = get_component_registry()
    for ctype, variants in V3_NATIVE_VARIANTS.items():
        desc = reg.get_by_type(ctype)
        assert desc is not None
        for v in variants:
            assert v in desc.variants, f"{ctype}: variant '{v}' 未登记 descriptor"


def test_every_descriptor_variant_has_template_entry() -> None:
    tmpl_reg = get_component_template_registry()
    comp_reg = get_component_registry()
    for desc in comp_reg._by_id.values():
        if not desc.variants:
            continue
        type_templates = tmpl_reg.find_by_type(desc.type)
        type_variants = {t.variant for t in type_templates if t.runtime_status == "native"}
        for v in desc.variants:
            assert v in type_variants, (
                f"{desc.type}: descriptor variant '{v}' 无对应 native 模板条目"
            )


def test_native_template_variants_in_descriptor_vocabulary() -> None:
    comp_reg = get_component_registry()
    tmpl_reg = get_component_template_registry()
    issues = []
    for tpl in tmpl_reg._by_id.values():
        desc = comp_reg.get(tpl.component_type) or comp_reg.get_by_type(tpl.component_type)
        if desc is None or not desc.variants:
            continue
        if tpl.runtime_status == "native" and tpl.variant not in desc.variants:
            issues.append(f"{tpl.id}: variant '{tpl.variant}' 越界")
    assert issues == []


def test_planned_templates_registered_but_not_selectable() -> None:
    """V4：V3 的三个前瞻模板（bivariate/uncertainty/hierarchy）已随渲染链
    落地转正为 native —— 见 test_v4_former_planned_templates_promoted。
    resolver 的『planned 不可选』语义仍需成立，用合成 planned 模板验证。"""
    from app.lib.cartography.component_templates import (
        reset_component_template_registry,
    )
    tmpl_reg = get_component_template_registry()
    try:
        tmpl_reg.register(_copy_as_planned(tmpl_reg.get("legend/academic")))
        sel = ComponentResolver().resolve(
            composition_template_id="composition.standard_analysis",
            map_model_id="administrative_choropleth",
            output_target="interactive",
            # 显式点名 planned 模板 —— 也必须被拒绝
            preferred_variants={"legend": "legend/synthetic-planned"},
        )
        assert sel.component_templates.get("legend") != "legend/synthetic-planned"
        chosen = tmpl_reg.get(sel.component_templates.get("legend", ""))
        assert chosen is None or chosen.runtime_status == "native"
    finally:
        reset_component_template_registry()


def _copy_as_planned(tpl):
    return tpl.model_copy(update={"id": "legend/synthetic-planned",
                                  "runtime_status": "planned"})


def test_v4_former_planned_templates_promoted() -> None:
    """V4：原前瞻模板转正 —— native 状态、variant 在 descriptor 词表内。"""
    tmpl_reg = get_component_template_registry()
    reg = get_component_registry()
    for tid, variant in [
        ("legend/bivariate", "bivariate"),
        ("legend/uncertainty", "uncertainty"),
        ("inset-map/hierarchy-locator", "hierarchy"),
    ]:
        tpl = tmpl_reg.get(tid)
        assert tpl is not None, f"{tid} 未登记"
        assert tpl.runtime_status == "native"
        desc = reg.get_by_type(tpl.component_type)
        assert variant in desc.variants


def test_default_template_selection_unchanged_for_existing_types() -> None:
    """扩容不改变既有类型的首选模板（resolver 取 (priority, id) 首个）。"""
    tmpl_reg = get_component_template_registry()
    expected_first = {
        "north_arrow": "north-arrow/minimal-black",
        "scale_bar": "scale-bar/minimal",
        "legend": "legend/academic",
        "continuous_colorbar": "colorbar/horizontal",
        "categorical_legend": "categorical-legend/academic",
        "title": "title/academic",
        "subtitle": "subtitle/default",
        "attribution": "attribution/default",
        "map_border": "frame/minimal",
        "graticule": "graticule/light",
        "statistics_panel": "statistics-panel/default",
        "chart_panel": "chart-panel/default",
        "export_layout": "export-layout/A4-landscape",
    }
    for ctype, tid in expected_first.items():
        cands = tmpl_reg.find_by_type(ctype)
        selectable = [c for c in cands if c.runtime_status == "native"]
        assert selectable and selectable[0].id == tid, (
            f"{ctype}: 首选模板漂移 → {selectable[0].id if selectable else None}（期望 {tid}）"
        )


def test_template_validate_catches_variant_drift() -> None:
    """校验器能抓 native variant 越界与 options.variant 双写漂移。

    用独立 registry 实例（不污染全局单例 —— 该进程后续测试还要用）。
    """
    from app.lib.cartography.component_templates import ComponentTemplate, ComponentTemplateRegistry

    tmpl_reg = ComponentTemplateRegistry()
    tmpl_reg.register(ComponentTemplate(
        id="title/bad-variant", component_type="title", category="annotation.title",
        variant="nonexistent_variant", priority=999,
    ))
    tmpl_reg.register(ComponentTemplate(
        id="title/bad-options", component_type="title", category="annotation.title",
        variant="academic", default_options={"variant": "report"}, priority=999,
    ))
    issues = tmpl_reg.validate()
    assert any("title/bad-variant" in i for i in issues)
    assert any("title/bad-options" in i for i in issues)


def test_seed_template_count_grew_without_removal() -> None:
    seed_ids = {t.id for t in SEED_COMPONENT_TEMPLATES}
    tmpl_reg = get_component_template_registry()
    for tid in seed_ids:
        assert tmpl_reg.has(tid), f"既有模板 {tid} 消失"


def test_registry_validate_clean_with_v3_expansion() -> None:
    assert get_component_registry().validate() == []
    assert get_component_template_registry().validate() == []
