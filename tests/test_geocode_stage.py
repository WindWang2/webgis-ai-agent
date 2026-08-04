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


def test_geocode_stage_all_refs_missing_yields_empty_rows():
    """Every parsed ref unresolved (cross-worker handoff break) => empty rows.

    This is the pure-stage signal the Celery task gates on: when there were
    rows to geocode (parsed_sources non-empty, row_count > 0) but every ref
    failed to load, ``result.rows`` is empty. The task-level fail-fast
    (``expected_rows > 0 and not result.rows``) turns this into a loud failure
    rather than handing an empty geocoded_ref_id to validate. Here we pin the
    stage's half of that contract.
    """
    parsed_sources = [
        {"ref_id": "ref_1", "row_count": 2, "mapping": {"address": "addr"}},
        {"ref_id": "ref_2", "row_count": 3, "mapping": {"address": "addr"}},
    ]

    def load_ref(ref_id):
        return None  # cross-worker break: every ref invisible

    async def batch_geocode(addresses, provider="amap", max_concurrency=3):
        pytest.fail("batch_geocode must not run when no rows loaded")

    result = _run(geocode_stage(
        parsed_sources,
        load_ref=load_ref,
        batch_geocode=batch_geocode,
    ))

    # The signal the task gates on:
    assert result.rows == []
    assert result.summary.total == 0
