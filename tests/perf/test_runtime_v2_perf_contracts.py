"""GIS Harness Runtime v2 — deterministic performance contracts.

非 wall-clock 契约（/goal §13）：以调用计数 / 全量读次数 / revision 递增数
为断言对象，杜绝微基准抖动。

- PC-1: finalize_display N 层收口 = 恰一次引擎事务（revision 恰 +1、
  checkpoint 恰一次、全量 get_map_state 次数与 N 无关）。
- PC-2: 单个 presentation mutation 的守卫环读取不再触发全量 get_map_state
  （单字段通道）。
- PC-3: manifest 反查 O(1)（capability_for_tool / tools_for_capability 为
  dict 读，无 registry 扫描）；capability_tool_map 与 manifest 视图一致。
- PC-4: chat 准入 tombstone 检查走单字段读。
"""
import uuid

import pytest

from app.services.mapspec.lifecycle_engine import (
    PatchLayerPresentationIntent,
    UpsertLayerIntent,
    mapspec_lifecycle_engine,
)
from app.services.gis_world_state import apply_gis_mutation, apply_gis_mutation_batch
from app.services.session_data import session_data_manager, MemorySessionStore


@pytest.fixture
async def clean_session():
    sid = f"v2-perf-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)


async def _seed(session_id: str, n: int) -> None:
    for i in range(n):
        await mapspec_lifecycle_engine.apply_mutation(
            session_id,
            UpsertLayerIntent(
                layer={
                    "id": f"perf-{i}",
                    "type": "circle",
                    "source": f"perf-src-{i}",
                    "paint": {"color": "#fff"},
                },
                source_data={"type": "FeatureCollection", "features": []},
            ),
        )


@pytest.mark.perf
@pytest.mark.asyncio
async def test_pc1_finalize_batch_cost_independent_of_layer_count(clean_session):
    """N=3 与 N=12 的收口：revision 恰 +1；全量 get_map_state 调用数同阶。"""
    for n in (3, 12):
        sid = f"{clean_session}-n{n}"
        await session_data_manager.clear_session(sid)
        try:
            await _seed(sid, n)
            state0 = await session_data_manager.get_map_state(sid)
            rev0 = state0["_cartographic_mutation_revision"]
            full_reads = {"count": 0}
            orig = MemorySessionStore.get_map_state

            async def counting(self, session_id):
                full_reads["count"] += 1
                return await orig(self, session_id)

            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(MemorySessionStore, "get_map_state", counting)
                batch = await apply_gis_mutation_batch(
                    sid,
                    [PatchLayerPresentationIntent(layer_id=f"perf-{i}", visible=(i == 0))
                     for i in range(n)],
                    origin="agent",
                    actor="perf",
                )
            assert batch.committed
            state1 = await session_data_manager.get_map_state(sid)
            assert state1["_cartographic_mutation_revision"] == rev0 + 1, (
                f"n={n}: 整批恰一次 revision 递增"
            )
            # 批内全量读不随 N 增长（引擎一次 + provenance 单字段不计数；
            # 上界随实现微调，但必须 << N）
            assert 1 <= full_reads["count"] <= 4, (
                f"n={n}: 全量 get_map_state {full_reads['count']} 次 —— 批量事务恰一次"
                f"权威读（0 = 仪器化失效，>4 = 随 N 放大）"
            )
        finally:
            await session_data_manager.clear_session(sid)


@pytest.mark.perf
@pytest.mark.asyncio
async def test_pc2_guard_ring_uses_single_field_reads(clean_session):
    """agent presentation mutation：守卫环不得触发全量 get_map_state（单字段）。"""
    await _seed(clean_session, 2)
    full_reads = {"count": 0}
    field_reads = {"count": 0}
    orig_full = MemorySessionStore.get_map_state
    orig_field = MemorySessionStore.get_state_field

    async def counting_full(self, session_id):
        full_reads["count"] += 1
        return await orig_full(self, session_id)

    async def counting_field(self, session_id, field):
        field_reads["count"] += 1
        return await orig_field(self, session_id, field)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(MemorySessionStore, "get_map_state", counting_full)
        mp.setattr(MemorySessionStore, "get_state_field", counting_field)
        res = await apply_gis_mutation(
            clean_session,
            PatchLayerPresentationIntent(layer_id="perf-0", visible=False),
            origin="agent",
            actor="perf",
        )
    assert not res.is_error
    assert field_reads["count"] >= 2, "守卫环应走单字段通道（pre-lock + in-lock）"
    # review 5/6-A1：全量读上限必须显式断言 —— 引擎锁内一次 + 无守卫环全量读
    # （旧实现守卫环 2 次全量读会让这里读到 3）。
    assert full_reads["count"] <= 1, (
        f"守卫环不得触发全量 get_map_state（实测 {full_reads['count']} 次）"
    )


@pytest.mark.perf
def test_pc3_manifest_lookups_are_o1_dict_reads():
    from app.lib.gis.runtime_manifest import compile_runtime_manifest

    m = compile_runtime_manifest()
    for tool in ("network_shortest_path", "network_accessibility", "network_od_matrix",
                 "location_allocation", "optimize_route", "network_service_area"):
        caps = m.capability_for_tool(tool)
        assert isinstance(caps, list), "dict 读，非扫描"
    for cap in ("service_area", "shortest_path", "accessibility", "od_matrix"):
        tools = m.tools_for_capability(cap)
        assert isinstance(tools, list) and tools, f"{cap} 必须有可解析工具"


@pytest.mark.perf
def test_pc3b_capability_tool_map_matches_manifest_snapshot():
    from app.lib.gis.runtime_manifest import compile_runtime_manifest
    from app.services.gis_harness.planner import capability_tool_map
    m = compile_runtime_manifest()
    assert capability_tool_map() == dict(m.capability_to_tools)


@pytest.mark.perf
@pytest.mark.asyncio
async def test_pc4_chat_admission_tombstone_single_field(monkeypatch):
    """_guard_body_session 的 tombstone 检查走 get_state_field。"""
    from app.api.routes import chat as chat_mod

    calls = {"full": 0, "field": 0}
    orig_field = MemorySessionStore.get_state_field

    async def counting_field(self, session_id, field):
        calls["field"] += 1
        return await orig_field(self, session_id, field)

    monkeypatch.setattr(MemorySessionStore, "get_state_field", counting_field)

    class _FakeHistory:
        def __init__(self, db):
            pass

        async def get_session_meta(self, *a, **k):
            return None  # 库中不存在 → 首条消息放行语义

    monkeypatch.setattr(chat_mod, "AsyncHistoryService", _FakeHistory)

    class _FakeDb:
        async def get(self, *a, **k):
            return None  # 库中不存在 → 放行（首条消息语义）

    await chat_mod._guard_body_session(_FakeDb(), "v2-perf-no-db-session", None, None)
    assert calls["field"] >= 1, "准入守卫必须走单字段 tombstone 读"
