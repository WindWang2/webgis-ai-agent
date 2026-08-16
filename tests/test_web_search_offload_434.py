"""Regression tests for #434: web_search's DuckDuckGo fallback must never block
the event loop and must be bounded by a real timeout.

Before the fix, ``web_search`` (an ``async def`` dispatched with the ASYNC
policy, i.e. awaited directly on the loop) called the fully synchronous
``DDGS().text()`` inline — every DDG search (the default path whenever
``BAIDU_QIANFAN_TOKEN`` is unset) froze all concurrent SSE streams for the
full HTTP round-trip, with no outer wall-clock bound. ``search_and_extract_poi``
shared the same fallback.

These tests mirror tests/test_event_loop_offload_386.py: fake a slow DDG
search with ``time.sleep``, drive it through real ``registry.dispatch``, and
assert a concurrent asyncio ticker keeps firing while the search runs and
that the work happened on a worker thread (not the loop thread). A separate
test asserts the wall-clock budget turns a hung DDG call into an error dict.

Run cost: ~2s total (0.6-0.8s fake sleeps), no network.
"""
import asyncio
import threading
import time
from unittest.mock import patch

import pytest

_main_thread = threading.get_ident()


class _SlowDDGS:
    """Fake duckduckgo_search client that blocks for `delay` seconds.

    Records the thread it ran on so tests can prove the sync HTTP work was
    pushed off the event loop.
    """

    observed: dict = {}
    delay: float = 0.8
    hang: bool = False  # hang=True → sleep far beyond any budget (timeout test)

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def text(self, keywords, **kwargs):
        _SlowDDGS.observed["thread"] = threading.get_ident()
        _SlowDDGS.observed.setdefault("calls", []).append(keywords)
        if _SlowDDGS.hang:
            time.sleep(5.0)
        else:
            time.sleep(_SlowDDGS.delay)
        return [
            {"title": "Slow Result", "body": "snippet", "href": "https://example.com"}
        ]


def _fresh_registry():
    from app.tools.registry import ToolRegistry
    from app.tools.web_crawler import register_crawler_tools

    registry = ToolRegistry()
    register_crawler_tools(registry)
    return registry


async def _assert_loop_responsive_during(awaitable_factory):
    """Same technique as tests/test_event_loop_offload_386.py.

    With the DDG work on the loop, the fake's time.sleep blocks everything and
    the 0.05s ticker cannot complete; with the work offloaded the dispatch task
    is still running when the ticker fires.
    """
    _SlowDDGS.observed = {}
    task = asyncio.create_task(awaitable_factory())
    await asyncio.sleep(0.15)  # let dispatch reach the slow work
    assert not task.done(), "work finished before the test could observe it"

    ticks = []

    async def _tick():
        await asyncio.sleep(0.05)
        ticks.append(True)

    tick = asyncio.create_task(_tick())
    await asyncio.sleep(0.15)
    assert tick.done() and ticks, "event loop was blocked during the DDG search"
    assert not task.done(), "event loop was blocked during the DDG search"
    return await task


# ─── Site 1: web_search DDG fallback (default path, no Qianfan token) ────────


@pytest.mark.asyncio
async def test_web_search_ddg_off_loop():
    """DDG fallback inside web_search must run in a worker thread."""
    registry = _fresh_registry()
    with patch("app.tools.web_crawler.settings") as s, \
         patch("app.tools.web_crawler.DDGS", _SlowDDGS):
        s.BAIDU_QIANFAN_TOKEN = ""  # auto → ddg fallback
        result = await _assert_loop_responsive_during(
            lambda: registry.dispatch("web_search", {"query": "成都 星巴克"})
        )
    assert _SlowDDGS.observed.get("thread") != _main_thread, (
        "DDG search ran on the event loop thread"
    )
    assert result.get("provider") == "duckduckgo"
    assert result["count"] == 1
    assert "security_notice" in result


# ─── Site 2: search_and_extract_poi shares the same DDG fallback ─────────────


@pytest.mark.asyncio
async def test_search_and_extract_poi_ddg_off_loop():
    registry = _fresh_registry()
    with patch("app.tools.web_crawler.settings") as s, \
         patch("app.tools.web_crawler.DDGS", _SlowDDGS):
        s.BAIDU_QIANFAN_TOKEN = ""
        result = await _assert_loop_responsive_during(
            lambda: registry.dispatch("search_and_extract_poi", {"query": "成都 星巴克"})
        )
    assert _SlowDDGS.observed.get("thread") != _main_thread, (
        "DDG search ran on the event loop thread"
    )
    assert result.get("type") == "poi_web_search"
    assert result["count"] == 1


@pytest.mark.asyncio
async def test_explicit_ddg_provider_off_loop():
    """provider='ddg' must go through the same offloaded path."""
    registry = _fresh_registry()
    with patch("app.tools.web_crawler.settings") as s, \
         patch("app.tools.web_crawler.DDGS", _SlowDDGS):
        s.BAIDU_QIANFAN_TOKEN = "bce-v3/tok"  # token set, but provider forced to ddg
        result = await _assert_loop_responsive_during(
            lambda: registry.dispatch(
                "web_search", {"query": "q", "provider": "ddg"}
            )
        )
    assert _SlowDDGS.observed.get("thread") != _main_thread, (
        "DDG search ran on the event loop thread"
    )
    assert result.get("provider") == "duckduckgo"


# ─── Wall-clock budget: a hung DDG call must yield an error, not an
#     unbounded wait (the sync client's internal retries cannot be trusted) ───


@pytest.mark.asyncio
async def test_ddg_timeout_returns_error(monkeypatch):
    import app.tools.web_crawler as mod

    monkeypatch.setattr(mod, "_DDG_WALL_CLOCK_S", 0.3)
    _SlowDDGS.observed = {}
    _SlowDDGS.hang = True
    registry = _fresh_registry()
    try:
        with patch("app.tools.web_crawler.settings") as s, \
             patch("app.tools.web_crawler.DDGS", _SlowDDGS):
            s.BAIDU_QIANFAN_TOKEN = ""
            started = time.monotonic()
            result = await registry.dispatch("web_search", {"query": "q"})
        elapsed = time.monotonic() - started
        assert "error" in result, f"expected timeout error dict, got {result}"
        assert "超时" in result["error"]
        assert elapsed < 3.0, f"dispatch waited {elapsed:.1f}s despite the 0.3s budget"
        # The loop must stay free while the abandoned worker thread hangs.
        ticks = []

        async def _tick():
            await asyncio.sleep(0.05)
            ticks.append(True)

        tick = asyncio.create_task(_tick())
        await asyncio.sleep(0.2)
        assert tick.done() and ticks, "event loop blocked by abandoned DDG thread"
    finally:
        _SlowDDGS.hang = False
