# Async Session Data Manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert `session_data_manager` and all 14 caller files to a fully async interface using `redis.asyncio`, eliminating synchronous Redis blocking from the FastAPI event loop, and fixing the `event_log` passthrough bug in `build_map_state_summary`.

**Architecture:** Define an async `Protocol`, migrate both backends, then cascade `await` through all caller files. `@cached_tool` and registry dispatch already handle async tool functions — zero changes needed there. Fix the `build_map_state_summary` event_log bug (line 474 always calls `get_event_log()` even when `_fetched=True`). Celery tasks in `task_chain.py` use `asyncio.run()` since they run outside the FastAPI event loop.

**Tech Stack:** Python 3.11+, `redis.asyncio` (redis-py 4.x built-in), `pytest-asyncio` with `asyncio_mode = auto` (already in `pytest.ini`), `fakeredis.aioredis` for Redis tests.

---

## File Structure

### New files
- `app/services/session_data_protocol.py` — async `Protocol` interface
- `tests/test_async_session_data.py` — async backend tests

### Modified files
- `app/services/session_data.py` — add `async def` to all public methods
- `app/services/session_data_redis.py` — switch to `redis.asyncio`, add `async/await` throughout
- `app/services/chat/context_builder.py` — all `build_*` → async, fix event_log bug, add `asyncio.gather` for parallel ref loads
- `app/tools/registry.py` — `_resolve_references` → async
- `app/tools/layer_manager.py` — 8 tool functions → async def, 16 sdm calls → await
- `app/tools/map_view.py` — tool functions → async def, 6 sdm calls → await
- `app/services/ws_service.py` — 13 handler functions → async def, 16 sdm calls → await
- `app/api/routes/chat.py` — 4 sdm calls → await
- `app/api/routes/layer.py` — 1 sdm call → await
- `app/services/plan_mode.py` — `store_plan`, `load_plan`, `update_plan_status` → async def
- `app/services/subagent.py` — 2 sdm calls → await
- `app/services/chat_engine.py` — 4 sdm calls → await, `_compose_request_messages` wrapper → async
- `app/tasks/explorer/task_chain.py` — `_store_ref`/`_load_ref` → use `asyncio.run()`, other calls → same
- `tests/test_context_builder_round1.py` — fixtures → async, calls → await
- `tests/test_context_builder_round2.py` — same
- `tests/test_context_builder_injection.py` — same
- `tests/test_layer_manager_phase2.py` — fixtures → async
- `tests/test_map_view_tools.py` — fixtures → async
- `tests/test_selected_feature.py` — fixtures → async
- `tests/test_session_overview.py` — fixtures → async, calls → await
- `tests/test_ws_service.py` — fixtures → async
- `tests/test_viewport_naming.py` — fixtures → async
- `tests/test_history_compression.py` — fixtures → async
- `tests/test_layer_api.py` — fixtures → async

---

## Task 1: Protocol File

**Files:**
- Create: `app/services/session_data_protocol.py`

This is a zero-risk type annotation file. No tests needed — it's a `Protocol` that both backends satisfy via structural subtyping.

- [ ] **Step 1: Create protocol file**

```python
# app/services/session_data_protocol.py
from typing import Any, Optional, Protocol


class SessionDataProtocol(Protocol):
    async def get(self, session_id: str, ref_id_or_alias: str) -> Optional[Any]: ...
    async def store(self, session_id: str, data: Any, prefix: str = "data") -> str: ...
    async def set_alias(self, session_id: str, ref_id: str, alias: str) -> None: ...
    async def list_refs(self, session_id: str) -> dict[str, str]: ...
    async def resolve_alias(self, session_id: str, ref_or_alias: str) -> str: ...
    async def get_map_state(self, session_id: str) -> dict[str, Any]: ...
    async def set_map_state(self, session_id: str, key: str, value: Any) -> None: ...
    async def update_layer_in_state(self, session_id: str, layer_id: str, updates: dict) -> None: ...
    async def remove_layer_from_state(self, session_id: str, layer_id: str) -> None: ...
    async def get_event_log(self, session_id: str) -> list[dict]: ...
    async def append_event(self, session_id: str, event: str, data: dict) -> None: ...
    async def get_started_at(self, session_id: str) -> Optional[str]: ...
    async def get_session_metadata(self, session_id: str) -> dict[str, Any]: ...
    async def clear_session(self, session_id: str) -> None: ...
```

- [ ] **Step 2: Commit**

```bash
git add app/services/session_data_protocol.py
git commit -m "feat(async-sdm): add SessionDataProtocol async interface"
```

---

## Task 2: Memory Backend Async Migration

**Files:**
- Modify: `app/services/session_data.py`
- Test: `tests/test_async_session_data.py`

Mechanical change: prefix all public methods with `async def`. No `await` needed (dict operations are synchronous). The async keyword makes each method return a coroutine that resolves immediately.

- [ ] **Step 1: Write failing test**

Create `tests/test_async_session_data.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_async_session_data.py -v -k "test_memory"
```

Expected: FAIL with `TypeError: object str can't be used in 'await' expression`

- [ ] **Step 3: Add `async def` to all public methods in `session_data.py`**

Change lines 23, 44, 50, 60, 80, 100, 108, 113, 117, 128, 133, 143, 147, 156 — the `def` keyword for each public method becomes `async def`. Body is unchanged in all cases.

```python
# Before (example — same change applies to all 14 public methods)
def store(self, session_id: str, data: Any, prefix: str = "data") -> str:

# After
async def store(self, session_id: str, data: Any, prefix: str = "data") -> str:
```

Full list of methods to change (add `async` before `def`):
- `store` (line 23)
- `set_alias` (line 44)
- `resolve_alias` (line 50)
- `get` (line 60)
- `list_refs` (line 80)
- `set_map_state` (line 100)
- `get_started_at` (line 108)
- `get_map_state` (line 113)
- `update_layer_in_state` (line 117)
- `remove_layer_from_state` (line 128)
- `append_event` (line 133)
- `get_event_log` (line 143)
- `get_session_metadata` (line 147)
- `clear_session` (line 156)

