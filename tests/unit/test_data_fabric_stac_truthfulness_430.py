"""Regression tests for #430: the STAC adapter must fail truthfully.

Contract (mirrors the adapter's own query() path and the ogc/wfs/arcgis
adapters): when a real endpoint is configured and it fails (unreachable,
non-200, empty catalog, missing child links, or any exception), discovery
returns an EMPTY dataset list and describe returns an honest stub carrying a
typed error — synthetic "landsat-8-c2-l2"/"cop-dem-30m" fixtures must never be
registered as if they were the remote source's real datasets, and feature
counts must never be fabricated. The synthetic fixtures survive ONLY on the
explicit no-endpoint demo path, clearly labeled as such.

These tests are RED on the pre-fix adapter and GREEN after the fix.
"""
from unittest.mock import MagicMock

import pytest
import requests

from app.schemas.data_fabric_schema import ConnectionProfile, DatasetDescriptor
from app.services.data_fabric.adapters.stac_adapter import (
    STACAdapter,
    SYNTHETIC_STAC_FIXTURES,
)
from app.services.data_fabric.manager import DataFabricManager
from app.services.data_fabric.metadata_cache import _describe_cache

_ENDPOINT = "https://stac.example.com/v1"

SYNTHETIC_IDS = set(SYNTHETIC_STAC_FIXTURES.keys())


@pytest.fixture(autouse=True)
def _clear_describe_cache():
    _describe_cache.invalidate()
    yield
    _describe_cache.invalidate()


class _FakeResp:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}

    def json(self):
        return self._json


def _adapter(endpoint: str = _ENDPOINT) -> STACAdapter:
    return STACAdapter(ConnectionProfile(
        source_type="stac",
        endpoint_url=endpoint,
        name="test_stac",
    ))


def _patch_session_get(monkeypatch, adapter: STACAdapter, handler) -> None:
    """把假 HTTP 处理器挂到适配器的 SSRF 安全会话上。

    适配器只经 ``self.session.get``（make_safe_session 产物）发请求——
    旧写法 patch 全局 ``requests.get`` 从未被命中，用例实际打到真实网络
    路径（#1011：离线机器 DNS 失败 → 假红）。
    """
    monkeypatch.setattr(adapter.session, "get", handler)


# ---------------------------------------------------------------------------
# list_datasets: configured endpoint failure → [] (never synthetic ids)
# ---------------------------------------------------------------------------

def test_list_datasets_unreachable_returns_empty(monkeypatch):
    def _conn_error(url, **kwargs):
        raise requests.exceptions.ConnectionError("no route to host")

    adapter = _adapter()
    _patch_session_get(monkeypatch, adapter, _conn_error)
    datasets = adapter.list_datasets()
    assert datasets == []
    assert not SYNTHETIC_IDS & {d.get("id") for d in datasets}


def test_list_datasets_server_error_returns_empty(monkeypatch):
    adapter = _adapter()
    _patch_session_get(monkeypatch, adapter, lambda url, **kw: _FakeResp(status_code=503))
    datasets = adapter.list_datasets()
    assert datasets == []


def test_list_datasets_non_json_200_returns_empty(monkeypatch):
    def _bad_json(url, **kwargs):
        resp = _FakeResp(status_code=200)
        resp.json = lambda: (_ for _ in ()).throw(ValueError("not json"))
        return resp

    adapter = _adapter()
    _patch_session_get(monkeypatch, adapter, _bad_json)
    assert adapter.list_datasets() == []


def test_list_datasets_empty_catalog_without_child_links_returns_empty(monkeypatch):
    """/collections 200-but-empty and a root catalog with NO child links → []
    (the old code fell back to synthetic fixtures here)."""
    def _router(url, **kwargs):
        if url.rstrip("/").endswith("/collections"):
            return _FakeResp(200, {"collections": []})
        return _FakeResp(200, {"links": [{"rel": "self", "href": "https://stac.example.com/v1"}]})

    adapter = _adapter()
    _patch_session_get(monkeypatch, adapter, _router)
    datasets = adapter.list_datasets()
    assert datasets == []
    assert not SYNTHETIC_IDS & {d.get("id") for d in datasets}


def test_list_datasets_success_path_untouched(monkeypatch):
    """/collections 200 with real collections still returns them."""
    def _router(url, **kwargs):
        assert url.rstrip("/").endswith("/collections"), "success path must not probe the root catalog"
        return _FakeResp(200, {"collections": [
            {"id": "sentinel-2-l2a", "title": "Sentinel-2 L2A", "description": "d", "license": "proprietary"},
        ]})

    adapter = _adapter()
    _patch_session_get(monkeypatch, adapter, _router)
    datasets = adapter.list_datasets()
    assert [d["id"] for d in datasets] == ["sentinel-2-l2a"]
    assert not SYNTHETIC_IDS & {d.get("id") for d in datasets}


