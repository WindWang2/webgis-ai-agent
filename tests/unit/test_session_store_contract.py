"""
Shared contract test suite for SessionStoreProtocol implementations.
Verifies identical behavior between MemorySessionStore and RedisSessionStore adapters.
"""
import pytest
from app.services.session_data import MemorySessionStore
from app.services.session_data_protocol import (
    SessionStoreProtocol,
    get_session_store,
    set_active_session_store,
)


from app.services.session_data_redis import RedisSessionStore

STORE_FACTORIES = [MemorySessionStore]


def _redis_store_factory():
    """RedisSessionStore backed by in-process fakeredis.

    The whole point of a contract suite is to keep two implementations honest
    about identical behaviour. Without Redis in the parametrize list, the only
    ordering contract exercised was MemorySessionStore — so the lpush-vs-deque
    inversion between backends shipped CI-green (REVIEW-P1-4).

    In-process fakeredis (not TcpFakeServer) is required: the TCP server
    raises `InvalidResponse Protocol Error: b'_'` on session keys containing
    underscores, which the production code does on every contract test. The
    injected client is wired in via the redis= test seam on RedisSessionStore
    so the production code path runs unchanged.
    """
    import fakeredis.aioredis

    from app.services.session_data_redis import RedisSessionStore

    return RedisSessionStore(
        redis_url="redis://unused",
        redis=fakeredis.aioredis.FakeRedis(decode_responses=False),
    )


if RedisSessionStore is not None:
    STORE_FACTORIES.append(_redis_store_factory)


@pytest.mark.parametrize("store_factory", STORE_FACTORIES)
@pytest.mark.asyncio
async def test_protocol_conformance(store_factory):
    store = store_factory()
    assert isinstance(store, SessionStoreProtocol)


@pytest.mark.parametrize("store_factory", STORE_FACTORIES)
@pytest.mark.asyncio
async def test_ref_store_get_overwrite(store_factory):
    store = store_factory()
    session_id = "contract_sess_1"

    # Store payload
    ref_id = await store.store(session_id, {"foo": "bar"}, prefix="data")
    assert ref_id.startswith("ref:data-")

    # Get payload
    data = await store.get(session_id, ref_id)
    assert data == {"foo": "bar"}

    # Overwrite payload
    ok = await store.overwrite(session_id, ref_id, {"foo": "updated"})
    assert ok is True
    updated = await store.get(session_id, ref_id)
    assert updated == {"foo": "updated"}


@pytest.mark.parametrize("store_factory", STORE_FACTORIES)
@pytest.mark.asyncio
async def test_alias_management(store_factory):
    store = store_factory()
    session_id = "contract_sess_2"

    ref_id = await store.store(session_id, {"name": "Test Layer"}, prefix="layer")
    await store.set_alias(session_id, ref_id, "active_layer")

    # Resolve alias
    resolved = await store.resolve_alias(session_id, "active_layer")
    assert resolved == ref_id

    # List refs
    refs = await store.list_refs(session_id)
    assert "active_layer" in refs.values()
    assert refs.get(ref_id) == "active_layer"


@pytest.mark.parametrize("store_factory", STORE_FACTORIES)
@pytest.mark.asyncio
async def test_map_state_mutations(store_factory):
    store = store_factory()
    session_id = "contract_sess_3"

    await store.set_map_state(session_id, "base_layer", "amap-vector")
    state = await store.get_map_state(session_id)
    assert state.get("base_layer") == "amap-vector"

    # Layer mutation
    await store.update_layer_in_state(session_id, "layer_1", {"color": "#ff0000"})
    state = await store.get_map_state(session_id)
    layer_ids = [line["id"] for line in state.get("layers", [])]
    assert "layer_1" in layer_ids

    # Layer removal
    await store.remove_layer_from_state(session_id, "layer_1")
    state_after = await store.get_map_state(session_id)
    layer_ids_after = [line["id"] for line in state_after.get("layers", [])]
    assert "layer_1" not in layer_ids_after


@pytest.mark.parametrize("store_factory", STORE_FACTORIES)
@pytest.mark.asyncio
async def test_event_log_and_metadata(store_factory):
    store = store_factory()
    session_id = "contract_sess_4"

    await store.append_event(session_id, "tool_executed", {"tool": "buffer"})
    events = await store.get_event_log(session_id)
    assert len(events) >= 1
    assert events[-1]["event"] == "tool_executed"

    metadata = await store.get_session_metadata(session_id)
    assert "map_state" in metadata
    assert "event_log" in metadata


