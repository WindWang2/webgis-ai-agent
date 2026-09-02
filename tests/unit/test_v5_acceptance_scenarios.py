"""V5 acceptance scenarios S2–S10 (S1 lives in test_pi_bridge_pool_v5b.py,
S5/S7 in test_ref_lifecycle_v5.py). Each scenario maps to the goal's
acceptance list; failures here are release-blocking for V5.

  S2  100 random disconnect/cancel events — no lock leak
  S3  cancel while Redis register — next session proceeds
  S4  cancel while Redis unregister — next session proceeds
  S6  evict ref — tile/feature endpoints serve no ghost data
  S8  EPSG:3857 → attribute_filter → central_feature — CRS correct end to end
  S9  EPSG:4490 — no fake CRS warning on legitimate Chinese geodata
  S10 legacy anonymous upload — no IDOR
"""
import asyncio
import random
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.agent_pi_bridge as bridge_mod
from app.agent_pi_bridge import PiBridge

import app.api.routes.chat  # noqa: F401 — warm lazy import


def _make_rpc() -> MagicMock:
    rpc = MagicMock()
    rpc.events = asyncio.Queue()
    rpc.start = AsyncMock()
    rpc.stop = AsyncMock()
    rpc.request = AsyncMock()
    return rpc


@pytest.fixture(autouse=True)
def _fast_heartbeats(monkeypatch):
    monkeypatch.setattr(bridge_mod, "PI_HEARTBEAT_INTERVAL", 0.01)
    monkeypatch.setattr(bridge_mod, "PI_EVENT_STREAM_TIMEOUT", 5.0)
    monkeypatch.setattr(bridge_mod, "PI_TURN_TOTAL_TIMEOUT", 10.0)


@pytest.fixture(autouse=True)
def _clean_bridge_state():
    bridge_mod._session_executed_sets.clear()
    bridge_mod._dispatch_result_cache.clear()
    saved = (bridge_mod._dispatch_service, bridge_mod._dispatch_service_registry)
    bridge_mod._dispatch_service = None
    bridge_mod._dispatch_service_registry = None
    bridge_mod._active_turns.clear()
    yield
    bridge_mod._session_executed_sets.clear()
    bridge_mod._dispatch_result_cache.clear()
    bridge_mod._dispatch_service, bridge_mod._dispatch_service_registry = saved
    bridge_mod._active_turns.clear()


async def _drive(bridge, **kwargs):
    chunks = []
    async for ev in bridge.stream_prompt(**kwargs):
        chunks.append(ev)
    return chunks


async def _assert_lock_free(bridge, timeout: float = 2.0) -> None:
    async def _probe():
        await bridge._lock.acquire()
        bridge._lock.release()

    await asyncio.wait_for(_probe(), timeout=timeout)


# ─── S2: 100 random disconnect/cancel events ───────────────────────────────


@pytest.mark.asyncio
async def test_s2_random_disconnect_storm_no_leak(monkeypatch):
    rpc = _make_rpc()
    bridge = PiBridge(rpc=rpc)
    monkeypatch.setattr(bridge_mod, "register_active_pi_turn", AsyncMock())
    monkeypatch.setattr(bridge_mod, "unregister_active_pi_turn", AsyncMock())

    rng = random.Random(20260902)
    for i in range(100):
        async def hang(cmd, data=None, _i=i):
            if cmd == "prompt":
                await rpc.events.put({
                    "type": "message_update",
                    "message": {"role": "assistant", "content": []},
                    "assistantMessageEvent": {
                        "type": "text_delta", "contentIndex": 0, "delta": f"x{_i}",
                    },
                })
                await asyncio.sleep(30)

        rpc.request = AsyncMock(side_effect=hang)
        task = asyncio.create_task(
            _drive(bridge, message=f"m{i}", session_id=f"sess-{i % 4}")
        )
        await asyncio.sleep(rng.uniform(0.0, 0.06))
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await _assert_lock_free(bridge)
        # Session table must not accumulate ghosts across the storm.
        assert len(bridge_mod._active_turns) <= 1, (
            f"active-turn table leaked entries: {list(bridge_mod._active_turns)}"
        )


# ─── S3/S4: cancel while Redis register/unregister ─────────────────────────


@pytest.mark.asyncio
async def test_s3_cancel_during_redis_register_next_session_proceeds(monkeypatch):
    """USE_REDIS=true 生产形态：register 的 Redis await 被取消后，下一个无关
    会话必须立刻拿到锁并完成 turn。"""
    rpc = _make_rpc()
    bridge = PiBridge(rpc=rpc)
    first = asyncio.Event()

    async def redis_register(session_id, turn_id, **kwargs):
        if not first.is_set():
            first.set()
            await asyncio.sleep(30)  # Redis I/O hangs (socket timeout absent)
        return None

    monkeypatch.setattr(bridge_mod, "register_active_pi_turn", redis_register)
    monkeypatch.setattr(bridge_mod, "unregister_active_pi_turn", AsyncMock())

    task = asyncio.create_task(_drive(bridge, message="m", session_id="sess-a"))
    await asyncio.wait_for(first.wait(), timeout=3.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await _assert_lock_free(bridge)

    # Next unrelated session completes a full turn.
    rpc.request = AsyncMock(side_effect=None)
    rpc.request = AsyncMock()

    async def ok_prompt(cmd, data=None):
        if cmd == "prompt":
            await rpc.events.put({
                "type": "message_update",
                "message": {"role": "assistant", "content": []},
                "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": "B"},
            })
            await rpc.events.put({"type": "agent_settled"})

    rpc.request = AsyncMock(side_effect=ok_prompt)
    chunks = await asyncio.wait_for(
        _drive(bridge, message="m2", session_id="sess-b"), timeout=5.0
    )
    assert any("done" in c for c in chunks)


