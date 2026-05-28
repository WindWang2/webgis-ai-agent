# Async Session Data Manager — Full Migration Design

**Goal:** Convert `session_data_manager` and all callers to a fully async interface, eliminating synchronous Redis blocking from the FastAPI event loop and enabling parallel ref-data loading in context build.

**Architecture:** Define an async `Protocol`, migrate both backends, then cascade `await` through all 14 call-site files. Fix the `event_log` passthrough bug (P2-2 item Z) as part of the context_builder migration.

**Tech Stack:** Python 3.11+, `redis.asyncio` (redis-py 4.x built-in), `pytest-asyncio` (already in requirements), `fakeredis` async variant for tests.

---

## Background

Every LLM request calls `compose_request_messages`, which calls `session_data_manager` methods. On the Redis backend these are synchronous blocking calls that hold the event loop while waiting for network I/O. The current `get_session_metadata()` pipeline batches 4 reads into 1 round-trip, but the event loop is still blocked during that round-trip. Full async migration makes those waits non-blocking, freeing the event loop to serve other concurrent requests.

Additionally, `build_map_state_summary` has a latent bug: it accepts an `event_log` parameter but ignores it and calls `get_event_log()` unconditionally (even when `_fetched=True`), adding one extra round-trip per LLM request. This is fixed as part of this migration.

---

## File Structure

### New files
- `app/services/session_data_protocol.py` — async `Protocol` (interface contract)

### Modified files
- `app/services/session_data.py` — memory backend: add `async def`, no `await` needed
- `app/services/session_data_redis.py` — Redis backend: switch to `redis.asyncio`, add `async/await`
- `app/tools/registry.py` — `_resolve_references` → async
- `app/services/chat/context_builder.py` — all `build_*` → async, gather for N refs, fix event_log bug
- `app/services/chat/dispatcher.py` — `await compose_request_messages(...)`
- `app/tools/layer_manager.py` — tool fns → async def, sdm calls → await (16 sites)
- `app/tools/map_view.py` — same (6 sites)
- `app/services/ws_service.py` — sdm calls → await (15 sites)
- `app/api/routes/layer.py` — 1 site
- `app/api/routes/chat.py` — 4 sites
- `app/services/plan_mode.py` — 3 sites
- `app/services/subagent.py` — 2 sites
- `app/services/chat_engine.py` — 3 sites
- `app/tasks/explorer/task_chain.py` — 2 sites
- `tests/**` — add `@pytest.mark.asyncio`, `await` on all sdm calls

---

## Component Designs

### 1. Protocol (`app/services/session_data_protocol.py`)

```python
from typing import Any, Optional, Protocol

class SessionDataProtocol(Protocol):
    async def get(self, session_id: str, ref_id_or_alias: str) -> Optional[Any]: ...
    async def store(self, session_id: str, data: Any, alias: str = "") -> str: ...
    async def list_refs(self, session_id: str) -> dict[str, str]: ...
    async def resolve_alias(self, session_id: str, ref_or_alias: str) -> str: ...
    async def get_map_state(self, session_id: str) -> dict[str, Any]: ...
    async def set_map_state(self, session_id: str, key: str, value: Any) -> None: ...
    async def get_event_log(self, session_id: str) -> list[dict]: ...
    async def append_event(self, session_id: str, event: str, data: dict) -> None: ...
    async def get_started_at(self, session_id: str) -> Optional[str]: ...
    async def get_session_metadata(self, session_id: str) -> dict[str, Any]: ...
    async def clear_session(self, session_id: str) -> None: ...
```

Both `MemorySessionDataManager` and `RedisSessionDataManager` satisfy this protocol via structural subtyping — no `class Foo(SessionDataProtocol)` inheritance required.

### 2. Memory Backend (`session_data.py`)

Mechanical change only: prefix all public methods with `async def`. No `await` added (dict operations are synchronous). The async keyword makes coroutines that return immediately.

