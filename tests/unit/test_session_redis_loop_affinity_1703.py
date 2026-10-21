"""#1703: RedisSessionStore legacy ``self._r`` must follow the current event loop.

``_ensure_connected`` 的 per-loop 快路径此前命中缓存后直接 ``return client``，
不刷新 ``self._r`` / ``self._bound_loop``；而本类 32 个方法在
``await self._ensure_connected()`` 之后直接读 ``self._r``。loop 交替
（REST/manager loop ↔ agent 工具 loop）后，回到旧 loop 的每次 Redis 操作都会
驱动另一个 loop 的客户端 —— redis-py 的 loop-affinity 硬约束（典型报错
``Event loop is closed`` / ``... attached to a different loop``）。

这些用例锁定「self._r 始终指向当前 loop 的最新客户端」这一不变式：
loop A 物化 → loop B 物化 → 回 loop A 读写，用的必须是 loop A 的客户端。
"""
import asyncio
import threading
from typing import Any, List, Optional
from unittest.mock import patch

import fakeredis.aioredis

from app.services.session_data_redis import RedisSessionStore


class _LoopAffineFakeRedis:
    """fakeredis 客户端代理，模拟 redis-py 的 loop-affinity 硬约束。

    客户端在创建时捕获所在 loop；此后每次被使用都记录当前 loop，并在
    「使用 loop != 创建 loop」时抛 RuntimeError —— 与 redis-py 在跨 loop
    使用连接池时的真实行为同构（#1703 的失败形态）。
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.used_loops: List[asyncio.AbstractEventLoop] = []
        self.owner_loop: Optional[asyncio.AbstractEventLoop] = None
        try:
            self.owner_loop = asyncio.get_running_loop()
        except RuntimeError:
            self.owner_loop = None

    def _touch(self) -> None:
        loop = asyncio.get_running_loop()
        self.used_loops.append(loop)
        if self.owner_loop is not None and loop is not self.owner_loop:
            raise RuntimeError(
                "Event loop is closed / client attached to a different loop "
                f"(bound={self.owner_loop!r}, used={loop!r})"
            )

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)

        if not callable(attr):
            return attr

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            self._touch()
            return attr(*args, **kwargs)

        return wrapper


def _run_in_new_loop(coro_factory) -> Any:
    """在独立线程的新 event loop 里跑一个协程工厂，返回 (result, loop, thread)。"""

    box: dict = {}

    def runner() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            box["result"] = loop.run_until_complete(coro_factory())
            box["loop"] = loop
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    t = threading.Thread(target=runner)
    t.start()
    t.join()
    return box


def _patched_from_url(created: list):
    def _from_url(*args: Any, **kwargs: Any) -> _LoopAffineFakeRedis:
        client = _LoopAffineFakeRedis(fakeredis.aioredis.FakeRedis(decode_responses=False))
        created.append(client)
        return client

    return _from_url


async def test_legacy_r_tracks_current_loop_after_alternation() -> None:
    """loop A 物化 → loop B 物化 → 回 loop A 读写：必须用 loop A 的客户端。"""
    store = RedisSessionStore(redis_url="redis://mock-1703:6379/0")
    created: list = []

    with patch("redis.asyncio.Redis.from_url", side_effect=_patched_from_url(created)):
        # ── loop A（本测试 loop）物化 ──────────────────────────────────────
        client_a = await store._ensure_connected()
        loop_a = asyncio.get_running_loop()
        assert store._loop_clients[loop_a] is client_a
        assert store._r is client_a and store._bound_loop is loop_a

        # ── loop B（另一线程的 loop）物化 ──────────────────────────────────
        box = _run_in_new_loop(store._ensure_connected)
        client_b = box["result"]
        loop_b = box["loop"]
        assert client_b is not client_a
        assert store._loop_clients[loop_b] is client_b
        assert store._r is client_b and store._bound_loop is loop_b
        # B 只物化、未使用
        assert client_b.used_loops == []

        # ── 回到 loop A：快路径命中 per-loop 缓存，legacy 字段必须刷回 A ────
        again = await store._ensure_connected()
        assert again is client_a
        assert store._r is client_a, (
            "#1703: per-loop 快路径命中后未刷新 self._r，旧 loop 会读到 "
            f"别的 loop 的客户端（bound={store._bound_loop!r}）"
        )
        assert store._bound_loop is loop_a

        # 真实读写路径（_ensure_connected 后直接读 self._r 的消费点）
        await store.set_session_clearing("sess-1703")
        assert await store.is_session_clearing("sess-1703") is True
        assert client_a.used_loops and client_a.used_loops[-1] is loop_a
        assert client_b.used_loops == [], "loop B 的客户端不得在 loop A 上被驱动"

    assert len(created) == 2, "loop 交替不得新建/复用错客户端"


async def test_legacy_r_tracks_current_loop_for_pipeline_consumer() -> None:
    """事务型消费点（self._r.pipeline()）同样跟随当前 loop。"""
    store = RedisSessionStore(redis_url="redis://mock-1703:6379/0")
    created: list = []

    with patch("redis.asyncio.Redis.from_url", side_effect=_patched_from_url(created)):
        loop_a = asyncio.get_running_loop()
        await store._ensure_connected()

        box = _run_in_new_loop(store._ensure_connected)
        assert box["result"] is not None

        # 回到 loop A 后走一个事务型消费点（set_map_state → self._r.pipeline()）
        assert await store.set_map_state("sess-1703b", "zoom", {"z": 5}) is True
        assert await store.get_state_field("sess-1703b", "zoom") == {"z": 5}
        assert store._r is store._loop_clients[loop_a]
        client_a = store._r
        assert client_a is not None
        assert client_a.used_loops and client_a.used_loops[-1] is loop_a
        client_b = created[1]
        assert client_b.used_loops == [], "loop B 的客户端不得在 loop A 上被驱动"
