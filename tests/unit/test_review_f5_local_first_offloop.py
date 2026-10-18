"""Review F5: async POI/OSM tools must run the synchronous local-first chain
(sqlite3 / pyogrio / GeoPandas) off the event loop."""
import asyncio
import time

import pytest

from app.tools.registry import ToolRegistry

_PAYLOAD = {"type": "FeatureCollection", "features": [], "count": 0,
            "source": "local_test"}


def _blocking(*_a, **_k):
    time.sleep(0.4)
    return dict(_PAYLOAD)


async def _max_loop_lag(coro) -> float:
    lags = []

    async def _ticker():
        while True:
            t0 = time.monotonic()
            await asyncio.sleep(0.01)
            lags.append(time.monotonic() - t0)

    t = asyncio.create_task(_ticker())
    await asyncio.sleep(0)
    try:
        res = await coro
    finally:
        t.cancel()
    assert res.get("source") == "local_test", res
    return max(lags) if lags else 999.0


def _registry():
    import app.tools.chinese_maps as cm
    import app.tools.osm as osm
    import app.tools.web_crawler as wc

    reg = ToolRegistry()
    osm.register_osm_tools(reg)
    cm.register_chinese_map_tools(reg)
    wc.register_crawler_tools(reg)
    return reg


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool,local_fn,kwargs",
    [
        ("query_osm_poi", "try_local_osm_poi", {"area": "成都市", "category": "restaurant"}),
        ("query_osm_roads", "try_local_osm_roads", {"area": "成都市"}),
        ("query_osm_boundary", "try_local_osm_boundary", {"name": "成都市"}),
        ("search_poi", "try_local_search_poi", {"keyword": "大学", "city": "成都"}),
        ("search_poi_around", "try_local_search_poi_around",
         {"center": [104.06, 30.67], "radius_m": 1000, "keyword": "大学"}),
        ("search_poi_polygon", "try_local_search_poi_polygon",
         {"polygon": [104.0, 30.6, 104.1, 30.7], "keyword": "大学"}),
        ("search_and_extract_poi", "try_local_web_poi", {"query": "成都 大学"}),
    ],
)
async def test_local_first_does_not_block_loop(monkeypatch, tool, local_fn, kwargs):
    import app.services.local_first as lf

    monkeypatch.setattr(lf, local_fn, _blocking)
    fn = _registry()._tools[tool]
    lag = await _max_loop_lag(fn(**kwargs))
    assert lag < 0.2, f"{tool}: event loop blocked for {lag:.2f}s"
