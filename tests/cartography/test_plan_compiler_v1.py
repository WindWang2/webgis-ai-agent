"""确定性编译器测试（F12 / ADR-0214 D3）。

锁定：同输入字节级同输出；最小 diff（paint 只携带变化键、组件只携带
变化字段、已满足目标产 no-op）；相位排序；blocked 零 mutation；
确定性 client_mutation_id。
"""
import pytest

from app.lib.cartography.plan_ir import (
    ComponentIntent,
    LayerBlueprint,
    LayerIntent,
    MapPlanIR,
    UserLockSnapshot,
)
from app.services.map_plan_compiler.compiler import compile_plan


def _ir(layer_intents=(), component_intents=(), **kw) -> MapPlanIR:
    return MapPlanIR(ir_id="mpir-comp01", layer_intents=list(layer_intents),
                     component_intents=list(component_intents), **kw)


def _blueprint(**kw) -> LayerBlueprint:
    body = dict(layer_type="fill", paint={"fill-color": "#333", "fill-opacity": 0.8})
    body.update(kw)
    return LayerBlueprint(**body)


@pytest.mark.cartography
def test_new_layer_upsert_only_for_missing_target():
    ir = _ir(layer_intents=[LayerIntent(
        intent_id="li-1", action="present_primary", layer_id="pl-new",
        source_ref="ds:pop", blueprint=_blueprint())])
    current = {"sources": {"ds:pop": {}}, "layers": [], "layout": {}}
    c = compile_plan(ir, current, base_revision=2)
    assert c.status == "compiled"
    assert len(c.mutations) == 1
    m = c.mutations[0]
    assert (m.step, m.phase, m.intent, m.target) == (1, 0, "upsert_layer", "pl-new")
    layer = m.payload["layer"]
    assert layer["id"] == "pl-new" and layer["source"] == "ds:pop"
    assert layer["type"] == "fill" and layer["visible"] is True
    assert m.client_mutation_id == f"pmc.{ir.ir_id}.01.upsert_layer"
    assert c.display_expectations.layers == {"pl-new": True}


@pytest.mark.cartography
def test_determinism_byte_identical():
    ir = _ir(layer_intents=[LayerIntent(
        intent_id="li-1", action="present_primary", layer_id="pl-new",
        source_ref="ds:pop", blueprint=_blueprint())])
    current = {"sources": {"ds:pop": {}}, "layers": [], "layout": {}}
    c1 = compile_plan(ir, current, base_revision=4)
    c2 = compile_plan(ir, current, base_revision=4)
    assert c1 == c2
    assert c1.model_dump() == c2.model_dump()
    assert c1.compile_digest == c2.compile_digest


@pytest.mark.cartography
def test_satisfied_targets_become_no_ops_not_mutations():
    ir = _ir(layer_intents=[LayerIntent(
        intent_id="li-1", action="present_primary", layer_id="pl-1",
        source_ref="ds:pop", blueprint=_blueprint())],
        component_intents=[ComponentIntent(intent_id="ci-1", component_type="title",
                                           component_id="comp-title", action="ensure")])
    current = {
        "sources": {"ds:pop": {}},
        "layers": [{"id": "pl-1", "source": "ds:pop", "type": "fill",
                    "layout": {"visibility": "visible"},
                    "paint": {"fill-color": "#333", "fill-opacity": 0.8}}],
        "layout": {"components": [{"id": "comp-title", "type": "title",
                                   "enabled": True, "position": "top-center"}]},
    }
    c = compile_plan(ir, current, base_revision=1)
    assert c.mutations == []
    targets = {n.target for n in c.no_ops}
    assert "layer:pl-1" in targets and "component:comp-title" in targets


@pytest.mark.cartography
def test_paint_delta_minimal_only_changed_keys():
    ir = _ir(layer_intents=[LayerIntent(
        intent_id="li-1", action="restyle", layer_id="pl-1", source_ref="ds:pop",
        blueprint=_blueprint(paint={"fill-color": "#6a1b9a", "fill-opacity": 0.8}))])
    current = {
        "sources": {"ds:pop": {}},
        "layers": [{"id": "pl-1", "source": "ds:pop", "type": "fill",
                    "layout": {"visibility": "visible"},
                    "paint": {"fill-color": "#333", "fill-opacity": 0.8,
                              "fill-outline-color": "#111"}}],
        "layout": {},
    }
    c = compile_plan(ir, current, base_revision=1)
    style = [m for m in c.mutations if m.intent == "patch_layer_style"]
    assert len(style) == 1
    assert style[0].payload["paint"] == {"fill-color": "#6a1b9a"}, \
        "只携带变化键；current 独有键不触碰"
    assert style[0].phase == 1