@pytest.mark.parametrize("store_factory", STORE_FACTORIES)
@pytest.mark.asyncio
async def test_event_log_preserves_chronological_order(store_factory):
    """REVIEW-P1-4 regression: both backends must return events oldest-first.

    The single-event check above is structurally blind to ordering. Consumers
    in chat/context_builder.py slice from the end (tool_calls[-5:],
    user_actions[-3:], pending[-3:]) to grab the most recent N; if the
    underlying list is newest-first, those slices silently return the oldest
    events and the LLM context is inverted in production (memory passes
    locally; Redis failed in prod).
    """
    store = store_factory()
    session_id = "contract_sess_order"

    # Distinct payload so we can tell them apart.
    await store.append_event(session_id, "first", {"i": 0})
    await store.append_event(session_id, "second", {"i": 1})

    events = await store.get_event_log(session_id)

    assert [e["event"] for e in events] == ["first", "second"], (
        f"append_event must produce oldest-first order; got "
        f"{[e['event'] for e in events]}"
    )
    # And confirm the tail-slice consumers (context_builder.py:219/223/87) get
    # the most recent events when they take [-N:].
    assert events[-1]["event"] == "second"
    assert events[-2]["event"] == "first"


@pytest.mark.parametrize("store_factory", STORE_FACTORIES)
@pytest.mark.asyncio
async def test_session_cleanup(store_factory):
    store = store_factory()
    session_id = "contract_sess_5"

    ref_id = await store.store(session_id, {"val": 123})
    assert await store.get(session_id, ref_id) == {"val": 123}

    await store.clear_session(session_id)
    assert await store.get(session_id, ref_id) is None


@pytest.mark.asyncio
async def test_factory_get_session_store():
    store = get_session_store()
    assert isinstance(store, SessionStoreProtocol)

    custom = MemorySessionStore()
    set_active_session_store(custom)
    assert get_session_store() is custom


def test_get_session_store_returns_canonical_singleton(monkeypatch):
    """P2: `get_session_store()` 必须返回规范单例 `session_data_manager`，
    而不是每次新建一个实例 —— 两个独立 RedisSessionStore 各持一份 L1 缓存，
    引擎经 session_data_manager 的写不会失效 explorer 经 get_session_store()
    的 L1（反之亦然），同 id 会话存在 ≤L1_TTL 的陈旧读取。共享实例后 L1
    写失效对所有消费方可见。

    `set_active_session_store()` 仍可显式覆盖（测试/替代 provider）；重置为
    None 后回到规范单例。后端选择（USE_REDIS）由 import 期工厂
    `create_session_data_manager()` 决定，其行为由 tests/unit/test_session_factory.py
    单独覆盖（这里不再重复 mock 工厂）。
    """
    import fakeredis.aioredis

    from app.services import session_data, session_data_protocol
    from app.services.session_data import MemorySessionStore
    from app.services.session_data_redis import RedisSessionDataManager

    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=False)
    use_redis = {"value": False}

    def _factory_with_fake():
        from app.services.session_data import MemorySessionStore as MemStore

        if use_redis["value"]:
            return RedisSessionDataManager(
                redis_url="redis://unused", redis=fake_redis
            )
        return MemStore()

    monkeypatch.setattr(
        "app.services.session_data.create_session_data_manager",
        _factory_with_fake,
    )

    try:
        session_data_protocol._active_store = None
        # 规范单例：与模块级 session_data_manager 同一实例
        assert session_data_protocol.get_session_store() is session_data.session_data_manager
        # 工厂（直接调用）仍遵循 USE_REDIS 选择后端
        use_redis["value"] = False
        assert isinstance(_factory_with_fake(), MemorySessionStore)
        use_redis["value"] = True
        assert isinstance(_factory_with_fake(), RedisSessionDataManager)
        # 显式覆盖仍生效，重置后回到规范单例
        custom = MemorySessionStore()
        session_data_protocol.set_active_session_store(custom)
        assert session_data_protocol.get_session_store() is custom
        session_data_protocol.set_active_session_store(None)
        assert session_data_protocol.get_session_store() is session_data.session_data_manager
    finally:
        session_data_protocol._active_store = None
