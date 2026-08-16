"""Regression tests for #425: Data Fabric routes must not run blocking sync
remote I/O on the event loop (sibling of the #386 offload sweep).

The five routes (create/probe/sync/preview/query) call sync manager methods
that drive ``requests.Session`` adapters with 5-15s timeouts (and, for sync,
a full catalog sync whose ThreadPoolExecutor shutdown waits on the calling
thread). A slow or hung remote source therefore froze the single uvicorn event
loop for the full timeout — every concurrent SSE chat stream stalled in
lockstep. Only ``materialize`` was async-safe.

Each test fakes the slow work with a sync ``time.sleep`` that records the
thread id it ran on, and asserts the main event loop stays responsive WHILE
the work is running — with the work on the loop, the fake's sleep blocks
everything and the ticker assertion fails deterministically (same technique as
tests/test_event_loop_offload_386.py).

A second defect on the same path is covered here: the sync
``query_catalog_item`` (preview/query routes) never enforced
``enforce_result_bounds`` — only materialize did — so preview/query responses
were unbounded.

Run cost: ~1s per offload test (0.8s fake sleep), no network.
"""
import asyncio
import threading
import time
from unittest.mock import MagicMock

import pytest

from app.schemas.data_fabric_schema import DataFabricHealth, QueryResult

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


# ─── shared fakes ────────────────────────────────────────────────────────────


def _fake_source_row():
    s = MagicMock()
    s.id = "ds_test"
    s.name = "Test Source"
    s.source_type = "ogc_api"
    s.endpoint_url = "https://example.com/ogc"
    s.connection_profile = {"options": {}, "allow_private": False}
    s.org_id = None
    s.owner_id = "u1"
    s.status = "unknown"
    s.capabilities_json = []
    return s


def _fake_db_for_source():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = _fake_source_row()
    return db


def _fake_db_for_catalog_item():
    """db whose query() yields a catalog item then its (tenant-owned) source."""
    from app.models.data_fabric import CatalogItemModel

    item = MagicMock()
    item.id = "cat_ds_test_layer"
    item.source_id = "ds_test"
    item.name = "layer"
    src = _fake_source_row()
    item.data_source = src

    db = MagicMock()

    def _q(model):
        m = MagicMock()
        m.filter.return_value.first.return_value = item if model is CatalogItemModel else src
        return m

    db.query.side_effect = _q
    return db


_USER = {"user_id": "u1", "org_id": None}


def _tiny_query_result(n_features=1):
    return QueryResult(
        dataset_id="cat_ds_test_layer",
        features=[
            {"type": "Feature", "geometry": None, "properties": {}} for _ in range(n_features)
        ],
        total_count=n_features,
    )


# ─── Site 1: create_data_source (probe + capabilities + full catalog sync) ──


@pytest.mark.asyncio
async def test_create_data_source_off_loop(monkeypatch):
    """create_data_source drives a remote probe AND an auto catalog sync —
    the whole manager call must run in a worker thread."""
    from app.api.routes import data_fabric as route_mod
    from app.services.data_fabric.manager import DataFabricManager

    observed = {}

    def _slow_create(**kwargs):
        observed["thread"] = threading.get_ident()
        time.sleep(0.8)
        return _fake_source_row()

    monkeypatch.setattr(DataFabricManager, "create_data_source", staticmethod(_slow_create))

    req = route_mod.CreateDataSourceRequest(
        name="Test OGC API Source",
        source_type="ogc_api",
        endpoint_url="https://example.com/ogc",
        options={},
    )
    res = await _assert_loop_responsive_while(
        lambda: route_mod.create_data_source(req, db=_fake_db_for_source(), user=dict(_USER))
    )
    assert observed["thread"] != _main_thread, "create_data_source ran on the event loop thread"
    assert res["success"] is True
    assert res["data_source"]["id"] == "ds_test"


# ─── Site 2: probe_data_source (sync requests probe, 5s timeout) ─────────────


@pytest.mark.asyncio
async def test_probe_data_source_off_loop(monkeypatch):
    from app.api.routes import data_fabric as route_mod
    from app.services.data_fabric.manager import DataFabricManager

    observed = {}

    def _slow_probe(profile):
        observed["thread"] = threading.get_ident()
        time.sleep(0.8)
        return DataFabricHealth(status="healthy", message="OK", latency_ms=10.0)

    monkeypatch.setattr(DataFabricManager, "probe_profile", staticmethod(_slow_probe))

    res = await _assert_loop_responsive_while(
        lambda: route_mod.probe_data_source("ds_test", db=_fake_db_for_source(), user=dict(_USER))
    )
    assert observed["thread"] != _main_thread, "probe ran on the event loop thread"
    assert res["status"] == "healthy"


