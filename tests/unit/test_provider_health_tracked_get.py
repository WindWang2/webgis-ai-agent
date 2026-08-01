"""Unit tests for tracked_provider_get & ProviderHealthTracker integration (ADR-0027)."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.services.provider_health import (
    ProviderHealthTracker,
    tracked_provider_get,
    check_amap_status,
    check_baidu_status,
    check_tianditu_status,
)


def test_business_status_checkers():
    """Test standard business status checkers for Amap, Baidu, and Tianditu."""
    # Amap success vs failure
    assert check_amap_status({"status": "1", "info": "OK"})[0] is True
    assert check_amap_status({"status": "0", "info": "INVALID_KEY"})[0] is False

    # Baidu success vs failure
    assert check_baidu_status({"status": 0, "result": {}})[0] is True
    assert check_baidu_status({"status": 2, "message": "Parameter Error"})[0] is False

    # Tianditu success vs failure
    assert check_tianditu_status({"returncode": "100", "data": []})[0] is True
    assert check_tianditu_status({"error": "Token expired"})[0] is False


@pytest.mark.asyncio
async def test_tracked_provider_get_success(monkeypatch):
    """Test successful request through tracked_provider_get."""
    tracker = ProviderHealthTracker(calls_per_minute=10)

    # Mock get_shared_client
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"status": "1", "pois": [{"name": "Park"}]})

    mock_get = MagicMock()
    mock_get.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_get.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_get)

    async def fake_get_shared_client():
        return mock_session

    monkeypatch.setattr("app.core.network.get_shared_client", fake_get_shared_client)

    res = await tracked_provider_get(
        "amap",
        "https://restapi.amap.com/v3/place/around",
        {"keywords": "park"},
        business_checker=check_amap_status,
        tracker=tracker,
    )

    assert "pois" in res
    snap = await tracker.snapshot()
    assert snap["amap"]["consecutive_errors"] == 0
    assert snap["amap"]["circuit_open"] is False


@pytest.mark.asyncio
async def test_tracked_provider_get_circuit_breaker(monkeypatch):
    """Test that open circuit breaker blocks attempt immediately."""
    tracker = ProviderHealthTracker(error_threshold=2, recovery_seconds=300)

    # Record 2 errors to open circuit breaker
    await tracker.record_error("amap", Exception("Error 1"))
    await tracker.record_error("amap", Exception("Error 2"))

    snap = await tracker.snapshot()
    assert snap["amap"]["circuit_open"] is True

    # Call tracked_provider_get should be blocked without network call
    res = await tracked_provider_get(
        "amap",
        "https://restapi.amap.com/v3/place/around",
        {},
        tracker=tracker,
    )

    assert "error" in res
    assert "暂时不可用" in res["error"]
