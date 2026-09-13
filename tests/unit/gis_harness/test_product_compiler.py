"""Product Compiler（ADR-0183 M4/M5）单元测试。

不变式：纯函数确定性（同输入同 digest）；explicit choice 优先；词表单源
（component_registry / chart_kinds / composition 槽位）；fallback 可解释。
"""
import pytest

from app.services.gis_harness.intent import resolve_map_request_intent
from app.services.gis_harness.planner import (
    MapProductPlan,
    PlannedLayer,
)
from app.services.gis_harness.product_compiler import (
    compile_product_spec,
    compile_digest,
)
from app.services.gis_harness.product_shapes import build_product_spec_from_plan
from app.services.gis_harness.product_spec import (
    MapProductSpec,
    ProductOverride,
    apply_product_edit,
    validate_product_spec,
)
from app.services.gis_harness.product_templates import (
    get_product_template_registry,
)


def _fixture(query: str = "成都小学分布情况，各区统计"):
    intent = resolve_map_request_intent(query)
    tpl_reg = get_product_template_registry()
    template = tpl_reg.find_for_recipe(
        "administrative_choropleth", intent.subject.category or "")
    plan = MapProductPlan(
        plan_id="plan-test",
        query=query,
        intent=intent,
        recipe_id="administrative_choropleth",
        template_id=template.id if template else "",
        charts=["admin_bar"],
        statistics=["admin_aggregation"],
        map_layers=[
            PlannedLayer(role="primary", layer_type="fill",
                         cartography="administrative_choropleth",
                         source_capability="admin_aggregation",
                         layer_id="lyr-admin", bound_ref="ref:abc"),
        ],
    )
    spec = build_product_spec_from_plan(plan, intent, template)
    assert validate_product_spec(spec) == []
    return spec, plan, template


def test_compile_deterministic_and_digest_stable():
    spec, plan, template = _fixture()
    r1 = compile_product_spec(spec, plan=plan, template=template)
    r2 = compile_product_spec(spec, plan=plan, template=template)
    assert r1.compile_digest == r2.compile_digest
    assert r1.compile_digest == compile_digest(r1)
    assert r1.ok is True
    assert r1.spec_digest


def test_compile_view_families_and_chart_requirement():
    spec, plan, template = _fixture("成都小学分布，配个柱状图")
    result = compile_product_spec(spec, plan=plan, template=template)
    by_kind = {v.kind: v for v in result.views}
    assert "chart_panel" in by_kind["chart"].component_types
    assert by_kind["map"].component_types == []
    chart_reqs = result.chart_requirements
    assert chart_reqs, "chart 视图 → 图表执行需求"
    req = chart_reqs[0]
    # spec.chart_kind 命中词表（柱状图→bar）→ 无 fallback；alias 透传
    assert req.chart_kind == "bar"
    assert req.bound_view_id == "v-map"
    assert req.dataset_ref == "ref:abc" or req.dataset_ref == ""


def test_compile_chart_alias_fallback_disclosed():
    spec, plan, template = _fixture()  # administrative_statistic → admin_bar 别名
    # plan.charts 非空 → builder 已建 chart 视图；chart_kind 为词表外别名
    chart_views = [v for v in spec.views if v.kind == "chart"]
    assert chart_views, "fixture plan.charts=['admin_bar'] → chart 视图在场"
    result = compile_product_spec(spec, plan=plan, template=template)
    chart_views_out = [v for v in result.views if v.kind == "chart"]
    assert chart_views_out and chart_views_out[0].chart_kind == "bar"
    codes = {fb.get("code") for fb in result.fallbacks}
    assert "chart_kind_unmapped" in codes
    fb = next(f for f in result.fallbacks if f["code"] == "chart_kind_unmapped")
    assert fb["from"] == "admin_bar" and fb["to"] == "bar"


def test_compile_user_override_suppresses_family():
    spec, plan, template = _fixture("成都小学分布，配个柱状图")
    spec2, errs, _ = apply_product_edit(
        spec, "toggle_component", "chart_panel",
        {"component_type": "chart_panel", "enabled": False}, "把统计图去掉")
    assert spec2 is not None and not errs
    result = compile_product_spec(spec2, plan=plan, template=template)
    chart_view = next(v for v in result.views if v.kind == "chart")
    assert "chart_panel" not in chart_view.component_types
    assert not result.chart_requirements
    assert any("suppressed by user override" in d for d in result.decisions)


def test_compile_slots_against_composition_template():
    spec, plan, template = _fixture()
    result = compile_product_spec(spec, plan=plan, template=template)
    if not result.slots:
        pytest.skip("composition template unresolved for this fixture")
    required = [s for s in result.slots if s.required]
    assert required, "统计型组合模板必有 required 槽位"
    assert all(s.satisfied for s in required), "default_components 覆盖 required 槽位"
    unsupported = [f for f in result.fallbacks
                   if f.get("code") == "required_slot_unsupported"]
    assert not unsupported


def test_compile_evidence_transcription():
    spec, plan, template = _fixture()
    result = compile_product_spec(spec, plan=plan, template=template)
    map_view = result.view("v-map")
    assert map_view is not None
    assert map_view.dataset_ref == "ref:abc"  # primary layer bound_ref 转录
    assert any(d.startswith("recipe:") for d in map_view.decisions)
    if template is not None:
        assert any(d.startswith("template:") for d in map_view.decisions)


def test_compile_disabled_view_drops_requirements():
    spec, plan, template = _fixture("成都小学分布，配个柱状图")
    spec2, errs, _ = apply_product_edit(spec, "toggle_view", "v-chart")
    assert spec2 is not None and not errs
    result = compile_product_spec(spec2, plan=plan, template=template)
    assert not result.chart_requirements, "禁用视图不产执行需求"


def test_compile_composition_unresolved_disclosed():
    spec, plan, template = _fixture()
    spec.composition_template_id = "composition.does_not_exist"
    result = compile_product_spec(spec, plan=plan, template=template)
    codes = {fb.get("code") for fb in result.fallbacks}
    assert "composition_template_unresolved" in codes


def test_compile_registry_unavailable_degrades_honestly(monkeypatch):
    spec, plan, template = _fixture("成都小学分布，配个柱状图")
    import app.services.gis_harness.product_compiler as pc

    monkeypatch.setattr(pc, "_registry_types", lambda: None)
    result = compile_product_spec(spec, plan=plan, template=template)
    codes = {fb.get("code") for fb in result.fallbacks}
    assert "component_registry_unavailable" in codes
    # 降级不阻断：chart 需求仍产出
    assert result.chart_requirements
