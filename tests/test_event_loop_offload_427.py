"""Regression tests for #427: map.py GeoJSON/SVG export serialization off the
event loop + GeoJSON export body size cap.

Siblings of #386 that the original sweep missed (only the PDF branch of
``map.py`` was offloaded by d8c0ab1):

  1. ``POST /api/v1/export/geojson`` ran ``json.dumps(data, indent=2)`` inline
     on an unbounded ``Any`` GeoJSON body — measured 576 ms loop stall for an
     11 MB export, 2.3 s for 45 MB.
  2. ``POST /api/v1/export`` (SVG branch) ran ``_sanitize_svg`` (defusedxml
     DOM parse of up to 50 MB) inline.
  3. The GeoJSON export body had NO size cap at all (file uploads are capped
     at 50 MB via MAX_EXPORT_SIZE).

Technique mirrors tests/test_event_loop_offload_386.py: the slow work is
faked with a sync ``time.sleep`` that records its thread id, and the test
asserts the main event loop stays responsive *while* the work is running.
"""
import asyncio
import gc
import json
import threading
import time

import pytest
from fastapi import HTTPException

from app.api.routes import map as map_mod

_main_thread = threading.get_ident()


async def _assert_loop_responsive_while(awaitable_factory, delay: float = 0.8):
    """Run awaitable_factory() and assert a 0.05s timer fires mid-flight.

    Deterministic: with the work offloaded the task is still running when the
    timer completes; with the work on the loop the task finishes before the
    test's own sleep resumes, so ``assert not task.done()`` fails.
    """
    task = asyncio.create_task(awaitable_factory())
    await asyncio.sleep(0.15)          # let it enter the slow work
    assert not task.done(), "work finished before the test could observe it"

    ticks = []

    async def _tick():
        await asyncio.sleep(0.05)
        ticks.append(True)

    tick = asyncio.create_task(_tick())
    await asyncio.sleep(0.15)
    assert tick.done() and ticks, "event loop was blocked during the work"
    assert not task.done(), "event loop was blocked during the work"
    return await task


def _req(geojson):
    return map_mod.GeoJSONExportRequest(geojson=geojson, filename="test_export")


# ─── Site 1: export_geojson json.dumps must run in a worker thread ──────────


@pytest.mark.asyncio
async def test_geojson_dumps_off_loop(monkeypatch, tmp_path):
    observed = {}

    def _slow_dumps(*a, **kw):
        observed["thread"] = threading.get_ident()
        time.sleep(0.8)
        return "{}"

    monkeypatch.setattr(map_mod.json, "dumps", _slow_dumps)
    monkeypatch.setattr(map_mod, "EXPORT_DIR", str(tmp_path))
    monkeypatch.setattr(map_mod, "_set_export_owner", lambda *a, **k: None)

    res = await _assert_loop_responsive_while(
        lambda: map_mod.export_geojson(_req({"type": "Point", "coordinates": [0, 0]}),
                                       _user={"user_id": "u1"})
    )
    assert observed["thread"] != _main_thread, "json.dumps ran on the event loop thread"
    assert res["format"] == "geojson"


