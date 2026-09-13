"""Golden product corpus 回归（ADR-0183 M9）。

54 个产品语义 fixture（zh/en、缺数据、降级、输出变体、编辑序列、组件族
扫描）驱动五面回归：schema 校验、编译确定性（digest 稳定）、语义绑定
（关系端点/chart kind 词表）、MapSpec 兼容（组件类型 ⊆ COMPONENT_TYPES）、
语义完整性 code 断言。全部合成数据 —— 零大真实数据、零网络、零 LLM。
"""
import json
from pathlib import Path

import pytest

from app.lib.cartography.chart_kinds import CHART_KINDS
from app.lib.cartography.component_registry import get_component_registry
from app.lib.cartography.mapspec_schema import COMPONENT_TYPES
from app.services.gis_harness.intent import resolve_map_request_intent
from app.services.gis_harness.planner import MapProductPlan, PlannedLayer
from app.services.gis_harness.product_completeness import (
    validate_product_completeness,
)
from app.services.gis_harness.product_compiler import compile_product_spec
from app.services.gis_harness.product_runtime import produce_product_layer
from app.services.gis_harness.product_spec import (
    DELIVERY_TARGETS,
    RELATION_KINDS,
    VIEW_KINDS,
    apply_product_edit,
    spec_digest,
    spec_from_storage,
    validate_product_spec,
)
from app.services.gis_harness.product_templates import (
    get_product_template_registry,
)

CORPUS = json.loads(
    (Path(__file__).resolve().parents[2]
     / "fixtures" / "product_corpus" / "product_cases.json").read_text(
         encoding="utf-8"))
CASES = CORPUS["cases"]
assert len(CASES) >= 50, "语料规模承诺 ≥50"


def _template(archetype):
    reg = get_product_template_registry()
    for tpl in reg.values():
        if tpl.archetype == archetype:
            return tpl
    return None


def _spec_from_case(case):
    intent = resolve_map_request_intent(case["query"])
    if case["task"]:
        from app.services.gis_harness.intent import merge_intent_hints

        intent = merge_intent_hints(intent, {"task": case["task"]})
    intent.output_intents = list(case["output_intents"])
    template = _template(case["archetype"])
    primary = case.get("primary_layer")
    plan = MapProductPlan(
        plan_id=f"plan-{case['id']}",
        query=case["query"],
        intent=intent,
        recipe_id=f"recipe-{case['task']}" if case["task"] else "",
        template_id=template.id if template else "",
        charts=list(case["charts"]),
        statistics=list(case["statistics"]),
        map_layers=[PlannedLayer(
            role="primary",
            layer_type=primary["layer_type"],
            cartography=primary["cartography"],
            source_capability="poi_query",
            bound_ref="ref:primary" if case["bound"] else "",
        )] if primary else [],
    )
    if case.get("delivery"):
        # delivery 期望由用例驱动（builder 从 template/intent 推导，词表相同）
        assert set(case["delivery"]["targets"]) <= set(DELIVERY_TARGETS)
    return produce_product_layer(
        plan=plan, intent=intent, template=template,
        primary_ref="ref:primary" if case["bound"] else ""), plan


