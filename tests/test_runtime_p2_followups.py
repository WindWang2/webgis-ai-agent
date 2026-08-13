"""P2 follow-up regression tests (post-PR #352 hardening round).

Covers the deferred P2 items fixed after the first adversarial review:

  A. F29  TaskQueueService._task_owners 无界增长 → LRU 有界化
  B.      rate limiter Redis 回退永久缓存 → 周期重探测
  C.      TurnResumeRegistry 重注册不刷新 LRU 序（MRU bump）
  D.      get_session_store() 与 session_data_manager 双单例 → 共享实例
  E.      _mint_one_map_action 复用陈旧 action_id（F25）→ 每次派发铸新 id

All deterministic — no wall-clock sleeps (injectable time / module-state reset).
"""
from __future__ import annotations

from typing import Any

import pytest

from app.services.task_queue import TaskQueueService
from app.services.chat.event_resume import TurnEventBuffer, TurnResumeRegistry
from app.services.tool_dispatch_service import ToolDispatchService


# ─── A. F29: _task_owners bounded LRU ────────────────────────────────────────


class TestTaskOwnersBounded:
    @pytest.fixture
    def small_owners(self, monkeypatch):
        monkeypatch.setattr(TaskQueueService, "_OWNERS_MAX_ENTRIES", 3)
        TaskQueueService._task_owners.clear()
        yield
        TaskQueueService._task_owners.clear()

    def test_evicts_oldest_beyond_cap(self, small_owners):
        TaskQueueService.register_owner("t1", "u1")
        TaskQueueService.register_owner("t2", "u2")
        TaskQueueService.register_owner("t3", "u3")
        TaskQueueService.register_owner("t4", "u4")  # 超上限 → 逐出 t1
        assert TaskQueueService.verify_owner("t1", "u1") is False
        assert TaskQueueService.verify_owner("t2", "u2") is True
        assert TaskQueueService.verify_owner("t3", "u3") is True
        assert TaskQueueService.verify_owner("t4", "u4") is True

    def test_verify_refreshes_recency(self, small_owners):
        TaskQueueService.register_owner("a", "u")
        TaskQueueService.register_owner("b", "u")
        TaskQueueService.register_owner("c", "u")
        # b 命中刷新 recency；再注册 d 逐出最旧的 a（而不是刚命中的 b）
        assert TaskQueueService.verify_owner("b", "u") is True
        TaskQueueService.register_owner("d", "u")
        assert TaskQueueService.verify_owner("a", "u") is False
        assert TaskQueueService.verify_owner("b", "u") is True
        assert TaskQueueService.verify_owner("d", "u") is True

    def test_register_ignores_empty_user(self, small_owners):
        TaskQueueService.register_owner("t1", "")
        assert TaskQueueService.verify_owner("t1", "") is False


# ─── B. rate limiter: fallback re-probe ──────────────────────────────────────