@pytest.mark.asyncio
async def test_geojson_large_payload_no_loop_lag(monkeypatch, tmp_path):
    """Real (unmocked) serialization of a ~45 MB FeatureCollection: loop
    ticker gaps during the export must stay < 100 ms (issue acceptance),
    normalized for machine noise.

    Why normalized: this box (and CI) can be CPU-saturated by unrelated
    processes, which delays ANY loop timer even with no blocking work. The
    test therefore first measures the ticker's max gap while the loop is
    idle, and asserts the during-export gap stays within
    max(idle noise, 100 ms) + slack.

    Why chunking is required (not just to_thread): Python 3.13's C JSON
    encoder holds the GIL for the entire encode, so a single
    asyncio.to_thread(json.dumps, ...) still stalls the loop for the full
    ~1 s of a 45 MB body — measured inline AND naive-to_thread gaps ≈ full
    dumps duration, chunked gaps ≈ per-batch GIL holds (a few ms).
    """
    feature = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [116.397128, 39.916527]},
        "properties": {"name": "x" * 180, "value": 42.0, "desc": "y" * 60},
    }
    payload = {"type": "FeatureCollection", "features": [feature] * 90000}
    body_len = len(json.dumps(payload))  # built outside the measured window
    assert body_len > 30_000_000  # sanity: ~45 MB pretty-printed, near the cap

    monkeypatch.setattr(map_mod, "EXPORT_DIR", str(tmp_path))
    monkeypatch.setattr(map_mod, "_set_export_owner", lambda *a, **k: None)

    async def _max_ticker_gap(during):
        stamps = []
        stop = [False]

        async def _ticker():
            while not stop[0]:
                stamps.append(time.perf_counter())
                await asyncio.sleep(0.02)

        ticker = asyncio.create_task(_ticker())
        await asyncio.sleep(0.1)
        if during is not None:
            await during()
        else:
            await asyncio.sleep(0.3)  # idle window of comparable length
        await asyncio.sleep(0.05)
        stop[0] = True
        await ticker
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        return max(gaps) if gaps else 0.0

    idle_gap = await _max_ticker_gap(None)

    # Measure twice and keep the MINIMUM gap. The regression this guards
    # (#427: one GIL-holding json.dumps chunk for the full ~45 MB body) is
    # deterministic — it stalls EVERY attempt for ~1 s. A one-off pause
    # (gen-2 GC over a heap grown by earlier tests in the full --cov suite,
    # CI scheduler noise) hits only one attempt, so min() filters it without
    # weakening the guard. Triggering a collection up front also moves any
    # pending GC work out of the measured window.
    gc.collect()
    export_gaps = [
        await _max_ticker_gap(
            lambda: map_mod.export_geojson(_req(payload), _user={"user_id": "u1"})
        )
        for _ in range(2)
    ]
    export_gap = min(export_gaps)

    bound = max(idle_gap, 0.1) + 0.05
    assert export_gap < bound, (
        f"loop stalled {export_gap * 1000:.0f} ms during GeoJSON export "
        f"(idle noise {idle_gap * 1000:.0f} ms, bound {bound * 1000:.0f} ms, "
        "min of 2 attempts) — "
        "serialization must not run as one GIL-holding chunk on/over the loop"
    )


# ─── Chunked serializer: byte-identical to json.dumps(indent=2) ─────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "doc",
    [
        # small FeatureCollection (single-dumps path)
        {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": None, "properties": {"name": "a"}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 2]},
             "properties": {}},
        ]},
        # empty containers
        {"type": "FeatureCollection", "features": []},
        {"a": {}, "b": [], "c": None},
        # non-FeatureCollection GeoJSON
        {"type": "Point", "coordinates": [0.0, -0.5]},
        # non-ascii keys/values, floats, bools
        {"名前": "北京市", "值": 3.14, "flag": True, "nil": None, "科学": 6.02e23},
        # features NOT last (trailing comma handling) + extra list
        {"features": [{"x": 1}], "type": "FeatureCollection", "values": [1, 2, 3]},
        # top-level list crossing the chunk threshold (scalars)
        {"type": "FeatureCollection", "features": [{"i": i, "v": i * 0.5}
                                                   for i in range(2501)]},
        # top-level list of lists crossing the threshold (nested reindent)
        {"type": "GeometryCollection", "geometries": [[[i, i + 1], [i + 2, i + 3]]
                                                      for i in range(2100)]},
    ],
)
async def test_serialize_geojson_byte_identical(doc):
    """The chunked serializer must reproduce json.dumps(indent=2) byte-for-byte
    (indent layout, separators, escaping, float repr) on both the batched and
    non-batched paths."""
    expected = json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8")
    got = await map_mod._serialize_geojson(doc)
    assert got == expected


