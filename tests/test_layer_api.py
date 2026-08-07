"""Layer & Task API tests"""
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch
from fastapi import FastAPI

from app.api.routes import layer as _mod
from app.core.auth import require_owned_session
from app.models.db_model import Conversation

_VALID_SID = "session-aaaaaaaaaaaaaaaa"  # >= min_length=8


@pytest.fixture
def app(monkeypatch):
    """跨租户守卫在隔离测试里依赖真 DB；
    stub 成 always-pass（跨租户隔离由 test_cross_tenant_isolation 覆盖）。"""
    async def _noop_verify(session_id, user_id, owner_token=None):
        return None
    monkeypatch.setattr(_mod, "_verify_session_owner", _noop_verify)

    app = FastAPI()
    app.dependency_overrides[require_owned_session] = lambda: Conversation(id=_VALID_SID)
    app.include_router(_mod.router, prefix="/api/v1")
    return app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


_VALID_SID = "session-aaaaaaaaaaaaaaaa"  # >= min_length=8


@pytest.mark.asyncio
async def test_get_session_layer_data_not_found(client):
    with patch.object(_mod.session_data_manager, "get", return_value=None):
        resp = await client.get("/api/v1/layers/data/ref-123", params={"session_id": _VALID_SID})
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_session_layer_data_success(client):
    mock_data = {"type": "FeatureCollection", "features": []}
    with patch.object(_mod.session_data_manager, "get", return_value=mock_data):
        resp = await client.get("/api/v1/layers/data/ref-123", params={"session_id": _VALID_SID})
        assert resp.status_code == 200
        assert resp.json()["type"] == "FeatureCollection"


@pytest.mark.asyncio
async def test_get_session_layer_data_rejects_short_session_id(client):
    """安全：session_id 过短应被 422 拒绝（能力令牌熵不足）。"""
    resp = await client.get("/api/v1/layers/data/ref-123", params={"session_id": "abc"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_layer_types(client):
    resp = await client.get("/api/v1/layer-types")
    assert resp.status_code == 200
    data = resp.json()
    assert "layer_types" in data
    assert "analysis_types" in data
    assert len(data["layer_types"]) >= 3
    assert len(data["analysis_types"]) >= 4


@pytest.mark.asyncio
async def test_get_session_layer_data_missing_session_id(client):
    resp = await client.get("/api/v1/layers/data/ref-123")
    assert resp.status_code == 422


# ─── MVT tile endpoint (Data Plane tracer bullet) ────────────────────────────


def _poi_fc(n=3):
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.4 + i * 0.01, 39.9 + i * 0.01]},
         "properties": {"name": f"p{i}"}}
        for i in range(n)
    ]}


@pytest.mark.asyncio
async def test_mvt_tile_success_and_content(client):
    """Tile 端点返回 gzip 的合法 MVT（含点要素 + 属性）。"""
    from tests.unit.test_mvt_encoder import _decode_tile
    fc = _poi_fc()
    with patch.object(_mod.session_data_manager, "get", return_value=fc):
        resp = await client.get(
            "/api/v1/layers/data/ref-123/tiles/1/1/0.mvt",
            params={"session_id": _VALID_SID},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/x-protobuf")
        assert "private" in resp.headers.get("cache-control", "")

        # httpx 自动解压 gzip —— resp.content 即原始 MVT bytes
        tile = resp.content
        layers = _decode_tile(tile)
        assert len(layers) == 1
        assert layers[0]["name"] == "data"
        assert len(layers[0]["features"]) == 3
        props = dict(zip((layers[0]["keys"][k] for k, _ in layers[0]["features"][0]["tags"]),
                         (layers[0]["values"][v] for _, v in layers[0]["features"][0]["tags"])))
        assert props["name"] == "p0"


@pytest.mark.asyncio
async def test_mvt_tile_empty_is_valid(client):
    """空瓦片返回合法空 MVT（无 layer），不是错误。"""
    with patch.object(_mod.session_data_manager, "get", return_value=_poi_fc()):
        resp = await client.get(
            "/api/v1/layers/data/ref-123/tiles/1/0/0.mvt",  # 点在东半球 → NW 空
            params={"session_id": _VALID_SID},
        )
        assert resp.status_code == 200
        assert resp.content == b""  # 空 tile：合法空 MVT message


@pytest.mark.asyncio
async def test_mvt_tile_not_found(client):
    with patch.object(_mod.session_data_manager, "get", return_value=None):
        resp = await client.get(
            "/api/v1/layers/data/ref-123/tiles/1/1/0.mvt",
            params={"session_id": _VALID_SID},
        )
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_mvt_tile_rejects_invalid_coords(client):
    """瓦片坐标越界（x >= 2^z）应 400。"""
    with patch.object(_mod.session_data_manager, "get", return_value=_poi_fc()):
        resp = await client.get(
            "/api/v1/layers/data/ref-123/tiles/1/2/0.mvt",  # z=1 → x ∈ {0,1}
            params={"session_id": _VALID_SID},
        )
        assert resp.status_code == 400


@pytest.mark.asyncio
async def test_mvt_tile_handles_nested_geojson_shapes(client):
    """兼容 {geojson: FC} 与 {type: poi_query, geojson: FC} 两种引用形状。"""
    from tests.unit.test_mvt_encoder import _decode_tile
    for shape in ({"geojson": _poi_fc(1)}, {"type": "poi_query", "area": "x", "geojson": _poi_fc(2)}):
        with patch.object(_mod.session_data_manager, "get", return_value=shape):
            resp = await client.get(
                "/api/v1/layers/data/ref-123/tiles/1/1/0.mvt",
                params={"session_id": _VALID_SID},
            )
            assert resp.status_code == 200
            layers = _decode_tile(resp.content)
            assert len(layers[0]["features"]) > 0
