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


# ─── Issue #483: bounded geocode work vs the hard Celery time limit ─────────
#
# explorer_geocode_task runs under soft_time_limit=290 / time_limit=300; the
# stage used to iterate every row of every source with no bound, so large
# datasets hard-killed the worker mid-run. The stage now takes a time budget:
# unique addresses are dispatched in ≤BATCH_SIZE partitions, the deadline is
# checked before each partition, un-dispatched rows are marked "skipped", and
# the summary carries honest partial-completion metadata — the task finishes
# gracefully before the soft limit instead of being killed.

from app.services.explorer.geocode_stage import GeocodeSummary


class _FakeClock:
    """Deterministic clock advanced manually by the fake geocoder."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _ok_batch_response(addresses, provider="amap"):
    return {
        "total": len(addresses),
        "success_count": len(addresses),
        "error_count": 0,
        "results": [
            {"index": i, "status": "ok", "address": a,
             "results": [{"location": [116.0 + i * 0.001, 39.9]}]}
            for i, a in enumerate(addresses)
        ],
        "errors": [],
        "provider": provider,
    }


def test_geocode_stage_zero_budget_skips_all_rows():
    """time_budget=0: no provider calls at all, every row honestly skipped,
    rows still carried (partial checkpoint for validate), metadata set."""
    rows = [{"name": f"r{i}", "addr": f"地址{i}"} for i in range(5)]
    parsed_sources = [{"ref_id": "ref_z", "row_count": len(rows),
                       "mapping": {"address": "addr"}}]

    async def batch_geocode(addresses, provider="amap", max_concurrency=3):
        pytest.fail("batch_geocode must not run with an exhausted budget")

    result = _run(geocode_stage(
        parsed_sources,
        load_ref=lambda ref_id: {"rows": rows, "mapping": {"address": "addr"}},
        batch_geocode=batch_geocode,
        time_budget=0,
        clock=lambda: 0.0,
    ))

    assert result.summary.deadline_exceeded is True
    assert result.summary.skipped == 5
    assert result.summary.total == 5
    assert result.summary.success == 0
    assert all(r["_geocode_status"] == "skipped" for r in result.rows)
    assert all(r["_lat"] is None for r in result.rows)
    assert "budget" in result.rows[0]["_geocode_error"]


def test_geocode_stage_stops_dispatching_at_deadline():
    """250 unique addresses, 100 per partition, 50s per partition, budget 75s:
    partition 1 dispatches (elapsed 0 < 75), partition 2 dispatches
    (elapsed 50 < 75), partition 3 is skipped (elapsed 100 >= 75) — 200
    geocoded, 50 honestly reported as skipped."""
    from app.services.geocode_strategy import BATCH_SIZE
    rows = [{"name": f"r{i}", "addr": f"地址{i}"} for i in range(250)]
    parsed_sources = [{"ref_id": "ref_d", "row_count": len(rows),
                       "mapping": {"address": "addr"}}]

    clock = _FakeClock()
    dispatched: list[int] = []

    async def batch_geocode(addresses, provider="amap", max_concurrency=3):
        assert len(addresses) <= BATCH_SIZE
        dispatched.append(len(addresses))
        clock.advance(50.0)
        return _ok_batch_response(addresses)

    result = _run(geocode_stage(
        parsed_sources,
        load_ref=lambda ref_id: {"rows": rows, "mapping": {"address": "addr"}},
        batch_geocode=batch_geocode,
        time_budget=75.0,
        clock=clock.monotonic,
    ))

    assert dispatched == [100, 100], f"unexpected dispatch pattern: {dispatched}"
    assert result.summary.success == 200
    assert result.summary.skipped == 50
    assert result.summary.deadline_exceeded is True
    statuses = [r["_geocode_status"] for r in result.rows]
    assert statuses.count("ok") == 200
    assert statuses.count("skipped") == 50
    # success_rate covers the rows actually attempted, not budget skips.
    assert result.summary.success_rate == 1.0


def test_geocode_stage_no_budget_processes_everything():
    """time_budget=None keeps the historical unbounded behavior (no skips) —
    the in-process pipeline path stays unchanged."""
    rows = [{"name": f"r{i}", "addr": f"地址{i}"} for i in range(3)]
    parsed_sources = [{"ref_id": "ref_n", "row_count": 3,
                       "mapping": {"address": "addr"}}]

    async def batch_geocode(addresses, provider="amap", max_concurrency=3):
        return _ok_batch_response(addresses)

    result = _run(geocode_stage(
        parsed_sources,
        load_ref=lambda ref_id: {"rows": rows, "mapping": {"address": "addr"}},
        batch_geocode=batch_geocode,
        time_budget=None,
        clock=lambda: 0.0,
    ))

    assert result.summary.skipped == 0
    assert result.summary.deadline_exceeded is False
    assert result.summary.success == 3


def test_geocode_stage_deadline_mid_second_source():
    """Budget exhausted while source 2 is queued: source 2 rows load and are
    marked skipped; source 1 results survive; summary reports both."""
    rows_1 = [{"name": "a1", "addr": "地址一"}, {"name": "a2", "addr": "地址二"}]
    rows_2 = [{"name": "b1", "addr": "地址三"}, {"name": "b2", "addr": "地址四"}]
    parsed_sources = [
        {"ref_id": "ref_s1", "row_count": 2, "mapping": {"address": "addr"}},
        {"ref_id": "ref_s2", "row_count": 2, "mapping": {"address": "addr"}},
    ]
    store = {}
    refs = {"ref_s1": {"rows": rows_1, "mapping": {"address": "addr"}},
            "ref_s2": {"rows": rows_2, "mapping": {"address": "addr"}}}

    clock = _FakeClock()
    progress: list[int] = []

    async def batch_geocode(addresses, provider="amap", max_concurrency=3):
        clock.advance(200.0)
        return _ok_batch_response(addresses)

    result = _run(geocode_stage(
        parsed_sources,
        load_ref=lambda ref_id: refs[ref_id],
        batch_geocode=batch_geocode,
        store_ref=lambda payload: store.update(payload) or "ref_out",
        on_progress=progress.append,
        time_budget=150.0,
        clock=clock.monotonic,
    ))

    assert result.summary.success == 2
    assert result.summary.skipped == 2
    assert result.summary.deadline_exceeded is True
    # Partial checkpoint: store received the annotated rows + honest summary.
    assert len(store["rows"]) == 4
    assert store["summary"]["skipped"] == 2
    assert store["summary"]["deadline_exceeded"] is True
    # Progress was still reported while work happened.
    assert progress


def test_geocode_stage_summary_dict_carries_partial_metadata():
    """GeocodeSummary.as_dict exposes skipped + deadline_exceeded so the task
    handoff can surface honest partial-completion metadata."""
    s = GeocodeSummary(total=10, success=6, failed=1, predefined=1, skipped=2,
                       success_rate=6 / 7, deadline_exceeded=True)
    d = s.as_dict()
    assert d["skipped"] == 2
    assert d["deadline_exceeded"] is True


def test_geocode_stage_predefined_rows_not_counted_skipped():
    """Adversarial: rows with coordinates are predefined regardless of budget;
    only un-dispatched geocode rows count as skipped."""
    rows = [
        {"name": "p", "addr": "x", "lat": 39.9, "lon": 116.4},
        {"name": "q", "addr": "y"},
    ]
    parsed_sources = [{"ref_id": "ref_p", "row_count": 2,
                       "mapping": {"address": "addr", "lat": "lat", "lon": "lon"}}]

    async def batch_geocode(addresses, provider="amap", max_concurrency=3):
        pytest.fail("must not dispatch with zero budget")

    result = _run(geocode_stage(
        parsed_sources,
        load_ref=lambda ref_id: {"rows": rows,
                                 "mapping": {"address": "addr", "lat": "lat", "lon": "lon"}},
        batch_geocode=batch_geocode,
        time_budget=0,
        clock=lambda: 0.0,
    ))

    assert result.summary.predefined == 1
    assert result.summary.skipped == 1
    assert result.rows[0]["_geocode_status"] == "predefined"
    assert result.rows[1]["_geocode_status"] == "skipped"
    assert result.summary.success_rate == 0.0


def test_geocode_task_bounded_by_time_budget_and_reports_partial_completion(monkeypatch):
    """The Celery adapter passes GEOCODE_STAGE_TIME_BUDGET into the stage and
    surfaces skipped_rows/deadline_exceeded in the validate-stage handoff."""
    from app.services.explorer import geocode_stage as gs_mod
    from app.services.session_data_protocol import set_active_session_store
    from app.services.session_data import MemorySessionStore
    from app.tasks.explorer import task_chain
    import asyncio as _asyncio

    captured: dict = {}

    class _FakeResult:
        rows = [{"name": "A", "_geocode_status": "ok"},
                {"name": "B", "_geocode_status": "skipped"}]
        summary = gs_mod.GeocodeSummary(
            total=2, success=1, skipped=1, success_rate=1.0,
            deadline_exceeded=True,
        )

    async def fake_stage(parsed_sources, *, load_ref, batch_geocode,
                         on_progress=None, **kwargs):
        captured.update(kwargs)
        return _FakeResult()

    monkeypatch.setattr(gs_mod, "geocode_stage", fake_stage)

    def fake_run_async(coro):
        loop = _asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    monkeypatch.setattr(task_chain, "_run_async", fake_run_async)

    store = MemorySessionStore()
    set_active_session_store(store)
    try:
        out = task_chain.explorer_geocode_task.apply(
            args=[{
                "task_id": "t-483",
                "parsed_results": [{"ref_id": "r1", "row_count": 2, "mapping": {}}],
            }]
        )
    finally:
        set_active_session_store(None)

    assert captured.get("time_budget") == gs_mod.GEOCODE_STAGE_TIME_BUDGET
    assert out.successful()
    assert out.result["skipped_rows"] == 1
    assert out.result["deadline_exceeded"] is True
    assert out.result["total_rows"] == 2
    assert out.result["geocoded_ref_id"]


# ─── #771 / #772: (0,0) failures, row attribution, precision fan-out ────────


def test_geocode_stage_zero_zero_row_is_failed_771():
    """#771: a provider miss that defaults location to [0,0] (tianditu/baidu)
    must land as _geocode_status="failed" — never an "ok" row at Null Island
    inflating summary.success_rate."""
    parsed_sources = [{"ref_id": "ref_1", "row_count": 1, "mapping": {"address": "addr"}}]
    rows = [{"name": "A", "addr": "不存在的地址"}]

    async def batch_geocode(addresses, provider="amap", max_concurrency=3):
        return {
            "results": [
                {"index": 0, "status": "ok", "address": "不存在的地址",
                 "results": [{"location": [0.0, 0.0]}], "provider": provider}
            ],
            "errors": [],
            "provider": provider,
        }

    result = _run(geocode_stage(
        parsed_sources,
        load_ref=lambda ref_id: {"rows": rows, "mapping": {"address": "addr"}},
        batch_geocode=batch_geocode,
    ))
    assert result.rows[0]["_geocode_status"] == "failed"
    assert result.rows[0]["_lat"] is None
    assert result.summary.failed == 1
    assert result.summary.success == 0


def test_geocode_stage_fans_out_provider_precision_and_fallback_772():
    """#772: rows carry _geocode_provider of the provider that ACTUALLY
    answered (with_fallback switch), its precision level, a per-row fallback
    marker, and summary.multi_provider becomes truthful."""
    parsed_sources = [{"ref_id": "ref_1", "row_count": 1, "mapping": {"address": "addr"}}]
    rows = [{"name": "A", "addr": "海淀某址"}]

    async def batch_geocode(addresses, provider="amap", max_concurrency=3):
        assert provider == "amap"
        return {
            "results": [
                {"index": 0, "status": "ok", "address": "海淀某址",
                 "results": [{"location": [116.3, 39.98], "precision_level": "district"}],
                 "provider": "baidu",
                 "provenance": {"source": "baidu"}}
            ],
            "errors": [],
            "provider": "amap",
        }

    result = _run(geocode_stage(
        parsed_sources,
        load_ref=lambda ref_id: {"rows": rows, "mapping": {"address": "addr"}},
        batch_geocode=batch_geocode,
    ))
    row = result.rows[0]
    assert row["_geocode_status"] == "ok"
    assert row["_geocode_provider"] == "baidu"       # actual provider, not amap
    assert row["_geocode_precision"] == "district"   # precision level kept
    assert row["_geocode_fallback"] is True          # per-row fallback marker
    assert result.summary.multi_provider is True     # truthful summary
