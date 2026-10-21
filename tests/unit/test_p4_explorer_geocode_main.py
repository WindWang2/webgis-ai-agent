"""Explorer — geocode_stage 主流程（P4 补强 E1：explorer 侧 geocode 零专测）。

预定义坐标跳过、缺地址失败标注、成功注解与汇总口径。
"""
from __future__ import annotations

import asyncio


from app.services.explorer.geocode_stage import geocode_stage


def _ok(lat: float, lon: float, provider: str = "amap") -> dict:
    # 扁平 item 形态：extract_lat_lon 的顶层 dict location 分支。
    return {"level": "门牌号", "location": {"lat": lat, "lon": lon},
            "provider": provider}


def _fake_geocoder(results: dict[str, dict] | None = None, fail: bool = False):
    async def _geocode(addresses, provider=None, **kw):  # noqa: ANN001, ANN003
        if fail:
            return {"error": "provider down"}
        out = {"results": [], "errors": []}
        for i, addr in enumerate(addresses):
            hit = (results or {}).get(addr)
            if hit is None:
                out["errors"].append({"index": i, "message": "not found"})
            else:
                r = dict(hit)
                r["index"] = i
                out["results"].append(r)
        return out

    return _geocode


def test_predefined_rows_skip_geocoding() -> None:
    parsed = [{"ref_id": "r1", "row_count": 1, "mapping": {"address": "addr", "lat": "la", "lon": "lo"}}]
    data = {"rows": [{"addr": "x", "la": 30.1, "lo": 120.2}], "mapping": parsed[0]["mapping"]}
    result = asyncio.run(geocode_stage(
        parsed, load_ref=lambda ref: data,
        batch_geocode=_fake_geocoder(), store_ref=None))
    row = result.rows[0]
    assert row["_geocode_status"] == "predefined"
    assert row["_lat"] == 30.1 and row["_lon"] == 120.2
    assert result.summary.predefined == 1


def test_missing_address_is_failed_not_geocoded() -> None:
    parsed = [{"ref_id": "r1", "row_count": 1, "mapping": {"address": "addr"}}]
    data = {"rows": [{"addr": None}], "mapping": {}}
    result = asyncio.run(geocode_stage(
        parsed, load_ref=lambda ref: data, batch_geocode=_fake_geocoder()))
    row = result.rows[0]
    assert row["_geocode_status"] == "failed"
    assert row["_geocode_error"] == "missing address"


def test_geocoded_row_annotation_roundtrip() -> None:
    parsed = [{"ref_id": "r1", "row_count": 1, "mapping": {"address": "addr"}}]
    data = {"rows": [{"addr": "成都市天府大道"}], "mapping": {"address": "addr"}}
    geocoder = _fake_geocoder({"成都市天府大道": _ok(30.5, 104.0)})
    result = asyncio.run(geocode_stage(
        parsed, load_ref=lambda ref: data, batch_geocode=geocoder))
    row = result.rows[0]
    assert row["_geocode_status"] == "ok"
    assert row["_lat"] == 30.5 and row["_lon"] == 104.0
    assert row["_geocode_provider"] == "amap"
    assert result.summary.success >= 1


def test_missing_ref_is_recorded_and_stage_survives() -> None:
    parsed = [{"ref_id": "gone", "row_count": 0, "mapping": {}}]
    result = asyncio.run(geocode_stage(
        parsed, load_ref=lambda ref: None, batch_geocode=_fake_geocoder()))
    assert result.rows == []
    assert result.summary.total == 0


def test_duplicate_addresses_geocode_once() -> None:
    parsed = [{"ref_id": "r1", "row_count": 3, "mapping": {"address": "addr"}}]
    data = {"rows": [{"addr": "同址"}, {"addr": "同址"}, {"addr": "同址"}],
            "mapping": {"address": "addr"}}
    calls = {"n": 0}

    async def geocode(addresses, provider=None, **kw):  # noqa: ANN001, ANN003
        calls["n"] += 1
        return {"results": [{"index": i, "location": {"lat": 30.5, "lon": 104.0}} for i, _ in enumerate(addresses)], "errors": []}

    result = asyncio.run(geocode_stage(parsed, load_ref=lambda ref: data, batch_geocode=geocode))
    assert calls["n"] == 1  # 去重后仅一次分区派发
    assert all(r["_geocode_status"] == "ok" for r in result.rows)


def test_progress_reports_zero_to_hundred() -> None:
    parsed = [{"ref_id": "r1", "row_count": 2, "mapping": {"address": "addr"}}]
    data = {"rows": [{"addr": "a"}, {"addr": None}], "mapping": {"address": "addr"}}
    seen: list[int] = []
    asyncio.run(geocode_stage(
        parsed, load_ref=lambda ref: data, batch_geocode=_fake_geocoder(fail=True),
        on_progress=seen.append))
    assert seen[0] == 0
    assert seen[-1] == 100
