"""S99-005: query failures must not disable a shared provider."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.provider_health import (
    ProviderHealthTracker, check_amap_status, check_baidu_status,
    check_tianditu_status, check_nominatim_status, tracked_provider_get,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,checker,payload,status", [
    ("tianditu", check_tianditu_status, {"status": "101", "msg": "找不到结果"}, 200),
    ("baidu", check_baidu_status, {"status": 2, "message": "Parameter Error"}, 200),
    ("amap", check_amap_status, {"status": "0", "infocode": "20000"}, 200),
    ("nominatim", check_nominatim_status, {"error": "Unable to geocode"}, 200),
    ("amap", check_amap_status, {}, 400),
    ("amap", check_amap_status, {}, 422),
])
async def test_bad_query_still_errors_without_opening_circuit(monkeypatch, provider, checker, payload, status):
    tracker = ProviderHealthTracker(calls_per_minute=20)
    response = AsyncMock(status=status)
    response.json.return_value = payload
    context = MagicMock()
    context.__aenter__.return_value = response
    session = MagicMock()
    session.get.return_value = context
    monkeypatch.setattr("app.core.network.get_shared_client", AsyncMock(return_value=session))
    await tracker.record_error(provider)
    for _ in range(6):
        result = await tracked_provider_get(provider, "https://example.test", {}, business_checker=checker, tracker=tracker)
        assert "error" in result
    state = (await tracker.snapshot())[provider]
    assert state["consecutive_errors"] == 0
    assert state["circuit_open"] is False
    assert session.get.call_count == 6


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,checker,payload,status", [
    ("amap", check_amap_status, {"status": "0", "infocode": "10001"}, 200),
    ("amap", check_amap_status, {"status": "0", "infocode": "10003"}, 200),
    ("baidu", check_baidu_status, {"status": 302}, 200),
    ("tianditu", check_tianditu_status, {"error": "Token expired"}, 200),
    ("tianditu", check_tianditu_status, {"status": "9999", "msg": "找不到结果"}, 200),
    ("amap", check_amap_status, [], 200),
    ("amap", check_amap_status, {}, 401),
    ("amap", check_amap_status, {}, 403),
    ("amap", check_amap_status, {}, 429),
    ("amap", check_amap_status, {}, 503),
])
async def test_provider_faults_still_trip_circuit(monkeypatch, provider, checker, payload, status):
    tracker = ProviderHealthTracker(calls_per_minute=20)
    response = AsyncMock(status=status)
    response.json.return_value = payload
    context = MagicMock()
    context.__aenter__.return_value = response
    session = MagicMock()
    session.get.return_value = context
    monkeypatch.setattr("app.core.network.get_shared_client", AsyncMock(return_value=session))
    for _ in range(6):
        result = await tracked_provider_get(provider, "https://example.test", {}, business_checker=checker, tracker=tracker)
        assert "error" in result
    state = (await tracker.snapshot())[provider]
    assert state["circuit_open"] is True
    assert state["consecutive_errors"] == 5
    assert session.get.call_count == 5


@pytest.mark.asyncio
async def test_network_failure_still_trips(monkeypatch):
    tracker = ProviderHealthTracker()
    monkeypatch.setattr("app.core.network.get_shared_client", AsyncMock(side_effect=TimeoutError()))
    for _ in range(5):
        assert "error" in await tracked_provider_get("amap", "https://example.test", {}, tracker=tracker)
    assert (await tracker.snapshot())["amap"]["circuit_open"] is True
