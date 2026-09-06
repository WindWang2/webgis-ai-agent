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

有界性：频道单条；消息 ≤256 字节 JSON（四个固定字段）；监听线程
daemon，异常永不外溢。
"""
from __future__ import annotations

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
_client_failed = False


def _client_cached() -> Optional[Any]:
    """复用 Redis 客户端（评审 MINOR：每事件一次 TCP 握手 → 惰性单例）。"""
    global _cached_client, _client_failed
    with _client_lock:
        if _cached_client is not None:
            return _cached_client
        if _client_failed:
            return None
        client = _redis_client()
        if client is None:
            _client_failed = True
            return None
        _cached_client = client
        return client


def broadcast_ref_invalidation(
    session_id: str, ref_id: str, reason: str = "REPLACE"
) -> bool:
    """发布 ref 失效事件（有界、无载荷；失败静默 = 依赖权威校验兜底）。"""
    client = _client_cached()
    if client is None:
        return False
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
    except Exception as exc:  # noqa: BLE001
        global _cached_client, _client_failed
        with _client_lock:
            _cached_client = None
            _client_failed = True  # 下次发布重新探测（Redis 恢复后自愈）
        logger.debug("[cache-broadcast] publish failed (harmless): %s", exc)
        return False


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

        def _loop() -> None:
            while True:
                try:
                    pubsub = client.pubsub(ignore_subscribe_messages=True)
                    pubsub.subscribe(CHANNEL)
                    for message in pubsub.listen():
                        if message and message.get("type") == "message":
                            _apply_event(str(message.get("data") or ""))
                except Exception as exc:  # noqa: BLE001 - 断连重试
                    logger.debug("[cache-broadcast] listener reconnect: %s", exc)
                    threading.Event().wait(5.0)

        thread = threading.Thread(target=_loop, daemon=True,
                                  name="cache-invalidation-listener")
        thread.start()
        _started = True
        return True