Note: `cleanup_idle_sessions` (line 163) is an internal housekeeping method, not in the Protocol — make it async too for consistency.

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_async_session_data.py -v -k "test_memory"
```

Expected: 6 PASSED

- [ ] **Step 5: Run full test suite**

```bash
pytest --ignore=tests/smoke_deep_enhancement.py -x -q 2>&1 | tail -20
```

Expected: existing tests FAIL because they call `session_data_manager.store(...)` without `await`. That is expected — we'll fix tests in Task 9. For now, check only the test_async_session_data tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/services/session_data.py tests/test_async_session_data.py
git commit -m "feat(async-sdm): make MemorySessionDataManager fully async"
```

---

## Task 3: Redis Backend Async Migration

**Files:**
- Modify: `app/services/session_data_redis.py`
- Test: `tests/test_async_session_data.py` (add Redis test cases)

Switch `redis.Redis` to `redis.asyncio.Redis`. Add `async def` + `await` to all methods. Use `async with self._r.pipeline() as pipe:` for pipeline pattern.

- [ ] **Step 1: Add failing Redis tests to `test_async_session_data.py`**

Append to `tests/test_async_session_data.py`:

```python
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
    assert meta["map_state"].get("zoom") == 10
    assert len(meta["event_log"]) == 1


async def test_redis_clear_session(fake_redis_sdm):
    await fake_redis_sdm.store("rs5", {})
    await fake_redis_sdm.clear_session("rs5")
    refs = await fake_redis_sdm.list_refs("rs5")
    assert refs == {}
```

- [ ] **Step 2: Run to verify fail**

```bash
pytest tests/test_async_session_data.py -v -k "redis"
```

Expected: ImportError or TypeError since `RedisSessionDataManager` is still sync.

- [ ] **Step 3: Rewrite `session_data_redis.py`**

Full replacement of the file. Key changes:
1. `import redis` → `import redis.asyncio as aioredis`
2. Remove `_AliasesProxy` class (unused externally since P3-5 migration to `resolve_alias`)
3. Constructor: change `self._r = redis.Redis.from_url(...)` → `self._r = aioredis.Redis.from_url(...)`; remove `self._aliases = _AliasesProxy(self._r)`
4. `ping()` stays sync but uses `asyncio.run()` for startup health check
5. All public methods: `def` → `async def`, add `await` before every Redis call
6. Pipelines: `pipe = self._r.pipeline()` + `pipe.execute()` → `async with self._r.pipeline() as pipe:` + `await pipe.execute()`
7. `_evict_ref` and `_refresh_session_ttl` remain sync helpers that add commands to an open pipeline (no await needed since they just queue commands)
8. Exception type: `redis.RedisError` → `aioredis.RedisError`

