"""Regression tests for review-swarm P2 findings in session_data_redis.py.

Covers:
- update_layer_in_state: non-list ``layers`` / non-dict elements degrade to
  False instead of leaking AttributeError/TypeError.
- overwrite: a WatchError (concurrent get() recency bump / control-ref write)
  is retried instead of being swallowed as a failed Redis write.
- get_ref_descriptor fallback: a descriptor computed from a payload that an
  overwrite superseded must not be cached (stale shape + new revision).
"""
import asyncio
import json

import fakeredis.aioredis
import pytest
import redis.asyncio as aioredis

from app.services.session_data_redis import RedisSessionStore


def _store() -> RedisSessionStore:
    return RedisSessionStore(
        redis_url="redis://unused",
        redis=fakeredis.aioredis.FakeRedis(decode_responses=False),
    )


def _fc(n: int) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"n": i},
                "geometry": {
                    "type": "Point",
                    "coordinates": [116.0 + i * 0.001, 39.9],
                },
            }
            for i in range(n)
        ],
    }


# ── update_layer_in_state: malformed layers shape degrades ────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "layers_value",
    [{"id": "L1"}, ["not-a-dict"], 42],
    ids=["json-dict", "non-dict-element", "non-list-scalar"],
)
async def test_update_layer_in_state_malformed_layers_returns_false(layers_value):
    store = _store()
    sid = "sess-layer-shape"
    state_key = store._state_key(sid)
    await store._r.hset(state_key, "layers", json.dumps(layers_value))

    ok = await store.update_layer_in_state(sid, "L1", {"opacity": 0.5})

    assert ok is False
    # No write must be attempted for malformed state.
    assert json.loads(await store._r.hget(state_key, "layers")) == layers_value


# ── overwrite: WatchError is a retryable conflict, not a write failure ─────

class _WatchErrorOncePipe:
    def __init__(self, parent, inner):
        self._parent = parent
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def __aenter__(self):
        await self._inner.__aenter__()
        return self

    async def __aexit__(self, *exc):
        return await self._inner.__aexit__(*exc)

    async def execute(self, *args, **kwargs):
        if self._parent.watch_errors == 0:
            self._parent.watch_errors += 1
            raise aioredis.WatchError("simulated concurrent modification")
        return await self._inner.execute(*args, **kwargs)


class _WatchErrorOnceRedis:
    """Delegates to fakeredis; the first pipeline EXEC raises WatchError."""

    def __init__(self, inner):
        self._inner = inner
        self.watch_errors = 0

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def pipeline(self, *args, **kwargs):
        return _WatchErrorOncePipe(self, self._inner.pipeline(*args, **kwargs))


@pytest.mark.asyncio
async def test_overwrite_retries_watch_error_and_succeeds():
    raw = fakeredis.aioredis.FakeRedis(decode_responses=False)
    store = RedisSessionStore(redis_url="redis://unused", redis=raw)
    sid = "sess-overwrite-watch"
    ref = await store.store(sid, {"v": 1})

    wrapped = _WatchErrorOnceRedis(raw)
    store._r = wrapped

    ok = await store.overwrite(sid, ref, {"v": 2})

    assert wrapped.watch_errors == 1, "WatchError was not raised by the fixture"
    assert ok is True, "WatchError must be retried, not reported as a failed write"
    assert json.loads(await raw.get(store._data_key(sid, ref))) == {"v": 2}


# ── get_ref_descriptor fallback: never cache a superseded payload ──────────

@pytest.mark.asyncio
async def test_descriptor_fallback_does_not_cache_superseded_payload(monkeypatch):
    store = _store()
    sid = "sess-descriptor-race"
    ref = await store.store(sid, _fc(1))
    await store._r.delete(store._descriptor_key(sid, ref))

    from app.schemas import ref_descriptor

    orig_compute = ref_descriptor.compute_descriptor
    loop = asyncio.get_running_loop()
    calls = {"n": 0}

    def racing_compute(ref_id, data):
        calls["n"] += 1
        if calls["n"] == 1:
            # The fallback has already read the OLD payload; the overwrite
            # commits (new payload + revision, descriptor delete) before the
            # fallback gets to write its descriptor.
            fut = asyncio.run_coroutine_threadsafe(
                store.overwrite(sid, ref, _fc(3)), loop
            )
            fut.result(timeout=10)
        return orig_compute(ref_id, data)

    monkeypatch.setattr(ref_descriptor, "compute_descriptor", racing_compute)

    d = await store.get_ref_descriptor(sid, ref)

    assert calls["n"] >= 2, "stale fallback descriptor was not recomputed"
    assert d is not None
    assert d["feature_count"] == 3, "descriptor served from the superseded payload"
    assert d["content_revision"] == 2

    raw = await store._r.get(store._descriptor_key(sid, ref))
    assert raw is not None, "descriptor cache was not repopulated from the new payload"
    cached = json.loads(raw)
    assert cached["feature_count"] == 3, "stale shape metadata was cached"
    assert cached["content_revision"] == 2, "stale shape metadata carries the new revision"
