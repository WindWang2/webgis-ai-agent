"""GOAL-02 M2：OGC Tiles 代理路由 —— 鉴权/缓存/ETag/typed 负例（route E2E）。

真实服务面：注册 ogc_api_tiles 数据源 → catalog item →
``/data-fabric/catalog/{id}/ogc-tiles/{z}/{x}/{y}?session_id=...``。
与 PMTiles 路由测试（test_pmtiles_tile_route）同纪律：鉴权先于缓存
（API-01）、fingerprint 版本化 ETag、空瓦片 204、错误 typed 不吞。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import data_fabric as df_routes
from app.core.database import Base, Engine, SessionLocal
from app.models.data_fabric import CatalogItemModel, DataSourceModel
from app.models.db_model import User
from app.services.auth_history_bridge import require_owned_session
from app.services.data_fabric.tile_cache import TILE_CACHE as df_tile_cache
from tests.data.ogc_service_fixtures import MEDIA_MVT, TILE_MVT_BYTES, VALID_TILE

_ITEM_ID = "admin:WebMercatorQuad"
_SOURCE_ID = "ds_goal02_ogc_tiles"
_OWNER_ID = "goal02-ogc-owner"
_SESSION_ID = "sess-goal02-tiles"


def _app(user_id: str = _OWNER_ID) -> FastAPI:
    application = FastAPI()
    application.include_router(df_routes.router, prefix="/api/v1")
    application.dependency_overrides[require_owned_session] = lambda: None
    # 路由声明的是 get_current_user_optional（瓦片浏览器路径匿名友好）——
    # 覆盖 optional 变体；跨租户负例把 user 换成他人验证租户门。
    application.dependency_overrides[df_routes.get_current_user_optional] = (
        lambda: {"user_id": user_id}
    )
    return application


def _client(user_id: str = _OWNER_ID) -> TestClient:
    return TestClient(_app(user_id))


def _url(item_id: str, z: int, x: int, y: int) -> str:
    return (
        f"/api/v1/data-fabric/catalog/{item_id}/ogc-tiles/{z}/{x}/{y}"
        f"?session_id={_SESSION_ID}"
    )


@pytest.fixture(autouse=True)
def patched_server(monkeypatch):
    """fake OGC Tiles 服务 + 受治理 adapter 的会话面接线 + catalog 种子。

    顺序（关键）：先建 server（endpoint URL 才能进 DataSourceModel 行），
    再挂 adapter 模块的 make_safe_session 替换，最后 seed catalog。
    """
    from tests.data.ogc_service_fixtures import OgcServiceFakeServer
    from tests.fixtures.data_fabric.fake_server import session_with_fake

    server = OgcServiceFakeServer()
    fake_url = server.add_ogc_tiles(collection_ids=("admin",))
    session = session_with_fake(tuple(server.routes))

    import app.services.data_fabric.adapters.ogc_tiles_adapter as tiles_mod
    monkeypatch.setattr(tiles_mod, "make_safe_session", lambda *a, **k: session)

    Base.metadata.create_all(bind=Engine)
    with SessionLocal() as db:
        if db.get(User, _OWNER_ID) is None:
            db.add(User(
                id=_OWNER_ID,
                username="goal02-ogc-owner",
                email="goal02-ogc-owner@example.com",
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
            name="ogc tiles source", source_type="ogc_api_tiles",
            endpoint_url=fake_url, connection_profile={}, capabilities_json=[],
        ))
        db.add(CatalogItemModel(
            id=_ITEM_ID, source_id=_SOURCE_ID, name="admin tiles",
            title="admin tiles", geometry_type="tile_vector",
            feature_type="tile", crs="EPSG:3857", bbox_json=None, tags_json=[],
            descriptor_json={}, meta_profile_json={},
            fingerprint="fp-goal02", availability="available",
        ))
        db.commit()
    df_tile_cache.invalidate_item(f"ogc-tiles:{_ITEM_ID}")
    yield server
    df_tile_cache.invalidate_item(f"ogc-tiles:{_ITEM_ID}")


class TestOgcTilesRoute:
    def test_serves_tile_bytes_with_etag_and_content_type(self, patched_server):
        client = _client()
        z, x, y = VALID_TILE
        r = client.get(_url(_ITEM_ID, z, x, y))
        assert r.status_code == 200, r.text
        assert r.content == TILE_MVT_BYTES
        assert r.headers["content-type"].startswith(MEDIA_MVT)
        assert r.headers["etag"]
        assert r.headers["x-tile-cache"] == "miss"

    def test_cache_hit_and_304(self, patched_server):
        client = _client()
        z, x, y = VALID_TILE
        r1 = client.get(_url(_ITEM_ID, z, x, y))
        assert r1.status_code == 200
        r2 = client.get(_url(_ITEM_ID, z, x, y))
        assert r2.headers["x-tile-cache"] == "hit"
        assert r2.headers["etag"] == r1.headers["etag"]
        r304 = client.get(
            _url(_ITEM_ID, z, x, y), headers={"If-None-Match": r1.headers["etag"]}
        )
        assert r304.status_code == 304

    def test_remote_missing_tile_is_204(self, patched_server):
        client = _client()
        r = client.get(_url(_ITEM_ID, 2, 0, 0))  # fixture 无此瓦片
        assert r.status_code == 204

    def test_zoom_outside_declared_range_is_204(self, patched_server):
        client = _client()
        r = client.get(_url(_ITEM_ID, 9, 0, 0))  # 服务声明 maxzoom=5
        assert r.status_code == 204

    def test_invalid_coordinates_400(self, patched_server):
        client = _client()
        assert client.get(_url(_ITEM_ID, 25, 0, 0)).status_code == 400
        assert client.get(_url(_ITEM_ID, 1, 2, 0)).status_code == 400

    def test_unknown_item_404(self, patched_server):
        client = _client()
        r = client.get(_url("no-such-item", *VALID_TILE))
        assert r.status_code == 404

    def test_cross_tenant_blocked_even_after_cache_hit(self, patched_server):
        """API-01：鉴权先于缓存 —— 跨租户调用不得借缓存命中拿字节。"""
        z, x, y = VALID_TILE
        owner = _client(_OWNER_ID)
        assert owner.get(_url(_ITEM_ID, z, x, y)).status_code == 200
        stranger = _client("someone-else-no-owner")
        assert stranger.get(_url(_ITEM_ID, z, x, y)).status_code == 404

    def test_missing_session_param_rejected(self, patched_server):
        """session_id 是硬前置（与 MVT layer 路由同语义）。"""
        client = _client()
        z, x, y = VALID_TILE
        r = client.get(
            f"/api/v1/data-fabric/catalog/{_ITEM_ID}/ogc-tiles/{z}/{x}/{y}"
        )
        assert r.status_code == 422

