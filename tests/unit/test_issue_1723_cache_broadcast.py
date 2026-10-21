"""#1723: cache_broadcast 在 async 热路径的阻塞 publish + 失败分支不退避。

锁定两条修复（均只动 ``app/services/cache_broadcast.py`` —— 它是唯一
生产调用方 ``ref_lifecycle.invalidate_ref_caches`` 的唯一收口）：

1. publish 失败分支补写 ``_client_failed_at``：退避窗口内本进程静默
   短路（不重建客户端、不再撞 1s 连接超时），窗口过后重探自愈 ——
   修复前该路径上退避机制永不生效（Redis 故障期每笔 ref 写卡一次）。
2. 有运行中事件循环时 publish 卸载到线程池：sync store() →
   invalidate_ref_caches 热路径不再被同步 socket 超时阻塞事件循环。
"""
import asyncio
import threading
import time

import pytest

import app.services.cache_broadcast as cb


@pytest.fixture
def reset_client_cache():
    cb._cached_client = None
    cb._client_failed_at = None
    yield
    cb._cached_client = None
    cb._client_failed_at = None


class _FailingClient:
    """publish 恒失败（模拟 Redis 故障期的连接失败）。"""

    def publish(self, channel, message):
        raise ConnectionError("redis down (simulated)")


def test_publish_failure_enters_backoff_window(reset_client_cache, monkeypatch):
    """#1723 修复一：失败分支必须写退避时间戳（与 ``_redis_client() is
    None`` 分支对齐），否则 ``_client_cached`` 的窗口判定永不生效。"""
    built = {"n": 0}

    def fake_redis_client():
        built["n"] += 1
        return _FailingClient()

    monkeypatch.setattr(cb, "_redis_client", fake_redis_client)

    assert cb.broadcast_ref_invalidation("s", "r", "OVERWRITE") is False
    assert cb._client_failed_at is not None, "失败分支未写退避时间戳"

    # 退避窗口内：静默短路 —— 不重建客户端、不再发起 publish。
    assert cb.broadcast_ref_invalidation("s", "r", "OVERWRITE") is False
    assert built["n"] == 1, "退避窗口内不得重新探测/重建客户端"

    # 窗口过后：重新探测（自愈，不是永久闩锁）。
    cb._client_failed_at = time.monotonic() - (cb._CLIENT_RETRY_S + 1)
    assert cb.broadcast_ref_invalidation("s", "r", "OVERWRITE") is False
    assert built["n"] == 2


class _SlowClient:
    """publish 阻塞 0.1s（模拟 socket 超时量级）并记录执行线程。"""

    def __init__(self) -> None:
        self.seen_thread: threading.Thread | None = None
        self.published: list = []

    def publish(self, channel, message):
        time.sleep(0.1)
        self.seen_thread = threading.current_thread()
        self.published.append((channel, message))
        return 1


@pytest.mark.asyncio
async def test_publish_offloaded_off_the_event_loop(reset_client_cache, monkeypatch):
    """#1723 修复二：async 热路径上 broadcast 立即返回，publish 在别的线程。"""
    client = _SlowClient()
    monkeypatch.setattr(cb, "_client_cached", lambda: client)

    loop_thread = threading.current_thread()
    started = time.monotonic()
    assert cb.broadcast_ref_invalidation("s", "r", "OVERWRITE") is True
    elapsed = time.monotonic() - started
    assert elapsed < 0.05, f"publish 阻塞了事件循环: {elapsed:.3f}s"

    await asyncio.sleep(0.3)  # 放卸载任务跑完
    assert client.seen_thread is not None, "卸载的 publish 未执行"
    assert client.seen_thread is not loop_thread, "publish 必须在事件循环外"


@pytest.mark.asyncio
async def test_authority_hot_path_does_not_block_on_publish(
    reset_client_cache, monkeypatch
):
    """#1723 端到端：async store() 链上的 invalidate_ref_caches（ref_lifecycle
    → broadcast_ref_invalidation）不得被同步 publish 卡住事件循环。"""
    import app.services.ref_lifecycle as rl

    client = _SlowClient()
    monkeypatch.setattr(cb, "_client_cached", lambda: client)

    loop_thread = threading.current_thread()
    started = time.monotonic()
    n = rl.invalidate_ref_caches(
        "sess-1723",
        ["ref:1723-a"],
        reason=rl.RefInvalidationReason.REPLACE,
        publish_broadcast=True,
    )
    elapsed = time.monotonic() - started
    assert n == 1
    assert elapsed < 0.05, f"失效权威被 publish 阻塞: {elapsed:.3f}s"

    await asyncio.sleep(0.3)
    assert client.seen_thread is not None, "卸载的 publish 未执行"
    assert client.seen_thread is not loop_thread, "publish 必须在事件循环外"
    # 消息契约不变：有界频道 + 无载荷信封。
    channel, message = client.published[0]
    assert channel == cb.CHANNEL
    import json

    event = json.loads(message)
    assert set(event) == {"kind", "session_id", "ref_id", "reason", "ts"}


def test_sync_context_publishes_inline(reset_client_cache, monkeypatch):
    """无运行中事件循环（同步 worker 线程）时保持原地发布语义 —— 不引入
    线程卸载的行为回归。"""
    client = _SlowClient()
    monkeypatch.setattr(cb, "_client_cached", lambda: client)

    caller_thread = threading.current_thread()
    assert cb.broadcast_ref_invalidation("s", "r", "OVERWRITE") is True
    assert client.seen_thread is caller_thread