# ─── Site 3: sync_data_source_catalog (full catalog sync) ────────────────────


@pytest.mark.asyncio
async def test_sync_data_source_catalog_off_loop(monkeypatch):
    """sync_catalog blocks for the entire catalog sync (list_datasets 10s
    timeout + describe pool shutdown(wait=True)) — must run off-loop."""
    from app.api.routes import data_fabric as route_mod
    from app.services.data_fabric.manager import DataFabricManager

    observed = {}

    def _slow_sync(db, source_id):
        observed["thread"] = threading.get_ident()
        time.sleep(0.8)
        return []

    monkeypatch.setattr(DataFabricManager, "sync_catalog", staticmethod(_slow_sync))

    res = await _assert_loop_responsive_while(
        lambda: route_mod.sync_data_source_catalog("ds_test", db=_fake_db_for_source(), user=dict(_USER))
    )
    assert observed["thread"] != _main_thread, "catalog sync ran on the event loop thread"
    assert res["synced_count"] == 0


# ─── Sites 4+5: preview / query (sync adapter.query with 10-30s timeouts) ────


def _patch_query_paths(monkeypatch, observed):
    """Patch BOTH query paths: the slow work itself (sync) and the async
    wrapper that must offload it. Pre-fix routes call the sync method on the
    loop (RED); post-fix routes await the async wrapper (GREEN)."""
    from app.services.data_fabric.manager import DataFabricManager

    def _slow_query(db, item_id, spec):
        observed["thread"] = threading.get_ident()
        time.sleep(0.8)
        return _tiny_query_result(2)

    async def _async_query(cls, db, item_id, spec, cancel_token=None):
        return await asyncio.to_thread(_slow_query, db, item_id, spec)

    monkeypatch.setattr(DataFabricManager, "query_catalog_item", staticmethod(_slow_query))
    monkeypatch.setattr(
        DataFabricManager, "query_catalog_item_async", classmethod(_async_query)
    )


@pytest.mark.asyncio
async def test_preview_catalog_item_off_loop(monkeypatch):
    from app.api.routes import data_fabric as route_mod

    observed = {}
    _patch_query_paths(monkeypatch, observed)

    res = await _assert_loop_responsive_while(
        lambda: route_mod.preview_catalog_item(
            "cat_ds_test_layer", limit=10, db=_fake_db_for_catalog_item(), user=dict(_USER)
        )
    )
    assert observed["thread"] != _main_thread, "preview query ran on the event loop thread"
    assert len(res["features"]) == 2


@pytest.mark.asyncio
async def test_query_catalog_item_route_off_loop(monkeypatch):
    from app.api.routes import data_fabric as route_mod
    from app.schemas.data_fabric_schema import QuerySpec

    observed = {}
    _patch_query_paths(monkeypatch, observed)

    res = await _assert_loop_responsive_while(
        lambda: route_mod.query_catalog_item(
            "cat_ds_test_layer",
            QuerySpec(limit=10),
            db=_fake_db_for_catalog_item(),
            user=dict(_USER),
        )
    )
    assert observed["thread"] != _main_thread, "pushdown query ran on the event loop thread"
    assert res["total_count"] == 2


# ─── Secondary defect: result bounds on the preview/query path ───────────────


def _patched_adapter_query(n_features):
    from app.services.data_fabric.manager import DataFabricManager

    class _FakeAdapter:
        def query(self, name, spec):
            return _tiny_query_result(n_features)

        def health(self):
            return DataFabricHealth(status="healthy", message="ok")

    return staticmethod(lambda profile: _FakeAdapter()), DataFabricManager


def test_query_catalog_item_enforces_result_bounds(monkeypatch):
    """The sync query path (preview/query routes) must enforce
    enforce_result_bounds — only materialize did before (#425)."""
    from app.services.data_fabric.errors import ResultTooLargeError
    from app.services.data_fabric.limits import max_features
    from app.services.data_fabric.manager import DataFabricManager
    from app.schemas.data_fabric_schema import QuerySpec

    get_adapter, _ = _patched_adapter_query(max_features() + 1)
    monkeypatch.setattr(DataFabricManager, "get_adapter", get_adapter)

    with pytest.raises(ResultTooLargeError):
        DataFabricManager.query_catalog_item(_fake_db_for_catalog_item(), "cat_ds_test_layer", QuerySpec(limit=10))


