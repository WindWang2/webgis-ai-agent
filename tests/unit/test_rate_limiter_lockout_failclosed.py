"""security F-08：Redis 故障期 count/record 降级进程内台账（登录锁出不失效）。"""
import pytest


class _BrokenPipe:
    def __getattr__(self, _name):
        return lambda *a, **k: None

    async def execute(self):
        raise ConnectionError("redis down")


class _BrokenClient:
    def pipeline(self):
        return _BrokenPipe()


@pytest.mark.asyncio
async def test_login_lockout_survives_redis_outage():
    from app.core.rate_limiter import RedisRateLimiter

    rl = RedisRateLimiter(_BrokenClient())

    async def _same_client():
        return rl._redis

    rl._ensure_client = _same_client  # 跳过 loop 重建
    key = "auth_login_fail:203.0.113.9"
    for _ in range(5):
        await rl.record(key, 300)
    assert await rl.count(key, 300) == 5, "Redis 故障期失败台账不得清零（fail-open）"
