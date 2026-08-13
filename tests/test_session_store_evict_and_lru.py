"""Shipped-path tests for Redis insert-then-evict and memory overwrite LRU."""
import pytest
import redis.asyncio as aioredis

from app.services.session_data import MemorySessionStore
from app.services.session_data_protocol import is_unavailable_ref
from app.services.session_data_redis import RedisSessionStore


class _FailNthExecute:
    """Wrap fakeredis so the Nth pipeline.execute() raises RedisError."""

    def __init__(self, inner, fail_on: int):
        self._inner = inner
        self.fail_on = fail_on
        self.executes = 0

    def pipeline(self, *args, **kwargs):
        return _FailNthPipe(self, self._inner.pipeline(*args, **kwargs))

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _FailNthPipe:
    def __init__(self, parent: _FailNthExecute, inner):
        self._parent = parent
        self._inner = inner

    async def execute(self, *args, **kwargs):
        self._parent.executes += 1
        if self._parent.executes == self._parent.fail_on:
            raise aioredis.RedisError("injected insert/evict failure")
        return await self._inner.execute(*args, **kwargs)

    async def __aenter__(self):
        await self._inner.__aenter__()
        return self

    async def __aexit__(self, *exc):
        return await self._inner.__aexit__(*exc)

    def __getattr__(self, name):
        return getattr(self._inner, name)


@pytest.mark.asyncio
async def test_redis_store_insert_failure_does_not_evict_existing():
    """At capacity, a failed write must not delete live refs.

    Evict-then-insert (BASE) commits eviction first: the next store's first
    pipeline is evict, the second is insert. Failing the second execute then
    drops the old ref and never writes the new one.

    Insert-then-evict (fix): first execute is insert. Failing the second
    (evict) keeps the new ref and does not wipe the prior payload as a
    precondition of the failed write.
    """
    import fakeredis.aioredis

    raw = fakeredis.aioredis.FakeRedis(decode_responses=False)
    store = RedisSessionStore(redis_url="redis://unused", redis=raw, capacity=1)
    first = await store.store("sess-evict", {"v": 1}, prefix="data")
    assert not is_unavailable_ref(first)
    assert await store.get("sess-evict", first) == {"v": 1}

    wrapped = _FailNthExecute(raw, fail_on=2)
    store._r = wrapped
    second = await store.store("sess-evict", {"v": 2}, prefix="data")

    # New payload is durable even if post-insert eviction fails.
    assert not is_unavailable_ref(second), second
    assert await store.get("sess-evict", second) == {"v": 2}


@pytest.mark.asyncio
async def test_memory_overwrite_bumps_lru_recency():
    """overwrite() is the plan/checkpoint durability path — it must not leave
    the just-updated ref as the next eviction victim."""
    store = MemorySessionStore(capacity=2)
    older = await store.store("sess-lru", {"n": 1})
    newer = await store.store("sess-lru", {"n": 2})
    assert await store.overwrite("sess-lru", older, {"n": 1, "updated": True})

    evicted_peer = await store.store("sess-lru", {"n": 3})
    assert await store.get("sess-lru", older) == {"n": 1, "updated": True}
    assert await store.get("sess-lru", evicted_peer) == {"n": 3}
    assert await store.get("sess-lru", newer) is None
