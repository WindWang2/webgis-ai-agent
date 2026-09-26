"""Plan 提交器测试（F12 / ADR-0214 D5）—— 真引擎 write-seam。

锁定：逐步 CAS 链式提交；superseded 中止（陈旧计划绝不静默续跑）；
幂等重放（同编译重复 apply = duplicate no-op、revision 不推进）；
receipt 有界环回链；blocked 编译零引擎提交。
"""
import shutil
import uuid

import pytest

from app.lib.cartography.plan_ir import (
    LayerBlueprint,
    LayerIntent,
    MapPlanIR,
)
from app.services.map_plan_compiler.apply import apply_plan
from app.services.map_plan_compiler.compiler import compile_plan
from app.services.map_plan_compiler.receipt import (
    PLAN_RECEIPTS_KEY,
    receipt_is_stale,
)
from app.services.mapspec.lifecycle_engine import MapSpecLifecycleEngine
from app.services.mapspec.store import BASE_STORAGE_DIR
from app.services.session_data import session_data_manager


@pytest.fixture
async def clean_session():
    sid = f"f12-apply-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    d = BASE_STORAGE_DIR / sid
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


def _ir(layer_id="pl-primary", visible=True, source_ref="src:pop") -> MapPlanIR:
    return MapPlanIR(
        ir_id="mpir-apply01",
        layer_intents=[LayerIntent(
            intent_id="li-01-primary", action="present_primary",
            layer_id=layer_id, source_ref=source_ref, expected_visible=visible,
            blueprint=LayerBlueprint(layer_type="fill", paint={"fill-color": "#313695"}),
        )],
    )