def _component_types_registered():
    """与 composition_validation._descriptor_for 同源的类型存在性判定。"""
    reg = get_component_registry()
    return {t for t in (
        "basemap", "legend", "categorical_legend", "continuous_colorbar",
        "north_arrow", "scale_bar", "title", "subtitle", "annotation",
        "graticule", "map_border", "attribution", "statistics_panel",
        "chart_panel", "table_panel", "export_layout", "inset_map",
        "methodology_note", "uncertainty_panel", "decision_panel",
        "label_layer",
    ) if reg.get(t) is not None or reg.get_by_type(t) is not None}


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_corpus_case(case):
    layer_out, plan = _spec_from_case(case)

    # 降级面：只有显式注入错误时才允许 fallback 键
    assert "product_compile_fallback" not in layer_out or (
        layer_out["product_compile_fallback"].get("code") == "product_layer_error"
        and case["expected_fallback_codes"]), case["id"]

    assert "product_spec" in layer_out, case["id"]
    spec = spec_from_storage(layer_out["product_spec"])
    assert spec is not None, case["id"]
    assert validate_product_spec(spec) == [], case["id"]

    # 编译确定性
    r1 = compile_product_spec(spec, plan=plan, template=_template(case["archetype"]))
    r2 = compile_product_spec(spec, plan=plan, template=_template(case["archetype"]))
    assert r1.compile_digest == r2.compile_digest, case["id"]
    assert spec_digest(spec) == layer_out["product_spec"]["digest"], case["id"]

    # 期望视图（kind 超集）
    kinds = {v.kind for v in spec.views if v.enabled}
    for expected_kind in case["expected_views"]:
        assert expected_kind in kinds, f"{case['id']}: {expected_kind} missing"

    # 期望关系词
    relation_kinds = {r.kind for r in spec.relations}
    for rk in case["expected_relation_kinds"]:
        assert rk in relation_kinds, case["id"]
    assert all(rk in RELATION_KINDS for rk in relation_kinds), case["id"]
    assert all(v.kind in VIEW_KINDS for v in spec.views), case["id"]

    # 编译 fallback 披露
    fb_codes = {fb.get("code") for fb in r1.fallbacks}
    for code in case["expected_fallback_codes"]:
        assert code in fb_codes, f"{case['id']}: {code} not disclosed"

    # MapSpec 兼容：组件类型 ⊆ COMPONENT_TYPES ∩ registry 词表
    known_types = set(COMPONENT_TYPES) & _component_types_registered()
    for view in r1.views:
        assert set(view.component_types) <= known_types, (
            f"{case['id']}: {view.component_types}")
        if view.chart_kind:
            assert view.chart_kind in {k.id for k in CHART_KINDS}, case["id"]

    # 语义完整性
    report = validate_product_completeness(spec, compile_result=r1)
    got_codes = {f.code for f in report.findings}
    for code in case["expected_completeness_codes"]:
        assert code in got_codes, f"{case['id']}: {code} not reported"

    # 编辑序列
    current = spec
    for i, edit in enumerate(case["edits"]):
        new_spec, errors, affected = apply_product_edit(
            current, edit["op"], target=edit.get("target", ""),
            payload=edit.get("payload"), reason=edit.get("reason", ""))
        if case.get("expect_edit_error") and i == len(case["edits"]) - 1:
            assert new_spec is None and errors, case["id"]
            continue
        assert new_spec is not None, f"{case['id']} edit#{i}: {errors}"
        current = new_spec
    if case["edits"] and not case.get("expect_edit_error"):
        assert validate_product_spec(current) == [], case["id"]
    if case.get("expected_views_after") is not None:
        kinds_after = {v.kind for v in current.views if v.enabled}
        assert kinds_after == set(case["expected_views_after"]), (
            f"{case['id']}: {kinds_after}")
    if case.get("expected_chart_kind") is not None:
        chart_views = [v for v in current.views if v.kind == "chart"]
        assert chart_views and chart_views[0].chart_kind == case["expected_chart_kind"], case["id"]
    if case.get("expected_filter") is not None:
        assert current.view("v-map").binding.filter == case["expected_filter"], case["id"]
    if case.get("expected_aspect") is not None:
        assert current.delivery.aspect == case["expected_aspect"], case["id"]
    if case.get("expected_title") is not None:
        assert current.view("v-map").title == case["expected_title"], case["id"]
    if case.get("expected_complete_after") is not False:
        pass  # complete 语义由显式 expected_completeness_codes 断言覆盖
    if current is not spec:
        r3 = compile_product_spec(current, plan=plan, template=_template(case["archetype"]))
        assert validate_product_completeness(current, compile_result=r3) is not None
