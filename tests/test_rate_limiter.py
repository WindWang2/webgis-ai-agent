"""Rate limiter tests"""
import pytest
import time

from app.core.rate_limiter import MemoryRateLimiter


class TestMemoryRateLimiter:
    @pytest.fixture
    def limiter(self):
        return MemoryRateLimiter()

    @pytest.mark.asyncio
    async def test_allows_requests_under_limit(self, limiter):
        for _ in range(5):
            assert await limiter.is_allowed("ip:1", max_requests=10, window_seconds=60)

    @pytest.mark.asyncio
    async def test_blocks_requests_over_limit(self, limiter):
        for _ in range(10):
            assert await limiter.is_allowed("ip:2", max_requests=10, window_seconds=60)
        assert not await limiter.is_allowed("ip:2", max_requests=10, window_seconds=60)

    @pytest.mark.asyncio
    async def test_window_resets_after_timeout(self, limiter):
        for _ in range(5):
            assert await limiter.is_allowed("ip:3", max_requests=5, window_seconds=0)
        time.sleep(0.01)
        assert await limiter.is_allowed("ip:3", max_requests=5, window_seconds=0)

    @pytest.mark.asyncio
    async def test_isolated_keys(self, limiter):
        for _ in range(10):
            assert await limiter.is_allowed("ip:a", max_requests=10, window_seconds=60)
        assert await limiter.is_allowed("ip:b", max_requests=10, window_seconds=60)