async def _seed(session_id: str, engine: MapSpecLifecycleEngine) -> int:
    from app.services.mapspec.lifecycle_engine import InitProjectIntent, UpsertSourceIntent

    seeded = await engine.apply_mutation(session_id, InitProjectIntent())
    assert not seeded.is_error
    up = await engine.apply_mutation(
        session_id,
        UpsertSourceIntent(source_id="src:pop", source={"type": "geojson"}),
    )
    assert not up.is_error
    return up.mutation_revision


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_apply_commits_sequentially_with_cas_chain(clean_session):
    engine = MapSpecLifecycleEngine()
    base_revision = await _seed(clean_session, engine)
    ir = _ir()
    state = await session_data_manager.get_map_state(clean_session)
    compilation = compile_plan(ir, state, base_revision=base_revision)
    assert compilation.status == "compiled"

    result = await apply_plan(clean_session, compilation, engine=engine)

    assert result.status == "applied"
    receipt = result.receipt
    assert receipt.status == "applied"
    assert len(receipt.applied) == len(compilation.mutations)
    assert all(a.ok and not a.duplicate for a in receipt.applied)
    assert receipt.final_revision == base_revision + len(compilation.mutations)
    assert receipt.final_fingerprint.startswith("carto-sha256:")
    # 引擎 CAS 链：逐步 expected_revision 都对上（无 superseded）
    assert not any(a.error_code for a in receipt.applied)
    # spec 真值：层已在场且可见（upsert 面 = schema visible 布尔）
    state = await session_data_manager.get_map_state(clean_session)
    doc = state.get("mapspec") or state
    layers = {l["id"]: l for l in doc.get("layers", [])}
    assert "pl-primary" in layers
    assert layers["pl-primary"].get("visible") is not False
    assert (layers["pl-primary"].get("layout") or {}).get("visibility", "visible") == "visible"
    # 回执已入 map_state 有界环（回链）
    ring = state.get(PLAN_RECEIPTS_KEY) or []
    assert len(ring) == 1
    assert ring[0]["receipt_id"] == receipt.receipt_id
    # decision record 已内嵌（plan_compile）
    assert receipt.decision.get("kind") == "plan_compile"
    assert receipt.decision.get("decision_id", "").startswith("dec_")


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_replay_same_compilation_is_all_duplicates(clean_session):
    engine = MapSpecLifecycleEngine()
    base_revision = await _seed(clean_session, engine)
    ir = _ir()
    state = await session_data_manager.get_map_state(clean_session)
    compilation = compile_plan(ir, state, base_revision=base_revision)
    first = await apply_plan(clean_session, compilation, engine=engine)
    assert first.status == "applied"

    # 陈旧 CAS 前提下重放同一编译：幂等去重优先于 superseded（ADR-0183）
    replay = await apply_plan(clean_session, compilation, engine=engine)
    assert replay.status == "applied"
    assert all(a.duplicate for a in replay.receipt.applied)
    # revision 未被重放推进
    assert replay.receipt.final_revision == first.receipt.final_revision


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_stale_base_revision_aborts_without_silent_continue(clean_session):
    engine = MapSpecLifecycleEngine()
    await _seed(clean_session, engine)
    stale_revision = 0  # 实际 revision 已 > 0
    ir = _ir()
    state = await session_data_manager.get_map_state(clean_session)
    compilation = compile_plan(ir, state, base_revision=stale_revision)
    result = await apply_plan(clean_session, compilation, engine=engine)
    assert result.status == "superseded"
    assert result.receipt.status == "superseded"
    codes = result.reason_codes
    assert "PLAN_APPLY_SUPERSEDED" in codes
    # spec 未被推进（首步即被拒）
    state = await session_data_manager.get_map_state(clean_session)
    doc = state.get("mapspec") or state
    assert all(l.get("id") != "pl-primary" for l in doc.get("layers", []))


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_blocked_compilation_never_touches_engine(clean_session):
    engine = MapSpecLifecycleEngine()
    base_revision = await _seed(clean_session, engine)
    before = await session_data_manager.get_map_state(clean_session)
    ir = _ir(source_ref="ds:ghost")  # data ref 不在场 → obligations blocked
    compilation = compile_plan(ir, {"sources": {}, "layers": []},
                               base_revision=base_revision)
    assert compilation.status == "blocked"
    result = await apply_plan(clean_session, compilation, engine=engine)
    assert result.status == "blocked"
    assert result.receipt.applied == []
    after = await session_data_manager.get_map_state(clean_session)
    assert (before.get("mapspec") or before).get("layers") == \
        (after.get("mapspec") or after).get("layers")


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_multi_turn_amendments_touch_only_targets(clean_session):
    """DoD：多轮小修改产生最小 diff —— 第二轮只提交被改节点。"""
    from app.services.map_plan_compiler.plan_amendment import PlanAmendment
    from app.services.map_plan_compiler.projector import amend_plan_ir

    engine = MapSpecLifecycleEngine()
    base_revision = await _seed(clean_session, engine)
    ir = _ir()
    state = await session_data_manager.get_map_state(clean_session)
    compilation = compile_plan(ir, state, base_revision=base_revision)
    first = await apply_plan(clean_session, compilation, engine=engine)
    assert first.status == "applied"

    state = await session_data_manager.get_map_state(clean_session)
    revision_now = int(state.get("_cartographic_mutation_revision", 0))
    ir2 = amend_plan_ir(ir, [PlanAmendment(
        kind="set_layer_visibility", layer_id="pl-primary", visible=False)])
    compilation2 = compile_plan(ir2, state, base_revision=revision_now)
    assert [m.intent for m in compilation2.mutations] == ["patch_layer_presentation"], \
        "第二轮只触达被改层"
    second = await apply_plan(clean_session, compilation2, engine=engine)
    assert second.status == "applied"
    state2 = await session_data_manager.get_map_state(clean_session)
    doc2 = state2.get("mapspec") or state2
    layers = {l["id"]: l for l in doc2.get("layers", [])}
    assert layers["pl-primary"]["layout"]["visibility"] == "none"
    ring = state2.get(PLAN_RECEIPTS_KEY) or []
    assert len(ring) == 2
    assert ring[0]["ir_id"] != ring[1]["ir_id"]
    assert ring[1]["supersedes"] == ring[0]["ir_id"]


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_receipt_staleness_detects_drift(clean_session):
    engine = MapSpecLifecycleEngine()
    base_revision = await _seed(clean_session, engine)
    compilation = compile_plan(_ir(), {"sources": {}, "layers": []},
                               base_revision=base_revision)
    result = await apply_plan(clean_session, compilation, engine=engine)
    receipt = result.receipt
    assert receipt_is_stale(receipt, "carto-sha256:other") is True
    assert receipt_is_stale(receipt, receipt.final_fingerprint) is False