```python
# Before
def get(self, session_id: str, ref_id_or_alias: str) -> Optional[Any]:
    ...

# After
async def get(self, session_id: str, ref_id_or_alias: str) -> Optional[Any]:
    ...  # body unchanged
```

### 3. Redis Backend (`session_data_redis.py`)

Switch client type and add `await` throughout.

```python
# Before
import redis

class RedisSessionDataManager:
    def __init__(self, redis_url: str):
        self._r = redis.Redis.from_url(redis_url, ...)

    def get(self, session_id, ref_id_or_alias):
        raw = self._r.get(data_key)
        pipe = self._r.pipeline()
        pipe.expire(...)
        pipe.execute()

# After
import redis.asyncio as aioredis

class RedisSessionDataManager:
    def __init__(self, redis_url: str):
        self._r = aioredis.Redis.from_url(redis_url, ...)

    async def get(self, session_id, ref_id_or_alias):
        raw = await self._r.get(data_key)
        async with self._r.pipeline() as pipe:
            pipe.expire(...)
            await pipe.execute()
```

`aioredis.Redis.from_url(...)` creates a connection pool synchronously (no network I/O at construction time), so the module-level singleton pattern is preserved.

Pipeline pattern: use `async with self._r.pipeline() as pipe:` for context-managed cleanup. Existing pipelines in `get_session_metadata`, `append_event`, `set_map_state` follow this pattern.

### 4. Registry (`registry.py`)

`_resolve_references` makes 3 sdm calls (`resolve_alias`, `get`, `list_refs`). Make it async and update the recursive calls.

```python
# Before
def _resolve_references(self, session_id, arguments, skip_keys=None):
    data = session_data_manager.get(session_id, arguments)
    return [self._resolve_references(session_id, v, skip_keys) for v in arguments]

# After
async def _resolve_references(self, session_id, arguments, skip_keys=None):
    data = await session_data_manager.get(session_id, arguments)
    return [await self._resolve_references(session_id, v, skip_keys) for v in arguments]
```

`_dispatch_impl` already has `async def`; add `await` before `self._resolve_references(...)`.

The tool dispatch path at lines 219–221 already handles `isawaitable`:
```python
result = self._tools[name](**arguments)
if inspect.isawaitable(result):
    result = await result
```
This means async tool functions work with **zero changes** to the dispatch logic.

### 5. Context Builder (`context_builder.py`)

Key changes:

**a) All `build_*` functions → `async def`**

**b) Fix event_log passthrough bug (P2-2 Z)**
```python
# Before (always fetches from Redis)
event_log = session_data_manager.get_event_log(session_id)

# After (use passed value if available)
if event_log is None:
    event_log = await session_data_manager.get_event_log(session_id)
```

`compose_request_messages` passes `event_log=event_log` from the `get_session_metadata` result, eliminating the extra round-trip.

**c) Parallel ref loading with `asyncio.gather`**

In `build_layer_schema`, when multiple refs need loading on cache miss:
```python
# Before (serial)
for ref_id in ref_ids:
    data = session_data_manager.get(session_id, ref_id)

# After (parallel)
import asyncio
datas = await asyncio.gather(*[
    session_data_manager.get(session_id, ref_id) for ref_id in ref_ids
])
```

Layer schema cache (P2-1) still applies — `asyncio.gather` only runs on cache misses.

**d) `compose_request_messages` signature**
```python
async def compose_request_messages(session_id: str, messages: list[dict]) -> list[dict]:
```

### 6. Tool Files

All tool registration closures/lambdas that use `session_data_manager` become `async def`:

```python
# Before
def get_layer_data(layer_ref: str, session_id: str = None) -> dict:
    ref_id = session_data_manager.resolve_alias(session_id, layer_ref)
    data = session_data_manager.get(session_id, ref_id)
    ...

# After
async def get_layer_data(layer_ref: str, session_id: str = None) -> dict:
    ref_id = await session_data_manager.resolve_alias(session_id, layer_ref)
    data = await session_data_manager.get(session_id, ref_id)
    ...
```

Files requiring this change: `layer_manager.py` (16 sites), `map_view.py` (6 sites).

