"""Receipt 契约测试（F12 / ADR-0214 D5）。

锁定：内容寻址 receipt_id（时序证据不入身份）、decision_record 溯源、
有界环、stale 判定、序列化 round-trip。
"""
import pytest

from app.services.map_plan_compiler.compiler import DisplayExpectation, compile_plan
from app.services.map_plan_compiler.receipt import (
    MAX_PLAN_RECEIPTS,
    CompileReceipt,
    bound_receipt_ring,
    make_plan_compile_decision,
    new_receipt_skeleton,
    receipt_is_stale,
)
from app.lib.cartography.plan_ir import LayerBlueprint, LayerIntent, MapPlanIR
from app.services.gis_harness.planner import MapProductPlan, PlannedLayer
from app.services.gis_harness.intent import MapRequestIntent
from app.services.map_plan_compiler.projector import project_plan_ir


def _compilation(base_revision: int = 3):
    ir = MapPlanIR(
        ir_id="mpir-rcpt01",
        layer_intents=[LayerIntent(
            intent_id="li-1", action="present_primary", layer_id="pl-1",
            source_ref="ds:pop",
            blueprint=LayerBlueprint(layer_type="fill", paint={"fill-color": "#333"}))],
    )
    current = {"sources": {"ds:pop": {}}, "layers": []}
    return compile_plan(ir, current, base_revision=base_revision)


@pytest.mark.cartography
def test_receipt_id_is_content_addressed_and_time_independent():
    c = _compilation()
    r1 = new_receipt_skeleton(c, session_id="s1")
    r2 = new_receipt_skeleton(c, session_id="s1")
    assert r1.receipt_id == r2.receipt_id, "created_at 不参与 receipt 身份"
    assert r1.receipt_id.startswith("mpr-")
    assert r1.status == "pending"
    assert r1.display_expectations == c.display_expectations.model_dump()


@pytest.mark.cartography
def test_receipt_staleness_semantics():
    c = _compilation()
    r = new_receipt_skeleton(c)
    assert receipt_is_stale(r, "carto-sha256:x") is True  # 未提交 = 永远 stale
    sealed = r.model_copy(update={"final_fingerprint": "carto-sha256:abc",
                                  "status": "applied"})
    assert receipt_is_stale(sealed, "carto-sha256:abc") is False
    assert receipt_is_stale(sealed, "carto-sha256:zzz") is True


@pytest.mark.cartography
def test_plan_compile_decision_record_additive_kind():
    c = _compilation()
    r = new_receipt_skeleton(c).model_copy(update={
        "status": "applied", "final_revision": 4,
        "reason_codes": ["PLAN_APPLIED", "PLAN_COMPILED"],
    })
    decision = make_plan_compile_decision(r)
    assert decision["kind"] == "plan_compile"
    assert decision["decision_id"].startswith("dec_")
    assert decision["selected"] == c.compile_id
    assert decision["inputs"]["ir_id"] == "mpir-rcpt01"
    # 有界投影：evidence_refs 来自 applied client_mutation_id
    assert isinstance(decision["evidence_refs"], list)


@pytest.mark.cartography
def test_receipt_ring_is_bounded_fifo():
    c = _compilation()
    ring: list = []
    for i in range(MAX_PLAN_RECEIPTS + 3):
        r = new_receipt_skeleton(c).model_copy(update={
            "receipt_id": f"mpr-{i:04d}"})
        ring = bound_receipt_ring(ring, r)
    assert len(ring) == MAX_PLAN_RECEIPTS
    assert ring[0]["receipt_id"] == f"mpr-{3:04d}", "最早被驱逐（FIFO）"
    assert ring[-1]["receipt_id"] == f"mpr-{MAX_PLAN_RECEIPTS + 2:04d}"


@pytest.mark.cartography
def test_receipt_roundtrip_via_json():
    c = _compilation()
    r = new_receipt_skeleton(c)
    dumped = r.model_dump(mode="json")
    r2 = CompileReceipt(**dumped)
    assert r2 == r


@pytest.mark.cartography
def test_blocked_compilation_receipt_carries_status():
    from app.lib.cartography.plan_ir import ExportObligation
    ir = MapPlanIR(
        ir_id="mpir-rcpt02",
        layer_intents=[LayerIntent(
            intent_id="li-1", action="present_primary", layer_id="pl-1",
            source_ref="ds:ghost",
            blueprint=LayerBlueprint(layer_type="fill"))],
        exports=[ExportObligation(fmt="hologram")],
    )
    c = compile_plan(ir, {"sources": {}, "layers": []}, base_revision=1)
    assert c.status == "blocked"
    r = new_receipt_skeleton(c)
    assert r.status == "blocked"
    assert "OBLIGATIONS_BLOCKED" in r.reason_codes


@pytest.mark.cartography
def test_projection_plan_fingerprint_transcribed():
    plan = MapProductPlan(
        plan_id="plan-rcpt", query="q", recipe_id="r",
        intent=MapRequestIntent(query="q"),
        map_layers=[PlannedLayer(role="primary", layer_type="fill",
                                 cartography="choropleth", bound_ref="ds:pop")],
    )
    ir = project_plan_ir(plan)
    assert ir.plan_fingerprint.startswith("planfp-")
    authorities = {a.kind for a in ir.authorities}
    assert {"plan", "recipe"} <= authorities
