"""Authoritative base, lock, source and receipt regressions for 1820/1863."""
import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.lib.cartography.plan_ir import AnalysisOutputRef, ComponentIntent, LayerBlueprint, LayerIntent, MapPlanIR
from app.services.map_plan_compiler.compiler import compile_plan


def _layer(action="present_primary", source="src", layer_id="out"):
    return LayerIntent(intent_id="li", action=action, layer_id=layer_id,
                       source_ref=source, blueprint=LayerBlueprint(layer_type="fill", paint={"fill-color": "red"}))


def test_mutation_identity_includes_base_revision_and_content():
    ir = MapPlanIR(ir_id="mpir-review", layer_intents=[_layer()])
    state = {"sources": {"src": {}}, "layers": []}
    first = compile_plan(ir, state, base_revision=2)
    second = compile_plan(ir, state, base_revision=3)
    other = compile_plan(ir, {**state, "view": {"zoom": 8}}, base_revision=2)
    identities = [c.mutations[0].client_mutation_id for c in (first, second, other)]
    assert len(set(identities)) == 3
    assert first == compile_plan(ir, state, base_revision=2)


def test_visibility_patch_clears_conflicting_legacy_visible_flag():
    from app.services.mapspec.mutation_primitives import _patch_layer_presentation
    from app.services.map_plan_compiler.compiler import _layer_visible
    original = {"id": "out", "visible": False, "layout": {"visibility": "none"}}
    shown = _patch_layer_presentation(original, visible=True, opacity=None)
    assert _layer_visible(shown) is True and "visible" not in shown
    assert original["visible"] is False


@pytest.mark.parametrize("action", ["set_visibility", "restyle"])
def test_amended_full_layer_intent_still_creates_absent_target(action):
    compilation = compile_plan(MapPlanIR(ir_id="mpir-review", layer_intents=[_layer(action)]), {"sources": {"src": {}}})
    assert compilation.status == "compiled"
    assert [m.intent for m in compilation.mutations] == ["upsert_layer"]
    assert compilation.mutations[0].payload["layer"]["paint"]["fill-color"] == "red"


def test_locked_ensure_is_only_allowed_when_it_really_is_noop():
    ci = ComponentIntent(intent_id="ci", component_id="title", component_type="title",
                         action="ensure", locked=True, options={"text": "original"})
    state = {"layout": {"components": [{"id": "title", "type": "title", "enabled": True,
                                        "position": "top-center", "options": {"text": "original"}}]}}
    ir = MapPlanIR(ir_id="mpir-review", component_intents=[ci])
    assert compile_plan(ir, state).mutations == []
    changed = ir.model_copy(update={"component_intents": [ci.model_copy(update={"options": {"text": "changed"}})]})
    compilation = compile_plan(changed, state)
    assert compilation.status == "blocked" and not compilation.mutations
    assert "PLAN_LOCK_CONFLICT" in compilation.obligations.reason_codes


def test_ir_analysis_output_is_not_its_own_liveness_proof():
    ir = MapPlanIR(ir_id="mpir-review", layer_intents=[_layer(source="forged")],
                   analysis_outputs=[AnalysisOutputRef(output_id="forged")])
    assert compile_plan(ir, {}).status == "blocked"
    assert compile_plan(ir, {}, analysis_output_ids=["forged"]).status == "compiled"


def test_layer_alias_resolves_to_authoritative_source_before_upsert():
    ir = MapPlanIR(ir_id="mpir-review", layer_intents=[_layer(source="alias")])
    state = {"sources": {"src": {}}, "layers": [{"id": "alias", "source": "src", "type": "fill"}]}
    assert compile_plan(ir, state).mutations[0].payload["layer"]["source"] == "src"


def test_existing_plan_title_keeps_grammar_required_obligation():
    from tests.cartography.test_plan_projector_v1 import _plan
    from app.lib.cartography.grammar_solver import GrammarRequest, solve_grammar
    from app.services.map_plan_compiler.projector import project_plan_ir
    grammar = solve_grammar(GrammarRequest(geometry="polygon", feature_count=120,
                                           fields=[], pinned_representation="administrative_choropleth"))
    ir = project_plan_ir(_plan(), grammar=grammar)
    title = next(ci for ci in ir.component_intents if ci.component_type == "title")
    assert title.required and title.intent_id in ir.final_display.expected_components


@pytest.mark.asyncio
async def test_apply_uses_actual_returned_revision_after_duplicate():
    from app.services.map_plan_compiler.apply import apply_plan
    from app.services.mapspec.lifecycle_engine import MapSpecResult
    ir = MapPlanIR(ir_id="mpir-review", layer_intents=[_layer(layer_id="a"), _layer(layer_id="b")])
    compilation = compile_plan(ir, {"sources": {"src": {}}}, base_revision=3)
    expected = []
    async def apply(*_args, **kwargs):
        expected.append(kwargs["expected_revision"])
        return MapSpecResult(duplicate=len(expected) == 1, mutation_revision=3 if len(expected) == 1 else 4)
    result = await apply_plan("review", compilation, engine=SimpleNamespace(apply_mutation=apply), record_receipt=False)
    assert expected == [3, 3] and result.receipt.final_revision == 4


@pytest.mark.asyncio
async def test_concurrent_plan_receipts_preserve_every_entry():
    from app.services.map_plan_compiler.apply import _store_receipt
    from app.services.map_plan_compiler.receipt import PLAN_RECEIPTS_KEY, new_receipt_skeleton
    from app.services.session_data import session_data_manager
    sid = "review-receipts-" + uuid.uuid4().hex
    ir = MapPlanIR(ir_id="mpir-review")
    receipts = [new_receipt_skeleton(compile_plan(ir, {}, base_revision=i), session_id=sid) for i in range(8)]
    await asyncio.gather(*(_store_receipt(sid, receipt) for receipt in receipts))
    state = await session_data_manager.get_map_state(sid)
    assert {r["receipt_id"] for r in state[PLAN_RECEIPTS_KEY]} == {r.receipt_id for r in receipts}
    await session_data_manager.clear_session(sid)