@pytest.mark.cartography
def test_visibility_only_change_is_presentation_patch():
    ir = _ir(layer_intents=[LayerIntent(
        intent_id="li-1", action="set_visibility", layer_id="pl-1",
        expected_visible=False)])
    current = {"layers": [{"id": "pl-1", "source": "ds:x", "type": "fill",
                           "layout": {"visibility": "visible"}}], "layout": {}}
    c = compile_plan(ir, current, base_revision=1)
    assert [m.intent for m in c.mutations] == ["patch_layer_presentation"]
    assert c.mutations[0].payload == {"visible": False}


@pytest.mark.cartography
def test_phases_ordered_present_patch_components_removals():
    ir = _ir(
        layer_intents=[
            LayerIntent(intent_id="li-new", action="present_primary", layer_id="pl-new",
                        source_ref="ds:pop", blueprint=_blueprint()),
            LayerIntent(intent_id="li-hide", action="set_visibility", layer_id="pl-old",
                        expected_visible=False),
        ],
        component_intents=[
            ComponentIntent(intent_id="ci-chart", component_type="chart_panel",
                            component_id="comp-chart", action="ensure", title="图"),
            ComponentIntent(intent_id="ci-old", component_type="subtitle",
                            component_id="comp-sub", action="remove"),
        ],
    )
    current = {
        "sources": {"ds:pop": {}},
        "layers": [{"id": "pl-old", "source": "ds:x", "type": "fill",
                    "layout": {"visibility": "visible"}}],
        "layout": {"components": [{"id": "comp-sub", "type": "subtitle",
                                   "enabled": True}]},
    }
    c = compile_plan(ir, current, base_revision=1)
    phases = [(m.phase, m.intent) for m in c.mutations]
    assert phases == [
        (0, "upsert_layer"),                    # 新数据层最先
        (1, "patch_layer_presentation"),        # 层修正
        (2, "patch_component"),                 # 组件补齐（chart ensure）
        (3, "remove_component"),                # 删除最后
    ]
    assert [m.step for m in c.mutations] == [1, 2, 3, 4]
    assert c.mutations[1].target == "pl-old"
    assert c.mutations[2].target == "comp-chart"


@pytest.mark.cartography
def test_component_patch_minimal_fields_and_zone_pin():
    ir = _ir(component_intents=[ComponentIntent(
        intent_id="ci-1", component_type="title", component_id="comp-title",
        action="patch", title="新标题", pinned_zone="top-left")])
    current = {"layout": {"components": [
        {"id": "comp-title", "type": "title", "enabled": True,
         "position": "top-center", "options": {"title": "旧标题"}}]}}
    c = compile_plan(ir, current, base_revision=1)
    m = c.mutations[0]
    assert m.intent == "patch_component"
    assert m.payload["options"] == {"title": "新标题"}
    assert m.payload["position"] == "top-left"
    assert "enabled" not in m.payload, "enabled 未变化不得出现"


@pytest.mark.cartography
def test_component_upsert_when_missing_with_default_zone():
    ir = _ir(component_intents=[ComponentIntent(
        intent_id="ci-g-legend", component_type="legend", component_id="comp-legend",
        action="ensure", required=True)])
    c = compile_plan(ir, {"layers": [], "layout": {}}, base_revision=1)
    m = [x for x in c.mutations if x.target == "comp-legend"][0]
    assert m.payload["upsert"] is True
    assert m.payload["component_type"] == "legend"
    assert m.payload["position"] == "top-left"
    assert m.payload["enabled"] is True
    assert "comp-legend" in c.display_expectations.components


@pytest.mark.cartography
def test_hide_absent_component_is_no_op_not_upsert():
    ir = _ir(component_intents=[ComponentIntent(
        intent_id="ci-h", component_type="legend", component_id="comp-ghost",
        action="hide")])
    c = compile_plan(ir, {"layers": [], "layout": {}}, base_revision=1)
    assert c.mutations == []
    assert c.no_ops[0].code == "TARGET_ABSENT"


@pytest.mark.cartography
def test_blocked_obligations_yield_zero_mutations():
    from app.lib.cartography.plan_ir import ExportObligation
    ir = _ir(layer_intents=[LayerIntent(
        intent_id="li-x", action="present_primary", layer_id="pl-1",
        source_ref="ds:ghost", blueprint=_blueprint())],
        exports=[ExportObligation(fmt="hologram")])
    c = compile_plan(ir, {"sources": {}, "layers": []}, base_revision=1)
    assert c.status == "blocked"
    assert c.mutations == []
    assert "OBLIGATIONS_BLOCKED" in c.reason_codes


