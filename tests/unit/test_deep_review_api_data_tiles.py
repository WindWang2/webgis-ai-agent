"""Deep-review API/DATA batch — MVT tile cache regressions.

API-01：租户鉴权必须在缓存查找之前（跨租户缓存命中不得返回字节）。
API-04：缓存键含 dataset fingerprint + 解析后的 org/owner；catalog sync
        fingerprint 变化时经 ``_DF_TILE_CACHE.invalidate_item`` 失效旧瓦片。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import data_fabric as df_routes
from app.core.database import Base, Engine, SessionLocal
from app.models.data_fabric import CatalogItemModel, DataSourceModel
from app.services.data_fabric.adapters.postgis_adapter import PostGISAdapter

_ITEM_ID = "cat_dr_tiles_layer"
_SOURCE_ID = "ds_dr_tiles"


def _app() -> FastAPI:
    application = FastAPI()
    application.include_router(df_routes.router, prefix="/api/v1")
    return application


def _client(user_id: str) -> TestClient:
    application = _app()
    application.dependency_overrides[df_routes.get_current_user] = (
        lambda: {"user_id": user_id}
    )
    return TestClient(application)


@pytest.fixture(autouse=True)
def _seed_tile_catalog():
    Base.metadata.create_all(bind=Engine)
    with SessionLocal() as db:
        db.query(CatalogItemModel).filter(CatalogItemModel.id == _ITEM_ID).delete()
        db.query(DataSourceModel).filter(DataSourceModel.id == _SOURCE_ID).delete()
        db.add(DataSourceModel(
            id=_SOURCE_ID, org_id=None, owner_id="tile-owner-a",
            name="tile source", source_type="postgis",
            endpoint_url="postgresql://example/db",
            connection_profile={}, capabilities_json=[],
        ))
        db.add(CatalogItemModel(
            id=_ITEM_ID, source_id=_SOURCE_ID, name="public.tile_layer",
            title="tile layer", geometry_type="Point", feature_type="vector",
            crs="EPSG:4326", bbox_json=None, tags_json=[],
            descriptor_json={}, meta_profile_json={},
            fingerprint="fp1", availability="available",
        ))
        db.commit()
    df_routes._DF_TILE_CACHE.invalidate_item(_ITEM_ID)
    yield


def _count_tile_calls(monkeypatch) -> dict:
    calls = {"n": 0}

    def _fake_tile(self, dataset, z, x, y, timeout_s=30.0):
        calls["n"] += 1
        return b"\x1a\x02tilebytes"

    monkeypatch.setattr(PostGISAdapter, "serve_mvt_tile", _fake_tile)
    return calls


def test_cache_hit_never_skips_cross_tenant_authz(monkeypatch):
    calls = _count_tile_calls(monkeypatch)
    url = f"/api/v1/data-fabric/catalog/{_ITEM_ID}/tiles/5/1/0.pbf"

    owner = _client("tile-owner-a")
    r1 = owner.get(url)
    assert r1.status_code == 200, r1.text
    assert calls["n"] == 1, "首次请求应构建瓦片"

    # 命中缓存的是 owner-a；owner-b 必须仍被租户门拦下（回归：旧键无租户，
    # 缓存命中在鉴权**之前**返回字节 → 跨租户读取）。
    stranger = _client("tile-owner-b")
    r2 = stranger.get(url)
    assert r2.status_code == 404, r2.text
    assert calls["n"] == 1, "被拒请求不得触发第二次构建"


def test_fingerprint_change_produces_new_tile(monkeypatch):
    calls = _count_tile_calls(monkeypatch)
    url = f"/api/v1/data-fabric/catalog/{_ITEM_ID}/tiles/5/1/0.pbf"

    owner = _client("tile-owner-a")
    r1 = owner.get(url)
    assert r1.status_code == 200 and calls["n"] == 1
    assert r1.headers["x-dataset-fingerprint"] == "fp1"[:16]

    with SessionLocal() as db:
        row = db.get(CatalogItemModel, _ITEM_ID)
        setattr(row, "fingerprint", "fp2")
        db.commit()

    r2 = owner.get(url)
    assert r2.status_code == 200, r2.text
    assert calls["n"] == 2, "fingerprint 变化后不得复用旧瓦片"
    assert r2.headers["x-dataset-fingerprint"] == "fp2"[:16]


def test_sync_catalog_invalidates_tiles_on_fingerprint_change(monkeypatch):
    """API-04：死代码 invalidate_item 必须接入 catalog sync。"""
    from app.schemas.data_fabric_schema import DatasetDescriptor
    from app.services.data_fabric.manager import DataFabricManager

    df_routes._DF_TILE_CACHE.put(
        (_ITEM_ID, "org:None|owner:tile-owner-a", "fp1", 5, 1, 0),
        (b"stale", "fp1"),
    )
    assert df_routes._DF_TILE_CACHE.get(
        (_ITEM_ID, "org:None|owner:tile-owner-a", "fp1", 5, 1, 0)
    ) is not None

    class _FakeAdapter:
        def list_datasets(self):
            return [{"id": "tile_layer", "title": "tile layer"}]

        def describe(self, name):
            return DatasetDescriptor(id=name, title="tile layer")

    ds_model = MagicMock()
    ds_model.id = _SOURCE_ID
    ds_model.name = "tile source"
    ds_model.source_type = "postgis"
    ds_model.endpoint_url = "postgresql://example/db"
    ds_model.connection_profile = {"options": {}, "allow_private": False}
    ds_model.org_id = None
    ds_model.owner_id = "tile-owner-a"

    existing = MagicMock()
    existing.id = _ITEM_ID
    existing.source_id = _SOURCE_ID
    existing.name = "tile_layer"
    existing.fingerprint = "fp1"
    existing.geometry_type = "Point"
    existing.availability = "available"
    for attr in ("title", "description", "feature_type", "crs",
                 "bbox_json", "descriptor_json", "meta_profile_json",
                 "updated_at"):
        setattr(existing, attr, None)

    db = MagicMock()
    ds_q = MagicMock()
    ds_q.filter.return_value.first.return_value = ds_model
    cat_q = MagicMock()
    cat_q.filter.return_value.all.return_value = [existing]
    db.query.side_effect = [ds_q, cat_q]

    monkeypatch.setattr(
        DataFabricManager, "get_adapter",
        staticmethod(lambda profile: _FakeAdapter()),
    )
    DataFabricManager.sync_catalog(db, _SOURCE_ID)

    assert df_routes._DF_TILE_CACHE.get(
        (_ITEM_ID, "org:None|owner:tile-owner-a", "fp1", 5, 1, 0)
    ) is None, "fingerprint 变化后 sync 必须失效旧键"
