"""工具结果缓存层 — Redis-backed, opt-in per tool.

入口：make_cache_key(name, args)、cached_tool(...) 装饰器（后续 Task 加入）。
键命名空间 tool_cache:v1:<sha256[:16]>，全量失效一条 SCAN | DEL 即可。
"""
import hashlib
import json
from typing import Optional, Any
import logging
import time

import redis as _redis

from app.core.config import settings

logger = logging.getLogger(__name__)


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
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning(f"[tool_cache] Corrupt cache value at {key}: {e}")
        return None


def set_cached(key: str, value: Any, ttl: int) -> None:
    """Redis 写。失败时仅 warning，绝不抛 — 不能因缓存写失败导致用户请求失败。"""
    try:
        payload = json.dumps(value, default=str).encode("utf-8")
        _get_redis_client().setex(key, ttl, payload)
    except (_redis.RedisError, TypeError, ValueError) as e:
        _warn_throttled(f"[tool_cache] Redis SET failed, dropping cache write: {type(e).__name__}: {e}")
