"""B-4（review-fix-rereview-20261108）：Redis 会话锁同步上下文释放。

`_ResilientSessionLock.release()` 的无运行 loop 分支此前用
`asyncio.run(coro)` 在临时新事件循环上执行 token 校验 DEL —— 而
redis-py AsyncRedis 连接池是 loop 亲和的：client 在主 loop 上用过，
临时循环上必然失败（被捕获记 warning），DEL 从未生效，同 session 的
后续 acquire 全部排队到 30s TTL 自然过期；且每次调用创建/销毁一个
事件循环。
"""
import asyncio

import pytest

from app.services.distributed_lock import _InProcessLock, _ResilientSessionLock


class LoopAffineFakeClient:
    """模拟 redis-py 的 loop 亲和语义：连接池绑定首次使用的 loop，
    在其它 loop 上调用必然失败（与真实 AsyncRedis 行为一致）。"""

    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None
        self.store: dict[str, str] = {}
        self.eval_calls: list[tuple[str, str]] = []

    def _check_loop(self) -> None:
        current = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = current
            return
        if current is not self._loop:
            raise RuntimeError(
                "Future attached to a different loop (redis-py pool affinity)"
            )

    async def set(self, key, token, nx=True, px=None):
        self._check_loop()
        if nx and key in self.store:
            return None
        self.store[key] = token
        return True

    async def eval(self, script, numkeys, key, token, *extra):
        self._check_loop()
        self.eval_calls.append((key, token))
        if b"del" in script or "del" in str(script):
            self.store.pop(key, None)
        return 1


@pytest.mark.asyncio
async def test_sync_release_from_worker_thread_reaches_bound_loop():
    """工作线程（无运行 loop）调 release()：DEL 必须递交到 client 绑定的
    主 loop 执行 —— 而不是 asyncio.run 一个必败的临时循环。"""
    client = LoopAffineFakeClient()
    lock = _ResilientSessionLock(
        client, "webgis:sessionlock:b4", _InProcessLock(),
        ttl_ms=30_000,
    )
    await lock.acquire()
    assert lock.mode == "redis"
    assert client.store.get("webgis:sessionlock:b4") == lock._token

    # 模拟外部适配器把 release 排回线程（无运行 loop 的同步上下文）。
    def _sync_release():
        lock.release()

    await asyncio.to_thread(_sync_release)

    # DEL 已在主 loop 上执行（token 校验通过，key 释放）。
    for _ in range(50):
        if client.eval_calls:
            break
        await asyncio.sleep(0.02)
    assert client.eval_calls == [("webgis:sessionlock:b4", lock._token)]
    assert "webgis:sessionlock:b4" not in client.store


@pytest.mark.asyncio
async def test_sync_release_without_bound_loop_does_not_raise():
    """绑定 loop 不可用（关停/孤立锁对象）：诚实保态 —— 不抛、不创建
    必败的临时事件循环，锁自然等 TTL（可观测 warning）。"""
    client = LoopAffineFakeClient()
    lock = _ResilientSessionLock(
        client, "webgis:sessionlock:b4c", _InProcessLock(),
        ttl_ms=30_000,
    )
    await lock.acquire()
    # 模拟绑定 loop 已死：释放前把它标记为不可递交（loop.close 后
    # is_running() 为 False；这里用一个已关闭的独立 loop 占位）。
    dead_loop = asyncio.new_event_loop()
    dead_loop.close()
    lock._bound_loop = dead_loop

    def _sync_release():
        lock.release()  # 不得抛、不得 asyncio.run

    await asyncio.to_thread(_sync_release)
    # DEL 未发出（无可用递交目标），key 等 TTL —— 诚实降级而非假成功。
    assert client.eval_calls == []
