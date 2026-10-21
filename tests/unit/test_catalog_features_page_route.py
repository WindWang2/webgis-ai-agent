"""GET /data-fabric/catalog/{item_id}/features（W12 keyset 分页）路由契约。

覆盖：keyset 游标透传（page_kind=cursor + cursor extras 进 QuerySpec）、
响应形状（next_cursor/has_more/fingerprint）、诚实降级（源不支持 →
has_more=False）、InvalidQueryError → 400（绝不静默错页）、鉴权先于查询。
"""
from unittest.mock import patch
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes import data_fabric as df_routes
from app.core.database import Base, Engine, SessionLocal
from app.models.data_fabric import CatalogItemModel, DataSourceModel
from app.models.db_model import User
from app.schemas.data_fabric_schema import QueryResult
from app.services.data_fabric.errors import InvalidQueryError
from app.services.data_fabric.manager import DataFabricManager

_ITEM_ID = "cat_page_layer"
_SOURCE_ID = "ds_page_src"
_OWNER_ID = "page-owner"


@pytest.fixture(autouse=True)
def _seed_page_catalog():
    Base.metadata.create_all(bind=Engine)
    with SessionLocal() as db:
        # CI 的 DATABASE_URL 指向 Postgres：data_sources.owner_id 有 FK 约束
        # （users.id），本地 SQLite 不强制、CI 全量跑必炸 ForeignKeyViolation。
        # 幂等补种 owner 用户（同 test_data_fabric_routes 的 seed 模式）。
        if db.get(User, _OWNER_ID) is None:
            db.add(User(
                id=_OWNER_ID,
                username="page-owner",
                email="page-owner@example.com",
                password_hash="scrypt$16384$8$1$00$00",
                role="editor",
                is_active=True,
                token_version=0,
                created_at=datetime.now(timezone.utc),
            ))
            db.commit()
        db.query(CatalogItemModel).filter(CatalogItemModel.id == _ITEM_ID).delete()
        db.query(DataSourceModel).filter(DataSourceModel.id == _SOURCE_ID).delete()
        db.add(DataSourceModel(
            id=_SOURCE_ID, org_id=None, owner_id=_OWNER_ID,
            name="page source", source_type="postgis",
            endpoint_url="postgresql://example/db",
            connection_profile={}, capabilities_json=[],
        ))
        db.add(CatalogItemModel(
            id=_ITEM_ID, source_id=_SOURCE_ID, name="public.page_layer",
            title="page layer", geometry_type="Point", feature_type="vector",
            crs="EPSG:4326", bbox_json=None, tags_json=[],
            descriptor_json={}, meta_profile_json={},
            fingerprint="fp-page-1", availability="available",
        ))
        db.commit()
    yield


def _app() -> FastAPI:
    application = FastAPI()
    application.include_router(df_routes.router, prefix="/api/v1")
    return application


@pytest.fixture
async def client():
    application = _app()
    application.dependency_overrides[df_routes.get_current_user] = (
        lambda: {"user_id": "page-owner"}
    )
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _fake_query_result(features, next_cursor=None, has_more=False):
    return QueryResult(
        dataset_id=_ITEM_ID,
        features=features,
        returned_count=len(features),
        next_cursor=next_cursor,
        has_more=has_more,
        total_matching=None,
    )


def _install_manager(monkeypatch, result, capture=None):
    # #1753：patch 类而非单例实例 —— 对实例的 monkeypatch 在 undo 时会把继承来的
    # 原方法写回实例 __dict__（常驻阴影），之后任何对同属性的类级 patch 都被静默
    # 击败（test_event_loop_offload_425 的 preview 用例因此走到真实 OGC 网络路径，
    # 全量串行套件永久挂起）。classmethod 包装保持调用签名。
    async def _fake_query(cls, session, item_id, spec):
        if capture is not None:
            capture["spec"] = spec
        return result

    monkeypatch.setattr(
        DataFabricManager, "query_catalog_item_async", classmethod(_fake_query)
    )