```python
"""Redis-backed session data manager - persistent storage with TTL and LRU eviction"""
import asyncio
import json
import time
import uuid
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

DATA_TTL = 2 * 60 * 60
STATE_TTL = 4 * 60 * 60
EVENTS_TTL = 4 * 60 * 60
SESSION_TTL = 4 * 60 * 60
MAX_EVENTS = 20


class RedisSessionDataManager:
    """Session-level data store backed by Redis with cursor support (LRU)."""

    def __init__(self, redis_url: str, capacity: int = 200, socket_timeout: float = 1.0):
        self._r = aioredis.Redis.from_url(
            redis_url,
            decode_responses=False,
            socket_timeout=socket_timeout,
            socket_connect_timeout=socket_timeout,
        )
        self.capacity = capacity

    def ping(self):
        """Sync health check for startup. asyncio.run() is safe here — no event loop exists at import time."""
        asyncio.run(self._r.ping())

    # --- key helpers (unchanged) ---
    @staticmethod
    def _data_key(session_id: str, ref_id: str) -> str:
        return f"session:{session_id}:data:{ref_id}"

    @staticmethod
    def _aliases_key(session_id: str) -> str:
        return f"session:{session_id}:aliases"

    @staticmethod
    def _refs_key(session_id: str) -> str:
        return f"session:{session_id}:refs"

    @staticmethod
    def _state_key(session_id: str) -> str:
        return f"session:{session_id}:state"

    @staticmethod
    def _events_key(session_id: str) -> str:
        return f"session:{session_id}:events"

    @staticmethod
    def _index_key(session_id: str) -> str:
        return f"session:{session_id}:index"

    @staticmethod
    def _refs_order_key(session_id: str) -> str:
        return f"session:{session_id}:refs_order"

    @staticmethod
    def _active_key() -> str:
        return "sessions:active"

    # --- core interface ---

    async def store(self, session_id: str, data: Any, prefix: str = "data") -> str:
        ref_id = f"ref:{prefix}-{uuid.uuid4().hex[:16]}"
        data_key = self._data_key(session_id, ref_id)
        order_key = self._refs_order_key(session_id)

        current_count = await self._r.zcard(order_key)
        if current_count >= self.capacity:
            overflow = current_count - self.capacity + 1
            oldest = await self._r.zrange(order_key, 0, overflow - 1)
            async with self._r.pipeline() as evict_pipe:
                for old_ref_bytes in oldest:
                    old_ref = old_ref_bytes.decode() if isinstance(old_ref_bytes, bytes) else old_ref_bytes
                    await self._evict_ref(evict_pipe, session_id, old_ref)
                await evict_pipe.execute()

        async with self._r.pipeline() as pipe:
            pipe.sadd(self._active_key(), session_id)
            pipe.set(data_key, json.dumps(data, ensure_ascii=False), ex=DATA_TTL)
            pipe.zadd(order_key, {ref_id: time.time()})
            pipe.sadd(self._index_key(session_id), ref_id)
            self._refresh_session_ttl(pipe, session_id)
            await pipe.execute()
        return ref_id

    async def set_alias(self, session_id: str, ref_id: str, alias: str) -> None:
        async with self._r.pipeline() as pipe:
            pipe.hset(self._aliases_key(session_id), alias, ref_id)
            pipe.hset(self._refs_key(session_id), ref_id, alias)
            self._refresh_session_ttl(pipe, session_id)
            await pipe.execute()

    async def resolve_alias(self, session_id: str, ref_or_alias: str) -> str:
        ref_id = await self._r.hget(self._aliases_key(session_id), ref_or_alias)
        if ref_id is None:
            return ref_or_alias
        return ref_id.decode() if isinstance(ref_id, bytes) else ref_id

    async def get(self, session_id: str, ref_id_or_alias: str) -> Optional[Any]:
        ref_id = await self._r.hget(self._aliases_key(session_id), ref_id_or_alias)
        if ref_id is not None:
            ref_id = ref_id.decode() if isinstance(ref_id, bytes) else ref_id
        else:
            ref_id = ref_id_or_alias

        data_key = self._data_key(session_id, ref_id)
        raw = await self._r.get(data_key)
        if raw is None:
            return None

        async with self._r.pipeline() as pipe:
            pipe.expire(data_key, DATA_TTL)
            pipe.zadd(self._refs_order_key(session_id), {ref_id: time.time()})
            await pipe.execute()
        return json.loads(raw)

    async def list_refs(self, session_id: str) -> dict[str, str]:
        ref_ids_bytes = await self._r.zrange(self._refs_order_key(session_id), 0, -1)
        if not ref_ids_bytes:
            return {}
        ref_ids = [r.decode() if isinstance(r, bytes) else r for r in ref_ids_bytes]
        raw_refs = await self._r.hgetall(self._refs_key(session_id))
        ref_to_alias = {
            (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
            for k, v in raw_refs.items()
        }
        return {rid: ref_to_alias.get(rid, "") for rid in ref_ids}

    async def set_map_state(self, session_id: str, key: str, value: Any) -> None:
        async with self._r.pipeline() as pipe:
            pipe.hsetnx(
                self._state_key(session_id),
                "_started_at",
                json.dumps(datetime.now(timezone.utc).isoformat(), ensure_ascii=False),
            )
            pipe.hset(self._state_key(session_id), key, json.dumps(value, ensure_ascii=False))
            pipe.expire(self._state_key(session_id), STATE_TTL)
            pipe.sadd(self._active_key(), session_id)
            self._refresh_session_ttl(pipe, session_id)
            await pipe.execute()

    async def get_started_at(self, session_id: str) -> Optional[str]:
        raw = await self._r.hget(self._state_key(session_id), "_started_at")
        if not raw:
            return None
        return json.loads(raw)

    async def get_map_state(self, session_id: str) -> dict[str, Any]:
        raw = await self._r.hgetall(self._state_key(session_id))
        if not raw:
            return {}
        return {
            (k.decode() if isinstance(k, bytes) else k): json.loads(v)
            for k, v in raw.items()
        }

    async def update_layer_in_state(self, session_id: str, layer_id: str, updates: dict) -> None:
        state = await self.get_map_state(session_id)
        layers = list(state.get("layers", []))
        for layer in layers:
            if layer.get("id") == layer_id:
                layer.update(updates)
                break
        else:
            layers.append({"id": layer_id, **updates})
        await self.set_map_state(session_id, "layers", layers)

    async def remove_layer_from_state(self, session_id: str, layer_id: str) -> None:
        state = await self.get_map_state(session_id)
        layers = state.get("layers", [])
        await self.set_map_state(
            session_id, "layers",
            [l for l in layers if l.get("id") != layer_id],
        )

    async def append_event(self, session_id: str, event: str, data: dict) -> None:
        entry = json.dumps(
            {"event": event, "data": data, "timestamp": datetime.now().isoformat()},
            ensure_ascii=False,
        )
        async with self._r.pipeline() as pipe:
            key = self._events_key(session_id)
            pipe.lpush(key, entry)
            pipe.ltrim(key, 0, MAX_EVENTS - 1)
            pipe.expire(key, EVENTS_TTL)
            pipe.sadd(self._active_key(), session_id)
            self._refresh_session_ttl(pipe, session_id)
            await pipe.execute()

    async def get_event_log(self, session_id: str) -> list[dict]:
        raw_list = await self._r.lrange(self._events_key(session_id), 0, -1)
        return [
            json.loads(item.decode() if isinstance(item, bytes) else item)
            for item in raw_list
        ]

    async def get_session_metadata(self, session_id: str) -> dict[str, Any]:
        """Fetch session metadata in a single async pipeline."""
        async with self._r.pipeline() as pipe:
            pipe.hgetall(self._state_key(session_id))
            pipe.zrange(self._refs_order_key(session_id), 0, -1)
            pipe.hgetall(self._refs_key(session_id))
            pipe.lrange(self._events_key(session_id), 0, -1)
            try:
                state_raw, ref_ids_bytes, raw_refs, events_raw = await pipe.execute()
            except aioredis.RedisError as e:
                logger.error("Failed to fetch session metadata via pipeline for %s: %s", session_id, e)
                return {
                    "map_state": await self.get_map_state(session_id),
                    "list_refs": await self.list_refs(session_id),
                    "event_log": await self.get_event_log(session_id),
                    "started_at": await self.get_started_at(session_id),
                }

        map_state: dict = {}
        started_at = None
        if state_raw:
            for k, v in state_raw.items():
                key = k.decode() if isinstance(k, bytes) else k
                try:
                    map_state[key] = json.loads(v)
                except (json.JSONDecodeError, TypeError):
                    continue
            started_at = map_state.get("_started_at")

        ref_ids = [r.decode() if isinstance(r, bytes) else r for r in (ref_ids_bytes or [])]
        ref_to_alias = {
            (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
            for k, v in (raw_refs or {}).items()
        }
        list_refs = {rid: ref_to_alias.get(rid, "") for rid in ref_ids}

        event_log = []
        for item in (events_raw or []):
            text = item.decode() if isinstance(item, bytes) else item
            try:
                event_log.append(json.loads(text))
            except (json.JSONDecodeError, TypeError):
                continue

        return {
            "map_state": map_state,
            "list_refs": list_refs,
            "event_log": event_log,
            "started_at": started_at,
        }

    async def clear_session(self, session_id: str) -> None:
        index_key = self._index_key(session_id)
        ref_ids = await self._r.smembers(index_key)
        async with self._r.pipeline() as pipe:
            for ref_bytes in ref_ids:
                ref_id = ref_bytes.decode() if isinstance(ref_bytes, bytes) else ref_bytes
                pipe.delete(self._data_key(session_id, ref_id))
            pipe.delete(
                index_key,
                self._aliases_key(session_id),
                self._refs_key(session_id),
                self._state_key(session_id),
                self._events_key(session_id),
                self._refs_order_key(session_id),
            )
            pipe.srem(self._active_key(), session_id)
            await pipe.execute()

    async def cleanup_idle_sessions(self, max_sessions: int = 100) -> None:
        active = await self._r.smembers(self._active_key())
        if not active or len(active) <= max_sessions:
            return
        scored = []
        for sid_bytes in active:
            sid = sid_bytes.decode() if isinstance(sid_bytes, bytes) else sid_bytes
            earliest = await self._r.zrange(self._refs_order_key(sid), 0, 0, withscores=True)
            score = earliest[0][1] if earliest else 0
            scored.append((sid, score))
        scored.sort(key=lambda x: x[1])
        to_remove = len(scored) - max_sessions + 10
        for sid, _ in scored[:to_remove]:
            await self.clear_session(sid)
        logger.info("Cleaned up %d idle sessions", min(to_remove, len(scored)))

    # --- private helpers ---

    async def _evict_ref(self, pipe, session_id: str, ref_id: str) -> None:
        """Add eviction commands to an open pipeline. The alias hget must be awaited outside."""
        # Look up alias outside pipeline (needs immediate result to feed into hdel)
        alias = await self._r.hget(self._refs_key(session_id), ref_id)
        pipe.delete(self._data_key(session_id, ref_id))
        pipe.zrem(self._refs_order_key(session_id), ref_id)
        pipe.srem(self._index_key(session_id), ref_id)
        if alias:
            alias_str = alias.decode() if isinstance(alias, bytes) else alias
            pipe.hdel(self._aliases_key(session_id), alias_str)
        pipe.hdel(self._refs_key(session_id), ref_id)

    def _refresh_session_ttl(self, pipe, session_id: str) -> None:
        for key in [
            self._aliases_key(session_id),
            self._refs_key(session_id),
            self._refs_order_key(session_id),
            self._index_key(session_id),
        ]:
            pipe.expire(key, SESSION_TTL)
```

