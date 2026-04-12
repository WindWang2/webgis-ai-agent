"""
Redis 缓存服务支持内存降级
"""
import json
from typing import Any, Optional
from cachetools import TTLCache

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)

_DEFAULT_TTL = 300  # 5 分钟


class CacheService:
    """
    通用 Redis 缓存服务。

    优先使用 Redis；若 Redis 不可用或未配置自动降级为进程内 TTLCache，
    并在 Redis 恢复后（下次操作触发探测）自动切换回来。
    """

    def __init__(self, maxsize: int = 2000, default_ttl: int = _DEFAULT_TTL) -> None:
        self._default_ttl = default_ttl
        self._memory: TTLCache = TTLCache(maxsize=maxsize, ttl=default_ttl)
        self._redis = None
        self._redis_available = False
        self._connect_redis()

    def _connect_redis(self) -> None:
        """尝试建立 Redis 连接；失败则静默降级。"""
        if not settings.REDIS_URL:
            logger.info("REDIS_URL 未配置使用内存缓存")
            return
        try:
            import redis as _redis
            client = _redis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
            client.ping()
            self._redis = client
            self._redis_available = True
            logger.info("Redis 缓存已连接：%s", settings.REDIS_URL)
        except Exception as exc:
            logger.warning("Redis 连接失败降级为内存缓存：%s", exc)

    def _check_redis(self) -> bool:
        """惰性探测：Redis 断线后尝试重连一次。"""
        if self._redis_available:
            return True
        self._connect_redis()
        return self._redis_available

    def get(self, key: str) -> Optional[Any]:
        if self._check_redis():
            try:
                raw = self._redis.get(key)
                if raw is not None:
                    return json.loads(raw)
            except Exception as exc:
                logger.warning("Redis get 失败降级内存：%s", exc)
                self._redis_available = False
        return self._memory.get(key)

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        effective_ttl = ttl if ttl is not None else self._default_ttl
        self._memory[key] = value
        if self._check_redis():
            try:
                self._redis.setex(key, effective_ttl, json.dumps(value, default=str))
            except Exception as exc:
                logger.warning("Redis set 失败：%s", exc)
                self._redis_available = False

    def delete(self, key: str) -> None:
        self._memory.pop(key, None)
        if self._check_redis():
            try:
                self._redis.delete(key)
            except Exception as exc:
                logger.warning("Redis delete 失败：%s", exc)
                self._redis_available = False

    @property
    def is_redis_available(self) -> bool:
        return self._redis_available


# 单例，供全局使用
cache_service = CacheService()