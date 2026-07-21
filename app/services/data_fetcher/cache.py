import hashlib
import json
from typing import Any, Optional
import logging
import redis
from cachetools import TTLCache
from app.core.config import settings

logger = logging.getLogger(__name__)

class CacheManager:
    _instance = None
    _memory_cache: TTLCache
    _redis_client: Optional[redis.Redis] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            # Initialize memory cache: max 1000 items, TTL 5 minutes
            cls._instance._memory_cache = TTLCache(maxsize=1000, ttl=300)
            # Initialize Redis client if configured
            if settings.REDIS_URL:
                try:
                    cls._instance._redis_client = redis.from_url(settings.REDIS_URL)
                    cls._instance._redis_client.ping()
                except Exception as e:
                    logger.warning("Redis connection failed, falling back to memory cache only: %s", e)
        return cls._instance

    def _generate_cache_key(self, query: Any) -> str:
        """Generate a unique cache key from query object.

        md5 在此用于缓存键生成（非安全用途）-- usedforsecurity=False 明确声明
        这一意图，同时让 bandit B324 不再告警。
        """
        query_json = json.dumps(query.dict(), sort_keys=True)
        return f"data_fetcher:{hashlib.md5(query_json.encode(), usedforsecurity=False).hexdigest()}"

    def get(self, query: Any) -> Optional[Any]:
        """Get data from cache, check memory first then Redis"""
        key = self._generate_cache_key(query)
        # Check memory cache first
        if key in self._memory_cache:
            return self._memory_cache[key]
        # Check Redis if available
        if self._redis_client:
            cached_data = self._redis_client.get(key)
            if cached_data:
                try:
                    data = json.loads(cached_data)
                    # Store in memory cache for faster access next time
                    self._memory_cache[key] = data
                    return data
                except (json.JSONDecodeError, TypeError):
                    pass
        return None

    def set(self, query: Any, data: Any, ttl: Optional[int] = None) -> None:
        """Store data in both memory and Redis cache"""
        key = self._generate_cache_key(query)
        ttl = ttl or settings.DEFAULT_CACHE_TTL
        # Store in memory cache
        self._memory_cache[key] = data
        # Store in Redis if available
        if self._redis_client:
            try:
                self._redis_client.setex(
                    key,
                    ttl,
                    json.dumps(data, default=str)
                )
            except Exception as e:
                logger.warning("Failed to write to Redis cache: %s", e)

    def invalidate(self, query: Any) -> None:
        """Invalidate cache for a specific query"""
        key = self._generate_cache_key(query)
        if key in self._memory_cache:
            del self._memory_cache[key]
        if self._redis_client:
            self._redis_client.delete(key)
