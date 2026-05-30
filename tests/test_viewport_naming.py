"""Round 3: 视口反查地名 — 缓存语义与 schedule_populate 行为"""
import asyncio

import pytest

from app.services import viewport_naming
from app.services.viewport_naming import (
    _format_address,
    _quantize,
    clear_cache,
    lookup,
    schedule_populate,
    schedule_populate_from_map_state,
    wait_all_tasks,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    clear_cache()
    yield
    clear_cache()


def test_quantize_collapses_nearby_coords():
    a = _quantize(116.405, 39.905)
    b = _quantize(116.406, 39.904)
    assert a == b


def test_quantize_distinguishes_far_coords():
    a = _quantize(116.4, 39.9)
    b = _quantize(121.5, 31.2)
    assert a != b


def test_lookup_miss_returns_none():
    assert lookup(116.4, 39.9) is None


def test_lookup_hits_after_manual_put():
    # 直接戳缓存验证 LRU 命中路径
    key = _quantize(116.4, 39.9)
    viewport_naming._put(key, "北京市 朝阳区")
    assert lookup(116.4, 39.9) == "北京市 朝阳区"
    # 5km 内的小幅平移仍命中
    assert lookup(116.42, 39.91) == "北京市 朝阳区"


def test_format_address_picks_3_levels():
    addr = {
        "country": "中国",
        "state": "北京市",
        "city": "北京",
        "suburb": "朝阳区",
        "road": "建国路",
    }
    out = _format_address(addr)
    # 应该按优先级取 country/state/city 前 3 个
    assert "中国" in out
    assert "北京市" in out


def test_format_address_skips_empty():
    out = _format_address({"country": None, "state": "", "city": "上海", "county": "浦东新区"})
    assert "上海" in out and "浦东新区" in out


def test_schedule_populate_rejects_out_of_range():
    # 不应触发 task，也不应抛
    schedule_populate(999, -200)
    assert lookup(999, -200) is None


@pytest.mark.asyncio
async def test_schedule_populate_writes_cache(monkeypatch):
    async def fake_fetch(lng, lat):
        return "测试区域"
    monkeypatch.setattr(viewport_naming, "_fetch_nominatim", fake_fetch)

    schedule_populate(116.4, 39.9)
    # 等待 fire-and-forget 任务跑完
    await wait_all_tasks()
    assert lookup(116.4, 39.9) == "测试区域"


@pytest.mark.asyncio
async def test_schedule_populate_dedupes_in_flight(monkeypatch):
    calls = []

    async def slow_fetch(lng, lat):
        calls.append((lng, lat))
        await asyncio.sleep(0.02)
        return "区域 A"

    monkeypatch.setattr(viewport_naming, "_fetch_nominatim", slow_fetch)

    schedule_populate(116.4, 39.9)
    schedule_populate(116.4, 39.9)
    schedule_populate(116.41, 39.91)  # 同量化键
    await wait_all_tasks()
    # 三次调用应只触发一次实际 fetch
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_schedule_populate_swallows_fetch_failure(monkeypatch):
    async def boom(lng, lat):
        return None  # 模拟上游失败

    monkeypatch.setattr(viewport_naming, "_fetch_nominatim", boom)

    schedule_populate(116.4, 39.9)
    await wait_all_tasks()
    # 失败不应缓存空字符串
    assert lookup(116.4, 39.9) is None


@pytest.mark.asyncio
async def test_env_summary_renders_cached_region(monkeypatch):
    from app.services.session_data import session_data_manager
    from app.services.chat.context_builder import build_map_state_summary

    async def fake_fetch(lng, lat):
        return "北京市 朝阳区"
    monkeypatch.setattr(viewport_naming, "_fetch_nominatim", fake_fetch)

    sid = "vp-summary"
    await session_data_manager.set_map_state(sid, "viewport",
                                       {"center": [116.4, 39.9], "zoom": 10})
    await session_data_manager.set_map_state(sid, "base_layer", "OSM 地图")

    # 第一轮 summary：缓存还没填充，应该没有"视口所在区域"行
    summary1 = await build_map_state_summary(sid)
    assert "视口所在区域" not in summary1
    # 但 schedule_populate 已经被触发，等待后台反查任务跑完
    await wait_all_tasks()

    summary2 = await build_map_state_summary(sid)
    assert "视口所在区域" in summary2
    assert "朝阳区" in summary2
    await session_data_manager.clear_session(sid)


def test_schedule_populate_from_map_state_extracts_center():
    # 没有运行中的 loop 时也不应抛
    schedule_populate_from_map_state({"viewport": {"center": [116.4, 39.9]}})
    schedule_populate_from_map_state({"viewport": {"center": "bad"}})
    schedule_populate_from_map_state(None)
    schedule_populate_from_map_state({})
    schedule_populate_from_map_state({"viewport": {}})


# ─── /review P1-3 regression: rate limit on Nominatim calls ─────────────


@pytest.mark.asyncio
async def test_populate_respects_rate_limit(monkeypatch):
    """When the token bucket is exhausted, _populate must skip the fetch.

    Otherwise an attacker walking lng/lat by 0.05° defeats the LRU cache
    and forces unbounded outbound Nominatim calls.
    """
    from app.services import viewport_naming as vn

    vn.clear_cache()
    call_count = {"n": 0}

    async def fake_fetch(lng, lat):
        call_count["n"] += 1
        return f"name-{call_count['n']}"

    monkeypatch.setattr(vn, "_fetch_nominatim", fake_fetch)
    # Tighten the limit so the test doesn't have to spam.
    monkeypatch.setattr(vn, "_RATE_LIMIT_MAX_PER_MINUTE", 3)

    # 5 unique cells; only first 3 should hit Nominatim.
    for i in range(5):
        await vn._populate(116.0 + i * 0.1, 39.0 + i * 0.1)

    assert call_count["n"] == 3, f"expected 3 fetches under rate limit, got {call_count['n']}"
    # And the budget should remain at 3 entries
    assert len(vn._rate_window) == 3


@pytest.mark.asyncio
async def test_rate_limit_window_purges_old_entries(monkeypatch):
    """Sliding window: timestamps older than 60s should be purged on next check."""
    from app.services import viewport_naming as vn
    import time as time_mod

    vn.clear_cache()
    monkeypatch.setattr(vn, "_RATE_LIMIT_MAX_PER_MINUTE", 2)

    # Manually inject two stale timestamps (older than the window)
    now = time_mod.monotonic()
    vn._rate_window.append(now - 120.0)
    vn._rate_window.append(now - 90.0)

    # Even though the deque has 2 entries, both are stale → next check allowed
    assert vn._rate_limit_check() is True
    # The stale ones should have been purged
    assert len(vn._rate_window) == 1, "stale timestamps should have been purged"


@pytest.mark.asyncio
async def test_shared_aiohttp_session_reused(monkeypatch):
    """/review P2-3: _get_aiohttp_session must return the SAME instance across calls
    (not a fresh ClientSession per Nominatim lookup)."""
    from app.services import viewport_naming as vn

    vn.clear_cache()
    # Reset shared session so we're testing a clean state.
    # Guard against a stale session from a previous event loop (closed loop raises RuntimeError).
    if vn._aiohttp_session is not None and not vn._aiohttp_session.closed:
        try:
            await vn._close_session()
        except RuntimeError:
            # Event loop from a previous test was closed; just reset the global.
            vn._aiohttp_session = None

    s1 = await vn._get_aiohttp_session()
    s2 = await vn._get_aiohttp_session()
    assert s1 is s2, "shared session should be the same instance"

    # Cleanup so we don't leak the connection between test files
    await vn._close_session()