@pytest.mark.asyncio
async def test_s4_cancel_during_redis_unregister_next_session_proceeds(monkeypatch):
    rpc = _make_rpc()
    bridge = PiBridge(rpc=rpc)
    in_unregister = asyncio.Event()

    async def ok_prompt(cmd, data=None):
        if cmd == "prompt":
            await rpc.events.put({
                "type": "message_update",
                "message": {"role": "assistant", "content": []},
                "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": "A"},
            })
            await rpc.events.put({"type": "agent_settled"})

    rpc.request = AsyncMock(side_effect=ok_prompt)
    task: asyncio.Task | None = None

    async def redis_unregister(session_id, turn_id):
        in_unregister.set()
        await asyncio.sleep(30)  # hung Redis eval

    monkeypatch.setattr(bridge_mod, "register_active_pi_turn", AsyncMock())
    monkeypatch.setattr(bridge_mod, "unregister_active_pi_turn", redis_unregister)

    task = asyncio.create_task(_drive(bridge, message="m", session_id="sess-a"))
    await asyncio.wait_for(in_unregister.wait(), timeout=3.0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    # Lock is released BEFORE the unregister await (INV-P4): probe right away.
    await _assert_lock_free(bridge, timeout=1.0)


# ─── S6: eviction kills ghost serving paths ────────────────────────────────


@pytest.mark.asyncio
async def test_s6_evicted_ref_serves_no_ghost_data():
    from app.services.mvt import spatial_index_cache, tile_lru_cache
    from app.services.session_data_redis import RedisSessionStore
    import fakeredis.aioredis

    store = RedisSessionStore(
        redis_url="redis://unused",
        redis=fakeredis.aioredis.FakeRedis(decode_responses=False),
        capacity=1,
    )
    sid = "sess-s6"
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {},
             "geometry": {"type": "Point", "coordinates": [116.0, 39.9]}}
        ],
    }
    r1 = await store.store(sid, fc)

    def _build():
        from app.services.mvt import build_spatial_index_entry
        return build_spatial_index_entry((sid, r1), fc)

    spatial_index_cache.get_or_build((sid, r1), _build)
    tile_lru_cache.put((sid, r1, 3, 5, 5), b"ghost")
    assert spatial_index_cache.get((sid, r1)) is not None  # fixture sanity
    assert tile_lru_cache.get((sid, r1, 3, 5, 5)) == b"ghost"

    r2 = await store.store(sid, fc)  # capacity=1 → r1 evicted
    assert await store.get(sid, r1) is None
    # Both derived serving paths dropped with the eviction (S6 core).
    assert spatial_index_cache.get((sid, r1)) is None, "ghost STRtree serves evicted ref"
    assert tile_lru_cache.get((sid, r1, 3, 5, 5)) is None, "ghost tiles serve evicted ref"
    assert await store.get(sid, r2) == fc


# ─── S8: EPSG:3857 → attribute_filter → central_feature → renderable ───────


@pytest.mark.heavy
@pytest.mark.asyncio
async def test_s8_3857_chain_crs_correct_end_to_end():
    from pyproj import Transformer
    from app.tools.advanced_spatial import register_advanced_spatial_tools
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_advanced_spatial_tools(registry)
    tr = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)

    fc = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:3857"}},
        "features": [
            {"type": "Feature",
             "properties": {"cat": "school" if i % 2 == 0 else "park"},
             "geometry": {"type": "Point",
                          "coordinates": list(tr.transform(116.0 + 0.002 * i, 39.9))}}
            for i in range(6)
        ],
    }

    filtered = await registry.dispatch(
        "attribute_filter", {"geojson": fc, "query": "cat == 'school'"}
    )
    assert filtered.get("success") is True, filtered
    fdata = filtered["data"]
    assert len(fdata["features"]) == 3
    assert fdata["crs"]["properties"]["name"] == "EPSG:3857"

    center = await registry.dispatch(
        "central_feature", {"geojson": fdata, "method": "central_feature"}
    )
    assert center.get("success") is True, center
    lon, lat = center["data"]["geometry"]["coordinates"]
    assert 115.9 < lon < 116.1 and 39.8 < lat < 40.0, (
        f"chain lost CRS: center at {(lon, lat)} (metres misread as degrees?)"
    )


# ─── S9: EPSG:4490 produces no fake CRS warning ────────────────────────────


@pytest.mark.heavy
@pytest.mark.asyncio
async def test_s9_4490_no_false_crs_warning(caplog):
    import logging
    from app.lib.geo_processor.core import gdf_from_features

    fc = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:4490"}},
        "features": [
            {"type": "Feature", "properties": {},
             "geometry": {"type": "Point", "coordinates": [104.06, 30.57]}}  # 成都
        ],
    }
    with caplog.at_level(logging.WARNING):
        gdf = gdf_from_features(fc, context="s9-4490")
    assert gdf is not None
    joined = " ".join(r.message for r in caplog.records)
    assert "未声明 crs" not in joined, "legitimately-declared 4490 must not warn as undeclared"
    assert "EPSG:4490" not in joined or "invalid" not in joined.lower()


# ─── S10: legacy anonymous upload cannot be IDOR'd ─────────────────────────


def test_s10_legacy_anon_upload_predicate_fail_closed():
    """The ownership predicate itself denies NULL/NULL — the matrix tests
    (test_upload_ownership_matrix_1109) cover the HTTP surface; this pins the
    unit contract for the release gate."""
    from app.core.auth import authorize_session_write

    class _C:
        user_id = None
        owner_token = None

    assert authorize_session_write(_C(), "any-authenticated-user", None) is False
    assert authorize_session_write(_C(), None, None) is False
