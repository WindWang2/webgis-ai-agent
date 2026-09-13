"""交互观察摄入（S4）+ turn context 集成（M4）测试（ADR-0180）。

覆盖：内容寻重去重、client_generation 单调、payload 白名单规范化、
有界环、kill-switch 回落、编译失败 fail-open、WS handler 通道、
late 事件不倒退。
"""
import uuid

import pytest

from app.services.gis_situation.compiler import compile_situation, situation_enabled
from app.services.gis_situation.diff import load_snapshot
from app.services.gis_situation.observation import (
    KIND_SELECTION,
    KIND_VIEWPORT,
    MAX_INTERACTION_RING,
    normalize_payload,
    record_interaction,
)
from app.services.gis_situation.turn_context import build_situation_turn_context
from tests.data.situation_fakes import FakeMapspecStore, FakeSituationStore
from tests.test_gis_situation_compile_diff import _full_store, _sid, _spec


@pytest.mark.asyncio
async def test_record_interaction_accepts_and_sequences():
    session_id = _sid()
    store = FakeSituationStore()
    ack1 = await record_interaction(session_id, KIND_VIEWPORT,
                                    {"center": [116.4, 39.9], "zoom": 11},
                                    client_generation=1, store=store)
    assert ack1.accepted and ack1.sequence == 1
    ack2 = await record_interaction(session_id, KIND_SELECTION,
                                    {"layer_id": "lyr-1", "feature_id": "f-9"},
                                    client_generation=2, store=store)
    assert ack2.accepted and ack2.sequence == 2
    ring = store._map_state["_situation_interactions"]
    assert len(ring) == 2
    assert ring[1]["payload"]["layer_id"] == "lyr-1"


@pytest.mark.asyncio
async def test_record_interaction_dedupes_consecutive_identical():
    session_id = _sid()
    store = FakeSituationStore()
    payload = {"center": [116.4, 39.9], "zoom": 11}
    await record_interaction(session_id, KIND_VIEWPORT, payload, store=store)
    dup = await record_interaction(session_id, KIND_VIEWPORT, payload, store=store)
    assert not dup.accepted and dup.reason == "duplicate"
    # 同载荷但不同 kind 不算重复。
    alt = await record_interaction(session_id, KIND_SELECTION,
                                   {"layer_id": "x"}, store=store)
    assert alt.accepted
    # 视口移走再移回（中间有别的帧）→ 不是重复，是有效往返。
    back = await record_interaction(session_id, KIND_VIEWPORT, payload, store=store)
    assert back.accepted


@pytest.mark.asyncio
async def test_record_interaction_stale_generation_rejected():
    session_id = _sid()
    store = FakeSituationStore()
    await record_interaction(session_id, KIND_VIEWPORT, {"zoom": 11},
                             client_generation=5, store=store)
    late = await record_interaction(session_id, KIND_VIEWPORT, {"zoom": 12},
                                    client_generation=4, store=store)
    assert not late.accepted and late.reason == "stale_generation"
    newer = await record_interaction(session_id, KIND_VIEWPORT, {"zoom": 12},
                                     client_generation=6, store=store)
    assert newer.accepted


@pytest.mark.asyncio
async def test_record_interaction_rejects_bad_input():
    session_id = _sid()
    store = FakeSituationStore()
    bad_kind = await record_interaction(session_id, "mousemove", {"x": 1}, store=store)
    assert not bad_kind.accepted and bad_kind.reason == "unknown_kind"
    bad_payload = await record_interaction(session_id, KIND_VIEWPORT, "oops", store=store)
    assert not bad_payload.accepted
    empty_payload = await record_interaction(session_id, KIND_VIEWPORT,
                                             {"bogus_key": 1}, store=store)
    assert not empty_payload.accepted


def test_normalize_payload_whitelist_and_bounds():
    payload = normalize_payload(KIND_SELECTION, {
        "layer_id": "lyr-" + "x" * 300,   # 超长截断
        "evil_script": "<img>",            # 白名单外丢弃
        "properties": {f"key{i:02d}": i for i in range(12)},  # 嵌套键数封顶
    })
    assert payload is not None
    assert "evil_script" not in payload
    assert len(payload["layer_id"]) <= 128
    # 嵌套（feature properties 任意键）走有界而非白名单：确定性取前 8 键。
    assert len(payload["properties"]) <= 8
    assert set(payload["properties"]) <= {f"key{i:02d}" for i in range(8)}

    # 总字节预算（512B）超限 → 整帧拒收。
    from app.services.gis_situation.observation import MAX_PAYLOAD_BYTES

    fat = normalize_payload(KIND_SELECTION, {
        "properties": {f"k{i}": "v" * 200 for i in range(8)},
    })
    assert fat is None
    assert MAX_PAYLOAD_BYTES == 512


@pytest.mark.asyncio
async def test_interaction_ring_bounded():
    session_id = _sid()
    store = FakeSituationStore()
    for i in range(MAX_INTERACTION_RING + 8):
        ack = await record_interaction(session_id, KIND_VIEWPORT,
                                       {"zoom": 1 + i}, store=store)
        assert ack.accepted
    ring = store._map_state["_situation_interactions"]
    assert len(ring) == MAX_INTERACTION_RING
    assert ring[-1]["sequence"] == MAX_INTERACTION_RING + 8


