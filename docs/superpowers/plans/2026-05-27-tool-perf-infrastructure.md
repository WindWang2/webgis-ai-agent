# Tool Performance Infrastructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land a three-layer tool perf infrastructure (Redis-backed result cache + automatic per-dispatch timing + payload-trim helper) and opt the four heaviest tools into it.

**Architecture:** Three independent concerns. `@cached_tool` decorator is opt-in per tool, keyed by `sha256(tool_name + canonical_args)` into Redis with TTL. The tool registry's `dispatch()` is wrapped with automatic timing that writes one JSONL line per call to `logs/tool_metrics.jsonl` plus an in-process aggregator that emits a `TOOL_METRICS_DIGEST` log line every 100 calls and at FastAPI lifespan shutdown. `trim_features()` is a plain helper tools call inside their function body before returning — it caps feature count and rounds coordinate precision, adding a top-level `_trim` envelope.

**Tech Stack:** Python 3, FastAPI, `redis-py` (sync), pytest + pytest-asyncio (auto mode), Pydantic v2. Reuses existing patterns from `app/services/session_data_redis.py` (sync `redis.Redis.from_url`) and `app/tools/registry.py` (sync + async tools dispatched via `inspect.isawaitable`).

**Source spec:** `docs/superpowers/specs/2026-05-27-tool-perf-infrastructure-design.md`

**Scope:** Phase 1 (infrastructure + timing for ALL tools, zero behavior change for users) + Phase 2 (opt the four heaviest tools into cache + trim). Phase 3 from the spec (data-driven second-tier opt-in after a week of digest data) is deliberately not in this plan — it requires production data that doesn't exist yet.

**Test runner:** `pytest tests/<file>::<test_name> -v` for single tests; `pytest tests/<file> -v` for a file; `pytest -v` for full suite. Existing `pytest.ini` enforces `--cov=app --cov-report=term-missing`. Don't run with `--cov-fail-under` locally unless asked — that's the CI gate.

**Conventions observed in this codebase:**
- Chinese-mixed prose in docstrings and design docs; bare-English identifiers and code.
- Sync tools predominantly; `chat_engine` adapts both via `inspect.isawaitable`.
- Tests live flat in `tests/` named `test_<thing>.py`; integration tests under `tests/integration/`.
- Conventional commit prefixes: `feat(area):`, `fix(area):`, `perf(area):`, `test(coverage):`, `docs:`, `chore:`.
- Commit trailer: `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>`.

---

## File Structure

### New files
| File | Responsibility |
|------|---------------|
| `app/lib/tool_cache.py` | Cache primitive: key generation (with ref-skip), Redis get/set with graceful degradation, the `@cached_tool` decorator (sync + async). Owns its own `redis.Redis` client built from `settings.REDIS_URL` — does NOT share session_data_manager's instance (separation of concerns). |
| `app/services/tool_metrics.py` | Per-call JSONL writer (`RotatingFileHandler`, 10MB cap, 5 backups), thread-safe aggregator (`{tool: (count, total_ms, max_ms, hit_count, error_count)}`), digest emitter triggered at N=100 calls and via explicit `emit_digest()`. |
| `tests/test_tool_cache.py` | Key normalization, ref-skip, Redis-down fallback, decorator sync/async/skip_if. |
| `tests/test_tool_metrics.py` | Record format, aggregator counts, digest at N=100 and on demand. |
| `tests/test_tool_trim.py` | max_features, precision rounding, mixed geometry, non-FC pass-through. |
| `tests/test_registry_timing.py` | Registry dispatch → metrics row; cache_hit propagates correctly. |
| `tests/test_heatmap_caching.py` | End-to-end: second identical heatmap call returns from cache; trim envelope appears. |

### Modified files
| File | What changes |
|------|--------------|
| `app/tools/_utils.py` | Add `trim_features(fc, max_features=5000, precision=6)` and re-export `cached_tool` from `app.lib.tool_cache` for ergonomic imports. |
| `app/tools/registry.py` | Wrap the body of `dispatch()` with timing + a `ContextVar` so `@cached_tool` can flip `cache_hit=True` when serving from cache. |
| `app/main.py` | Call `tool_metrics.emit_digest()` inside the lifespan after `yield`. |
| `app/tools/spatial.py` | Opt `buffer_analysis` and `heatmap_data` into `@cached_tool` + `trim_features`. |
| `app/tools/advanced_spatial.py` | Opt `h3_binning` into `@cached_tool` + `trim_features`. |
| `app/tools/spatial_stats.py` | Opt `kde_contours` into `@cached_tool` + `trim_features`. |

---

## PHASE 1: Infrastructure (Tasks 1–9)

Every task in Phase 1 is TDD: failing test first, minimal implementation, verify pass, commit. Each task is one logical unit and one commit.

---

### Task 1: Cache key generation (`make_cache_key`)

**Files:**
- Create: `app/lib/tool_cache.py`
- Create: `tests/test_tool_cache.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tool_cache.py` with:

```python
"""Cache key generation tests for app.lib.tool_cache."""
import pytest

from app.lib.tool_cache import make_cache_key


def test_make_cache_key_deterministic():
    k1 = make_cache_key("heatmap_data", {"a": 1, "b": 2})
    k2 = make_cache_key("heatmap_data", {"a": 1, "b": 2})
    assert k1 == k2
    assert k1.startswith("tool_cache:v1:")
    # 16 hex chars after the prefix
    assert len(k1.split(":")[-1]) == 16


def test_make_cache_key_sorted_keys():
    # Same args in different insertion order must produce the same key.
    k1 = make_cache_key("heatmap_data", {"a": 1, "b": 2})
    k2 = make_cache_key("heatmap_data", {"b": 2, "a": 1})
    assert k1 == k2


def test_make_cache_key_tool_name_in_hash():
    k1 = make_cache_key("heatmap_data", {"a": 1})
    k2 = make_cache_key("h3_binning", {"a": 1})
    assert k1 != k2


def test_make_cache_key_nonjson_falls_back_to_str():
    from datetime import datetime
    # Should NOT raise — default=str handles datetime, set, etc.
    k = make_cache_key("x", {"t": datetime(2026, 5, 27)})
    assert k.startswith("tool_cache:v1:")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tool_cache.py -v`
Expected: 4 FAILs with `ImportError: cannot import name 'make_cache_key' from 'app.lib.tool_cache'` or `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

Create `app/lib/tool_cache.py`:

```python
"""工具结果缓存层 — Redis-backed, opt-in per tool.

入口：make_cache_key(name, args)、cached_tool(...) 装饰器（后续 Task 加入）。
键命名空间 tool_cache:v1:<sha256[:16]>，全量失效一条 SCAN | DEL 即可。
"""
import hashlib
import json
from typing import Optional


