"""Regression tests for #590 / #592: data-plane serialization and file IO off
the event loop.

#590 (sibling of #427/#499): ``GET /layers/data/{ref_id}`` returned multi-MB
GeoJSON dicts to FastAPI's default ``JSONResponse`` — the encode ran inline on
the event loop (third missed site of the offload discipline; the descriptor
fallback's ``json.loads`` + full-feature scan were equally on-loop).

#592: three sync file-IO sites were inline in async routes — the upload temp
write (≤200 MB), the map-export temp write + atomic replace (≤50 MB), and the
shared-report HTML view's ``open().read()`` (multi-MB HTML). The report view
now returns ``FileResponse`` (worker-thread streaming) instead.

Technique mirrors tests/test_event_loop_offload_427.py / _386.py: the slow work
is faked with a sync ``time.sleep`` that records its thread id, and the test
asserts the main event loop stays responsive *while* the work is running — with
the work on the loop, the fake's sleep blocks everything and the ticker
assertion fails deterministically.
"""
import asyncio
import io
import threading
import time
from unittest.mock import MagicMock

import pytest
from fastapi import UploadFile
from fastapi.responses import FileResponse

from app.api.routes import layer as layer_mod
from app.api.routes import map as map_mod
from app.api.routes import report as report_mod
from app.api.routes import upload as upload_mod
from app.services.session_data_protocol import SessionRefDataResult

_main_thread = threading.get_ident()

_VALID_SID = "session-aaaaaaaaaaaaaaaa"


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