def test_list_datasets_no_endpoint_demo_mode_labeled_synthetic():
    """Explicit no-endpoint demo path keeps the fixtures AND labels them so no
    caller can mistake demo data for remote data."""
    datasets = _adapter(endpoint="").list_datasets()
    ids = {d["id"] for d in datasets}
    assert ids == SYNTHETIC_IDS
    for entry in datasets:
        assert entry.get("source") == "synthetic-demo"


# ---------------------------------------------------------------------------
# describe: no fixture fallback, no fabricated feature counts
# ---------------------------------------------------------------------------

def test_describe_endpoint_failure_returns_honest_stub(monkeypatch):
    def _conn_error(url, **kwargs):
        raise requests.exceptions.ConnectionError("no route to host")

    adapter = _adapter()
    _patch_session_get(monkeypatch, adapter, _conn_error)
    desc = adapter.describe("some-real-collection")
    assert isinstance(desc, DatasetDescriptor)
    assert desc.id == "some-real-collection"
    # Never a fabricated count and never the fixture payload.
    assert desc.feature_count is None
    assert desc.title != SYNTHETIC_STAC_FIXTURES["landsat-8-c2-l2"]["title"]
    assert desc.metadata.get("error")


def test_describe_endpoint_error_status_is_typed(monkeypatch):
    adapter = _adapter()
    _patch_session_get(monkeypatch, adapter, lambda url, **kw: _FakeResp(status_code=503))
    desc = adapter.describe("some-real-collection")
    assert desc.feature_count is None
    assert desc.metadata.get("error_type") == "SOURCE_BAD_RESPONSE"


def test_describe_synthetic_id_with_configured_endpoint_is_not_fixture(monkeypatch):
    """A REAL collection whose id collides with a synthetic fixture id must be
    served from the endpoint, never from the built-in fixture."""
    def _router(url, **kwargs):
        return _FakeResp(200, {
            "id": "landsat-8-c2-l2",
            "title": "Real Landsat",
            "description": "from the real endpoint",
            "license": "PDDL",
        })

    adapter = _adapter()
    _patch_session_get(monkeypatch, adapter, _router)
    desc = adapter.describe("landsat-8-c2-l2")
    assert desc.title == "Real Landsat"
    assert desc.feature_count is None  # not the fabricated 10000


def test_describe_success_does_not_fabricate_feature_count(monkeypatch):
    adapter = _adapter()
    _patch_session_get(monkeypatch, adapter, lambda url, **kw: _FakeResp(200, {
        "id": "sentinel-2-l2a", "title": "Sentinel-2 L2A",
    }))
    desc = adapter.describe("sentinel-2-l2a")
    assert desc.title == "Sentinel-2 L2A"
    assert desc.feature_count is None


def test_describe_reports_item_count_when_source_provides_it(monkeypatch):
    adapter = _adapter()
    _patch_session_get(monkeypatch, adapter, lambda url, **kw: _FakeResp(200, {
        "id": "sentinel-2-l2a", "title": "Sentinel-2 L2A", "item_count": 4321,
    }))
    desc = adapter.describe("sentinel-2-l2a")
    assert desc.feature_count == 4321


def test_describe_no_endpoint_demo_mode_labeled(monkeypatch):
    desc = _adapter(endpoint="").describe("landsat-8-c2-l2")
    assert desc.metadata.get("source") == "synthetic-demo"
    # Demo fixtures may carry their fixture counts, but ONLY on this labeled path.
    assert desc.feature_count == SYNTHETIC_STAC_FIXTURES["landsat-8-c2-l2"]["item_count"]


# ---------------------------------------------------------------------------
# sync_catalog level: failing endpoint registers ZERO catalog items
# ---------------------------------------------------------------------------

def _mock_db(ds_model):
    db = MagicMock()
    ds_q = MagicMock()
    ds_q.filter.return_value.first.return_value = ds_model
    cat_q = MagicMock()
    cat_q.filter.return_value.all.return_value = []
    db.query.side_effect = [ds_q, cat_q]
    return db


def test_sync_catalog_with_failing_stac_endpoint_registers_nothing(monkeypatch):
    def _conn_error(url, **kwargs):
        raise requests.exceptions.ConnectionError("no route to host")

    adapter = _adapter()
    _patch_session_get(monkeypatch, adapter, _conn_error)
    ds = MagicMock()
    ds.id = "src_stac"
    ds.name = "stac"
    ds.source_type = "stac"
    ds.endpoint_url = _ENDPOINT
    ds.connection_profile = {"options": {}, "allow_private": False}
    db = _mock_db(ds)
    monkeypatch.setattr(
        DataFabricManager, "get_adapter", staticmethod(lambda profile: _adapter())
    )

    items = DataFabricManager.sync_catalog(db, "src_stac")

    # Zero datasets registered; the trailing commit is an empty transaction.
    assert items == []
    db.add.assert_not_called()