Tools that don't use `session_data_manager` (e.g., `chart.py`, `annotation.py`, `coord_transform.py`) need **no changes**.

### 7. Services and Routes

All callers are already `async def` functions (FastAPI routes, WebSocket handlers, etc.) — adding `await` before each `session_data_manager.*` call is mechanical.

High-count files:
- `ws_service.py` (15 sites) — WebSocket handler, already async
- `api/routes/chat.py` (4 sites) — FastAPI route handlers, already async

---

## Data Flow (after migration)

```
FastAPI request (async)
  └─ await dispatcher.stream(session_id, messages)
       └─ await compose_request_messages(session_id, messages)
            └─ await sdm.get_session_metadata(session_id)   ← 1 Redis pipeline, non-blocking
                 # event_log, map_state, list_refs, started_at all returned
            └─ await build_map_state_summary(..., event_log=event_log)
                 └─ asyncio.gather(*[sdm.get(s, r) for r in refs])  ← parallel on cache miss
       └─ await tool_fn(..., session_id=session_id)  ← already async via registry
            └─ await sdm.get(session_id, ref_id)
            └─ await sdm.store(session_id, result)
            └─ await sdm.append_event(session_id, ...)
```

---

## Error Handling

**Redis unavailable:** Both Redis backend methods and `get_session_metadata` already have `except redis.RedisError` fallback paths. These become `except aioredis.RedisError` (same class hierarchy under `redis.exceptions`).

**Tool errors:** Unchanged — tools return error dicts, dispatcher handles them via existing `std_error_response` path.

**Pipeline failures:** `async with self._r.pipeline() as pipe` cleans up on exception. Fall back to individual calls on `RedisError` (same as current `get_session_metadata` fallback pattern).

---

## Testing

**`pytest-asyncio`** already in `requirements.txt`. Add to `pytest.ini`:
```ini
[pytest]
asyncio_mode = auto
```
This marks all `async def test_*` functions automatically, eliminating per-test `@pytest.mark.asyncio`.

**In-memory backend tests:** Add `async def` to test functions, add `await` before sdm calls. Behavior unchanged.

**Redis backend tests:** Switch from `fakeredis.FakeRedis` to `fakeredis.FakeRedis` with `aioredis` mode:
```python
import fakeredis
server = fakeredis.FakeServer()
redis_client = fakeredis.FakeRedis(server=server, connected=True)
```
For async, use `fakeredis.aioredis.FakeRedis(server=server)` (available in fakeredis >= 2.0).

**Regression focus:**
- `test_context_builder_round1.py` — verify `get_event_log` not called when `event_log` passed
- `test_layer_manager_phase2.py` — all 13 tool tests pass with async signatures
- `test_viewport_naming.py` — existing async tests should work unchanged
- `test_context_builder_injection.py` — security tests pass, async only adds `await`

---

## Build Sequence

1. `session_data_protocol.py` — new file, zero risk
2. `session_data.py` — memory backend async, no behavior change; all existing tests pass
3. `session_data_redis.py` — Redis async backend; run Redis-specific tests with fakeredis
4. `context_builder.py` — async + gather + event_log fix; update context_builder tests
5. `registry.py` — async `_resolve_references`; dispatch tests
6. `layer_manager.py` + `map_view.py` — tool async migration
7. Remaining services/routes (`ws_service.py`, `chat.py`, `plan_mode.py`, `subagent.py`, `chat_engine.py`, `task_chain.py`, `dispatcher.py`)
8. `tests/` — add `asyncio_mode = auto` to `pytest.ini`; add `await` to all sdm calls in tests

Each task in steps 1–7 is independently testable. Run `pytest` after each step.

---

## Non-goals

- `tool_cache.py` (`get_cached`/`set_cached`) — remains synchronous. These are fast in-process operations and the async wrapper in `@cached_tool` already handles awaitable tool functions. Converting to async Redis here yields negligible benefit.
- WebSocket connection management — not changed.
- Database (SQLAlchemy) sessions — already have an `async_db_session()` helper; unchanged.
