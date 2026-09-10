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


@pytest.mark.asyncio
async def test_get_rate_limiter_synchronized_burst(monkeypatch):
    """CORE-08: Verify concurrent get_rate_limiter calls serialize and create client once."""
    import asyncio
    from unittest.mock import AsyncMock, patch
    import app.core.rate_limiter as rl_mod

    monkeypatch.setattr(rl_mod, "_rate_limiter", None)
    monkeypatch.setattr(rl_mod, "_rate_limiter_fallback_at", None)
    monkeypatch.setattr(rl_mod, "_limiter_lock", None)

    fake_client = AsyncMock()
    fake_client.ping = AsyncMock(return_value=True)

    create_count = 0

    def fake_from_url(*args, **kwargs):
        nonlocal create_count
        create_count += 1
        return fake_client

    with patch("redis.asyncio.from_url", side_effect=fake_from_url):
        results = await asyncio.gather(*[rl_mod.get_rate_limiter() for _ in range(30)])

    assert create_count == 1, f"Expected 1 client creation, got {create_count}"
    assert len(results) == 30
    first = results[0]
    assert all(r is first for r in results)
