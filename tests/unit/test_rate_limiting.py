"""Unit tests for RateLimiter sliding-window enforcement and eviction (Issue 02)."""
import pytest
import asyncio
from unittest.mock import patch
from app.core.rate_limiter import MemoryRateLimiter
from app.services.ws_service import handle_viewport_change


@pytest.mark.asyncio
async def test_memory_rate_limiter_sliding_window():
    limiter = MemoryRateLimiter()
    key = "test_user_ip"

    # Allow max 3 requests per 2 seconds
    assert await limiter.is_allowed(key, max_requests=3, window_seconds=2)
    assert await limiter.is_allowed(key, max_requests=3, window_seconds=2)
    assert await limiter.is_allowed(key, max_requests=3, window_seconds=2)

    # 4th request should be blocked
    assert not await limiter.is_allowed(key, max_requests=3, window_seconds=2)

    # Wait 2.1 seconds for window to slide
    await asyncio.sleep(2.1)
    assert await limiter.is_allowed(key, max_requests=3, window_seconds=2)


@pytest.mark.asyncio
async def test_memory_rate_limiter_key_isolation():
    limiter = MemoryRateLimiter()

    # IP 1 uses up max requests
    assert await limiter.is_allowed("ip_1", max_requests=1, window_seconds=10)
    assert not await limiter.is_allowed("ip_1", max_requests=1, window_seconds=10)

    # IP 2 should still be allowed
    assert await limiter.is_allowed("ip_2", max_requests=1, window_seconds=10)


@pytest.mark.asyncio
async def test_memory_rate_limiter_eviction():
    limiter = MemoryRateLimiter()
    await limiter.is_allowed("temp_key", max_requests=1, window_seconds=1)

    # Clear deque manually to simulate window expiry
    limiter._requests["temp_key"].clear()
    limiter._last_evict = 0.0  # Force eviction trigger

    limiter._maybe_evict()
    assert "temp_key" not in limiter._requests


@pytest.mark.asyncio
async def test_ws_viewport_change_rate_limiting():
    session_id = "sess_rate_limit_test"
    data = {"center": [116.4, 39.9], "zoom": 10}

    with patch("app.services.viewport_naming.schedule_populate") as mock_populate, \
         patch("app.services.session_data.session_data_manager.set_map_state") as mock_set_state:
        mock_set_state.return_value = None

        # 1st call should trigger schedule_populate
        await handle_viewport_change(session_id, data)
        assert mock_populate.call_count == 1

        # 2nd immediate call (within 5s window) should be throttled by rate limiter
        await handle_viewport_change(session_id, data)
        assert mock_populate.call_count == 1
