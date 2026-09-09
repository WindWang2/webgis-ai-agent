"""Collab event bus —— 服务端协作通知平面（Workbench V6 / ADR-0119）。

事件信封（单扇出路径；发布者不本地直发，降级模式除外）::

    {"v": 1, "kind": "doc|delta|presentation|op|presence|lock|artifact",
     "sid": <session_id>, "seq": <int>, "ts": <iso8601>, "data": {...}}

seq 语义：
- mutation 派生事件（doc/delta/presentation/op）``seq = mutation_revision``
  —— 与 CAS 单调同源，天然充当 replay cursor；
- 瞬态事件（presence/lock）``seq = 服务端接收时刻 time.time_ns()``（不信
  客户端时钟）—— 只参与「新者胜」，不参与 replay。

载荷上限（超限降级为 truncated 提示，接收方 refetch 权威）：
doc/delta ≤ 64KB，其余 ≤ 2KB。事件不含任何凭据。

多进程：PUBLISH ``webgis:collab:{sid}``，每进程一个 psubscribe listener
task 扇出给本地连接。无 Redis：进程内直连扇出（degraded —— 跨进程不可达
是诚实降级，正确性由 revision 对账兜底，见 ws_collab 的 pong/sync）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

CHANNEL_PREFIX = "webgis:collab:"

#: doc/delta 事件载荷上限（与 workbench doc 的 256KB 持久化闸解耦 —— 总线
#: 只运 delta/小 doc；>64KB 的 doc 变更发 truncated 提示，接收方 refetch）。
MAX_DOC_EVENT_BYTES = 64 * 1024
#: 其余事件上限（服务端生成，天然小；防将来误用放大）。
MAX_EVENT_BYTES = 2 * 1024

MUTATION_KINDS = frozenset({"doc", "delta", "presentation", "op"})
VALID_KINDS = MUTATION_KINDS | {"presence", "lock", "artifact"}

#: 断连重连退避（秒）。与 cache_broadcast 同量级。
_LISTENER_RECONNECT_S = 5.0
#: Redis 客户端故障后的重探退避（秒）。
_CLIENT_RETRY_S = 30.0

# 类型回调：async callable(envelope_dict)。慢消费者由 ws_collab 的每连接
# 有界外发队列兜底（队列满 → 断开该连接），总线本身不阻塞。
LocalListener = Callable[[Dict[str, Any]], Any]


def channel_name(session_id: str) -> str:
    return f"{CHANNEL_PREFIX}{session_id}"


def build_envelope(
    kind: str,
    session_id: str,
    data: Dict[str, Any],
    *,
    seq: Optional[int] = None,
) -> Dict[str, Any]:
    """构造事件信封；seq 缺省 = 服务端接收时刻（瞬态事件语义）。"""
    if kind not in VALID_KINDS:
        raise ValueError(f"unknown collab event kind: {kind!r}")
    return {
        "v": 1,
        "kind": kind,
        "sid": str(session_id),
        "seq": int(seq) if seq is not None else time.time_ns(),
        "ts": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }


def bound_payload(envelope: Dict[str, Any]) -> Dict[str, Any]:
    """载荷预算闸：超限 → 降级为 truncated 提示（接收方 refetch 权威）。

    doc/delta 事件保留 revision/seq（对账锚点）与 actor；其余事件超限直接
    丢弃 data（瞬态事件本来就可丢）。绝不放大总线载荷。
    """
    kind = envelope.get("kind")
    limit = MAX_DOC_EVENT_BYTES if kind in ("doc", "delta") else MAX_EVENT_BYTES
    try:
        encoded = json.dumps(envelope, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return {**envelope, "data": {}, "truncated": True}
    if len(encoded.encode("utf-8")) <= limit:
        return envelope
    if kind in ("doc", "delta"):
        keep = {
            k: envelope["data"].get(k)
            for k in ("revision", "actor", "origin")
            if k in envelope["data"]
        }
        keep["truncated"] = True
        return {**envelope, "data": keep, "truncated": True}
    return {**envelope, "data": {}, "truncated": True}


class CollabBus:
    """每进程一个实例：publish（Redis 或进程内）+ psubscribe 扇出。"""

    def __init__(self) -> None:
        self._listeners: Dict[str, set[LocalListener]] = {}
        self._lock = asyncio.Lock()
        self._listener_task: Optional[asyncio.Task] = None
        self._client: Optional[Any] = None
        self._client_failed_at: Optional[float] = -_CLIENT_RETRY_S  # 首探立即放行
        self._stopping = False

    # ── 本地扇出注册 ────────────────────────────────────────────────────
    async def add_local_listener(self, session_id: str, fn: LocalListener) -> "Callable[[], None]":
        async with self._lock:
            bucket = self._listeners.get(session_id)
            if bucket is None:
                bucket = set()
                self._listeners[session_id] = bucket
            bucket.add(fn)
            self._maybe_gc(session_id)

        def _remove() -> None:
            # 同步移除（WS finally 路径可能已在事件循环外）；绝不抛出。
            bucket = self._listeners.get(session_id)
            if bucket is not None:
                bucket.discard(fn)
                if not bucket:
                    self._listeners.pop(session_id, None)

        return _remove

    def _maybe_gc(self, session_id: str) -> None:
        bucket = self._listeners.get(session_id)
        if bucket is not None and not bucket:
            self._listeners.pop(session_id, None)

    def _dispatch_local(self, envelope: Dict[str, Any]) -> int:
        """扇出给本进程该会话的全部 listener；回调异常隔离（绝不外溢）。"""
        bucket = self._listeners.get(envelope.get("sid", ""))
        if not bucket:
            return 0
        delivered = 0
        for fn in tuple(bucket):
            try:
                result = fn(envelope)
                if asyncio.iscoroutine(result):
                    # listener 约定为同步快速入队；防御性兜底：协程则后台化
                    # 并记日志（生产 listener 不应返回协程）。
                    asyncio.get_running_loop().create_task(result)
            except Exception:  # noqa: BLE001 — 单个消费者故障不放大
                logger.debug("[collab-bus] local listener raised", exc_info=True)
            else:
                delivered += 1
        return delivered

    # ── 发布 ────────────────────────────────────────────────────────────
    async def publish(
        self,
        session_id: str,
        kind: str,
        data: Dict[str, Any],
        *,
        seq: Optional[int] = None,
    ) -> bool:
        """发布事件（预算闸内）；返回是否成功发布到任一路径。

        单扇出路径纪律：Redis 可用 → 仅 PUBLISH（本进程经 listener 收到后
        扇出，含发布者自己的连接）；不可用 → 仅本地直发。绝不双发（op
        journal 会双计，评审 m3）。
        """
        envelope = bound_payload(build_envelope(kind, session_id, data, seq=seq))
        client = self._redis_client()
        if client is not None:
            try:
                await client.publish(
                    channel_name(session_id),
                    json.dumps(envelope, ensure_ascii=False, default=str),
                )
                return True
            except Exception as exc:  # noqa: BLE001 — 降级到本地直发
                logger.debug("[collab-bus] publish failed, degrading local: %s", exc)
                self._drop_client()
        # 降级本地直发：无本地 listener 也是成功的发布（事件本就可丢，
        # 正确性由 revision 对账兜底 —— 返回值只反映「走了哪条路径」）。
        self._dispatch_local(envelope)
        return True

    # ── Redis 客户端（懒探 + 退避重探，模式同 cache_broadcast）─────────
    def _redis_client(self) -> Optional[Any]:
        if self._stopping:
            return None
        if self._client is not None:
            return self._client
        now = time.monotonic()
        if (
            self._client_failed_at is not None
            and now - self._client_failed_at < _CLIENT_RETRY_S
        ):
            return None
        try:
            from app.core.config import settings

            if not getattr(settings, "USE_REDIS", False):
                self._client_failed_at = now
                return None
            import redis.asyncio as aioredis

            self._client = aioredis.from_url(
                getattr(settings, "REDIS_URL", "redis://localhost:6379/0"),
                socket_connect_timeout=1,
                socket_timeout=2,
                decode_responses=True,
            )
            self._client_failed_at = None
            return self._client
        except Exception:  # noqa: BLE001 — Redis 是增值路径
            self._client_failed_at = now
            return None

    def _drop_client(self) -> None:
        self._client = None
        self._client_failed_at = time.monotonic()

    # ── listener task（lifespan 启停）──────────────────────────────────
    def start(self) -> bool:
        """启动 psubscribe listener（幂等）；无 Redis 返回 False（降级）。"""
        if self._listener_task is not None:
            return True
        client = self._redis_client()
        if client is None:
            return False
        self._stopping = False
        self._listener_task = asyncio.get_running_loop().create_task(
            self._listen_loop(), name="collab-bus-listener"
        )
        return True

    async def stop(self) -> None:
        self._stopping = True
        task = self._listener_task
        self._listener_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:  # noqa: BLE001
                pass
            self._client = None

    async def _listen_loop(self) -> None:
        """psubscribe ``webgis:collab:*`` 单连接纪元；异常 → 退避重订。"""
        while not self._stopping:
            client = self._redis_client()
            if client is None:
                await asyncio.sleep(_LISTENER_RECONNECT_S)
                continue
            try:
                pubsub = client.pubsub(ignore_subscribe_messages=True)
                await pubsub.psubscribe(f"{CHANNEL_PREFIX}*")
                try:
                    async for message in pubsub.listen():
                        if self._stopping:
                            return
                        if message and message.get("type") == "pmessage":
                            self._apply_raw(str(message.get("data") or ""))
                finally:
                    try:
                        await pubsub.aclose()
                    except Exception:  # noqa: BLE001
                        pass
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001 — 断连重试
                logger.debug("[collab-bus] listener reconnect: %s", exc)
                self._drop_client()
                if not self._stopping:
                    await asyncio.sleep(_LISTENER_RECONNECT_S)

    def _apply_raw(self, raw: str) -> None:
        try:
            envelope = json.loads(raw)
        except (ValueError, TypeError):
            return
        if not isinstance(envelope, dict) or envelope.get("v") != 1:
            return
        if envelope.get("kind") not in VALID_KINDS:
            return
        self._dispatch_local(envelope)

    # ── 观测（测试/诊断）────────────────────────────────────────────────
    def local_listener_count(self, session_id: str) -> int:
        return len(self._listeners.get(session_id, ()))

    def degraded(self) -> bool:
        """当前是否处于进程内降级模式（无 Redis 客户端可用）。诊断值。"""
        return self._redis_client() is None


#: 进程级单例（ws_collab 路由与 mutation 挂钩共用）。
bus = CollabBus()