- [ ] **Step 4: Run Redis tests**

```bash
pytest tests/test_async_session_data.py -v -k "redis"
```

Expected: 5 PASSED

- [ ] **Step 5: Run full async session data tests**

```bash
pytest tests/test_async_session_data.py -v
```

Expected: 11 PASSED

- [ ] **Step 6: Commit**

```bash
git add app/services/session_data_redis.py tests/test_async_session_data.py
git commit -m "feat(async-sdm): migrate RedisSessionDataManager to redis.asyncio"
```

---

## Task 4: Context Builder Async Migration

**Files:**
- Modify: `app/services/chat/context_builder.py`
- Test: `tests/test_context_builder_round1.py` (partial — full test migration in Task 9)

Key changes:
1. `build_layer_schema` → `async def`, `await session_data_manager.get(...)`
2. `format_layer_lines` → `async def`, use `asyncio.gather` for parallel schema loading
3. `build_session_overview` → `async def`, `await` sdm calls in `_fetched=False` path
4. `build_map_state_summary` → `async def`, fix event_log bug at line 474
5. `compose_request_messages` → `async def`, `await` all `build_*` calls

- [ ] **Step 1: Write failing test for event_log bug fix**

Add to `tests/test_context_builder_round1.py` (will be run after async migration — add but skip for now with a note):

```python
async def test_event_log_not_fetched_again_when_prefetched(monkeypatch):
    """P2-2 bug: get_event_log must NOT be called again when event_log is already passed in."""
    from app.services.chat.context_builder import build_map_state_summary
    from app.services.session_data import session_data_manager

    call_count = 0
    original = session_data_manager.get_event_log
    async def counting_get_event_log(session_id):
        nonlocal call_count
        call_count += 1
        return await original(session_id)
    monkeypatch.setattr(session_data_manager, "get_event_log", counting_get_event_log)

    await build_map_state_summary(
        "prefetch-test",
        state={},
        inventory={},
        event_log=[{"event": "prefetched", "data": {}}],
        _fetched=True,
    )
    assert call_count == 0, "get_event_log was called even though event_log was prefetched"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_context_builder_round1.py::test_event_log_not_fetched_again_when_prefetched -v
```

