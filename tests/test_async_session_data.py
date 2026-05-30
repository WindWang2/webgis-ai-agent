"""Async interface tests for both session_data backends."""
import pytest

from app.services.session_data import SessionDataManager


async def test_memory_store_returns_ref():
    sdm = SessionDataManager()
    ref = await sdm.store("s1", {"x": 1})
    assert ref.startswith("ref:")


async def test_memory_get_returns_stored_data():
    sdm = SessionDataManager()
    ref = await sdm.store("s2", {"hello": "world"})
    data = await sdm.get("s2", ref)
    assert data == {"hello": "world"}


async def test_memory_resolve_alias():
    sdm = SessionDataManager()
    ref = await sdm.store("s3", {})
    await sdm.set_alias("s3", ref, "my-layer")
    resolved = await sdm.resolve_alias("s3", "my-layer")
    assert resolved == ref


async def test_memory_get_session_metadata():
    sdm = SessionDataManager()
    await sdm.store("s4", {"a": 1})
    meta = await sdm.get_session_metadata("s4")
    assert "map_state" in meta
    assert "list_refs" in meta
    assert "event_log" in meta
    assert "started_at" in meta


async def test_memory_append_and_get_event_log():
    sdm = SessionDataManager()
    await sdm.append_event("s5", "click", {"x": 1})
    log = await sdm.get_event_log("s5")
    assert len(log) == 1
    assert log[0]["event"] == "click"


async def test_memory_clear_session():
    sdm = SessionDataManager()
    await sdm.store("s6", {})
    await sdm.clear_session("s6")
    refs = await sdm.list_refs("s6")
    assert refs == {}


import fakeredis.aioredis


@pytest.fixture
def fake_redis_sdm():
    """RedisSessionDataManager backed by fakeredis (async)."""
    server = fakeredis.FakeServer()
    from app.services.session_data_redis import RedisSessionDataManager
    sdm = RedisSessionDataManager.__new__(RedisSessionDataManager)
    sdm._r = fakeredis.aioredis.FakeRedis(server=server)
    sdm.capacity = 200
    return sdm


async def test_redis_store_returns_ref(fake_redis_sdm):
    ref = await fake_redis_sdm.store("rs1", {"val": 42})
    assert ref.startswith("ref:")


async def test_redis_get_returns_stored_data(fake_redis_sdm):
    ref = await fake_redis_sdm.store("rs2", {"hello": "redis"})
    data = await fake_redis_sdm.get("rs2", ref)
    assert data == {"hello": "redis"}


async def test_redis_set_alias_and_resolve(fake_redis_sdm):
    ref = await fake_redis_sdm.store("rs3", {})
    await fake_redis_sdm.set_alias("rs3", ref, "城区")
    resolved = await fake_redis_sdm.resolve_alias("rs3", "城区")
    assert resolved == ref


async def test_redis_get_session_metadata_pipeline(fake_redis_sdm):
    await fake_redis_sdm.store("rs4", {"a": 1})
    await fake_redis_sdm.set_map_state("rs4", "zoom", 10)
    await fake_redis_sdm.append_event("rs4", "click", {})
    meta = await fake_redis_sdm.get_session_metadata("rs4")
    assert "map_state" in meta
    assert "list_refs" in meta
    assert "event_log" in meta
    assert "started_at" in meta
    assert meta["map_state"].get("zoom") == 10
    assert len(meta["event_log"]) == 1
    assert meta["started_at"] is not None


async def test_redis_clear_session(fake_redis_sdm):
    await fake_redis_sdm.store("rs5", {})
    await fake_redis_sdm.clear_session("rs5")
    refs = await fake_redis_sdm.list_refs("rs5")
    assert refs == {}


async def test_redis_get_started_at_records_started_at(fake_redis_sdm):
    sid = "rs_start"
    assert await fake_redis_sdm.get_started_at(sid) is None
    await fake_redis_sdm.set_map_state(sid, "base_layer", "OSM 地图")
    started = await fake_redis_sdm.get_started_at(sid)
    assert started is not None
    # 不会被后续写入覆盖
    original = started
    await fake_redis_sdm.set_map_state(sid, "viewport", {"center": [0, 0], "zoom": 5})
    assert await fake_redis_sdm.get_started_at(sid) == original
    await fake_redis_sdm.clear_session(sid)


async def test_redis_started_at_set_by_store(fake_redis_sdm):
    sid = "rs_store_only"
    assert await fake_redis_sdm.get_started_at(sid) is None
    await fake_redis_sdm.store(sid, {"type": "FeatureCollection", "features": []}, prefix="t")
    assert await fake_redis_sdm.get_started_at(sid) is not None
    await fake_redis_sdm.clear_session(sid)