class TestRateLimiterReprobe:
    @pytest.fixture
    def reset_module_state(self):
        import app.core.rate_limiter as rl

        saved = (rl._rate_limiter, rl._rate_limiter_fallback_at)
        rl._rate_limiter = None
        rl._rate_limiter_fallback_at = None
        yield rl
        rl._rate_limiter, rl._rate_limiter_fallback_at = saved

    @pytest.mark.asyncio
    async def test_fallback_then_reprobe_after_interval(self, monkeypatch, reset_module_state):
        import app.core.rate_limiter as rl
        from app.core.rate_limiter import MemoryRateLimiter, RedisRateLimiter

        calls = {"n": 0}

        class _FakeClient:
            def __init__(self, url, **kw):
                self.url = url

            async def ping(self):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("redis down")
                return True

        monkeypatch.setattr("redis.asyncio.from_url", _FakeClient)
        monkeypatch.setattr(rl, "_FALLBACK_REPROBE_S", 60.0)

        limiter1 = await rl.get_rate_limiter()
        assert isinstance(limiter1, MemoryRateLimiter)
        assert rl._rate_limiter_fallback_at is not None

        # 间隔内不重探测（仍内存版）
        monkeypatch.setattr("app.core.rate_limiter.time.monotonic", lambda: 100.0)
        rl._rate_limiter_fallback_at = 60.0  # 40s 前回退，< 60s 间隔
        limiter_same = await rl.get_rate_limiter()
        assert limiter_same is limiter1

        # 超过间隔 → 重探测成功 → 切回 Redis 后端
        rl._rate_limiter_fallback_at = 30.0  # 70s 前回退
        limiter2 = await rl.get_rate_limiter()
        assert isinstance(limiter2, RedisRateLimiter)
        assert rl._rate_limiter_fallback_at is None
        assert calls["n"] == 2

    @pytest.mark.asyncio
    async def test_reprobe_failure_refreshes_fallback(self, monkeypatch, reset_module_state):
        import app.core.rate_limiter as rl
        from app.core.rate_limiter import MemoryRateLimiter

        calls = {"n": 0}

        class _AlwaysDown:
            async def ping(self):
                calls["n"] += 1
                raise RuntimeError("redis down")

        monkeypatch.setattr("redis.asyncio.from_url", lambda url, **kw: _AlwaysDown())
        monkeypatch.setattr(
            "app.core.rate_limiter.time.monotonic", lambda: 1000.0
        )
        rl._rate_limiter_fallback_at = 0.0  # 早已超过间隔
        limiter = await rl.get_rate_limiter()
        assert isinstance(limiter, MemoryRateLimiter)
        assert rl._rate_limiter_fallback_at == 1000.0  # 刷新回退时刻，不每次打 Redis
        await rl.get_rate_limiter()
        assert calls["n"] == 1  # 间隔内不再探测


# ─── C. TurnResumeRegistry MRU bump ──────────────────────────────────────────


class TestResumeRegistryMRU:
    def _buffer(self, sid: str, n_events: int = 0) -> TurnEventBuffer:
        b = TurnEventBuffer(session_id=sid, message=f"msg-{sid}", max_events=8)
        for i in range(n_events):
            b.record(f"data: x{i}\n\n")
        return b

    def test_re_register_refreshes_lru_recency(self):
        reg = TurnResumeRegistry(max_sessions=3, max_buffers_per_session=2)
        reg.register("A", self._buffer("A"))
        reg.register("B", self._buffer("B"))
        reg.register("C", self._buffer("C"))
        # 重注册 A → A 变为最新；再注册 D 应逐出 B（最旧），而不是 A
        reg.register("A", self._buffer("A", 1))
        reg.register("D", self._buffer("D"))
        assert reg.get("A") is not None
        assert reg.get("D") is not None
        assert reg.get("B") is None
        assert reg.get("C") is not None


# ─── D. get_session_store() shares the canonical singleton ───────────────────


class TestSessionStoreSingleton:
    def test_get_session_store_is_session_data_manager(self):
        from app.services import session_data
        from app.services.session_data_protocol import (
            get_session_store,
            set_active_session_store,
        )

        try:
            set_active_session_store(None)
            store = get_session_store()
            assert store is session_data.session_data_manager
        finally:
            set_active_session_store(None)

    def test_set_active_store_still_overrides(self):
        from app.services.session_data import MemorySessionStore
        from app.services.session_data_protocol import (
            get_session_store,
            set_active_session_store,
        )

        custom = MemorySessionStore()
        try:
            set_active_session_store(custom)
            assert get_session_store() is custom
        finally:
            set_active_session_store(None)


# ─── E. F25: _mint_one_map_action always mints a fresh id ────────────────────


class TestMintMapActionFreshId:
    def test_reuse_scenario_mints_distinct_ids(self):
        cmd: dict[str, Any] = {
            "command": "fly_to",
            "params": {"longitude": 116.3, "latitude": 39.9},
            "action_id": "stale-from-previous-turn",  # 跨 turn 陈旧 id
        }
        e1 = ToolDispatchService._mint_one_map_action(cmd)
        e2 = ToolDispatchService._mint_one_map_action(cmd)
        assert e1 is not None and e2 is not None
        assert e1["action_id"] != e2["action_id"]
        assert e1["action_id"] != "stale-from-previous-turn"
        assert cmd["action_id"] == e2["action_id"]

    def test_missing_command_returns_none(self):
        assert ToolDispatchService._mint_one_map_action({"params": {}}) is None