@pytest.mark.asyncio
async def test_compiler_projects_interaction_ring_into_situation():
    session_id = _sid()
    store = _full_store(session_id)
    await record_interaction(session_id, KIND_SELECTION,
                             {"layer_id": "lyr-heat", "feature_id": "f-1"},
                             client_generation=9, store=store)
    situation = await compile_situation(
        session_id, store=store, mapspec_store=FakeMapspecStore(_spec()),
    )
    ring_fact = situation.interaction.recent_interactions
    assert ring_fact.status == "known"
    assert ring_fact.value[-1]["kind"] == KIND_SELECTION
    assert situation.identity.revision.interaction_sequence == 1


# ── turn context（M4）─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_turn_context_renders_projection_and_persists_snapshot(monkeypatch):
    session_id = _sid()
    store = _full_store(session_id)
    spec_store = FakeMapspecStore(_spec())
    monkeypatch.setattr("app.services.session_plan.load_session_plan",
                        _fake_plan_none)
    text = await build_situation_turn_context(
        session_id, turn_id="t-1", store=store, mapspec_store=spec_store,
    )
    assert text and text.startswith("[GIS 情境")
    assert "turn marker" not in text  # marker 仍由 bind_turn_prompt 负责
    snapshot = await load_snapshot(session_id, store=store)
    assert snapshot is not None
    assert snapshot.identity.turn_id == "t-1"


async def _fake_plan_none(session_id):
    return None


@pytest.mark.asyncio
async def test_turn_context_flag_off_returns_none(monkeypatch):
    monkeypatch.setenv("GIS_SITUATION_CONTEXT", "0")
    assert situation_enabled() is False
    text = await build_situation_turn_context(
        "s-x", store=FakeSituationStore(), mapspec_store=FakeMapspecStore({}),
    )
    assert text is None  # 调用方据此回落 legacy env block


@pytest.mark.asyncio
async def test_turn_context_compile_failure_fail_open(monkeypatch):
    """compile_situation 本身崩溃 → fail-open 返回 None（回落 legacy 文本块）。

    单个权威源失败不触发此路径（那是 partial 降级，投影照常带证据输出）；
    这里防御的是投影/编译代码自身的意外缺陷。
    """

    async def _boom(*args, **kwargs):
        raise RuntimeError("compiler bug")

    monkeypatch.setattr("app.services.gis_situation.turn_context.compile_situation", _boom)
    monkeypatch.setenv("GIS_SITUATION_CONTEXT", "1")
    text = await build_situation_turn_context(
        "s-x", store=FakeSituationStore(), mapspec_store=FakeMapspecStore(_spec()),
    )
    assert text is None  # fail-open：调用方回落 _build_environment_turn_context


@pytest.mark.asyncio
async def test_second_turn_delta_marks_changes(monkeypatch):
    monkeypatch.setattr("app.services.session_plan.load_session_plan",
                        _fake_plan_none)
    session_id = _sid()
    store = _full_store(session_id)
    spec_store = FakeMapspecStore(_spec())
    first = await build_situation_turn_context(
        session_id, store=store, mapspec_store=spec_store,
    )
    assert "本轮变更" not in first  # 首轮无上一轮快照
    store._map_state["base_layer"] = "天地图暗色"
    second = await build_situation_turn_context(
        session_id, store=store, mapspec_store=spec_store,
    )
    assert "本轮变更" in second
    assert "*底图" in second


@pytest.mark.asyncio
async def test_late_compile_cannot_regress_projection(monkeypatch):
    """迟到观察（更旧 revision）到来：快照不倒退、投影按无增量降级。"""
    monkeypatch.setattr("app.services.session_plan.load_session_plan",
                        _fake_plan_none)
    session_id = _sid()
    store = _full_store(session_id)
    spec_store = FakeMapspecStore(_spec())
    await build_situation_turn_context(session_id, store=store, mapspec_store=spec_store)
    stored = await load_snapshot(session_id, store=store)
    assert stored.identity.revision.mutation_revision == 4

    store._map_state["_cartographic_mutation_revision"] = 2  # 人为回退（迟到）
    second = await build_situation_turn_context(
        session_id, store=store, mapspec_store=spec_store,
    )
    assert second is not None  # fail-open：仍投影本轮事实
    stored2 = await load_snapshot(session_id, store=store)
    assert stored2.identity.revision.mutation_revision == 4  # 快照仍前进态


@pytest.mark.asyncio
async def test_ws_perception_handler_routes_to_ingest():
    from app.services.session_data import session_data_manager
    from app.services.ws_service import handle_situation_interaction

    session_id = f"s-ws-{uuid.uuid4().hex[:8]}"
    await handle_situation_interaction(session_id, {
        "kind": KIND_VIEWPORT,
        "payload": {"center": [116.4, 39.9], "zoom": 11},
        "client_generation": 3,
        "observed_at": "2026-09-13T00:00:00+00:00",
    })
    state = await session_data_manager.get_map_state(session_id)
    ring = state.get("_situation_interactions")
    assert ring and ring[0]["kind"] == KIND_VIEWPORT
    assert ring[0]["client_generation"] == 3
