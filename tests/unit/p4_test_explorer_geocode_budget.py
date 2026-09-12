"""Explorer — geocode_stage 预算截断与多 provider 降级（P4 补强 E1/#483）。

时间预算（issue #483）诚实截断：skipped 行单独计数、deadline_exceeded
置位、success_rate 不把截断冒充 provider 失败。
"""
from __future__ import annotations

import asyncio

from app.services.explorer.geocode_stage import geocode_stage


def _fake_geocoder():
    async def _geocode(addresses, provider=None, **kw):  # noqa: ANN001, ANN003
        return {"results": [{"index": i, "location": {"lat": 30.5, "lon": 104.0}} for i, _ in enumerate(addresses)], "errors": []}

    return _geocode


def test_budget_exhaustion_marks_skipped_and_sets_deadline() -> None:
    rows = [{"addr": f"地址{i}"} for i in range(20)]
    parsed = [{"ref_id": "r1", "row_count": len(rows), "mapping": {"address": "addr"}}]
    ticks = {"t": 0}

    def clock() -> float:
        ticks["t"] += 1
        return float(ticks["t"])

    result = asyncio.run(geocode_stage(
        parsed, load_ref=lambda ref: {"rows": rows, "mapping": {"address": "addr"}},
        batch_geocode=_fake_geocoder(), time_budget=0.0, clock=clock))
    assert result.summary.deadline_exceeded is True
    assert result.summary.skipped >= 1
    skipped_rows = [r for r in result.rows if r["_geocode_status"] == "skipped"]
    assert skipped_rows, "预算耗尽后的行必须诚实标注 skipped"


def test_budget_respects_dispatch_boundaries() -> None:
    rows = [{"addr": f"地址{i}"} for i in range(4)]
    parsed = [{"ref_id": "r1", "row_count": 4, "mapping": {"address": "addr"}}]
    # 预算足够：无 skipped、无 deadline 标记。
    result = asyncio.run(geocode_stage(
        parsed, load_ref=lambda ref: {"rows": rows, "mapping": {"address": "addr"}},
        batch_geocode=_fake_geocoder(), time_budget=10.0, clock=lambda: 1.0))
    assert result.summary.deadline_exceeded is False
    assert result.summary.skipped == 0


def test_provider_error_dict_rotates_and_still_answers() -> None:
    rows = [{"addr": "地址A"}]
    parsed = [{"ref_id": "r1", "row_count": 1, "mapping": {"address": "addr"}}]

    async def flaky(addresses, provider=None, **kw):  # noqa: ANN001, ANN003
        if provider == "amap":
            return {"error": "quota exhausted"}
        return {"results": [{"index": i, "location": {"lat": 30.5, "lon": 104.0}, "provider": provider}
                            for i, _ in enumerate(addresses)], "errors": []}

    result = asyncio.run(geocode_stage(
        parsed, load_ref=lambda ref: {"rows": rows, "mapping": {"address": "addr"}},
        batch_geocode=flaky, providers=["amap", "tianditu"]))
    assert result.rows[0]["_geocode_status"] == "ok"
    assert result.summary.multi_provider is True


def test_all_providers_failing_is_failed_not_success() -> None:
    rows = [{"addr": "地址A"}]
    parsed = [{"ref_id": "r1", "row_count": 1, "mapping": {"address": "addr"}}]

    async def down(addresses, provider=None, **kw):  # noqa: ANN001, ANN003
        return {"error": "down"}

    result = asyncio.run(geocode_stage(
        parsed, load_ref=lambda ref: {"rows": rows, "mapping": {"address": "addr"}},
        batch_geocode=down, providers=["amap", "tianditu"]))
    assert result.rows[0]["_geocode_status"] == "failed"
    assert result.summary.failed >= 1


def test_store_ref_receives_rows_and_summary() -> None:
    rows = [{"addr": "地址A"}]
    parsed = [{"ref_id": "r1", "row_count": 1, "mapping": {"address": "addr"}}]
    stored: list[dict] = []
    asyncio.run(geocode_stage(
        parsed, load_ref=lambda ref: {"rows": rows, "mapping": {"address": "addr"}},
        batch_geocode=_fake_geocoder(), store_ref=stored.append))
    assert len(stored) == 1
    assert stored[0]["summary"]["total"] == 1