def make_cache_key(tool_name: str, args: dict) -> Optional[str]:
    """构造确定性缓存键。

    args 内任一叶子值是 'ref:' 开头的字符串时返回 None — 调用方据此跳过缓存。
    （ref:xxx 是会话内可变数据引用，同一引用不同时刻解析结果不同。）
    """
    if _contains_ref(args):
        return None
    canonical = json.dumps(args, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(f"{tool_name}::{canonical}".encode()).hexdigest()[:16]
    return f"tool_cache:v1:{digest}"


def _contains_ref(value) -> bool:
    """递归检查任一叶子是否是 'ref:' 开头的字符串。"""
    if isinstance(value, str):
        return value.startswith("ref:")
    if isinstance(value, dict):
        return any(_contains_ref(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_ref(v) for v in value)
    return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tool_cache.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Add the ref-skip test, watch it pass without further code**

Append to `tests/test_tool_cache.py`:

```python
def test_make_cache_key_skips_ref_string():
    assert make_cache_key("x", {"geojson": "ref:abc123"}) is None


def test_make_cache_key_skips_ref_nested_in_list():
    assert make_cache_key("x", {"items": ["a", "ref:b"]}) is None


def test_make_cache_key_skips_ref_deep_nested():
    assert make_cache_key("x", {"a": {"b": {"c": "ref:x"}}}) is None


def test_make_cache_key_no_ref_returns_key():
    assert make_cache_key("x", {"a": "normal", "b": ["c", "d"]}) is not None
```

Run: `pytest tests/test_tool_cache.py -v`
Expected: 8 PASS.

- [ ] **Step 6: Commit**

```bash
git add app/lib/tool_cache.py tests/test_tool_cache.py
git commit -m "$(cat <<'EOF'
feat(tool_cache): add make_cache_key with ref-skip detection

Deterministic sha256-based keys with sorted JSON args, namespaced under
tool_cache:v1:. Any 'ref:xxx' leaf in args yields None — callers skip cache
to avoid serving stale results for mutable session-data references.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Redis get/set primitives with graceful degradation

**Files:**
- Modify: `app/lib/tool_cache.py`
- Modify: `tests/test_tool_cache.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tool_cache.py`:

```python
from unittest.mock import patch, MagicMock

from app.lib.tool_cache import get_cached, set_cached, _reset_redis_client_for_tests


@pytest.fixture(autouse=True)
def _reset_redis():
    """每个测试重置模块级 redis 单例，避免测试间污染。"""
    _reset_redis_client_for_tests()
    yield
    _reset_redis_client_for_tests()


def test_get_cached_miss_returns_none():
    with patch("app.lib.tool_cache._get_redis_client") as mock_client:
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_client.return_value = mock_redis
        assert get_cached("tool_cache:v1:nope") is None


def test_get_cached_hit_decodes_json():
    with patch("app.lib.tool_cache._get_redis_client") as mock_client:
        mock_redis = MagicMock()
        mock_redis.get.return_value = b'{"success": true, "data": 42}'
        mock_client.return_value = mock_redis
        assert get_cached("tool_cache:v1:hit") == {"success": True, "data": 42}


def test_set_cached_writes_with_ttl():
    with patch("app.lib.tool_cache._get_redis_client") as mock_client:
        mock_redis = MagicMock()
        mock_client.return_value = mock_redis
        set_cached("tool_cache:v1:k", {"a": 1}, ttl=3600)
        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args
        assert call_args.args[0] == "tool_cache:v1:k"
        assert call_args.args[1] == 3600
        # value is JSON-encoded bytes
        import json as j
        assert j.loads(call_args.args[2]) == {"a": 1}


def test_get_cached_redis_down_returns_none_does_not_raise():
    """Redis 抛 ConnectionError → get_cached 返回 None，工具调用照常走未命中路径。"""
    import redis as redis_mod
    with patch("app.lib.tool_cache._get_redis_client") as mock_client:
        mock_redis = MagicMock()
        mock_redis.get.side_effect = redis_mod.ConnectionError("down")
        mock_client.return_value = mock_redis
        # MUST NOT raise
        assert get_cached("tool_cache:v1:k") is None


def test_set_cached_redis_down_swallows_error():
    """Redis SET 失败时工具调用必须照常返回结果。"""
    import redis as redis_mod
    with patch("app.lib.tool_cache._get_redis_client") as mock_client:
        mock_redis = MagicMock()
        mock_redis.setex.side_effect = redis_mod.ConnectionError("down")
        mock_client.return_value = mock_redis
        # MUST NOT raise
        set_cached("tool_cache:v1:k", {"a": 1}, ttl=3600)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tool_cache.py -v -k "get_cached or set_cached"`
Expected: 5 FAILs with `ImportError: cannot import name 'get_cached'`.

- [ ] **Step 3: Write minimal implementation**

Append to `app/lib/tool_cache.py`:

```python
import json as _json
import logging
import time
from typing import Any

import redis as _redis

from app.core.config import settings

logger = logging.getLogger(__name__)

# 进程级单例。lazy 初始化使得 import 期间 Redis 不可达也不会炸 import。
_redis_client: Optional["_redis.Redis"] = None
_last_warning_ts: float = 0.0
_WARN_THROTTLE_SEC = 60.0


def _get_redis_client() -> "_redis.Redis":
    global _redis_client
    if _redis_client is None:
        _redis_client = _redis.Redis.from_url(
            settings.REDIS_URL,
            decode_responses=False,
            socket_timeout=1.0,
            socket_connect_timeout=1.0,
        )
    return _redis_client


def _reset_redis_client_for_tests() -> None:
    """仅供测试使用：清空单例 + warning 时间戳。"""
    global _redis_client, _last_warning_ts
    _redis_client = None
    _last_warning_ts = 0.0


def _warn_throttled(msg: str) -> None:
    global _last_warning_ts
    now = time.monotonic()
    if now - _last_warning_ts >= _WARN_THROTTLE_SEC:
        logger.warning(msg)
        _last_warning_ts = now


def get_cached(key: str) -> Optional[Any]:
    """Redis 读。失败/未命中均返回 None — 调用方据此走未命中路径。"""
    try:
        raw = _get_redis_client().get(key)
    except _redis.RedisError as e:
        _warn_throttled(f"[tool_cache] Redis GET failed, bypassing cache: {type(e).__name__}: {e}")
        return None
    if raw is None:
        return None
    try:
        return _json.loads(raw)
    except (_json.JSONDecodeError, TypeError) as e:
        logger.warning(f"[tool_cache] Corrupt cache value at {key}: {e}")
        return None


def set_cached(key: str, value: Any, ttl: int) -> None:
    """Redis 写。失败时仅 warning，绝不抛 — 不能因缓存写失败导致用户请求失败。"""
    try:
        payload = _json.dumps(value, default=str).encode("utf-8")
        _get_redis_client().setex(key, ttl, payload)
    except (_redis.RedisError, TypeError, ValueError) as e:
        _warn_throttled(f"[tool_cache] Redis SET failed, dropping cache write: {type(e).__name__}: {e}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tool_cache.py -v`
Expected: 13 PASS (8 from Task 1 + 5 new).

- [ ] **Step 5: Commit**

```bash
git add app/lib/tool_cache.py tests/test_tool_cache.py
git commit -m "$(cat <<'EOF'
feat(tool_cache): add get_cached/set_cached with graceful Redis fallback

Lazy module-level redis.Redis client built from settings.REDIS_URL, separate
from session_data_manager. ConnectionError / RedisError on either path is
logged (throttled to once per minute) and swallowed — get returns None,
set drops the write. User-facing tool calls never fail due to cache problems.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `@cached_tool` decorator — sync, async, skip_if

**Files:**
- Modify: `app/lib/tool_cache.py`
- Modify: `tests/test_tool_cache.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tool_cache.py`:

```python
import asyncio
from unittest.mock import MagicMock, patch

from app.lib.tool_cache import cached_tool


def test_cached_tool_sync_second_call_skips_inner():
    """第二次相同参数：内层函数不再被调用，结果来自 Redis。"""
    inner = MagicMock(return_value={"success": True, "data": "computed"})
    inner.__name__ = "fake_tool"

    storage = {}

    def fake_get(key):
        return storage.get(key)

    def fake_setex(key, ttl, value):
        storage[key] = value

    with patch("app.lib.tool_cache._get_redis_client") as mock_client:
        mock_redis = MagicMock()
        mock_redis.get.side_effect = fake_get
        mock_redis.setex.side_effect = fake_setex
        mock_client.return_value = mock_redis

        wrapped = cached_tool(ttl=3600)(inner)
        r1 = wrapped(geojson="x", distance=10)
        r2 = wrapped(geojson="x", distance=10)

    assert r1 == r2 == {"success": True, "data": "computed"}
    assert inner.call_count == 1  # 第二次没调用内层


def test_cached_tool_sync_different_args_both_compute():
    inner = MagicMock(return_value={"r": 1})
    inner.__name__ = "fake_tool"

    storage = {}
    with patch("app.lib.tool_cache._get_redis_client") as mock_client:
        mock_redis = MagicMock()
        mock_redis.get.side_effect = lambda k: storage.get(k)
        mock_redis.setex.side_effect = lambda k, ttl, v: storage.__setitem__(k, v)
        mock_client.return_value = mock_redis

        wrapped = cached_tool(ttl=3600)(inner)
        wrapped(x=1)
        wrapped(x=2)

    assert inner.call_count == 2


def test_cached_tool_async_second_call_skips_inner():
    inner = MagicMock()
    inner.__name__ = "fake_async_tool"

    async def async_impl(**kwargs):
        inner(**kwargs)
        return {"r": "async"}

    storage = {}
    with patch("app.lib.tool_cache._get_redis_client") as mock_client:
        mock_redis = MagicMock()
        mock_redis.get.side_effect = lambda k: storage.get(k)
        mock_redis.setex.side_effect = lambda k, ttl, v: storage.__setitem__(k, v)
        mock_client.return_value = mock_redis

        wrapped = cached_tool(ttl=3600)(async_impl)
        r1 = asyncio.run(wrapped(x=1))
        r2 = asyncio.run(wrapped(x=1))

    assert r1 == r2 == {"r": "async"}
    assert inner.call_count == 1


def test_cached_tool_skip_if_predicate_bypasses_cache():
    inner = MagicMock(return_value={"r": 1})
    inner.__name__ = "fake_tool"

    with patch("app.lib.tool_cache._get_redis_client") as mock_client:
        mock_redis = MagicMock()
        mock_client.return_value = mock_redis

        wrapped = cached_tool(ttl=3600, skip_if=lambda kw: kw.get("realtime"))(inner)
        wrapped(x=1, realtime=True)
        wrapped(x=1, realtime=True)

    assert inner.call_count == 2
    # 缓存层完全未介入
    mock_redis.get.assert_not_called()
    mock_redis.setex.assert_not_called()


def test_cached_tool_ref_args_bypass_cache():
    """args 含 ref:xxx 时跳过缓存层。"""
    inner = MagicMock(return_value={"r": 1})
    inner.__name__ = "fake_tool"

    with patch("app.lib.tool_cache._get_redis_client") as mock_client:
        mock_redis = MagicMock()
        mock_client.return_value = mock_redis

        wrapped = cached_tool(ttl=3600)(inner)
        wrapped(geojson="ref:abc")
        wrapped(geojson="ref:abc")

    assert inner.call_count == 2
    mock_redis.get.assert_not_called()
    mock_redis.setex.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tool_cache.py -v -k "cached_tool"`
Expected: 5 FAILs with `ImportError: cannot import name 'cached_tool'`.

- [ ] **Step 3: Write minimal implementation**

Append to `app/lib/tool_cache.py`:

```python
import functools
import inspect
from contextvars import ContextVar
from typing import Callable

# Registry 的 timing wrapper 读此变量判断当前 dispatch 是否命中缓存。
cache_hit_var: ContextVar[bool] = ContextVar("tool_cache_hit", default=False)


def cached_tool(ttl: int = 3600, skip_if: Optional[Callable[[dict], bool]] = None):
    """工具函数装饰器：Redis 命中即返回，未命中调内层 + 写回。

    Args:
        ttl: 缓存生存时间（秒）。默认 1 小时。
        skip_if: 谓词函数 (kwargs_dict) -> bool。返回真值时双向旁路缓存。

    内层函数可以是 sync 也可以是 async；通过 inspect.iscoroutinefunction 分支。
    """
    def decorator(func: Callable) -> Callable:
        is_async = inspect.iscoroutinefunction(func)
        tool_name = getattr(func, "__name__", "anonymous_tool")

        if is_async:
            @functools.wraps(func)
            async def async_wrapper(**kwargs):
                if skip_if is not None and skip_if(kwargs):
                    return await func(**kwargs)
                key = make_cache_key(tool_name, kwargs)
                if key is None:
                    return await func(**kwargs)
                cached = get_cached(key)
                if cached is not None:
                    cache_hit_var.set(True)
                    return cached
                result = await func(**kwargs)
                set_cached(key, result, ttl)
                return result
            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(**kwargs):
            if skip_if is not None and skip_if(kwargs):
                return func(**kwargs)
            key = make_cache_key(tool_name, kwargs)
            if key is None:
                return func(**kwargs)
            cached = get_cached(key)
            if cached is not None:
                cache_hit_var.set(True)
                return cached
            result = func(**kwargs)
            set_cached(key, result, ttl)
            return result
        return sync_wrapper

    return decorator
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tool_cache.py -v`
Expected: 18 PASS (13 prior + 5 new).

- [ ] **Step 5: Commit**

```bash
git add app/lib/tool_cache.py tests/test_tool_cache.py
git commit -m "$(cat <<'EOF'
feat(tool_cache): add @cached_tool decorator with sync/async/skip_if support

Detects coroutine functions via inspect.iscoroutinefunction and produces the
matching wrapper shape. skip_if predicate and ref:xxx-containing args both
bypass the cache layer entirely. A ContextVar (cache_hit_var) signals to the
registry's timing wrapper whether the dispatch served from cache — wired up
in Task 7.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `trim_features` helper

**Files:**
- Modify: `app/tools/_utils.py`
- Create: `tests/test_tool_trim.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tool_trim.py`:

```python
"""trim_features tests — payload trim helper for heavy GeoJSON returns."""
import pytest

from app.tools._utils import trim_features


def _point(lon, lat, **props):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": props or {},
    }


def _fc(features):
    return {"type": "FeatureCollection", "features": features}


def test_trim_features_under_threshold_unchanged():
    fc = _fc([_point(116.0, 39.0)])
    out = trim_features(fc, max_features=5000)
    # No _trim envelope when nothing was trimmed.
    assert "_trim" not in out
    assert len(out["features"]) == 1


def test_trim_features_exactly_threshold_unchanged():
    fc = _fc([_point(116.0, 39.0) for _ in range(5000)])
    out = trim_features(fc, max_features=5000)
    assert "_trim" not in out
    assert len(out["features"]) == 5000


def test_trim_features_over_threshold_clips_to_max():
    fc = _fc([_point(116.0, 39.0) for _ in range(5001)])
    out = trim_features(fc, max_features=5000)
    assert len(out["features"]) == 5000
    assert out["_trim"] == {
        "original_count": 5001,
        "kept_count": 5000,
        "precision": 6,
        "reason": "max_features",
    }


def test_trim_features_keeps_first_n_not_random():
    fc = _fc([_point(0, i, idx=i) for i in range(10)])
    out = trim_features(fc, max_features=5)
    kept_indices = [f["properties"]["idx"] for f in out["features"]]
    assert kept_indices == [0, 1, 2, 3, 4]


def test_trim_features_rounds_point_precision():
    fc = _fc([_point(121.123456789, 39.987654321)])
    out = trim_features(fc, max_features=5000, precision=6)
    coords = out["features"][0]["geometry"]["coordinates"]
    assert coords == [121.123457, 39.987654]


def test_trim_features_rounds_polygon_precision():
    poly = {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [116.123456789, 39.123456789],
                [117.123456789, 39.123456789],
                [117.123456789, 40.123456789],
                [116.123456789, 39.123456789],
            ]],
        },
        "properties": {},
    }
    out = trim_features(_fc([poly]), precision=6)
    ring = out["features"][0]["geometry"]["coordinates"][0]
    assert ring[0] == [116.123457, 39.123457]


def test_trim_features_non_fc_returns_unchanged():
    """非 FeatureCollection 输入：原样返回 + warning。"""
    out = trim_features({"type": "Point", "coordinates": [1, 2]})
    assert out == {"type": "Point", "coordinates": [1, 2]}


def test_trim_features_empty_features_list():
    fc = _fc([])
    out = trim_features(fc)
    assert out["features"] == []
    assert "_trim" not in out


def test_trim_features_default_max_is_5000():
    fc = _fc([_point(0, 0) for _ in range(5001)])
    out = trim_features(fc)  # no max_features arg
    assert len(out["features"]) == 5000
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tool_trim.py -v`
Expected: 9 FAILs with `ImportError: cannot import name 'trim_features' from 'app.tools._utils'`.

- [ ] **Step 3: Write minimal implementation**

Append to `app/tools/_utils.py`:

```python
# ============================================================================
# Payload trim — 重 GeoJSON 返回的统一裁剪
# ============================================================================

def trim_features(fc: dict, max_features: int = 5000, precision: int = 6) -> dict:
    """裁剪 FeatureCollection 的载荷：保留前 N 条 + 几何坐标四舍五入。

    Args:
        fc: 输入字典。非 FeatureCollection 时原样返回 + warning。
        max_features: 超过则截断保留前 N。默认 5000。
        precision: 坐标小数位。默认 6（赤道 ≈ 10cm，肉眼无差）。

    Returns:
        裁剪后的 FeatureCollection。仅在实际发生裁剪时多一个顶层 "_trim" 键。
    """
    if not isinstance(fc, dict) or fc.get("type") != "FeatureCollection":
        logger.warning(
            f"[trim_features] non-FeatureCollection input (type={fc.get('type') if isinstance(fc, dict) else type(fc).__name__}); returning unchanged"
        )
        return fc

    features = fc.get("features", []) or []
    original_count = len(features)
    trimmed = original_count > max_features
    kept = features[:max_features] if trimmed else features

    # 几何坐标四舍五入到 precision 位。pure-data 转换，不改 type/properties。
    rounded = [_round_feature(f, precision) for f in kept]

    out = dict(fc)
    out["features"] = rounded
    if trimmed:
        out["_trim"] = {
            "original_count": original_count,
            "kept_count": len(rounded),
            "precision": precision,
            "reason": "max_features",
        }
    return out


def _round_feature(feature: dict, precision: int) -> dict:
    geom = feature.get("geometry")
    if not isinstance(geom, dict):
        return feature
    new_geom = dict(geom)
    new_geom["coordinates"] = _round_coords(geom.get("coordinates"), precision)
    new_feat = dict(feature)
    new_feat["geometry"] = new_geom
    return new_feat


def _round_coords(coords, precision: int):
    """递归 round。Point→[x,y]，LineString→[[x,y],...]，Polygon→[[[x,y],...]] 等。"""
    if isinstance(coords, (int, float)):
        return round(coords, precision)
    if isinstance(coords, list):
        return [_round_coords(c, precision) for c in coords]
    return coords
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tool_trim.py -v`
Expected: 9 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/tools/_utils.py tests/test_tool_trim.py
git commit -m "$(cat <<'EOF'
feat(tool_utils): add trim_features helper for payload reduction

Caps FeatureCollection to max_features (default 5000, first-N kept) and rounds
all coordinates to precision (default 6 ≈ 10cm at equator). Adds a top-level
"_trim" envelope only when trimming actually occurred. Non-FC input returned
unchanged with a warning — defensive against tools passing odd shapes.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: `tool_metrics.record_tool_call` + JSONL writer

**Files:**
- Create: `app/services/tool_metrics.py`
- Create: `tests/test_tool_metrics.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tool_metrics.py`:

```python
"""tool_metrics tests — JSONL writer + in-process aggregator + digest emission."""
import json
import os
import pytest

from app.services import tool_metrics


@pytest.fixture(autouse=True)
def _isolated_metrics(tmp_path, monkeypatch):
    """每个测试用临时日志文件 + 重置聚合器。"""
    log_path = tmp_path / "tool_metrics.jsonl"
    monkeypatch.setattr(tool_metrics, "LOG_PATH", str(log_path))
    tool_metrics._reset_for_tests()
    yield log_path
    tool_metrics._reset_for_tests()


def test_record_tool_call_writes_one_jsonl_line(_isolated_metrics):
    tool_metrics.record_tool_call(
        tool="heatmap_data",
        arg_bytes=1234,
        result_bytes=56789,
        duration_ms=312,
        cache_hit=False,
        error=None,
        session_id="sess1",
    )
    text = _isolated_metrics.read_text().strip()
    assert text.count("\n") == 0  # exactly one line
    row = json.loads(text)
    assert row["tool"] == "heatmap_data"
    assert row["arg_bytes"] == 1234
    assert row["result_bytes"] == 56789
    assert row["duration_ms"] == 312
    assert row["cache_hit"] is False
    assert row["error"] is None
    assert row["session_id"] == "sess1"
    assert "ts" in row and row["ts"].endswith("Z")


def test_record_tool_call_cache_hit_true(_isolated_metrics):
    tool_metrics.record_tool_call(
        tool="heatmap_data", arg_bytes=10, result_bytes=20,
        duration_ms=1, cache_hit=True, error=None, session_id=None,
    )
    row = json.loads(_isolated_metrics.read_text().strip())
    assert row["cache_hit"] is True
    assert row["session_id"] is None


def test_record_tool_call_error_records_class_name(_isolated_metrics):
    tool_metrics.record_tool_call(
        tool="osm_fetch", arg_bytes=100, result_bytes=0,
        duration_ms=2000, cache_hit=False, error="TimeoutError", session_id=None,
    )
    row = json.loads(_isolated_metrics.read_text().strip())
    assert row["error"] == "TimeoutError"


def test_record_tool_call_disk_failure_does_not_raise(monkeypatch, _isolated_metrics):
    """写盘失败不能阻塞工具调用。"""
    def boom(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(tool_metrics, "_write_jsonl_line", boom)
    # MUST NOT raise
    tool_metrics.record_tool_call(
        tool="x", arg_bytes=0, result_bytes=0, duration_ms=0,
        cache_hit=False, error=None, session_id=None,
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tool_metrics.py -v`
Expected: 4 FAILs with `ModuleNotFoundError: No module named 'app.services.tool_metrics'`.

- [ ] **Step 3: Write minimal implementation**

Create `app/services/tool_metrics.py`:

```python
"""工具调用计时 — 每次 dispatch 一行 JSONL + 进程级聚合器 + digest 输出。

入口:
    record_tool_call(...)  # 在 registry.dispatch 包装里调用
    emit_digest()          # 在 FastAPI lifespan shutdown 时调用

文件: logs/tool_metrics.jsonl (10MB 轮转，5 备份)。
"""
import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# 路径可在测试中 monkeypatch 替换。
LOG_PATH = os.path.join("logs", "tool_metrics.jsonl")

_DIGEST_EVERY_N = 100

# 聚合器：tool_name → [count, total_ms, max_ms, hit_count, error_count]
_aggregator: dict[str, list[int]] = {}
_call_counter: int = 0
_lock = threading.Lock()


def _reset_for_tests() -> None:
    global _aggregator, _call_counter
    with _lock:
        _aggregator = {}
        _call_counter = 0


def _ensure_log_dir() -> None:
    d = os.path.dirname(LOG_PATH)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)


def _write_jsonl_line(line: str) -> None:
    _ensure_log_dir()
    # 简单追加；轮转留到 Task 6 用 RotatingFileHandler 接管。
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def record_tool_call(
    *,
    tool: str,
    arg_bytes: int,
    result_bytes: int,
    duration_ms: int,
    cache_hit: bool,
    error: Optional[str],
    session_id: Optional[str],
) -> None:
    """落一行 JSONL + 更新聚合器。失败时仅 logger.warning，不抛。"""
    row = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "tool": tool,
        "session_id": session_id,
        "arg_bytes": arg_bytes,
        "result_bytes": result_bytes,
        "duration_ms": duration_ms,
        "cache_hit": cache_hit,
        "error": error,
    }
    line = json.dumps(row, separators=(",", ":"))
    try:
        _write_jsonl_line(line)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[tool_metrics] write failed (dropping row): {type(e).__name__}: {e}")

    # 聚合器更新留到 Task 6
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tool_metrics.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/tool_metrics.py tests/test_tool_metrics.py
git commit -m "$(cat <<'EOF'
feat(tool_metrics): add record_tool_call writing per-dispatch JSONL rows

One line per tool call to logs/tool_metrics.jsonl with schema:
{ts, tool, session_id, arg_bytes, result_bytes, duration_ms, cache_hit, error}.
Disk failures are logged once and swallowed — never block the tool call.
Aggregator + digest land in Task 6.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Aggregator + digest emission (auto at N=100 and manual)

**Files:**
- Modify: `app/services/tool_metrics.py`
- Modify: `tests/test_tool_metrics.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tool_metrics.py`:

```python
def test_aggregator_counts_after_synthetic_calls(_isolated_metrics):
    for _ in range(3):
        tool_metrics.record_tool_call(
            tool="A", arg_bytes=0, result_bytes=0, duration_ms=100,
            cache_hit=False, error=None, session_id=None,
        )
    for _ in range(2):
        tool_metrics.record_tool_call(
            tool="A", arg_bytes=0, result_bytes=0, duration_ms=50,
            cache_hit=True, error=None, session_id=None,
        )
    tool_metrics.record_tool_call(
        tool="A", arg_bytes=0, result_bytes=0, duration_ms=200,
        cache_hit=False, error="ValueError", session_id=None,
    )
    snap = tool_metrics.aggregator_snapshot()
    assert snap["A"]["count"] == 6
    assert snap["A"]["total_ms"] == 3 * 100 + 2 * 50 + 200
    assert snap["A"]["max_ms"] == 200
    assert snap["A"]["hit_count"] == 2
    assert snap["A"]["error_count"] == 1


def test_emit_digest_writes_log_line(caplog, _isolated_metrics):
    for _ in range(5):
        tool_metrics.record_tool_call(
            tool="heatmap_data", arg_bytes=0, result_bytes=0, duration_ms=120,
            cache_hit=False, error=None, session_id=None,
        )
    with caplog.at_level("INFO", logger="app.services.tool_metrics"):
        tool_metrics.emit_digest()
    matching = [r for r in caplog.records if "TOOL_METRICS_DIGEST" in r.getMessage()]
    assert len(matching) == 1
    msg = matching[0].getMessage()
    assert "n=5" in msg
    assert "heatmap_data" in msg


def test_emit_digest_empty_aggregator_emits_nothing(caplog, _isolated_metrics):
    with caplog.at_level("INFO", logger="app.services.tool_metrics"):
        tool_metrics.emit_digest()
    matching = [r for r in caplog.records if "TOOL_METRICS_DIGEST" in r.getMessage()]
    assert len(matching) == 0


def test_auto_digest_at_100_calls(caplog, _isolated_metrics):
    with caplog.at_level("INFO", logger="app.services.tool_metrics"):
        for _ in range(100):
            tool_metrics.record_tool_call(
                tool="A", arg_bytes=0, result_bytes=0, duration_ms=1,
                cache_hit=False, error=None, session_id=None,
            )
    matching = [r for r in caplog.records if "TOOL_METRICS_DIGEST" in r.getMessage()]
    assert len(matching) == 1


def test_no_digest_at_99_calls(caplog, _isolated_metrics):
    with caplog.at_level("INFO", logger="app.services.tool_metrics"):
        for _ in range(99):
            tool_metrics.record_tool_call(
                tool="A", arg_bytes=0, result_bytes=0, duration_ms=1,
                cache_hit=False, error=None, session_id=None,
            )
    matching = [r for r in caplog.records if "TOOL_METRICS_DIGEST" in r.getMessage()]
    assert len(matching) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tool_metrics.py -v -k "aggregator or digest"`
Expected: 5 FAILs with `AttributeError: module 'app.services.tool_metrics' has no attribute 'aggregator_snapshot'` (or `emit_digest`).

- [ ] **Step 3: Write minimal implementation**

Replace the body of `record_tool_call` in `app/services/tool_metrics.py` to update the aggregator, and append the new functions. Apply this Edit:

Replace this block at end of `record_tool_call`:

```python
    # 聚合器更新留到 Task 6
```

with:

```python
    _update_aggregator(tool, duration_ms, cache_hit, error)


def _update_aggregator(tool: str, duration_ms: int, cache_hit: bool, error: Optional[str]) -> None:
    global _call_counter
    with _lock:
        slot = _aggregator.setdefault(tool, [0, 0, 0, 0, 0])
        # [count, total_ms, max_ms, hit_count, error_count]
        slot[0] += 1
        slot[1] += duration_ms
        if duration_ms > slot[2]:
            slot[2] = duration_ms
        if cache_hit:
            slot[3] += 1
        if error:
            slot[4] += 1
        _call_counter += 1
        should_digest = (_call_counter % _DIGEST_EVERY_N == 0)
    if should_digest:
        emit_digest()


def aggregator_snapshot() -> dict:
    """聚合器只读快照，便于测试 / dashboard."""
    with _lock:
        return {
            t: {
                "count": v[0],
                "total_ms": v[1],
                "max_ms": v[2],
                "hit_count": v[3],
                "error_count": v[4],
            }
            for t, v in _aggregator.items()
        }


def emit_digest() -> None:
    """输出 TOOL_METRICS_DIGEST 一行总结。空聚合器时不输出。"""
    with _lock:
        if not _aggregator:
            return
        n = _call_counter
        # top 5 by cumulative ms
        top_cum = sorted(
            _aggregator.items(), key=lambda kv: kv[1][1], reverse=True
        )[:5]
        # top 5 by max_ms (p99 proxy)
        top_max = sorted(
            _aggregator.items(), key=lambda kv: kv[1][2], reverse=True
        )[:5]
        errors = [(t, v[4]) for t, v in _aggregator.items() if v[4] > 0]

    cum_str = ",".join(f'("{t}",{v[1]},{v[0]},{v[3]})' for t, v in top_cum)
    max_str = ",".join(f'("{t}",{v[2]})' for t, v in top_max)
    err_str = ",".join(f'("{t}",{n})' for t, n in errors)
    logger.info(
        f"TOOL_METRICS_DIGEST n={n} top_cumulative=[{cum_str}] top_p99=[{max_str}] errors=[{err_str}]"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tool_metrics.py -v`
Expected: 9 PASS (4 from Task 5 + 5 new).

- [ ] **Step 5: Commit**

```bash
git add app/services/tool_metrics.py tests/test_tool_metrics.py
git commit -m "$(cat <<'EOF'
feat(tool_metrics): add thread-safe aggregator + digest emission

In-process aggregator keyed by tool_name tracks count, total_ms, max_ms,
hit_count, error_count. Emits TOOL_METRICS_DIGEST log line automatically every
100 calls and on explicit emit_digest() — meant to be called from FastAPI
lifespan shutdown. threading.Lock for executor-thread safety.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Registry timing wrapper + cache_hit propagation

**Files:**
- Modify: `app/tools/registry.py:115` (the `dispatch` method body)
- Create: `tests/test_registry_timing.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_registry_timing.py`:

```python
"""Registry timing wrapper tests — every dispatch records one metrics row."""
import json
import pytest

from app.services import tool_metrics
from app.tools.registry import ToolRegistry
from app.lib.tool_cache import cached_tool, _reset_redis_client_for_tests
from unittest.mock import patch, MagicMock


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    log_path = tmp_path / "tool_metrics.jsonl"
    monkeypatch.setattr(tool_metrics, "LOG_PATH", str(log_path))
    tool_metrics._reset_for_tests()
    _reset_redis_client_for_tests()
    yield log_path
    tool_metrics._reset_for_tests()
    _reset_redis_client_for_tests()


@pytest.mark.asyncio
async def test_dispatch_records_one_metrics_row(_isolated):
    reg = ToolRegistry()

    def fake_tool(x: int) -> dict:
        return {"r": x * 2}
    reg.register("fake_tool", "test", fake_tool)

    await reg.dispatch("fake_tool", {"x": 3}, session_id="s1")

    lines = _isolated.read_text().strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["tool"] == "fake_tool"
    assert row["cache_hit"] is False
    assert row["session_id"] == "s1"
    assert row["duration_ms"] >= 0
    assert row["error"] is None


@pytest.mark.asyncio
async def test_dispatch_records_cache_hit_on_second_call(_isolated):
    reg = ToolRegistry()
    storage = {}

    @cached_tool(ttl=3600)
    def fake_tool(x: int) -> dict:
        return {"r": x * 2}
    reg.register("fake_tool", "test", fake_tool)

    with patch("app.lib.tool_cache._get_redis_client") as mock_client:
        mock_redis = MagicMock()
        mock_redis.get.side_effect = lambda k: storage.get(k)
        mock_redis.setex.side_effect = lambda k, ttl, v: storage.__setitem__(k, v)
        mock_client.return_value = mock_redis

        await reg.dispatch("fake_tool", {"x": 3}, session_id="s1")
        await reg.dispatch("fake_tool", {"x": 3}, session_id="s1")

    lines = _isolated.read_text().strip().splitlines()
    assert len(lines) == 2
    row1 = json.loads(lines[0])
    row2 = json.loads(lines[1])
    assert row1["cache_hit"] is False
    assert row2["cache_hit"] is True


@pytest.mark.asyncio
async def test_dispatch_records_error_class(_isolated):
    reg = ToolRegistry()

    def boom_tool() -> dict:
        raise RuntimeError("nope")
    reg.register("boom_tool", "test", boom_tool)

    # dispatch catches and returns std_error_response — we still expect a row.
    result = await reg.dispatch("boom_tool", {}, session_id=None)
    assert result.get("success") is False
    lines = _isolated.read_text().strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["tool"] == "boom_tool"
    assert row["error"] == "RuntimeError"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_registry_timing.py -v`
Expected: 3 FAILs — no metrics row is being written because dispatch doesn't call record_tool_call yet.

- [ ] **Step 3: Modify `dispatch` in `app/tools/registry.py`**

The current `dispatch` returns from many branches. The simplest correct integration is to wrap the existing body in a small helper that captures timing + cache_hit + error class.

Apply this Edit. Find the existing method header at `app/tools/registry.py:115`:

```python
    async def dispatch(self, name: str, arguments: dict | str, session_id: Optional[str] = None) -> Any:
        """执行工具，包含 Pydantic 校验与透明解引用"""
        from app.tools._utils import std_error_response
```

Replace the entire `dispatch` method (from `async def dispatch` through `return result` at line ~215) with this version that wraps in timing. **Preserve all existing internal logic exactly** — only add the wrapping:

```python
    async def dispatch(self, name: str, arguments: dict | str, session_id: Optional[str] = None) -> Any:
        """执行工具，包含 Pydantic 校验与透明解引用。

        外层装饰：自动落 tool_metrics 一行 JSONL（含 cache_hit、错误类、时延）。
        cache_hit 通过 ContextVar 从 @cached_tool 装饰器传上来——同一 asyncio.Task
        内 ContextVar 自动跨 await 边界传播，无需 copy_context()。
        """
        import time as _time
        import json as _json

        from app.services import tool_metrics
        from app.lib.tool_cache import cache_hit_var

        token = cache_hit_var.set(False)  # 重置 — 每次 dispatch 都从未命中开始
        start = _time.perf_counter()
        error_cls: Optional[str] = None
        result: Any = None
        try:
            arg_bytes = len(_json.dumps(arguments, default=str))
        except Exception:
            arg_bytes = 0

        try:
            result = await self._dispatch_impl(name, arguments, session_id)
        except Exception as e:  # noqa: BLE001
            # _dispatch_impl 内层已经把工具异常转成 std_error_response 字典；
            # 这里 catch 只是兜底（譬如 validate 路径意外漏抛的）。
            error_cls = type(e).__name__
            raise
        finally:
            duration_ms = int((_time.perf_counter() - start) * 1000)
            # 从已返回的 result 推断错误（_dispatch_impl 把工具异常转成 success=False）
            if isinstance(result, dict) and result.get("success") is False:
                error_cls = error_cls or result.get("error_type") or result.get("code")
            try:
                result_bytes = len(_json.dumps(result, default=str)) if result is not None else 0
            except Exception:
                result_bytes = 0
            cache_hit = cache_hit_var.get()
            tool_metrics.record_tool_call(
                tool=name,
                arg_bytes=arg_bytes,
                result_bytes=result_bytes,
                duration_ms=duration_ms,
                cache_hit=cache_hit,
                error=error_cls,
                session_id=session_id,
            )
            cache_hit_var.reset(token)

        return result

    async def _dispatch_impl(self, name: str, arguments: dict | str, session_id: Optional[str] = None) -> Any:
        """原 dispatch 主体 — 不含 metrics 包装，便于测试与复用。"""
        from app.tools._utils import std_error_response
```

Then leave the rest of the original `dispatch` body (from `if name not in self._tools` through `return result`) **as-is** under the new `_dispatch_impl` name. Do not change any tool-execution logic.

No new top-level imports needed — `time`, `json`, `tool_metrics`, and `cache_hit_var` are all imported inside the method (matches the existing in-function import style at registry.py:117).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_registry_timing.py -v`
Expected: 3 PASS.

Then run the full registry tests to verify no regression:

Run: `pytest tests/test_chat_engine.py tests/test_chat_engine_planning.py tests/test_chat_engine_tracking.py -v`
Expected: all existing PASS.

- [ ] **Step 5: Commit**

```bash
git add app/tools/registry.py tests/test_registry_timing.py
git commit -m "$(cat <<'EOF'
feat(registry): wrap dispatch with automatic tool_metrics timing

Every tool dispatch now writes one JSONL row with duration_ms, arg/result
bytes, cache_hit (via ContextVar from @cached_tool), and error class.
Existing dispatch logic is moved verbatim into _dispatch_impl; the public
dispatch wraps it with timing + metrics. Zero behavior change for tool
authors and chat_engine callers.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Wire `emit_digest()` to FastAPI lifespan shutdown

**Files:**
- Modify: `app/main.py:27-49` (the `lifespan` function)

- [ ] **Step 1: Write a smoke test**

Append to `tests/test_tool_metrics.py`:

```python
def test_emit_digest_is_module_exported():
    """app/main.py 需要 import emit_digest — 验证它是公共 API。"""
    from app.services.tool_metrics import emit_digest
    assert callable(emit_digest)
```

Run: `pytest tests/test_tool_metrics.py::test_emit_digest_is_module_exported -v`
Expected: PASS (function already exists from Task 6).

- [ ] **Step 2: Modify the lifespan**

Edit `app/main.py`. Find this block:

```python
    yield

    from app.core.network import close_shared_client
    await close_shared_client()
    Engine.dispose()
```

Replace with:

```python
    yield

    # 输出工具调用 digest（top 累计 / top p99 / 错误），便于运维定位最慢工具
    try:
        from app.services.tool_metrics import emit_digest
        emit_digest()
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).warning(f"[lifespan] emit_digest failed: {e}")

    from app.core.network import close_shared_client
    await close_shared_client()
    Engine.dispose()
```

- [ ] **Step 3: Verify the app still imports clean**

Run: `python -c "from app.main import app; print('ok')"`
Expected: `ok` (no import error).

Run a quick chat-engine smoke to make sure nothing broke at boot:

Run: `pytest tests/test_chat_engine.py -v -x`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add app/main.py tests/test_tool_metrics.py
git commit -m "$(cat <<'EOF'
feat(main): emit tool_metrics digest at FastAPI lifespan shutdown

Final snapshot of top-N cumulative + top-N p99 + error counts goes to logs
when the app shuts down. Wrapped in try/except so a metrics failure can never
block the orderly shutdown of DB pool and shared HTTP client.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Phase 1 ship gate — full suite + coverage check

**Files:** none (verification + commit if needed)

- [ ] **Step 1: Run the full test suite**

Run: `pytest -v`
Expected: all PASS. If any existing test fails, fix it before proceeding — Phase 1 must be zero-behavior-change.

- [ ] **Step 2: Verify a real boot writes a metrics row**

Manual smoke (run if possible):

```bash
# Start the app briefly
python -c "
import asyncio
from app.tools.registry import ToolRegistry
from app.services import tool_metrics
import os
os.makedirs('logs', exist_ok=True)
async def main():
    r = ToolRegistry()
    def fake(x): return {'ok': True, 'x': x}
    r.register('smoke', 'smoke test', fake)
    await r.dispatch('smoke', {'x': 1}, session_id=None)
    tool_metrics.emit_digest()
asyncio.run(main())
"
tail -5 logs/tool_metrics.jsonl
```

Expected: one JSONL line with `"tool":"smoke","cache_hit":false`.

- [ ] **Step 3: Re-export `cached_tool` from `_utils` for ergonomics**

Edit `app/tools/_utils.py`. Add at the top (after the existing imports):

```python
# 缓存装饰器从 lib 单点导出。新工具 from app.tools._utils import cached_tool, trim_features
from app.lib.tool_cache import cached_tool  # noqa: F401
```

Run: `pytest tests/test_tool_cache.py tests/test_tool_trim.py -v`
Expected: all PASS.

- [ ] **Step 4: Commit if changes**

```bash
git add app/tools/_utils.py
git commit -m "$(cat <<'EOF'
chore(tool_utils): re-export cached_tool for one-stop import path

Tool authors: from app.tools._utils import cached_tool, trim_features.
Keeps the actual implementation in app.lib.tool_cache but gives tools a
single conventional import surface.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

**Phase 1 ship gate satisfied** when: all tests green, manual boot writes JSONL, no behavior change visible to any tool caller.

---

## PHASE 2: Opt in the four heaviest tools (Tasks 10–14)

Phase 2 opts `buffer_analysis`, `heatmap_data`, `h3_binning`, `kde_contours` into both `@cached_tool` and `trim_features`. Each tool is one task. Each task is one TDD cycle and one commit.

---

### Task 10: Opt `buffer_analysis` into cache + trim

**Files:**
- Modify: `app/tools/spatial.py:210-226` (the `buffer_analysis` registration)
- Create: `tests/test_buffer_caching.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_buffer_caching.py`:

```python
"""buffer_analysis perf opt-in: cache hit on identical call + trim envelope on big input."""
import pytest
from unittest.mock import patch, MagicMock

from app.tools.registry import ToolRegistry
from app.tools.spatial import register_spatial_tools
from app.lib.tool_cache import _reset_redis_client_for_tests
from app.services import tool_metrics


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_metrics, "LOG_PATH", str(tmp_path / "tool_metrics.jsonl"))
    tool_metrics._reset_for_tests()
    _reset_redis_client_for_tests()
    yield
    tool_metrics._reset_for_tests()
    _reset_redis_client_for_tests()


@pytest.mark.asyncio
async def test_buffer_analysis_second_call_cache_hit():
    reg = ToolRegistry()
    register_spatial_tools(reg)

    storage = {}
    args = {
        "geojson": {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.0, 39.0]}, "properties": {}}
        ]},
        "distance": 100.0,
        "unit": "m",
    }
    with patch("app.lib.tool_cache._get_redis_client") as mock_client:
        mock_redis = MagicMock()
        mock_redis.get.side_effect = lambda k: storage.get(k)
        mock_redis.setex.side_effect = lambda k, ttl, v: storage.__setitem__(k, v)
        mock_client.return_value = mock_redis

        r1 = await reg.dispatch("buffer_analysis", args, session_id=None)
        r2 = await reg.dispatch("buffer_analysis", args, session_id=None)

    assert r1 == r2
    # The second dispatch must have set cache_hit=True in its metrics row.
    import json
    lines = [json.loads(l) for l in
             open(tool_metrics.LOG_PATH).read().strip().splitlines()]
    assert lines[0]["cache_hit"] is False
    assert lines[1]["cache_hit"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_buffer_caching.py -v`
Expected: FAIL — second call writes `cache_hit=False` because `buffer_analysis` isn't decorated yet.

- [ ] **Step 3: Modify `app/tools/spatial.py:210-226`**

Apply this Edit to the `buffer_analysis` registration block. Find:

```python
    @tool(registry, name="buffer_analysis",
           description=(
               "缓冲区分析：对点/线/面要素生成指定距离的缓冲多边形。"
               "\n何时用：『学校 500m 范围内』『地铁站 1km 缓冲』『高压线两侧 50m 退让』等距离邻近查询的母图层；"
               "做空间叠加 (overlay_analysis) 前的几何准备。"
               "\n何时不用：(1) 多个距离环 (如 100/300/500m) — 用 multi_ring_buffer；"
               "(2) 路网真实通达距离 — 用 isochrone_analysis (按时间) 或 service_area_simple；"
               "(3) 仅需统计数量而不需缓冲几何 — 用 spatial_aggregate 配合点数据。"
               "\n关键约束：distance 必须 > 0；单位严格按 unit (默认米)；"
               "投影会自动转 UTM 做精确缓冲，结果回 WGS84。"
           ),
           args_model=BufferAnalysisArgs)
    def buffer_analysis(geojson: Any, distance: float, unit: str = "m") -> dict:
        data = safe_parse_geojson(geojson)
        features = data.get("features", [])
        res = SpatialAnalyzer.buffer(features, distance, unit)
        return res.to_llm_response()
```

Replace with (note: `@cached_tool` goes BELOW `@tool` so it wraps the inner function; `trim_features` is called at the return site):

```python
    @tool(registry, name="buffer_analysis",
           description=(
               "缓冲区分析：对点/线/面要素生成指定距离的缓冲多边形。"
               "\n何时用：『学校 500m 范围内』『地铁站 1km 缓冲』『高压线两侧 50m 退让』等距离邻近查询的母图层；"
               "做空间叠加 (overlay_analysis) 前的几何准备。"
               "\n何时不用：(1) 多个距离环 (如 100/300/500m) — 用 multi_ring_buffer；"
               "(2) 路网真实通达距离 — 用 isochrone_analysis (按时间) 或 service_area_simple；"
               "(3) 仅需统计数量而不需缓冲几何 — 用 spatial_aggregate 配合点数据。"
               "\n关键约束：distance 必须 > 0；单位严格按 unit (默认米)；"
               "投影会自动转 UTM 做精确缓冲，结果回 WGS84。"
           ),
           args_model=BufferAnalysisArgs)
    @cached_tool(ttl=86400)
    def buffer_analysis(geojson: Any, distance: float, unit: str = "m") -> dict:
        data = safe_parse_geojson(geojson)
        features = data.get("features", [])
        res = SpatialAnalyzer.buffer(features, distance, unit)
        out = res.to_llm_response()
        # 裁剪可能很大的缓冲结果载荷（万级缓冲 polygon 顶点数）
        if isinstance(out, dict) and out.get("type") == "FeatureCollection":
            out = trim_features(out)
        elif isinstance(out, dict) and isinstance(out.get("data"), dict) and out["data"].get("type") == "FeatureCollection":
            out["data"] = trim_features(out["data"])
        return out
```

Also add the imports at the top of `app/tools/spatial.py` if not already present. Find the existing imports near line 1-10 and add:

```python
from app.tools._utils import cached_tool, trim_features
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_buffer_caching.py -v`
Expected: PASS.

Also confirm existing buffer tests still pass:

Run: `pytest tests/unit/test_spatial_buffer.py -v` (and any other `buffer`-related test)
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/tools/spatial.py tests/test_buffer_caching.py
git commit -m "$(cat <<'EOF'
perf(tools): opt buffer_analysis into result cache + payload trim

@cached_tool(ttl=86400): buffer is a deterministic geometry op, safe to cache
for 1 day. trim_features clips the FC to 5000 features + rounds precision to
6 decimals (~10cm) before returning — cuts wire payload 30-50% for large
buffer fan-outs.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Opt `heatmap_data` into cache + trim

**Files:**
- Modify: `app/tools/spatial.py:260-300` (the `heatmap_data` registration)
- Create: `tests/test_heatmap_caching.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_heatmap_caching.py`:

```python
"""End-to-end: heatmap_data cache hit + trim envelope on big inputs."""
import pytest
from unittest.mock import patch, MagicMock

from app.tools.registry import ToolRegistry
from app.tools.spatial import register_spatial_tools
from app.lib.tool_cache import _reset_redis_client_for_tests
from app.services import tool_metrics


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_metrics, "LOG_PATH", str(tmp_path / "tool_metrics.jsonl"))
    tool_metrics._reset_for_tests()
    _reset_redis_client_for_tests()
    yield
    tool_metrics._reset_for_tests()
    _reset_redis_client_for_tests()


