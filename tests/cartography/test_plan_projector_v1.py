"""投影层测试（F12 / ADR-0214 D2）。

锁定：MapProductPlan/GrammarDecision → MapPlanIR 纯投影；多轮 amendment
确定性 + 锁目标 fail-closed + 未知名目标 fail-closed。
"""
import pytest

from app.lib.cartography.grammar_types import GRAMMAR_VERSION
from app.lib.cartography.grammar_solver import GrammarRequest, solve_grammar
from app.lib.cartography.plan_ir import UserLockSnapshot
from app.services.gis_harness.components import CartographyComponent
from app.services.gis_harness.intent import MapRequestIntent
from app.services.gis_harness.planner import MapProductPlan, PlannedLayer
from app.services.map_plan_compiler.plan_amendment import PlanAmendment
from app.services.map_plan_compiler.projector import amend_plan_ir, project_plan_ir


def _plan(**overrides) -> MapProductPlan:
    body = dict(
        plan_id="plan-pj01",
        query="湖北省人口密度分级设色图",
        recipe_id="choropleth_ratio",
        intent=MapRequestIntent(query="湖北省人口密度分级设色图"),
        template_id="tpl-basic",
        map_layers=[
            PlannedLayer(role="primary", layer_type="fill", cartography="choropleth",
                         bound_ref="ds:pop", enabled=True),
            PlannedLayer(role="reference", layer_type="line", cartography="boundary",
                         bound_ref="ds:boundary", enabled=True),
        ],
        components=[CartographyComponent(id="comp-title", type="title",
                                         enabled=True, position="top-center")],
        exports=["png"],
        manifest_fingerprint="mfpr-abc123",
    )
    body.update(overrides)
    return MapProductPlan(**body)


@pytest.mark.cartography
def test_projection_is_pure_and_deterministic():
    ir1 = project_plan_ir(_plan())
    ir2 = project_plan_ir(_plan())
    assert ir1 == ir2
    assert ir1.ir_id == ir2.ir_id
    assert ir1.ir_fingerprint() == ir2.ir_fingerprint()


@pytest.mark.cartography
def test_projection_maps_layers_and_expected_visibility():
    ir = project_plan_ir(_plan(map_layers=[
        PlannedLayer(role="primary", layer_type="fill", cartography="choropleth",
                     bound_ref="ds:pop", enabled=True),
        PlannedLayer(role="secondary", layer_type="circle", cartography="point_overlay",
                     bound_ref="ds:pts", enabled=False),
    ]))
    assert [li.action for li in ir.layer_intents] == [
        "present_primary", "present_secondary"]
    assert ir.layer_intents[0].expected_visible is True
    assert ir.layer_intents[1].expected_visible is False  # planner 显式禁用 → 期望隐藏
    assert ir.layer_intents[0].blueprint is not None
    assert ir.layer_intents[0].blueprint.layer_type == "fill"


@pytest.mark.cartography
def test_projection_transcribes_locks_and_exports_and_final_display():
    locks = UserLockSnapshot(layer_ids=["pl-li-02-reference"], component_ids=["comp-x"],
                             workbench_revision=7)
    ir = project_plan_ir(_plan(), user_locks=locks)
    assert ir.user_locks.layer_ids == ["pl-li-02-reference"]
    assert ir.layer_intents[1].locked is True
    assert [e.fmt for e in ir.exports] == ["png"]
    # final display 期望面：非 remove 层全部进入
    assert set(ir.final_display.expected_visible_layers) == {
        "li-01-primary", "li-02-reference"}


@pytest.mark.cartography
def test_projection_merges_grammar_obligations_with_alias_and_required_flags():
    grammar = solve_grammar(GrammarRequest(
        geometry="polygon", feature_count=120,
        fields=[],
        pinned_representation="administrative_choropleth",
    ))
    ir = project_plan_ir(_plan(), grammar=grammar)
    types = [ci.component_type for ci in ir.component_intents]
    assert "title" in types  # 来自 plan.components（不重复补）
    # grammar 义务族按 registry 词表对齐且带 required 标志
    for ci in ir.component_intents:
        if ci.intent_id.startswith("ci-g-"):
            assert ci.required == (ci.component_type in (
                "title", "legend", "scale_bar", "north_arrow", "attribution"))
    assert len(set(ci.component_id for ci in ir.component_intents)) == \
        len(ir.component_intents), "组件 id 无重复"
    # grammar authority 引用（fingerprint + version 转录）
    kinds = {a.kind: a for a in ir.authorities}
    assert kinds["grammar_decision"].fingerprint == grammar.fingerprint
    assert kinds["grammar_decision"].schema_version == GRAMMAR_VERSION
    assert "plan_compile" not in ir.ir_fingerprint()  # 无关词不进 IR（词表卫生）


