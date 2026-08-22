"""Unit tests for tracked_provider_get & ProviderHealthTracker integration (ADR-0027)."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.services.provider_health import (
    PROVIDER_NAMES,
    ProviderHealthTracker,
    tracked_provider_get,
    check_amap_status,
    check_baidu_status,
    check_tianditu_status,
    check_overpass_status,
    check_nominatim_status,
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
    # #771: the checker now requires status == "1000" — a payload without the
    # success status (previously accepted, e.g. {"returncode": "100"}) and
    # business failures like {"status": "101"} are rejected, so the geocoder
    # can no longer default a missing location to (0, 0) and count it as ok.
    assert check_tianditu_status({"status": "1000", "msg": "ok"})[0] is True
    assert check_tianditu_status({"status": "101", "msg": "找不到结果"})[0] is False
    assert check_tianditu_status({"returncode": "100", "data": []})[0] is False
    assert check_tianditu_status({"error": "Token expired"})[0] is False

    # Overpass success vs failure
    assert check_overpass_status({"elements": []})[0] is True
    assert check_overpass_status({"remark": "query error"})[0] is False
    assert check_overpass_status("not-a-dict")[0] is False

    # Nominatim: search 返回 list、reverse 返回 dict、错误 dict 拒绝
    assert check_nominatim_status([])[0] is True
    assert check_nominatim_status({"display_name": "X", "address": {}})[0] is True
    assert check_nominatim_status({"error": "Unable to geocode"})[0] is False
    assert check_nominatim_status("nope")[0] is False


def test_provider_names_include_osm():
    """OSM 家族已纳入 PROVIDER_NAMES（熔断日志友好显示）。"""
    assert {"overpass", "nominatim"} <= PROVIDER_NAMES


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


# ── Issue #433: bounded sliding window under sustained failure ───────────────


@pytest.mark.asyncio
async def test_call_timestamps_bounded_under_sustained_failure():
    """持续失败（熔断打开后仍不停打点）不得让 call_timestamps 无界增长。

    旧实现只在 record_success 里剪枝：失败/被拒路径只 append 不清理，
    长时间故障下窗口按原始尝试速率增长（86,400 次/天 ≈ 3.5MB/provider/天）。
    """
    tracker = ProviderHealthTracker(calls_per_minute=60, error_threshold=5, recovery_seconds=300)
    for _ in range(3000):
        await tracker.record_attempt("amap")
        await tracker.record_error("amap", Exception("down"))
    window = tracker._state["amap"].call_timestamps
    assert len(window) <= 60, f"call_timestamps unbounded: {len(window)} entries"


@pytest.mark.asyncio
async def test_call_timestamps_bounded_under_rate_limit_saturation():
    """纯限流饱和（无熔断）：被拒尝试不写入窗口，长度恒 ≤ calls_per_minute。"""
    tracker = ProviderHealthTracker(calls_per_minute=60)
    denials = 0
    for _ in range(3000):
        if not await tracker.record_attempt("amap"):
            denials += 1
    assert denials > 0, "limiter never tripped — test is vacuous"
    window = tracker._state["amap"].call_timestamps
    assert len(window) <= 60, f"call_timestamps unbounded: {len(window)} entries"


@pytest.mark.asyncio
async def test_rate_window_allows_limit_then_denies_then_expires(monkeypatch):
    """限流边界与过窗放行；被拒尝试不得延长锁定窗（#433）。"""
    tracker = ProviderHealthTracker(calls_per_minute=3)
    fake_now = 1000.0
    monkeypatch.setattr("app.services.provider_health.time.time", lambda: fake_now)

    for _ in range(3):
        assert await tracker.record_attempt("amap") is True
    assert await tracker.record_attempt("amap") is False
    # 持续被拒的打点不应把窗口无限向后推
    for _ in range(100):
        assert await tracker.record_attempt("amap") is False
    # 60s 窗口过期后重新放行
    fake_now += 61
    assert await tracker.record_attempt("amap") is True


@pytest.mark.asyncio
async def test_can_call_prunes_expired_timestamps(monkeypatch):
    """can_call 剪枝过期时间戳（不再全量 O(n) 扫描复制）。"""
    tracker = ProviderHealthTracker(calls_per_minute=5)
    fake_now = 1000.0
    monkeypatch.setattr("app.services.provider_health.time.time", lambda: fake_now)
    for _ in range(4):
        await tracker.record_attempt("baidu")
    window = tracker._state["baidu"].call_timestamps
    assert len(window) == 4
    fake_now += 120  # all expired
    assert await tracker.can_call("baidu") is True
    assert len(window) == 0, "expired timestamps must be pruned, not just filtered"


@pytest.mark.asyncio
async def test_tracked_provider_get_post(monkeypatch):
    """POST 分支：Overpass 风格查询体经 session.post 提交（issue #310）。"""
    tracker = ProviderHealthTracker(calls_per_minute=10)

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"elements": []})

    mock_post = MagicMock()
    mock_post.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_post.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_post)

    async def fake_get_shared_client():
        return mock_session

    monkeypatch.setattr("app.core.network.get_shared_client", fake_get_shared_client)

    res = await tracked_provider_get(
        "overpass",
        "https://overpass-api.de/api/interpreter",
        {},
        method="POST",
        data={"data": "[out:json];node(1);out;"},
        timeout=60,
        business_checker=check_overpass_status,
        tracker=tracker,
    )

    assert "elements" in res
    # POST 走 session.post 而非 session.get，且携带 data 与超时。
    mock_session.post.assert_called_once()
    call_kwargs = mock_session.post.call_args.kwargs
    assert call_kwargs.get("data") == {"data": "[out:json];node(1);out;"}
    assert call_kwargs.get("timeout") == 60
    mock_session.get.assert_not_called()