Expected: FAIL — `TypeError: 'coroutine' object is not iterable` or similar (sync sdm call can't be awaited yet, plus the function is sync)

- [ ] **Step 3: Migrate `context_builder.py`**

Add `import asyncio` at the top (after the existing imports). Then apply these changes:

**3a. `build_layer_schema` (line 101):**
```python
# Before
def build_layer_schema(session_id: str, ref_id: str, sample_size: int = 5) -> dict | None:
    ...
    data = session_data_manager.get(session_id, ref_id)

# After
async def build_layer_schema(session_id: str, ref_id: str, sample_size: int = 5) -> dict | None:
    ...
    data = await session_data_manager.get(session_id, ref_id)
```

**3b. `format_layer_lines` (line 555):**
```python
# Before
def format_layer_lines(inventory, active_layers, session_id=None, viewport_bounds=None) -> list[str]:
    ...
    schema = build_layer_schema(session_id, ref_id)

# After
async def format_layer_lines(inventory, active_layers, session_id=None, viewport_bounds=None) -> list[str]:
    ...
    # Parallel schema loading (asyncio.gather runs cache hits and misses concurrently)
    if inventory and session_id:
        ref_ids_with_inv = [(rid, alias) for rid, alias in inventory.items()]
        schemas = await asyncio.gather(
            *[build_layer_schema(session_id, rid) for rid, _ in ref_ids_with_inv],
            return_exceptions=True,
        )
        schema_map = {
            rid: (s if isinstance(s, dict) else None)
            for (rid, _), s in zip(ref_ids_with_inv, schemas)
        }
    else:
        schema_map = {}

    out: list[str] = []
    if inventory:
        visibility_map = {l.get("id"): l for l in active_layers if l.get("id")}
        for ref_id, alias in inventory.items():
            meta = visibility_map.get(ref_id) or next(
                (m for aid, m in visibility_map.items() if aid in ref_id or ref_id in aid), {},
            )
            visible = meta.get("visible")
            status = "可见" if visible is True else "隐藏" if visible is False else "未知"
            attrs = []
            if alias:
                attrs.append(f"别名={_untrusted(alias)}")
            if meta.get("type"):
                attrs.append(f"类型={_untrusted(meta['type'])}")
            if meta.get("featureCount") is not None:
                attrs.append(f"要素={meta['featureCount']}")
            style_str = format_style_summary(meta.get("style"))
            if style_str:
                attrs.append(style_str)
            tail = f" [{', '.join(attrs)}]" if attrs else ""
            line = f"{ref_id}{tail} ({status})"
            schema = schema_map.get(ref_id)
            if schema:
                line += f" | {format_layer_schema(schema, viewport_bounds)}"
            out.append(line)
        return out
    # fallback: frontend-reported layers (unchanged)
    for layer in active_layers:
        lid = layer.get("id", "unknown")
        name = layer.get("name", lid)
        attrs = []
        if layer.get("type"):
            attrs.append(f"类型={_untrusted(layer['type'])}")
        if layer.get("featureCount") is not None:
            attrs.append(f"要素={layer['featureCount']}")
        opacity = layer.get("opacity", 1.0)
        attrs.append(f"不透明度={opacity:.0%}")
        style_str = format_style_summary(layer.get("style"))
        if style_str:
            attrs.append(style_str)
        status = "可见" if layer.get("visible") else "隐藏"
        out.append(f"{_untrusted(name)} (id={_untrusted(lid)}, {', '.join(attrs)}) ({status})")
    return out
```

**3c. `build_session_overview` (line 243):**
```python
# Before
def build_session_overview(session_id, messages=None, started_at=None, event_log=None, inventory=None, _fetched=False) -> str | None:
    if not _fetched:
        started_at = session_data_manager.get_started_at(session_id) ...
        event_log = session_data_manager.get_event_log(session_id) or []
        inventory = session_data_manager.list_refs(session_id) or {}

# After
async def build_session_overview(session_id, messages=None, started_at=None, event_log=None, inventory=None, _fetched=False) -> str | None:
    if not _fetched:
        started_at = await session_data_manager.get_started_at(session_id) if hasattr(session_data_manager, "get_started_at") else None
        event_log = await session_data_manager.get_event_log(session_id) or []
        inventory = await session_data_manager.list_refs(session_id) or {}
```

**3d. `build_map_state_summary` (line 379):**

Change function signature to `async def`. Update the `_fetched=False` path:
```python
# Before (lines 392-395)
if not _fetched:
    state = session_data_manager.get_map_state(session_id)
    inventory = session_data_manager.list_refs(session_id)

# After
if not _fetched:
    state = await session_data_manager.get_map_state(session_id)
    inventory = await session_data_manager.list_refs(session_id)
```

Change `format_layer_lines` call to `await`:
```python
# Before (line 462)
layer_lines = format_layer_lines(...)

# After
layer_lines = await format_layer_lines(...)
```

**Fix the event_log bug (line 474):**
```python
# Before (bug: ignores the event_log parameter, always re-fetches)
event_log = session_data_manager.get_event_log(session_id)
tool_calls, user_actions, pending = _split_events(event_log)

# After (use passed value if available)
if event_log is None:
    event_log = await session_data_manager.get_event_log(session_id)
tool_calls, user_actions, pending = _split_events(event_log)
```

**3e. `compose_request_messages` (line 792):**
```python
# Before
def compose_request_messages(session_id: str, messages: list[dict]) -> list[dict]:
    ...
    if hasattr(session_data_manager, "get_session_metadata"):
        metadata = session_data_manager.get_session_metadata(session_id)
        ...
        env_summary = build_map_state_summary(session_id, state=map_state, inventory=list_refs, _fetched=True)
        overview = build_session_overview(session_id, messages, started_at=started_at, event_log=event_log, inventory=list_refs, _fetched=True)
    else:
        env_summary = build_map_state_summary(session_id)
        overview = build_session_overview(session_id, messages)

# After
async def compose_request_messages(session_id: str, messages: list[dict]) -> list[dict]:
    ...
    if hasattr(session_data_manager, "get_session_metadata"):
        metadata = await session_data_manager.get_session_metadata(session_id)
        ...
        env_summary = await build_map_state_summary(
            session_id, state=map_state, inventory=list_refs, event_log=event_log, _fetched=True
        )
        overview = await build_session_overview(
            session_id, messages, started_at=started_at, event_log=event_log, inventory=list_refs, _fetched=True
        )
    else:
        env_summary = await build_map_state_summary(session_id)
        overview = await build_session_overview(session_id, messages)
```

- [ ] **Step 4: Run the event_log bug test**

```bash
pytest tests/test_context_builder_round1.py::test_event_log_not_fetched_again_when_prefetched -v
```

Expected: PASS

- [ ] **Step 5: Run context_builder tests (will have failures from sync fixtures — expected)**

```bash
pytest tests/test_context_builder_round1.py tests/test_context_builder_round2.py tests/test_context_builder_injection.py -v 2>&1 | tail -20
```

Expected: most tests fail because fixtures call sync sdm. Will be fixed in Task 9.

- [ ] **Step 6: Commit**

```bash
git add app/services/chat/context_builder.py tests/test_context_builder_round1.py
git commit -m "feat(async-sdm): make context_builder fully async, fix event_log passthrough bug"
```

---

## Task 5: Registry `_resolve_references` Async

**Files:**
- Modify: `app/tools/registry.py`

`_resolve_references` makes 3 sdm calls and is called from `_dispatch_impl` which is already `async def`.

- [ ] **Step 1: Make `_resolve_references` async**

```python
# Before (line 266)
def _resolve_references(self, session_id: str, arguments: Any, skip_keys: Optional[set[str]] = None) -> Any:
    ...
    _resolved = session_data_manager.resolve_alias(session_id, arguments) if session_id else arguments
    ...
    data = session_data_manager.get(session_id, arguments)
    ...
    available_refs = session_data_manager.list_refs(session_id)
    ...
    return [self._resolve_references(session_id, v, skip_keys) for v in arguments]

# After
async def _resolve_references(self, session_id: str, arguments: Any, skip_keys: Optional[set[str]] = None) -> Any:
    ...
    _resolved = await session_data_manager.resolve_alias(session_id, arguments) if session_id else arguments
    ...
    data = await session_data_manager.get(session_id, arguments)
    ...
    available_refs = await session_data_manager.list_refs(session_id)
    ...
    return [await self._resolve_references(session_id, v, skip_keys) for v in arguments]
```

Also update the call site in `_dispatch_impl` (line 184):
```python
# Before
arguments = self._resolve_references(session_id, arguments, skip_keys={...})

# After
arguments = await self._resolve_references(session_id, arguments, skip_keys={...})
```

- [ ] **Step 2: Run registry tests**

```bash
pytest tests/test_tool_registry.py -v
```

Expected: existing tests may fail if fixtures are sync — check. If test_tool_registry.py calls sdm, it will need fixture updates too (Task 9). Run and note failures.

- [ ] **Step 3: Commit**

```bash
git add app/tools/registry.py
git commit -m "feat(async-sdm): make _resolve_references async"
```

---

## Task 6: Layer Manager Tool Functions Async

**Files:**
- Modify: `app/tools/layer_manager.py`

16 sdm call sites across 8 tool functions. Pattern: add `async def` to function definition, `await` before each sdm call.

- [ ] **Step 1: Find all sdm call sites**

```bash
grep -n "session_data_manager\." app/tools/layer_manager.py
```

Expected output shows sites at lines 39, 54, 108, 126, 129, 167, 168, 223, 230, 232, 238, 270, 271, 288, 316, 319.

- [ ] **Step 2: Add `async def` and `await` to all tool functions that call sdm**

For each function that calls `session_data_manager.*`, add `async` before `def` and `await` before each call.

The functions to convert are (verify with `grep -n "^def " app/tools/layer_manager.py`):
- `put_layer` (contains lines 39, 54)
- `set_base_layer` (contains line 108)
- `show_hide_layer` (contains lines 126, 129)
- `remove_layer` (contains lines 167, 168)
- `reorder_layer` (contains lines 223, 230, 232, 238)
- `update_layer_style` (contains lines 270, 271)
- `get_layer_data` (contains lines 288+)
- `rename_layer` (contains lines 316, 319)

Pattern for each:
```python
# Before
def put_layer(geojson: dict, alias: str = "", session_id: str = None) -> dict:
    ref_id = session_data_manager.store(session_id, geojson, prefix="geojson")
    session_data_manager.set_alias(session_id, ref_id, alias)
    layers = session_data_manager.list_refs(session_id)
    ...

# After
async def put_layer(geojson: dict, alias: str = "", session_id: str = None) -> dict:
    ref_id = await session_data_manager.store(session_id, geojson, prefix="geojson")
    await session_data_manager.set_alias(session_id, ref_id, alias)
    layers = await session_data_manager.list_refs(session_id)
    ...
```

Apply this same pattern to all 8 functions: `async def` + `await` before every `session_data_manager.*` call.

- [ ] **Step 3: Run layer manager tests**

```bash
pytest tests/test_layer_manager_phase2.py -v
```

Expected: tests may fail if fixtures are sync (see Task 9). Tool dispatch tests that use `await registry.dispatch(...)` should work once fixtures are fixed. Note which tests fail.

- [ ] **Step 4: Commit**

```bash
git add app/tools/layer_manager.py
git commit -m "feat(async-sdm): make layer_manager tool functions async"
```

---

## Task 7: Map View Tool Functions Async

**Files:**
- Modify: `app/tools/map_view.py`

6 sdm call sites across tool functions.

- [ ] **Step 1: Find all sdm call sites**

```bash
grep -n "session_data_manager\." app/tools/map_view.py
```

Expected: lines 89, 91, 93, 193, 197, 204 (approximately).

- [ ] **Step 2: Add `async def` and `await`**

Pattern is identical to Task 6. For each function containing sdm calls:
```python
# Before
def get_visible_layers(layer_ref: str = None, session_id: str = None) -> dict:
    candidate = session_data_manager.resolve_alias(session_id, layer_ref)
    map_state = session_data_manager.get_map_state(session_id) or {}
    ...

# After
async def get_visible_layers(layer_ref: str = None, session_id: str = None) -> dict:
    candidate = await session_data_manager.resolve_alias(session_id, layer_ref)
    map_state = await session_data_manager.get_map_state(session_id) or {}
    ...
```

Apply same pattern to all functions touching sdm (check via grep output from Step 1).

- [ ] **Step 3: Run map view tests**

```bash
pytest tests/test_map_view_tools.py -v
```

Note failures (expected until Task 9 fixes fixtures).

- [ ] **Step 4: Commit**

```bash
git add app/tools/map_view.py
git commit -m "feat(async-sdm): make map_view tool functions async"
```

---

## Task 8: Remaining Services and Routes

**Files:**
- Modify: `app/services/ws_service.py`
- Modify: `app/api/routes/chat.py`
- Modify: `app/api/routes/layer.py`
- Modify: `app/services/plan_mode.py`
- Modify: `app/services/subagent.py`
- Modify: `app/services/chat_engine.py`
- Modify: `app/tasks/explorer/task_chain.py`

All callers are already `async def` functions (FastAPI routes, WebSocket handlers) except:
- `ws_service.py` handlers: currently sync `def`, must become `async def`
- `plan_mode.py` store/load functions: called from `async def execute_plan_async`, must become `async def`
- `task_chain.py`: Celery tasks (sync context), use `asyncio.run()` instead of `await`

### 8a. `ws_service.py`

- [ ] **Step 1: Convert all 13 handler functions from `def` to `async def` and add `await`**

```bash
grep -n "^def handle_" app/services/ws_service.py
```

For each handler function listed (handle_viewport_change, handle_layer_toggled, handle_layer_opacity, handle_layer_removed, handle_base_layer_changed, handle_mode_changed, handle_upload, handle_state_snapshot, handle_layers_changed, handle_layers_reordered, etc.):

```python
# Before
def handle_viewport_change(session_id: str, data: dict):
    session_data_manager.set_map_state(session_id, "viewport", viewport)

# After
async def handle_viewport_change(session_id: str, data: dict):
    await session_data_manager.set_map_state(session_id, "viewport", viewport)
```

Apply `async def` + `await` before all 16 sdm call sites in the file.

Also check if these handlers are called from other async contexts and update callers as needed.

### 8b. `chat.py`

- [ ] **Step 2: Add `await` to 4 sdm calls (routes already `async def`)**

```bash
grep -n "session_data_manager\." app/api/routes/chat.py
```

At lines 138, 157, 159, 161 — all inside `async def` route handlers. Add `await`:
```python
# Before (line 138)
state = session_data_manager.get_map_state(session_id)

# After
state = await session_data_manager.get_map_state(session_id)
```

Same for lines 157, 159, 161.

### 8c. `layer.py`

- [ ] **Step 3: Add `await` to 1 sdm call (route already `async def`)**

```python
# Before (line 40)
data = session_data_manager.get(session_id, ref_id)

# After
data = await session_data_manager.get(session_id, ref_id)
```

### 8d. `plan_mode.py`

- [ ] **Step 4: Make `store_plan`, `load_plan`, `update_plan_status` async**

```python
# Before
def store_plan(session_id: str, plan: PlanProposal) -> str:
    ...
    return session_data_manager.store(session_id, payload, prefix="plan")

def load_plan(session_id: str, plan_id: str) -> Optional[dict]:
    return session_data_manager.get(session_id, plan_id)

def update_plan_status(session_id: str, plan_id: str, **updates: Any) -> None:
    plan_data = load_plan(session_id, plan_id)
    ...

# After
async def store_plan(session_id: str, plan: PlanProposal) -> str:
    ...
    return await session_data_manager.store(session_id, payload, prefix="plan")

async def load_plan(session_id: str, plan_id: str) -> Optional[dict]:
    return await session_data_manager.get(session_id, plan_id)

async def update_plan_status(session_id: str, plan_id: str, **updates: Any) -> None:
    plan_data = await load_plan(session_id, plan_id)
    ...
```

Also update all callers of these functions in `chat_engine.py` and `plan_mode.py` itself to add `await`.

### 8e. `subagent.py`

- [ ] **Step 5: Add `await` to 2 sdm calls (already in `async def run`)**

```python
# Before (lines 155, 181)
refs_before = set(session_data_manager.list_refs(self.parent_session_id).keys())
refs_after = set(session_data_manager.list_refs(self.parent_session_id).keys())

# After
refs_before = set((await session_data_manager.list_refs(self.parent_session_id)).keys())
refs_after = set((await session_data_manager.list_refs(self.parent_session_id)).keys())
```

### 8f. `chat_engine.py`

- [ ] **Step 6: Add `await` to 4 sdm calls, make `_compose_request_messages` async**

Lines 400, 493 (inside `async def chat` and `async def chat_stream`):
```python
# Before
session_data_manager.set_map_state(session_id, k, v)

# After
await session_data_manager.set_map_state(session_id, k, v)
```

Line 775 (inside `async def clear_session`):
```python
# Before
session_data_manager.clear_session(session_id)

# After
await session_data_manager.clear_session(session_id)
```

The wrapper at line 202:
```python
# Before
def _compose_request_messages(self, session_id: str, messages: list[dict]) -> list[dict]:
    return _compose_request_messages_fn(session_id, messages)

# After
async def _compose_request_messages(self, session_id: str, messages: list[dict]) -> list[dict]:
    return await _compose_request_messages_fn(session_id, messages)
```

Update callers of `_compose_request_messages` at lines ~417 and ~543:
```python
# Before
messages_with_context = self._compose_request_messages(session_id, messages)

# After
messages_with_context = await self._compose_request_messages(session_id, messages)
```

### 8g. `task_chain.py`

- [ ] **Step 7: Use `asyncio.run()` in Celery task helpers**

```python
# Before
def _store_ref(data: dict, prefix: str = "explorer") -> str:
    from app.services.session_data import session_data_manager
    ref_id = session_data_manager.store("explorer", data, prefix=prefix)
    return ref_id

def _load_ref(ref_id: str):
    from app.services.session_data import session_data_manager
    return session_data_manager.get("explorer", ref_id)

# After
def _store_ref(data: dict, prefix: str = "explorer") -> str:
    from app.services.session_data import session_data_manager
    return asyncio.run(session_data_manager.store("explorer", data, prefix=prefix))

def _load_ref(ref_id: str):
    from app.services.session_data import session_data_manager
    return asyncio.run(session_data_manager.get("explorer", ref_id))
```

For the other sdm calls in task_chain.py (lines 126, 151, 153, 185 — inside Celery `@task` functions):
```python
# Before
session_data_manager.append_event(session_id, "tool_executed", event_payload)
geojson_ref = session_data_manager.store(session_id, target_data, prefix="geojson")

# After
asyncio.run(session_data_manager.append_event(session_id, "tool_executed", event_payload))
geojson_ref = asyncio.run(session_data_manager.store(session_id, target_data, prefix="geojson"))
```

Note: `asyncio` is already imported at line 3 of `task_chain.py`.

- [ ] **Step 8: Run full test suite to check non-test-fixture failures**

```bash
pytest -x -q --ignore=tests/smoke_deep_enhancement.py 2>&1 | grep -E "FAILED|ERROR|passed|failed" | tail -20
```

- [ ] **Step 9: Commit all service/route changes**

```bash
git add app/services/ws_service.py app/api/routes/chat.py app/api/routes/layer.py \
        app/services/plan_mode.py app/services/subagent.py app/services/chat_engine.py \
        app/tasks/explorer/task_chain.py
git commit -m "feat(async-sdm): add await to all session_data_manager call sites in services/routes"
```

---

## Task 9: Test Suite Migration

**Files:**
- Modify: 11 test files (listed below)

`asyncio_mode = auto` is already in `pytest.ini` — no change needed there. The only required changes are:
1. Make `@pytest.fixture` functions that call sdm into `async def` fixtures
2. Add `await` before all sdm calls in fixtures and test functions
3. Where `def test_*` functions call async functions (like `build_layer_schema`, `compose_request_messages`), convert them to `async def test_*`

The pattern is identical across all 11 files. I'll show it fully for one file and then list the others.

- [ ] **Step 1: Migrate `tests/test_context_builder_round1.py`**

Change sync fixtures that call sdm to async:
```python
# Before
@pytest.fixture
def session_with_polygon_layer():
    sid = "ctx-round1-session"
    ref = session_data_manager.store(sid, gj, prefix="t")
    session_data_manager.set_alias(sid, ref, "街道")
    session_data_manager.set_map_state(sid, "viewport", {...})
    session_data_manager.set_map_state(sid, "base_layer", "OSM 地图")
    session_data_manager.set_map_state(sid, "layers", [...])
    yield sid, ref
    session_data_manager.clear_session(sid)

# After
@pytest.fixture
async def session_with_polygon_layer():
    sid = "ctx-round1-session"
    ref = await session_data_manager.store(sid, gj, prefix="t")
    await session_data_manager.set_alias(sid, ref, "街道")
    await session_data_manager.set_map_state(sid, "viewport", {...})
    await session_data_manager.set_map_state(sid, "base_layer", "OSM 地图")
    await session_data_manager.set_map_state(sid, "layers", [...])
    yield sid, ref
    await session_data_manager.clear_session(sid)
```

Change sync test functions that call async functions:
```python
# Before
def test_build_layer_schema_infers_types(session_with_polygon_layer):
    sid, ref = session_with_polygon_layer
    schema = build_layer_schema(sid, ref)

# After
async def test_build_layer_schema_infers_types(session_with_polygon_layer):
    sid, ref = session_with_polygon_layer
    schema = await build_layer_schema(sid, ref)
```

Apply same fixture/test conversion to all `def test_*` functions in this file that call async functions.

- [ ] **Step 2: Run test_context_builder_round1.py**

```bash
pytest tests/test_context_builder_round1.py -v
```

Expected: All PASSED.

- [ ] **Step 3: Migrate remaining 10 test files**

Apply the same pattern (async fixtures + await before sdm/build_* calls) to:

- `tests/test_context_builder_round2.py`
- `tests/test_context_builder_injection.py`
- `tests/test_layer_manager_phase2.py` — fixtures that call sdm.store, sdm.set_alias, sdm.clear_session
- `tests/test_map_view_tools.py` — same
- `tests/test_selected_feature.py` — same
- `tests/test_session_overview.py` — same, plus test functions that call `build_session_overview`
- `tests/test_ws_service.py` — fixtures + direct sdm calls in tests
- `tests/test_viewport_naming.py` — fixtures
- `tests/test_history_compression.py` — fixtures + calls to `compose_request_messages`
- `tests/test_layer_api.py` — fixtures

For each file, the rule is:
1. `grep -n "session_data_manager\." tests/test_FILE.py` — find all sdm calls
2. For each call: if it's in a fixture, make fixture `async def`; if it's in a test, make test `async def`
3. Add `await` before every sdm call
4. For calls to context_builder functions that are now async (`build_layer_schema`, `build_map_state_summary`, `compose_request_messages`, etc.) — add `await`

- [ ] **Step 4: Run the full test suite**

```bash
pytest -q --ignore=tests/smoke_deep_enhancement.py 2>&1 | tail -10
```

Expected: all tests PASS (zero failures, zero errors related to async sdm).

- [ ] **Step 5: Commit**

```bash
git add tests/
git commit -m "feat(async-sdm): migrate all tests to async fixtures and await sdm calls"
```

---

## Final Verification

- [ ] **Run the full test suite one more time**

```bash
pytest -q --ignore=tests/smoke_deep_enhancement.py
```

Expected: zero failures.

- [ ] **Verify no blocking sync Redis calls remain**

```bash
grep -rn "session_data_manager\." app/ | grep -v "await session_data_manager\." | grep -v "asyncio.run(session_data_manager\." | grep -v "hasattr(session_data_manager" | grep -v "#"
```

Expected: zero results (every sdm call is either `await`-ed or wrapped in `asyncio.run()`).

- [ ] **Final commit**

```bash
git add -A
git commit -m "feat(async-sdm): complete async session_data_manager migration

Full async migration of session_data_manager and all 14 caller files.
Eliminates synchronous Redis blocking from FastAPI event loop.
Fixes event_log passthrough bug in build_map_state_summary (P2-2 item Z).
Enables parallel ref loading via asyncio.gather in format_layer_lines."
```

---

## Notes

- `tool_cache.py` (`get_cached`/`set_cached`) stays synchronous — fast in-process operations, no benefit from async.
- `@cached_tool` decorator already handles async tool functions via `inspect.iscoroutinefunction` — zero changes needed there.
- Registry dispatch at lines 219-221 already uses `if inspect.isawaitable(result): result = await result` — zero changes needed there.
- `_AliasesProxy` class is removed in Task 3 since all callers use `resolve_alias()` (P3-5 migration already done).
- Celery tasks in `task_chain.py` use `asyncio.run()` — safe because Celery workers run outside the FastAPI event loop.