@pytest.mark.asyncio
async def test_keyset_cursor_passthrough_and_response_shape(client, monkeypatch):
    capture = {}
    feats = [{"type": "Feature", "id": i, "geometry": {"type": "Point", "coordinates": [1, 2]},
              "properties": {"i": i}} for i in range(3)]
    _install_manager(monkeypatch, _fake_query_result(feats, next_cursor="cur-2", has_more=True), capture)
    with patch.object(df_routes, "_authorize_catalog_item", lambda *a, **k: None):
        r = await client.get(
            f"/api/v1/data-fabric/catalog/{_ITEM_ID}/features",
            params={"limit": 3, "cursor": "cur-1", "order_by": "i ASC", "fields": "i"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["returned_count"] == 3 and body["next_cursor"] == "cur-2" and body["has_more"] is True
    assert body["dataset_id"] == _ITEM_ID
    assert len(body["features"]) == 3
    # keyset 语义必须进 QuerySpec extras（V2 管线契约）
    spec = capture["spec"]
    assert spec.model_extra.get("page_kind") == "cursor"
    assert spec.model_extra.get("cursor") == "cur-1"
    assert spec.limit == 3
    assert spec.order_by == "i ASC"


@pytest.mark.asyncio
async def test_honest_degrade_when_source_lacks_pagination(client, monkeypatch):
    _install_manager(monkeypatch, _fake_query_result([{"type": "Feature", "id": 0, "geometry": None,
                                                      "properties": {}}], next_cursor=None, has_more=False))
    with patch.object(df_routes, "_authorize_catalog_item", lambda *a, **k: None):
        r = await client.get(f"/api/v1/data-fabric/catalog/{_ITEM_ID}/features", params={"limit": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["has_more"] is False and body["next_cursor"] is None, "不支持 keyset 的源必须诚实降级"


@pytest.mark.asyncio
async def test_invalid_keyset_maps_400(client, monkeypatch):
    async def _boom(cls, session, item_id, spec):
        raise InvalidQueryError("keyset cursor requires a uniform order key")

    # #1753：类级 patch（实例级 patch 的 undo 会留下常驻实例阴影，击败后续类级 patch）
    monkeypatch.setattr(DataFabricManager, "query_catalog_item_async", classmethod(_boom))
    with patch.object(df_routes, "_authorize_catalog_item", lambda *a, **k: None):
        r = await client.get(
            f"/api/v1/data-fabric/catalog/{_ITEM_ID}/features",
            params={"cursor": "cur-1"},
        )
    assert r.status_code == 400, "键集失效必须 400，绝不静默错页"
    assert r.json()["detail"]["error"] == "invalid_query"


@pytest.mark.asyncio
async def test_authz_runs_before_query(client, monkeypatch):
    calls = {"authz": 0, "query": 0}

    def _deny(*a, **k):
        calls["authz"] += 1
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="not found")

    async def _fake_query(cls, session, item_id, spec):
        calls["query"] += 1
        return _fake_query_result([])

    monkeypatch.setattr(df_routes, "_authorize_catalog_item", _deny)
    # #1753：类级 patch（实例级 patch 的 undo 会留下常驻实例阴影，击败后续类级 patch）
    monkeypatch.setattr(
        DataFabricManager, "query_catalog_item_async", classmethod(_fake_query)
    )
    r = await client.get(f"/api/v1/data-fabric/catalog/{_ITEM_ID}/features")
    assert r.status_code == 404
    assert calls["authz"] == 1 and calls["query"] == 0, "鉴权必须先于任何数据访问"


@pytest.mark.asyncio
async def test_limit_bounds_enforced(client):
    r = await client.get(
        f"/api/v1/data-fabric/catalog/{_ITEM_ID}/features", params={"limit": 5000}
    )
    assert r.status_code == 422, "页大小上限必须被参数校验拒绝"