def _big_fc(n: int = 2500) -> dict:
    """n-feature FeatureCollection, large enough to hit the chunked encoder."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [116.0 + i * 0.001, 39.9]},
                "properties": {"id": i, "name": f"p{i}"},
            }
            for i in range(n)
        ],
    }


# ─── #590 Site 1: /layers/data response serialization ────────────────────────


@pytest.mark.asyncio
async def test_layer_data_serialization_batched_off_loop(monkeypatch):
    """The data endpoint must serialize the payload in worker-thread batches
    (each C-encoder call holds the GIL a few ms), not as one inline encode on
    the event loop, and must still return 200 application/json."""
    from app.lib import geojson_serializer

    observed = {}
    real_encode_batch = geojson_serializer._encode_batch

    def _slow_batch(elements, pad):
        observed["thread"] = threading.get_ident()
        time.sleep(0.35)  # simulated multi-MB batch encode
        return real_encode_batch(elements, pad)

    monkeypatch.setattr(geojson_serializer, "_encode_batch", _slow_batch)

    class _FakeSDM:
        async def get_ref_data(self, session_id, ref_id, owner_token=None):
            return SessionRefDataResult(success=True, data=_big_fc(2500))

    monkeypatch.setattr(layer_mod, "session_data_manager", _FakeSDM())

    resp = await _assert_loop_responsive_while(
        lambda: layer_mod.get_session_layer_data(
            ref_id="ref-big", session_id=_VALID_SID, owner_token=None,
            _conv=MagicMock(),
        )
    )
    assert observed["thread"] != _main_thread, "serialization ran on the event loop thread"
    assert resp.media_type == "application/json"
    assert b'"FeatureCollection"' in resp.body


# ─── #590 Site 2: descriptor fallback (route-level scan offload) ─────────────


@pytest.mark.asyncio
async def test_layer_descriptor_fallback_scan_off_loop(monkeypatch):
    """Pre-V3 ref fallback: the full-feature scan + bbox compute must run in a
    worker thread, not inline on the event loop."""
    from app.tools import _utils as tools_utils

    observed = {}
    real_bbox = tools_utils._feature_collection_bbox

    def _slow_bbox(fc, max_features=5000):
        observed["thread"] = threading.get_ident()
        time.sleep(0.8)
        return real_bbox(fc, max_features)

    monkeypatch.setattr(tools_utils, "_feature_collection_bbox", _slow_bbox)

    class _FakeSDM:
        async def get_ref_descriptor_authorized(self, session_id, ref_id, owner_token=None):
            return SessionRefDataResult(
                success=False, error_type="NotFound", error="no descriptor"
            )

        async def get_ref_data(self, session_id, ref_id, owner_token=None):
            return SessionRefDataResult(success=True, data=_big_fc(2500))

    monkeypatch.setattr(layer_mod, "session_data_manager", _FakeSDM())

    resp = await _assert_loop_responsive_while(
        lambda: layer_mod.get_layer_descriptor(
            ref_id="ref-old", session_id=_VALID_SID, owner_token=None,
            _conv=MagicMock(),
        )
    )
    assert observed["thread"] != _main_thread, "descriptor fallback scan ran on the event loop thread"
    assert resp["feature_count"] == 2500
    assert resp["point_count"] == 2500
    assert resp["geometry_types"] == ["Point"]


# ─── #590 Site 3: Redis descriptor fallback json.loads offload ───────────────


@pytest.mark.asyncio
async def test_redis_descriptor_fallback_loads_off_loop(monkeypatch):
    """Redis backend: when the meta/descriptor key is missing the fallback reads
    the full payload and json.loads it — the loads must run in a worker thread."""
    import json

    from app.services import session_data_redis as sdr

    observed = {}
    real_loads = json.loads

    def _slow_loads(*a, **kw):
        observed["thread"] = threading.get_ident()
        time.sleep(0.8)
        return real_loads(*a, **kw)

    monkeypatch.setattr(sdr.json, "loads", _slow_loads)  # global json.loads (427 precedent)

    payload = json.dumps(_big_fc(2500)).encode("utf-8")

    class _FakeRedis:
        def __init__(self):
            self.sets = []

        async def get(self, key):
            # descriptor key contains ":meta:", data key contains ":data:"
            if ":meta:" in str(key):
                return None
            return payload

        async def set(self, *a, **kw):
            self.sets.append((a, kw))
            return True

    store = sdr.RedisSessionStore(redis_url="redis://unused", redis=_FakeRedis())

    result = await _assert_loop_responsive_while(
        lambda: store.get_ref_descriptor(_VALID_SID, "ref-big")
    )
    assert observed["thread"] != _main_thread, "json.loads ran on the event loop thread"
    assert result is not None
    assert result["feature_count"] == 2500


# ─── #592 Site 1: upload temp write off the loop ─────────────────────────────


def _fake_upload_meta(**overrides) -> dict:
    meta = {
        "output_path": "data/uploads/x/test.geojson",
        "file_type": "vector",
        "format": "geojson",
        "crs": "EPSG:4326",
        "geometry_type": "Point",
        "feature_count": 1,
        "bbox": [116.0, 39.9, 116.1, 40.0],
    }
    meta.update(overrides)
    return meta


class _FakeUploadDB:
    """Minimal async_db_session double (session_id=None → skip conv check)."""

    def __init__(self):
        self.record = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def add(self, record):
        self.record = record

    async def flush(self):
        pass

    async def refresh(self, record):
        record.id = 1


@pytest.mark.asyncio
async def test_upload_temp_write_off_loop(monkeypatch, tmp_path):
    """upload_files must write the (≤200 MB) temp file in a worker thread."""
    observed = {}
    real_write = upload_mod._write_upload_bytes

    def _slow_write(path, content):
        observed["thread"] = threading.get_ident()
        time.sleep(0.8)
        real_write(path, content)

    monkeypatch.setattr(upload_mod, "_write_upload_bytes", _slow_write)

    def _fake_get_upload_dir(data_dir, upload_id):
        d = tmp_path / upload_id
        d.mkdir(parents=True, exist_ok=True)  # 真实实现 eager 建目录
        return d

    monkeypatch.setattr(upload_mod, "get_upload_dir", _fake_get_upload_dir)
    monkeypatch.setattr(
        upload_mod, "parse_vector", lambda path, upload_dir, upload_id: _fake_upload_meta()
    )
    monkeypatch.setattr(upload_mod, "save_meta", lambda *a, **k: None)
    monkeypatch.setattr(upload_mod, "async_db_session", _FakeUploadDB)

    file = UploadFile(file=io.BytesIO(b'{"type":"Point"}'), filename="test.geojson")
    try:
        res = await _assert_loop_responsive_while(
            lambda: upload_mod.upload_files(
                files=[file], session_id=None, owner_token=None, _user={"user_id": "u1"}
            )
        )
        assert observed["thread"] != _main_thread, "file write ran on the event loop thread"
        assert res.id == 1
        assert res.original_name == "test.geojson"
    finally:
        await file.close()


# ─── #592 Site 2: map export temp write + replace off the loop ───────────────


@pytest.mark.asyncio
async def test_map_export_upload_persist_off_loop(monkeypatch, tmp_path):
    """upload_map_export must persist the (≤50 MB) export in a worker thread."""
    observed = {}
    real_persist = map_mod._persist_export_file

    def _slow_persist(filename, content, ext):
        observed["thread"] = threading.get_ident()
        time.sleep(0.8)
        real_persist(filename, content, ext)

    monkeypatch.setattr(map_mod, "_persist_export_file", _slow_persist)
    monkeypatch.setattr(map_mod, "EXPORT_DIR", str(tmp_path))
    monkeypatch.setattr(map_mod, "_set_export_owner", lambda *a, **k: None)

    file = UploadFile(file=io.BytesIO(b"pngbytes"), filename="map.png")
    try:
        res = await _assert_loop_responsive_while(
            lambda: map_mod.upload_map_export(file, title="t", _user={"user_id": "u1"})
        )
        assert observed["thread"] != _main_thread, "export write ran on the event loop thread"
        assert res["success"] is True
        assert (tmp_path / res["filename"]).exists()
    finally:
        await file.close()


# ─── #592 Site 3: shared report HTML view streams via FileResponse ───────────


@pytest.mark.asyncio
async def test_shared_report_html_view_returns_file_response(monkeypatch, tmp_path):
    """view_shared_report must not open().read() the whole HTML on the loop —
    it returns FileResponse (worker-thread streaming), inline rendering kept."""
    from app.models.report import Report

    html = tmp_path / "report.html"
    html.write_text("<html><body>hello</body></html>", encoding="utf-8")
    report = Report(
        id="rep-000000000000000000000000000001",
        session_id="sess-1",
        format="html",
        status="completed",
        file_path=str(html),
    )
    monkeypatch.setattr(report_mod, "REPORT_DIR", str(tmp_path))

    class _FakeDB:
        async def execute(self, *a, **k):
            result = MagicMock()
            result.scalar_one_or_none.return_value = report
            return result

    resp = await report_mod.view_shared_report("share-code", db=_FakeDB())
    # FileResponse 在迭代响应时才分块读取文件（anyio 线程池）—— 路由本身不再
    # 同步 read 整个 HTML（#592）。
    assert isinstance(resp, FileResponse)
    assert resp.media_type == "text/html"