@pytest.mark.asyncio
async def test_geojson_oversized_body_413(monkeypatch, tmp_path):
    """GeoJSON export body must be bounded: serialized output larger than
    MAX_EXPORT_SIZE (50 MB, same cap as file uploads) → 413, nothing written."""
    monkeypatch.setattr(map_mod, "EXPORT_DIR", str(tmp_path))
    monkeypatch.setattr(map_mod, "_set_export_owner", lambda *a, **k: None)
    # Shrink the cap so the test payload exceeds it without building 50 MB.
    monkeypatch.setattr(map_mod, "MAX_EXPORT_SIZE", 1_000)

    big = {"type": "Point", "coordinates": [0, 0], "pad": "x" * 5_000}
    with pytest.raises(HTTPException) as exc_info:
        await map_mod.export_geojson(_req(big), _user={"user_id": "u1"})
    assert exc_info.value.status_code == 413

    assert list(tmp_path.iterdir()) == [], "oversized GeoJSON must not be written to disk"


@pytest.mark.asyncio
async def test_geojson_cap_matches_upload_cap():
    """The GeoJSON cap must be the same 50 MB budget as file uploads."""
    assert map_mod.MAX_EXPORT_SIZE == 50 * 1024 * 1024


@pytest.mark.asyncio
async def test_geojson_dumps_type_error_still_400(monkeypatch, tmp_path):
    """json.dumps TypeError (non-serializable member) must still map to 400
    after offloading."""

    def _boom(*a, **kw):
        raise TypeError("Object of type set is not JSON serializable")

    monkeypatch.setattr(map_mod.json, "dumps", _boom)
    monkeypatch.setattr(map_mod, "EXPORT_DIR", str(tmp_path))
    with pytest.raises(HTTPException) as exc_info:
        await map_mod.export_geojson(_req({"type": "Point"}), _user={"user_id": "u1"})
    assert exc_info.value.status_code == 400


# ─── Site 2: upload_map_export SVG sanitization must run in a worker thread ──


@pytest.mark.asyncio
async def test_svg_sanitize_off_loop(monkeypatch, tmp_path):
    """defusedxml DOM parse of a ≤50 MB SVG must run in a worker thread."""
    import io

    from fastapi import UploadFile

    observed = {}

    def _slow_sanitize(content):
        observed["thread"] = threading.get_ident()
        time.sleep(0.8)
        return content

    monkeypatch.setattr(map_mod, "_sanitize_svg", _slow_sanitize)
    monkeypatch.setattr(map_mod, "EXPORT_DIR", str(tmp_path))
    monkeypatch.setattr(map_mod, "_set_export_owner", lambda *a, **k: None)

    file = UploadFile(file=io.BytesIO(b"<svg/>"), filename="map.svg")
    try:
        res = await _assert_loop_responsive_while(
            lambda: map_mod.upload_map_export(file, title="t", _user={"user_id": "u1"})
        )
        assert res["success"] is True
        assert observed["thread"] != _main_thread, "_sanitize_svg ran on the event loop thread"
        assert (tmp_path / res["filename"]).exists()
    finally:
        await file.close()


@pytest.mark.asyncio
async def test_svg_sanitize_http_error_propagates(monkeypatch, tmp_path):
    """HTTPException(400) raised inside the offloaded sanitizer must surface
    as-is (not be swallowed into a 500)."""
    import io

    from fastapi import UploadFile

    def _reject(content):
        raise HTTPException(status_code=400, detail="SVG 解析失败")

    monkeypatch.setattr(map_mod, "_sanitize_svg", _reject)
    monkeypatch.setattr(map_mod, "EXPORT_DIR", str(tmp_path))

    file = UploadFile(file=io.BytesIO(b"not-svg"), filename="map.svg")
    try:
        with pytest.raises(HTTPException) as exc_info:
            await map_mod.upload_map_export(file, _user={"user_id": "u1"})
        assert exc_info.value.status_code == 400
    finally:
        await file.close()