def _make_point_fc(n: int) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [116.0 + i * 0.0001, 39.0]},
             "properties": {"idx": i}}
            for i in range(n)
        ],
    }


@pytest.mark.asyncio
async def test_heatmap_native_mode_second_call_cache_hit():
    """render_type=native 路径走 cache（无 Celery 依赖，测试最稳）."""
    reg = ToolRegistry()
    register_spatial_tools(reg)

    storage = {}
    args = {"geojson": _make_point_fc(10), "render_type": "native"}

    with patch("app.lib.tool_cache._get_redis_client") as mock_client:
        mock_redis = MagicMock()
        mock_redis.get.side_effect = lambda k: storage.get(k)
        mock_redis.setex.side_effect = lambda k, ttl, v: storage.__setitem__(k, v)
        mock_client.return_value = mock_redis

        r1 = await reg.dispatch("heatmap_data", args, session_id=None)
        r2 = await reg.dispatch("heatmap_data", args, session_id=None)

    assert r1 == r2

    import json
    lines = [json.loads(l) for l in
             open(tool_metrics.LOG_PATH).read().strip().splitlines()]
    assert lines[0]["cache_hit"] is False
    assert lines[1]["cache_hit"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_heatmap_caching.py -v`
Expected: FAIL — `heatmap_data` not yet decorated.

- [ ] **Step 3: Modify `app/tools/spatial.py:260-300`**

Apply this Edit to the `heatmap_data` registration. Find:

```python
    @tool(registry, name="heatmap_data",
           description=(
               "点要素热力图。✅ 用于：用户宽泛询问『分布』『热度』『密度趋势』时"
               "的首选——优先 render_type='native' 原生渲染，轻量、不增加数据负担。"
               "\n❌ 不要用于：(1) 需要网格统计值（每格计数/求和）— 用 h3_binning；"
               "(2) 需要矢量等值面用于导出/制图 — 用 kde_contours；"
               "(3) 需要连续概率面做后续叠加分析 — 用 kde_surface。"
           ),
           args_model=HeatmapDataArgs)
    def heatmap_data(geojson: Any, cell_size: int = 500, radius: int = 2000, render_type: str = "raster", palette: str = "classic") -> dict:
```

Replace with:

```python
    @tool(registry, name="heatmap_data",
           description=(
               "点要素热力图。✅ 用于：用户宽泛询问『分布』『热度』『密度趋势』时"
               "的首选——优先 render_type='native' 原生渲染，轻量、不增加数据负担。"
               "\n❌ 不要用于：(1) 需要网格统计值（每格计数/求和）— 用 h3_binning；"
               "(2) 需要矢量等值面用于导出/制图 — 用 kde_contours；"
               "(3) 需要连续概率面做后续叠加分析 — 用 kde_surface。"
           ),
           args_model=HeatmapDataArgs)
    @cached_tool(ttl=3600)
    def heatmap_data(geojson: Any, cell_size: int = 500, radius: int = 2000, render_type: str = "raster", palette: str = "classic") -> dict:
```

The TTL is 1h (not 1d) because heatmap is often derived from layers that users may edit — shorter TTL keeps stale data risk low.

Then find the existing returns inside `heatmap_data` (near the end of the function) and ensure any FeatureCollection-shaped return goes through `trim_features`. Locate the existing `if result.get("success"):` block near `app/tools/spatial.py:299` and add trim at every return point. Specifically, find each `return ...` inside this function and apply:

For the native return (around line 284):

```python
            return data
```

Replace with:

```python
            # native mode returns the input shape — trim if it's an FC
            if isinstance(data, dict) and data.get("type") == "FeatureCollection":
                data = trim_features(data)
            return data
```

For the raster/grid return at the end (find the final `return result` of the function body, ~line 305), replace `return result` with:

```python
            if isinstance(result, dict) and isinstance(result.get("data"), dict) and result["data"].get("type") == "FeatureCollection":
                result["data"] = trim_features(result["data"])
            return result
```

(If `heatmap_data` already has multiple return statements with different shapes, apply the same `if isinstance(...) and ...type == FeatureCollection: trim_features` pattern to each; don't change non-FC returns.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_heatmap_caching.py -v`
Expected: PASS.

Run the existing heatmap tests to verify no regression:

Run: `pytest tests/ -v -k heatmap`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/tools/spatial.py tests/test_heatmap_caching.py
git commit -m "$(cat <<'EOF'
perf(tools): opt heatmap_data into result cache + payload trim

@cached_tool(ttl=3600): heatmap is often derived from user-editable layers,
so 1h TTL not 1d. trim_features applied at every FeatureCollection return
path (native + raster + grid modes).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: Opt `h3_binning` into cache + trim

**Files:**
- Modify: `app/tools/advanced_spatial.py:285` (the `h3_binning` registration)
- Create: `tests/test_h3_binning_caching.py`

- [ ] **Step 1: Read the existing `h3_binning` registration**

Run: `sed -n '280,330p' app/tools/advanced_spatial.py`

Note the function signature and return shape — needed for the test and the trim integration.

- [ ] **Step 2: Write the failing test**

Create `tests/test_h3_binning_caching.py`:

```python
"""h3_binning perf opt-in: cache hit + trim on big bins."""
import pytest
from unittest.mock import patch, MagicMock

from app.tools.registry import ToolRegistry
from app.tools.advanced_spatial import register_advanced_spatial_tools
from app.lib.tool_cache import _reset_redis_client_for_tests
from app.services import tool_metrics


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_metrics, "LOG_PATH", str(tmp_path / "tool_metrics.jsonl"))
    tool_metrics._reset_for_tests()
    _reset_redis_client_for_tests()
    yield
    tool_metrics._reset_for_tests()
    _reset_redis_client_for_tests()


@pytest.mark.asyncio
async def test_h3_binning_second_call_cache_hit():
    reg = ToolRegistry()
    register_advanced_spatial_tools(reg)

    storage = {}
    args = {
        "geojson": {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.0, 39.0]}, "properties": {}}
            for _ in range(5)
        ]},
        # use whatever the tool's required args are — adjust based on observed signature
        "resolution": 7,
    }
    with patch("app.lib.tool_cache._get_redis_client") as mock_client:
        mock_redis = MagicMock()
        mock_redis.get.side_effect = lambda k: storage.get(k)
        mock_redis.setex.side_effect = lambda k, ttl, v: storage.__setitem__(k, v)
        mock_client.return_value = mock_redis

        r1 = await reg.dispatch("h3_binning", args, session_id=None)
        r2 = await reg.dispatch("h3_binning", args, session_id=None)

    assert r1 == r2
    import json
    lines = [json.loads(l) for l in
             open(tool_metrics.LOG_PATH).read().strip().splitlines()]
    assert lines[1]["cache_hit"] is True
```

If `h3_binning` requires the `h3` library and it's not installed in dev, mark this test `@pytest.mark.heavy` to match the existing marker convention:

```python
@pytest.mark.heavy
@pytest.mark.asyncio
async def test_h3_binning_second_call_cache_hit():
    ...
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_h3_binning_caching.py -v` (or with `-m heavy` if marker added)
Expected: FAIL — h3_binning not yet decorated.

- [ ] **Step 4: Modify `app/tools/advanced_spatial.py`**

Find the `h3_binning` registration block (starting around line 285). Add `@cached_tool(ttl=3600)` BELOW `@tool(...)` and ABOVE `def h3_binning(...)`. Inside the function body, locate every `return ...` that returns a dict containing a FeatureCollection and wrap with `trim_features`. Pattern: same as Task 11.

Add the imports at the top of `app/tools/advanced_spatial.py` if not already:

```python
from app.tools._utils import cached_tool, trim_features
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_h3_binning_caching.py -v`
Expected: PASS.

Run existing h3 tests:

Run: `pytest tests/ -v -k h3`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add app/tools/advanced_spatial.py tests/test_h3_binning_caching.py
git commit -m "$(cat <<'EOF'
perf(tools): opt h3_binning into result cache + payload trim

@cached_tool(ttl=3600) + trim_features at FC return paths. H3 hex grids
explode quickly with resolution — trim caps the output size before it hits
the wire.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 13: Opt `kde_contours` into cache + trim

**Files:**
- Modify: `app/tools/spatial_stats.py:208` (the `kde_contours` registration)
- Create: `tests/test_kde_contours_caching.py`

- [ ] **Step 1: Read the existing `kde_contours` registration**

Run: `sed -n '205,260p' app/tools/spatial_stats.py`

Note signature + return shape.

- [ ] **Step 2: Write the failing test**

Create `tests/test_kde_contours_caching.py`:

```python
"""kde_contours perf opt-in: cache hit + trim on contour FC."""
import pytest
from unittest.mock import patch, MagicMock

from app.tools.registry import ToolRegistry
from app.tools.spatial_stats import register_spatial_stats_tools
from app.lib.tool_cache import _reset_redis_client_for_tests
from app.services import tool_metrics


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_metrics, "LOG_PATH", str(tmp_path / "tool_metrics.jsonl"))
    tool_metrics._reset_for_tests()
    _reset_redis_client_for_tests()
    yield
    tool_metrics._reset_for_tests()
    _reset_redis_client_for_tests()


@pytest.mark.heavy  # scipy/sklearn dependency — gate behind heavy marker
@pytest.mark.asyncio
async def test_kde_contours_second_call_cache_hit():
    reg = ToolRegistry()
    register_spatial_stats_tools(reg)

    storage = {}
    args = {
        "geojson": {"type": "FeatureCollection", "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [116.0 + i * 0.01, 39.0]},
             "properties": {}}
            for i in range(20)
        ]},
    }
    with patch("app.lib.tool_cache._get_redis_client") as mock_client:
        mock_redis = MagicMock()
        mock_redis.get.side_effect = lambda k: storage.get(k)
        mock_redis.setex.side_effect = lambda k, ttl, v: storage.__setitem__(k, v)
        mock_client.return_value = mock_redis

        r1 = await reg.dispatch("kde_contours", args, session_id=None)
        r2 = await reg.dispatch("kde_contours", args, session_id=None)

    assert r1 == r2
    import json
    lines = [json.loads(l) for l in
             open(tool_metrics.LOG_PATH).read().strip().splitlines()]
    assert lines[1]["cache_hit"] is True
```

(Verify the actual registration function name — likely `register_spatial_stats_tools`. If different, adjust the import.)

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_kde_contours_caching.py -v -m heavy`
Expected: FAIL — not yet decorated.

- [ ] **Step 4: Modify `app/tools/spatial_stats.py`**

Same pattern as Tasks 10–12. Add `@cached_tool(ttl=86400)` (KDE is deterministic, safe for 1 day) below the `@tool(...)` decorator on `kde_contours`. Wrap FC returns with `trim_features`.

Add at top of file:

```python
from app.tools._utils import cached_tool, trim_features
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_kde_contours_caching.py -v -m heavy`
Expected: PASS.

Run existing spatial_stats tests:

Run: `pytest tests/ -v -k spatial_stats`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add app/tools/spatial_stats.py tests/test_kde_contours_caching.py
git commit -m "$(cat <<'EOF'
perf(tools): opt kde_contours into result cache + payload trim

@cached_tool(ttl=86400) — KDE is deterministic, safe to cache 1 day.
trim_features on contour FC returns shrinks payload for dense outputs.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 14: Phase 2 ship gate — full suite + manual digest verification

**Files:** none (verification + final commit)

- [ ] **Step 1: Run the full test suite**

Run: `pytest -v`
Expected: all PASS (skipping `-m heavy` tests if scipy/h3 not installed locally — that's CI's job).

- [ ] **Step 2: Smoke-run a fake heatmap workflow and confirm a digest line**

```bash
python -c "
import asyncio, os
os.makedirs('logs', exist_ok=True)
# Clear prior smoke
open('logs/tool_metrics.jsonl', 'w').close()

from app.tools.registry import ToolRegistry
from app.tools.spatial import register_spatial_tools
from app.services import tool_metrics

async def main():
    r = ToolRegistry()
    register_spatial_tools(r)
    fc = {'type': 'FeatureCollection', 'features': [
        {'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [116.0, 39.0]}, 'properties': {}}
    ]}
    # First call: miss; second: hit
    await r.dispatch('buffer_analysis', {'geojson': fc, 'distance': 100.0, 'unit': 'm'}, session_id=None)
    await r.dispatch('buffer_analysis', {'geojson': fc, 'distance': 100.0, 'unit': 'm'}, session_id=None)
    tool_metrics.emit_digest()

asyncio.run(main())
"
echo '--- JSONL rows ---'
cat logs/tool_metrics.jsonl
echo '--- Digest (in stderr / app log) ---'
# The digest goes to logger.info — re-run if needed with log capture
```

Expected:
- Two JSONL rows in `logs/tool_metrics.jsonl`: first `cache_hit:false`, second `cache_hit:true`
- One `TOOL_METRICS_DIGEST n=2 top_cumulative=[("buffer_analysis",...)]` line visible (capture stderr if needed)

- [ ] **Step 3: Update CHANGELOG.md**

Edit `CHANGELOG.md`. Add to the top (or under the current version's "Added" section):

```markdown
### Performance

- Tool-layer result cache (`@cached_tool`) opt-in via decorator, Redis-keyed,
  with graceful fallback when Redis is unreachable.
- Automatic per-dispatch timing in `ToolRegistry.dispatch` — every tool call
  writes one JSONL row to `logs/tool_metrics.jsonl` and contributes to an
  in-process aggregator that emits a `TOOL_METRICS_DIGEST` line every 100
  calls and at FastAPI shutdown.
- `trim_features` helper for payload reduction (caps FeatureCollection at
  5000 features, rounds coordinates to 6 decimals).
- `buffer_analysis`, `heatmap_data`, `h3_binning`, `kde_contours` opted in.
```

- [ ] **Step 4: Final commit**

```bash
git add CHANGELOG.md
git commit -m "$(cat <<'EOF'
docs(changelog): record tool-layer perf infrastructure landing

Cache + timing + trim shipped; 4 heaviest tools opted in.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review Notes

Reviewed against spec `2026-05-27-tool-perf-infrastructure-design.md`:

**Spec coverage:**
- ✅ `make_cache_key` with `v1` prefix + sorted JSON + ref-skip → Task 1
- ✅ `get_cached`/`set_cached` with graceful Redis-down fallback → Task 2
- ✅ `@cached_tool` decorator sync/async/skip_if → Task 3
- ✅ `trim_features` max_features + precision + non-FC pass-through → Task 4
- ✅ `record_tool_call` JSONL writer with documented schema → Task 5
- ✅ In-process aggregator + auto digest at N=100 + manual `emit_digest()` → Task 6
- ✅ Registry timing wrapper + `cache_hit_var` ContextVar → Task 7
- ✅ Lifespan shutdown calls `emit_digest()` → Task 8
- ✅ Phase 2 opt-in for `buffer_analysis`, `heatmap_data`, `h3_binning`, `kde_contours` → Tasks 10–13
- ✅ Phase 1/Phase 2 ship gates with full pytest → Tasks 9, 14

**Out of scope (per spec, NOT in this plan):** Celery routing changes, sampling helpers, active cache invalidation, admin dashboard, OpenTelemetry, cache stampede protection, Phase 3 second-tier opt-in (data-driven).

**Type/method-name consistency check:**
- `cache_hit_var` (ContextVar) — used in Task 3 (set) and Task 7 (read). ✅
- `record_tool_call` signature — keyword-only, parameters `tool, arg_bytes, result_bytes, duration_ms, cache_hit, error, session_id` — used identically in Tasks 5 and 7. ✅
- `_reset_redis_client_for_tests` and `_reset_for_tests` — internal test hooks, used consistently in their respective test fixtures. ✅
- `_get_redis_client` patched in tests in Tasks 2, 3, 10, 11, 12, 13 — consistent. ✅

**Known sharp edges to watch during execution:**
- Task 7's ContextVar propagation relies on the fact that asyncio.Task carries one Context across `await` boundaries inside the same task — `cache_hit_var.set()` inside the decorator and `cache_hit_var.get()` in dispatch's `finally` are guaranteed to see the same task-local value. Using `set()`/`reset(token)` rather than `copy_context()` keeps it simple. If a future change moves dispatch into a child task via `asyncio.create_task` without explicit context propagation, this would break.
- Task 11's `heatmap_data` has multiple return paths (native / Celery success / fallback) — make sure trim is applied at each FC return, not just the first one. The plan step lists each return point explicitly; do not skip.
- Task 12 / 13 — `register_advanced_spatial_tools` and `register_spatial_stats_tools` function names are assumed by convention. The Read step at the start of each task verifies the actual exports; adjust imports if the actual names differ.

---

Plan complete and saved to `docs/superpowers/plans/2026-05-27-tool-perf-infrastructure.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
