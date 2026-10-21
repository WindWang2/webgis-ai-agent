"""跨进程缓存失效广播（ADR-0101 D11，V4 §27）。

``ref_lifecycle`` 仍是**唯一**失效权威：本模块只把失效事件**通知**给
其他进程，让它们的本地派生缓存尽早失效 —— 消息只含 id + reason，绝无
载荷；**正确性绝不依赖收到广播**（指纹/revision 校验仍是权威；错过
广播的进程按既有 TTL/指纹语义安全恢复）。

模式：
- Redis 可用（``settings.USE_REDIS`` 且客户端可建）：PUBLISH 到
  有界频道；后台监听线程把事件接到 ``ref_lifecycle.invalidate_ref_caches``
  （同进程失效，幂等）。
- 无 Redis：进程内模式照常工作 —— 发布是 no-op，不监听。

热路径卸载（#1723）：有运行中事件循环时（async 热路径，如 session
store() → 失效权威），同步 publish 卸载到 loop 默认线程池 —— 连接失败
要吃满 ``socket_connect_timeout=1``，绝不在事件循环上阻塞；失败后写入
退避时间戳，退避窗口内本进程静默跳过、窗口过后重探自愈。

有界性：频道单条；消息 ≤256 字节 JSON（四个固定字段）；监听线程
daemon，异常永不外溢。
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

CHANNEL = "webgis:cache-invalidation"

_MAX_FIELD = 128
_started = False
_start_lock = threading.Lock()


def _redis_client() -> Optional[Any]:
    """Redis 客户端（运行时可选；任何失败 → None = 进程内模式）。"""
    try:
        from app.core.config import settings

        if not getattr(settings, "USE_REDIS", False):
            return None
        import redis  # 仓库声明依赖（requirements.txt）

        return redis.Redis.from_url(
            getattr(settings, "REDIS_URL", "redis://localhost:6379/0"),
            socket_connect_timeout=1,
            socket_timeout=2,
            decode_responses=True,
        )
    except Exception:  # noqa: BLE001 - 广播是增值，绝不阻断主路径
        return None


_client_lock = threading.Lock()
_cached_client: Optional[Any] = None
_client_failed_at: Optional[float] = None
#: Redis 不可用后的重试间隔（评审 MAJOR：永久闩锁 = 特性死亡到进程重启；
#: 时间性退避让 Redis 恢复后自愈，同时避免每次事件都撞 2s socket 超时）。
_CLIENT_RETRY_S = 30.0


def _client_cached() -> Optional[Any]:
    """复用 Redis 客户端；失败后按 ``_CLIENT_RETRY_S`` 退避重探（自愈）。"""
    global _cached_client, _client_failed_at
    with _client_lock:
        if _cached_client is not None:
            return _cached_client
        import time as _time

        if _client_failed_at is not None and (
            _time.monotonic() - _client_failed_at < _CLIENT_RETRY_S
        ):
            return None  # 退避窗口内：诚实跳过（正确性由权威校验兜底）
        client = _redis_client()
        if client is None:
            _client_failed_at = _time.monotonic()
            return None
        _cached_client = client
        _client_failed_at = None
        return client


def _publish(
    client: Any, session_id: str, ref_id: str, reason: str
) -> bool:
    """同步发布（阻塞 IO；调用方负责在事件循环外卸载，见
    ``broadcast_ref_invalidation``）。"""
    global _cached_client, _client_failed_at
    try:
        message = json.dumps({
            "kind": "ref_invalidation",
            "session_id": str(session_id)[:_MAX_FIELD],
            "ref_id": str(ref_id)[:_MAX_FIELD],
            "reason": str(reason)[:32],
            "ts": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False)
        client.publish(CHANNEL, message)
        return True
    except Exception as exc:  # noqa: BLE001 - 广播是增值，绝不阻断主路径
        import time as _time

        with _client_lock:
            _cached_client = None  # 丢缓存；退避后重探（Redis 恢复自愈）
            # #1723：失败分支必须写退避时间戳（与 ``_redis_client() is None``
            # 分支对齐）—— 不写则 ``_client_cached`` 的窗口判定永不生效，
            # Redis 故障期每次 ref 写都重建客户端再撞一次连接超时（1s/次），
            # 且毫无退避。
            _client_failed_at = _time.monotonic()
        logger.debug("[cache-broadcast] publish failed (harmless): %s", exc)
        return False


#: 卸载 publish 的未完成 future 强引用集（asyncio 文档：丢弃引用可能被
#: GC、done 回调丢失）；done 后自动移除，语义同 ref_lifecycle 的任务集。
_offloaded_publishes: set = set()


def _offload_publish(
    loop: asyncio.AbstractEventLoop,
    client: Any,
    session_id: str,
    ref_id: str,
    reason: str,
) -> None:
    """把阻塞 publish 卸载到 loop 的默认线程池（#1723）。

    同步客户端 publish 在连接失败时吃满 ``socket_connect_timeout=1``
    （读 2s）—— 在 async 热路径（session store() → 失效权威）上执行会
    阻塞整个事件循环。卸载后调用方立即返回；失败退避照常在 ``_publish``
    内生效（退避窗口内静默跳过）。
    """
    future = loop.run_in_executor(
        None, _publish, client, session_id, ref_id, reason
    )
    _offloaded_publishes.add(future)
    future.add_done_callback(_offloaded_publishes.discard)


def broadcast_ref_invalidation(
    session_id: str, ref_id: str, reason: str = "REPLACE"
) -> bool:
    """发布 ref 失效事件（有界、无载荷；失败静默 = 依赖权威校验兜底）。

    有运行中事件循环（async 热路径）时同步 publish 卸载到线程池，绝不
    在 loop 上阻塞近 1s 的连接超时（#1723）；此时返回 True 表示「已卸载」
    而非「已确认送达」（best-effort 通知）。无 loop（同步 worker 线程）
    时原地发布。任何失败都进入 ``_CLIENT_RETRY_S`` 退避窗口：窗口内
    本进程静默跳过，窗口过后重探自愈。
    """
    client = _client_cached()
    if client is None:
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        _offload_publish(loop, client, session_id, ref_id, reason)
        return True
    return _publish(client, session_id, ref_id, reason)


def _apply_event(message: str) -> None:
    """把广播事件接到本地失效权威（ref_lifecycle；幂等、无载荷）。"""
    try:
        event = json.loads(message)
        if not isinstance(event, dict) or event.get("kind") != "ref_invalidation":
            return
        session_id = event.get("session_id")
        ref_id = event.get("ref_id")
        raw_reason = event.get("reason", "REPLACE")
        if not session_id or not ref_id:
            return
        from app.services.ref_lifecycle import (
            RefInvalidationReason,
            invalidate_ref_caches,
        )

        try:
            reason = RefInvalidationReason(str(raw_reason))
        except ValueError:
            reason = RefInvalidationReason.REPLACE
        # 签名是 (session_id, ref_ids: list[str], reason: RefInvalidationReason)
        # —— 评审 MAJOR：此前传 (str, str, str) 会逐字符迭代 ref 且在
        # reason.value 上 AttributeError（被吞）→ 跨进程失效静默失效。
        # publish_broadcast=False：广播监听路径绝不再发布（round-2 评审
        # CRITICAL —— 否则收到通知→再失效→再发布 = 无限广播风暴）。
        invalidate_ref_caches(str(session_id), [str(ref_id)], reason=reason,
                              publish_broadcast=False)
    except Exception as exc:  # noqa: BLE001 - 监听路径永不外溢
        logger.debug("[cache-broadcast] apply failed (harmless): %s", exc)


def start_listener() -> bool:
    """启动后台监听（幂等；无 Redis → False = 进程内模式）。"""
    global _started
    with _start_lock:
        if _started:
            return True
        client = _redis_client()
        if client is None:
            return False

        thread = threading.Thread(target=_listen_loop, args=(client,), daemon=True,
                                  name="cache-invalidation-listener")
        thread.start()
        _started = True
        return True


#: 断连后的重连退避（秒）。生产路径默认；测试可注入更小值有界等待。
_LISTENER_RECONNECT_S = 5.0


def _listen_once(client: Any) -> None:
    """一个 subscribe/listen 纪元：断连/出错时以异常退出，由 _listen_loop 重试。"""
    pubsub = client.pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe(CHANNEL)
    for message in pubsub.listen():
        if message and message.get("type") == "message":
            _apply_event(str(message.get("data") or ""))


def _listen_loop(client: Any, *, reconnect_wait_s: float = _LISTENER_RECONNECT_S,
                 stop: Optional[threading.Event] = None) -> None:
    """监听主循环：任何异常 → 等待 ``reconnect_wait_s`` → 重新订阅（自愈）。

    Redis 断连期间错过的事件不补投 —— 安全（正确性由 ref_lifecycle 权威 +
    TTL/epoch 语义兜底，广播只是提前失效的增值通知）。``stop`` 置位后于
    下一次异常/纪元边界退出（仅供测试注入；生产线程随进程存活）。
    """
    while stop is None or not stop.is_set():
        try:
            _listen_once(client)
        except Exception as exc:  # noqa: BLE001 - 断连重试
            logger.debug("[cache-broadcast] listener reconnect: %s", exc)
            if stop is not None and stop.is_set():
                break
            threading.Event().wait(reconnect_wait_s)