def test_query_catalog_item_allows_bounded_results(monkeypatch):
    from app.services.data_fabric.manager import DataFabricManager
    from app.schemas.data_fabric_schema import QuerySpec

    get_adapter, _ = _patched_adapter_query(3)
    monkeypatch.setattr(DataFabricManager, "get_adapter", get_adapter)

    res = DataFabricManager.query_catalog_item(
        _fake_db_for_catalog_item(), "cat_ds_test_layer", QuerySpec(limit=10)
    )
    assert len(res.features) == 3


def test_query_catalog_item_empty_result_passes(monkeypatch):
    """Adversarial: an empty (failed/empty remote) result must pass through —
    bounds guard rejects oversized payloads, never invents data."""
    from app.services.data_fabric.manager import DataFabricManager
    from app.schemas.data_fabric_schema import QuerySpec

    get_adapter, _ = _patched_adapter_query(0)
    monkeypatch.setattr(DataFabricManager, "get_adapter", get_adapter)

    res = DataFabricManager.query_catalog_item(
        _fake_db_for_catalog_item(), "cat_ds_test_layer", QuerySpec(limit=10)
    )
    assert res.features == []


@pytest.mark.asyncio
async def test_query_catalog_item_async_enforces_result_bounds(monkeypatch):
    """The async path (used by the preview/query routes after the offload
    fix, and by materialize) must enforce bounds too."""
    from app.services.data_fabric.errors import ResultTooLargeError
    from app.services.data_fabric.limits import max_features
    from app.services.data_fabric.manager import DataFabricManager
    from app.schemas.data_fabric_schema import QuerySpec

    get_adapter, _ = _patched_adapter_query(max_features() + 1)
    monkeypatch.setattr(DataFabricManager, "get_adapter", get_adapter)

    with pytest.raises(ResultTooLargeError):
        await DataFabricManager.query_catalog_item_async(
            _fake_db_for_catalog_item(), "cat_ds_test_layer", QuerySpec(limit=10)
        )


@pytest.mark.asyncio
async def test_preview_route_maps_result_too_large_to_413(monkeypatch):
    """Adversarial: an oversized remote result on the preview path surfaces as
    an actionable 413 (same contract as materialize), not a raw 400."""
    from fastapi.responses import JSONResponse

    from app.api.routes import data_fabric as route_mod
    from app.services.data_fabric.errors import ResultTooLargeError
    from app.services.data_fabric.manager import DataFabricManager

    async def _raise_async(cls, db, item_id, spec, cancel_token=None):
        raise ResultTooLargeError("query returned 999999 features (limit 1000)")

    def _inert_sync(db, item_id, spec):
        return _tiny_query_result(0)

    monkeypatch.setattr(
        DataFabricManager, "query_catalog_item_async", classmethod(_raise_async)
    )
    monkeypatch.setattr(DataFabricManager, "query_catalog_item", staticmethod(_inert_sync))
    res = await route_mod.preview_catalog_item(
        "cat_ds_test_layer", limit=10, db=_fake_db_for_catalog_item(), user=dict(_USER)
    )
    assert isinstance(res, JSONResponse)
    assert res.status_code == 413


@pytest.mark.asyncio
async def test_query_route_maps_result_too_large_to_413(monkeypatch):
    from fastapi.responses import JSONResponse

    from app.api.routes import data_fabric as route_mod
    from app.schemas.data_fabric_schema import QuerySpec
    from app.services.data_fabric.errors import ResultTooLargeError
    from app.services.data_fabric.manager import DataFabricManager

    async def _raise_async(cls, db, item_id, spec, cancel_token=None):
        raise ResultTooLargeError("query result ~99999999 bytes exceeds limit")

    def _inert_sync(db, item_id, spec):
        return _tiny_query_result(0)

    monkeypatch.setattr(
        DataFabricManager, "query_catalog_item_async", classmethod(_raise_async)
    )
    monkeypatch.setattr(DataFabricManager, "query_catalog_item", staticmethod(_inert_sync))
    res = await route_mod.query_catalog_item(
        "cat_ds_test_layer",
        QuerySpec(limit=10),
        db=_fake_db_for_catalog_item(),
        user=dict(_USER),
    )
    assert isinstance(res, JSONResponse)
    assert res.status_code == 413
