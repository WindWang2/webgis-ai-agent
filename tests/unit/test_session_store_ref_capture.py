"""Session stores report only refs created by the active task context."""
import asyncio

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "redis"])
async def test_store_capture_is_task_scoped_and_excludes_control_refs(backend):
    from app.lib.runtime.ref_capture import capture_created_refs
    if backend == "memory":
        from app.services.session_data import MemorySessionStore
        store = MemorySessionStore()
    else:
        import fakeredis.aioredis
        from app.services.session_data_redis import RedisSessionStore
        store = RedisSessionStore("redis://fake-capture/0")
        store._r = fakeredis.aioredis.FakeRedis()
        async def connected():
            return store._r
        store._ensure_connected = connected

    async def task(name):
        with capture_created_refs() as refs:
            created = await store.store("same-session", {"name": name})
            await store.store("same-session", {"revision": 1}, prefix="sessionplan-current")
        return created, refs.snapshot()

    left, right = await asyncio.gather(task("left"), task("right"))
    assert left[0] != right[0]
    assert left[1] == [left[0]]
    assert right[1] == [right[0]]