@pytest.mark.cartography
def test_locked_target_never_reaches_mutations():
    locks = UserLockSnapshot(layer_ids=["pl-lock"])
    ir = _ir(layer_intents=[LayerIntent(
        intent_id="li-1", action="set_visibility", layer_id="pl-lock",
        expected_visible=False, locked=True)], user_locks=locks)
    c = compile_plan(ir, {"layers": [{"id": "pl-lock", "type": "fill"}]}, base_revision=1)
    assert c.status == "blocked"
    assert c.mutations == []


@pytest.mark.cartography
def test_rebind_replaces_source_via_merged_upsert():
    ir = _ir(layer_intents=[LayerIntent(
        intent_id="li-1", action="rebind", layer_id="pl-1", source_ref="ds:new")])
    current = {"sources": {"ds:new": {}},
               "layers": [{"id": "pl-1", "source": "ds:old", "type": "fill",
                           "paint": {"fill-color": "#333"}}]}
    c = compile_plan(ir, current, base_revision=1)
    m = c.mutations[0]
    assert m.intent == "upsert_layer" and m.payload.get("merge") is True
    layer = m.payload["layer"]
    assert layer["source"] == "ds:new"
    assert layer["paint"] == {"fill-color": "#333"}, "合并 upsert 保留既有表达面"


@pytest.mark.cartography
def test_classification_change_routes_to_legend_merge_upsert():
    ir = _ir(layer_intents=[LayerIntent(
        intent_id="li-1", action="restyle", layer_id="pl-1",
        blueprint=_blueprint(paint={"fill-color": "#333"},
                             classification={"k": 6, "method": "quantile"}))])
    current = {"sources": {},
               "layers": [{"id": "pl-1", "type": "fill",
                           "paint": {"fill-color": "#333"},
                           "legend_spec": {"classification": {"k": 5, "method": "quantile"}}}]}
    c = compile_plan(ir, current, base_revision=1)
    m = c.mutations[0]
    assert m.intent == "upsert_layer" and m.payload.get("merge") is True
    assert m.payload["layer"]["legend_spec"]["classification"]["k"] == 6
    assert m.payload["layer"]["paint"] == {"fill-color": "#333"}, \
        "paint 未变时合并不触碰 paint"


@pytest.mark.cartography
def test_graph_summary_and_supersedes_propagated():
    ir = _ir(supersedes="mpir-old")
    c = compile_plan(ir, {"layers": [], "layout": {}}, base_revision=1)
    assert c.supersedes == "mpir-old"
    assert isinstance(c.component_graph_summary, dict)
    assert c.compile_id.startswith("pmcc-")


@pytest.mark.cartography
def test_bound_layer_id_maps_to_options_layer_id():
    """review P2-1：binding 型组件的图层绑定不得丢弃（chart 契约走
    options.layerId）。ensure 与 patch 两分支都要携带。"""
    ci = ComponentIntent(intent_id="ci-c", component_type="chart_panel",
                         component_id="comp-chart", action="ensure",
                         bound_layer_id="pl-primary", title="结构")
    c = compile_plan(_ir(component_intents=[ci]), {"layers": [], "layout": {}},
                     base_revision=1)
    m = [x for x in c.mutations if x.target == "comp-chart"][0]
    assert m.payload["options"]["layerId"] == "pl-primary"

    ci2 = ci.model_copy(update={"action": "patch"})
    current = {"layout": {"components": [
        {"id": "comp-chart", "type": "chart_panel", "enabled": True,
         "position": "none", "options": {"title": "结构"}}]}}
    c2 = compile_plan(_ir(component_intents=[ci2]), current, base_revision=1)
    m2 = c2.mutations[0]
    assert m2.payload["options"] == {"layerId": "pl-primary"}, "只携带变化键"


@pytest.mark.cartography
def test_determinism_invariant_to_current_dict_insertion_order():
    """review P2-6：current spec 的键插入序不影响编译产物。"""
    ir = _ir(layer_intents=[LayerIntent(
        intent_id="li-1", action="restyle", layer_id="pl-1",
        blueprint=_blueprint(paint={"fill-color": "#111"}))])
    layer = {"id": "pl-1", "source": "ds:pop", "type": "fill",
             "paint": {"fill-color": "#333", "fill-opacity": 0.9},
             "legend_spec": {"items": [1, 2, 3]}}
    current_a = {"sources": {"ds:pop": {}}, "layers": [layer], "layout": {}}
    reordered_layer = {k: layer[k] for k in reversed(list(layer.keys()))}
    current_b = {"layout": {}, "layers": [reordered_layer],
                 "sources": {"ds:pop": {}}}
    c_a = compile_plan(ir, current_a, base_revision=2)
    c_b = compile_plan(ir, current_b, base_revision=2)
    assert c_a.model_dump() == c_b.model_dump()