@pytest.mark.cartography
def test_amend_revision_bump_and_supersede_deterministic():
    ir = project_plan_ir(_plan())
    ir2a = amend_plan_ir(ir, [PlanAmendment(kind="set_title", title="新标题")])
    ir2b = amend_plan_ir(ir, [PlanAmendment(kind="set_title", title="新标题")])
    assert ir2a == ir2b
    assert ir2a.revision == ir.revision + 1
    assert ir2a.supersedes == ir.ir_id
    assert ir2a.ir_id != ir.ir_id
    # 未触碰节点保持原样（最小演进）
    assert ir2a.layer_intents == ir.layer_intents


@pytest.mark.cartography
def test_amend_visibility_targets_existing_layer_only():
    ir = project_plan_ir(_plan())
    ir2 = amend_plan_ir(ir, [PlanAmendment(
        kind="set_layer_visibility", layer_id="pl-li-02-reference", visible=False)])
    assert ir2.layer_intents[1].action == "set_visibility"
    assert ir2.layer_intents[1].expected_visible is False
    assert ir2.final_display.expected_visible_layers["li-02-reference"] is False
    with pytest.raises(ValueError, match="未知 layer_id"):
        amend_plan_ir(ir, [PlanAmendment(
            kind="set_layer_visibility", layer_id="pl-nope", visible=False)])


@pytest.mark.cartography
def test_amend_restyle_updates_blueprint_minimally():
    ir = project_plan_ir(_plan())
    ir2 = amend_plan_ir(ir, [PlanAmendment(
        kind="restyle_layer", layer_id="pl-li-01-primary",
        paint={"fill-color": "#6a1b9a"}, classification={"k": 6, "method": "quantile"})])
    bp = ir2.layer_intents[0].blueprint
    assert bp.paint["fill-color"] == "#6a1b9a"
    assert bp.classification["k"] == 6
    assert ir2.layer_intents[0].action == "restyle"


@pytest.mark.cartography
def test_amend_add_chart_and_export_and_pin_zone():
    ir = project_plan_ir(_plan())
    ir2 = amend_plan_ir(ir, [
        PlanAmendment(kind="add_chart", chart_type="chart_panel",
                      title="人口结构", layer_id="pl-li-01-primary"),
        PlanAmendment(kind="add_export", fmt="pdf"),
        PlanAmendment(kind="pin_component_zone", component_id="comp-title", zone="top-left"),
    ])
    chart = [ci for ci in ir2.component_intents if ci.component_type == "chart_panel"]
    assert len(chart) == 1 and chart[0].title == "人口结构"
    assert [e.fmt for e in ir2.exports] == ["png", "pdf"]
    assert ir2.component_intents[0].pinned_zone == "top-left"
    assert ir2.layout.pinned_zones["comp-title"] == "top-left"
    # 重复 add_export 不产生重复义务
    ir3 = amend_plan_ir(ir2, [PlanAmendment(kind="add_export", fmt="pdf")])
    assert [e.fmt for e in ir3.exports] == ["png", "pdf"]


@pytest.mark.cartography
def test_amend_fails_closed_on_locked_targets():
    locks = UserLockSnapshot(layer_ids=["pl-li-01-primary"],
                             component_ids=["comp-title"])
    ir = project_plan_ir(_plan(), user_locks=locks)
    with pytest.raises(ValueError, match="锁定"):
        amend_plan_ir(ir, [PlanAmendment(
            kind="set_layer_visibility", layer_id="pl-li-01-primary", visible=False)])
    with pytest.raises(ValueError, match="锁定"):
        amend_plan_ir(ir, [PlanAmendment(kind="set_title", title="覆盖用户标题")])
    with pytest.raises(ValueError, match="锁定"):
        amend_plan_ir(ir, [PlanAmendment(
            kind="remove_component", component_id="comp-title")])


@pytest.mark.cartography
def test_amend_remove_marks_intent_without_touching_others():
    ir = project_plan_ir(_plan())
    ir2 = amend_plan_ir(ir, [PlanAmendment(
        kind="remove_layer", layer_id="pl-li-02-reference")])
    assert ir2.layer_intents[1].action == "remove"
    assert "li-02-reference" not in ir2.final_display.expected_visible_layers
    assert ir2.layer_intents[0] == ir.layer_intents[0]
