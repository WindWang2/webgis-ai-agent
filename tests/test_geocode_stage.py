"""Tests for the pure :func:`geocode_stage` — the geocoding algorithm of the
Explorer pipeline, exercised through its interface.

Migrated from ``test_geocode_enhancement.py`` (architecture-review C2). The
prior tests patched module symbols (``_load_ref`` / ``_store_ref`` /
``batch_geocode_cn``) and drove the algorithm via ``explorer_geocode_task.run``;
these call :func:`geocode_stage` directly with the three dependencies injected
as kwargs. The interface is the test surface — no Celery, no module-globals.
"""
import asyncio

import pytest

from app.services.explorer.geocode_stage import geocode_stage


def _run(coro):
    """Drive the async stage on a fresh loop (mirrors the Celery _run_async shape)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_geocode_stage_maps_results_back_and_returns_success_rate():
    """One provider, all succeed: results map back by index, success_rate == 1.0."""
    parsed_sources = [
        {"ref_id": "ref_1", "row_count": 2, "mapping": {"address": "addr"}},
    ]

    rows = [
        {"name": "A", "addr": "北京"},
        {"name": "B", "addr": "上海"},
    ]

    def load_ref(ref_id):
        return {"rows": rows, "mapping": {"address": "addr"}}

    async def batch_geocode(addresses, provider="amap", max_concurrency=3):
        return {
            "total": 2,
            "success_count": 2,
            "error_count": 0,
            "results": [
                {"index": 0, "status": "ok", "address": "北京", "results": [{"location": [116.4, 39.9]}]},
                {"index": 1, "status": "ok", "address": "上海", "results": [{"location": [121.5, 31.2]}]},
            ],
            "errors": [],
            "provider": "amap",
        }

    stored = {}

    def store_ref(payload):
        stored.update(payload)
        return "geocoded_ref_123"

    result = _run(geocode_stage(
        parsed_sources,
        load_ref=load_ref,
        batch_geocode=batch_geocode,
        store_ref=store_ref,
    ))

    assert result.summary.total == 2
    assert result.summary.success == 2
    assert result.summary.failed == 0
    assert result.summary.success_rate == 1.0
    assert result.summary.multi_provider is False

    for row in result.rows:
        assert row["_geocode_status"] == "ok"
        assert row["_geocode_provider"] == "amap"
        assert row["_geocode_error"] is None

    assert result.rows[0]["_lat"] == 39.9
    assert result.rows[0]["_lon"] == 116.4
    assert result.rows[1]["_lat"] == 31.2
    assert result.rows[1]["_lon"] == 121.5

    # store_ref received rows + summary exactly as the task would persist them
    assert stored["rows"] == result.rows
    assert stored["summary"]["success_rate"] == 1.0


def test_geocode_stage_multi_provider_fallback():
    """First provider fails 50% (>30% threshold) → rotate to baidu → 75% overall."""
    parsed_sources = [
        {"ref_id": "ref_1", "row_count": 4, "mapping": {"address": "addr"}},
    ]

    rows = [
        {"name": "A", "addr": "北京"},
        {"name": "B", "addr": "上海"},
        {"name": "C", "addr": "广州"},
        {"name": "D", "addr": "深圳"},
    ]

    async def batch_geocode(addresses, provider="amap", max_concurrency=3):
        if provider == "amap":
            return {
                "total": len(addresses),
                "success_count": 2,
                "error_count": 2,
                "results": [
                    {"index": 0, "status": "ok", "address": addresses[0], "results": [{"location": [116.4, 39.9]}]},
                    {"index": 1, "status": "ok", "address": addresses[1], "results": [{"location": [121.5, 31.2]}]},
                ],
                "errors": [
                    {"index": 2, "status": "error", "address": addresses[2], "error": "not found"},
                    {"index": 3, "status": "error", "address": addresses[3], "error": "not found"},
                ],
                "provider": "amap",
            }
        elif provider == "baidu":
            return {
                "total": len(addresses),
                "success_count": 1,
                "error_count": 1,
                "results": [
                    {"index": 0, "status": "ok", "address": addresses[0], "results": [{"location": [113.3, 23.1]}]},
                ],
                "errors": [
                    {"index": 1, "status": "error", "address": addresses[1], "error": "not found"},
                ],
                "provider": "baidu",
            }
        else:
            return {
                "total": len(addresses),
                "success_count": 0,
                "error_count": len(addresses),
                "results": [],
                "errors": [{"index": i, "status": "error", "address": a, "error": "failed"} for i, a in enumerate(addresses)],
                "provider": provider,
            }

    def load_ref(ref_id):
        return {"rows": rows, "mapping": {"address": "addr"}}

    result = _run(geocode_stage(
        parsed_sources,
        load_ref=load_ref,
        batch_geocode=batch_geocode,
    ))

    assert result.summary.total == 4
    assert result.summary.success_rate == 0.75
    assert result.summary.success == 3
    assert result.summary.failed == 1
    assert result.summary.multi_provider is True

    statuses = [r["_geocode_status"] for r in result.rows]
    assert statuses.count("ok") == 3
    assert statuses.count("failed") == 1


def test_geocode_stage_empty_data_returns_zero_rate():
    """Empty parsed_sources: no rows, success_rate 0.0, load_ref never called."""
    called = {"load": False}

    def load_ref(ref_id):
        called["load"] = True
        return None

    async def batch_geocode(addresses, provider="amap", max_concurrency=3):
        pytest.fail("batch_geocode must not run on empty input")

    result = _run(geocode_stage(
        [],
        load_ref=load_ref,
        batch_geocode=batch_geocode,
    ))

    assert result.summary.total == 0
    assert result.summary.success_rate == 0.0
    assert result.rows == []
    assert called["load"] is False


def test_geocode_stage_predefined_coordinates_skipped():
    """Rows with existing lat/lon are marked predefined and excluded from geocoding."""
    parsed_sources = [
        {"ref_id": "ref_1", "row_count": 3, "mapping": {"address": "addr", "lat": "latitude", "lon": "longitude"}},
    ]

    rows = [
        {"name": "A", "addr": "北京", "latitude": 39.9, "longitude": 116.4},
        {"name": "B", "addr": "上海", "latitude": None, "longitude": None},
        {"name": "C", "addr": "广州"},
    ]

    def load_ref(ref_id):
        return {"rows": rows, "mapping": {"address": "addr", "lat": "latitude", "lon": "longitude"}}

    batch_calls = []

    async def batch_geocode(addresses, provider="amap", max_concurrency=3):
        batch_calls.append(list(addresses))
        return {
            "total": 2,
            "success_count": 2,
            "error_count": 0,
            "results": [
                {"index": 0, "status": "ok", "address": "上海", "results": [{"location": [121.5, 31.2]}]},
                {"index": 1, "status": "ok", "address": "广州", "results": [{"location": [113.3, 23.1]}]},
            ],
            "errors": [],
            "provider": "amap",
        }

    result = _run(geocode_stage(
        parsed_sources,
        load_ref=load_ref,
        batch_geocode=batch_geocode,
    ))

    assert result.summary.total == 3
    assert result.summary.success_rate == 1.0
    assert result.summary.success == 2
    assert result.summary.predefined == 1
    assert result.summary.failed == 0

    row_a = result.rows[0]
    assert row_a["_geocode_status"] == "predefined"
    assert row_a["_lat"] == 39.9
    assert row_a["_lon"] == 116.4
    assert row_a["_geocode_provider"] is None

    row_b = result.rows[1]
    assert row_b["_geocode_status"] == "ok"
    assert row_b["_lat"] == 31.2
    assert row_b["_lon"] == 121.5
    assert row_b["_geocode_provider"] == "amap"

    row_c = result.rows[2]
    assert row_c["_geocode_status"] == "ok"
    assert row_c["_lat"] == 23.1
    assert row_c["_lon"] == 113.3

    # Only the two non-predefined rows were sent to the geocoder
    assert len(batch_calls) == 1
    assert batch_calls[0] == ["上海", "广州"]


def test_geocode_stage_all_providers_failed():
    """Every provider returns errors: all rows failed with all_providers_failed."""
    parsed_sources = [
        {"ref_id": "ref_1", "row_count": 2, "mapping": {"address": "addr"}},
    ]

    rows = [
        {"name": "A", "addr": "北京"},
        {"name": "B", "addr": "上海"},
    ]

    call_count = {"n": 0}

    async def batch_geocode(addresses, provider="amap", max_concurrency=3):
        call_count["n"] += 1
        return {
            "total": len(addresses),
            "success_count": 0,
            "error_count": len(addresses),
            "results": [],
            "errors": [
                {"index": i, "status": "error", "address": a, "error": "service unavailable"}
                for i, a in enumerate(addresses)
            ],
            "provider": provider,
        }

    def load_ref(ref_id):
        return {"rows": rows, "mapping": {"address": "addr"}}

    result = _run(geocode_stage(
        parsed_sources,
        load_ref=load_ref,
        batch_geocode=batch_geocode,
    ))

    assert result.summary.total == 2
    assert result.summary.success_rate == 0.0
    assert result.summary.success == 0
    assert result.summary.failed == 2

    for row in result.rows:
        assert row["_geocode_status"] == "failed"
        assert row["_lat"] is None
        assert row["_lon"] is None
        assert row["_geocode_error"] == "all_providers_failed"

    # Both rows in one batch → all 3 providers tried once each
    assert call_count["n"] == 3


def test_geocode_stage_dedupes_duplicate_addresses():
    """Duplicate addresses are geocoded once, not N times.

    Regression for the no-dedup defect: every row with a non-empty address
    went into the geocode chunk verbatim, so duplicate addresses (common in
    real datasets - e.g. multiple rows in the same district) were billed
    N times against quota-limited/paid providers.
    """
    parsed_sources = [
        {"ref_id": "ref_1", "row_count": 4, "mapping": {"address": "addr"}},
    ]

    # Four rows, but only two unique addresses.
    rows = [
        {"name": "A", "addr": "北京"},
        {"name": "B", "addr": "上海"},
        {"name": "C", "addr": "北京"},  # dup of A
        {"name": "D", "addr": "上海"},  # dup of B
    ]

    def load_ref(ref_id):
        return {"rows": rows, "mapping": {"address": "addr"}}

    geocoded_addresses: list[str] = []

    async def batch_geocode(addresses, provider="amap", max_concurrency=3):
        geocoded_addresses.extend(addresses)
        locs = {"北京": [116.4, 39.9], "上海": [121.5, 31.2]}
        return {
            "total": len(addresses),
            "success_count": len(addresses),
            "error_count": 0,
            "results": [
                {"index": i, "status": "ok", "address": a, "results": [{"location": locs[a]}]}
                for i, a in enumerate(addresses)
            ],
            "errors": [],
            "provider": "amap",
        }

    result = _run(geocode_stage(
        parsed_sources,
        load_ref=load_ref,
        batch_geocode=batch_geocode,
    ))

    # Each unique address geocoded exactly once (2 calls, not 4).
    assert geocoded_addresses == ["北京", "上海"], (
        f"expected deduped geocode of 2 unique addresses, got {geocoded_addresses}"
    )
    # All four rows got results (fan-out from the deduped set).
    assert result.summary.total == 4
    assert result.summary.success == 4
    # Duplicate-address rows share the same coordinates.
    assert result.rows[0]["_lat"] == result.rows[2]["_lat"]  # both 北京
    assert result.rows[1]["_lat"] == result.rows[3]["_lat"]  # both 上海
    assert result.rows[0]["_lat"] != result.rows[1]["_lat"]   # 北京 != 上海


def test_geocode_stage_dedup_preserves_result_mapping():
    """Rows with the same address both get the correct lat/lon.

    Pins the fan-out: the dedup must map the unique-address result back to
    every row_idx that shares it, not just the first.
    """
    parsed_sources = [
        {"ref_id": "ref_1", "row_count": 2, "mapping": {"address": "addr"}},
    ]

    rows = [
        {"name": "A", "addr": "同一地址"},
        {"name": "B", "addr": "同一地址"},
    ]

    def load_ref(ref_id):
        return {"rows": rows, "mapping": {"address": "addr"}}

    async def batch_geocode(addresses, provider="amap", max_concurrency=3):
        assert addresses == ["同一地址"]
        return {
            "total": 1,
            "success_count": 1,
            "error_count": 0,
            "results": [
                {"index": 0, "status": "ok", "address": "同一地址", "results": [{"location": [116.4, 39.9]}]},
            ],
            "errors": [],
            "provider": "amap",
        }

    result = _run(geocode_stage(
        parsed_sources,
        load_ref=load_ref,
        batch_geocode=batch_geocode,
    ))

    assert result.summary.total == 2
    assert result.summary.success == 2
    # Both rows got the same coordinates from the single geocode call.
    # location=[116.4, 39.9] is [lon, lat] (geocoding API convention), so
    # _lat=39.9, _lon=116.4 (see extract_lat_lon in geocode_strategy.py).
    assert result.rows[0]["_lat"] == 39.9
    assert result.rows[0]["_lon"] == 116.4
    assert result.rows[1]["_lat"] == 39.9
    assert result.rows[1]["_lon"] == 116.4
