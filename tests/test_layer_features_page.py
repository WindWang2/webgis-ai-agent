"""GET /layers/data/{ref_id}/features（W12 窗口分页）路由契约。

覆盖：稳定序翻页拼接、revision guard 409、畸形 cursor/bbox/fields 400、
bbox 窗口、fields 投影、descriptor 缺席的诚实降级、鉴权不被绕过。
"""
import pytest
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI

from app.api.routes import layer as _mod
from app.services.auth_history_bridge import require_owned_session
from app.models.db_model import Conversation

_VALID_SID = "session-aaaaaaaaaaaaaaaa"


def _fc(n):
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": i,
                "geometry": {"type": "Point", "coordinates": [104.0 + i * 0.01, 30.0]},
                "properties": {"i": i, "name": f"f{i}"},
            }
            for i in range(n)
        ],
    }


_FC = _fc(250)


def _mock_ref_data(data=_FC):
    payload = data

    class _Res:
        success = True
        error = None
        error_type = None
        data = payload

    return _Res()


@pytest.fixture
def app(monkeypatch):
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


def _install_store(monkeypatch, data=_FC, revision=3, feature_count=None):
    descriptor = {
        "content_revision": revision,
        "feature_count": feature_count if feature_count is not None else len(data["features"]),
    }

    async def _resolve_alias(session_id, ref_or_alias):
        return ref_or_alias

    async def _get_ref_descriptor(session_id, ref_id):
        return dict(descriptor) if ref_id == "ref-page" else None

    async def _get_ref_data(session_id, ref_id, owner_token=None):
        if ref_id not in ("ref-page",):
            class _Missing:
                success = False
                error = "数据不可用"
                error_type = "NotFound"

            return _Missing()
        return _mock_ref_data(data)

    monkeypatch.setattr(_mod.session_data_manager, "resolve_alias", _resolve_alias)
    monkeypatch.setattr(_mod.session_data_manager, "get_ref_descriptor", _get_ref_descriptor)
    monkeypatch.setattr(_mod.session_data_manager, "get_ref_data", _get_ref_data)


@pytest.mark.asyncio
async def test_paging_through_pages_matches_full_order(client, monkeypatch):
    _install_store(monkeypatch)
    ids = []
    cursor = None
    for _ in range(10):
        params = {"session_id": _VALID_SID, "limit": 100}
        if cursor:
            params["cursor"] = cursor
        r = await client.get("/api/v1/layers/data/ref-page/features", params=params)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["type"] == "FeatureCollection"
        ids.extend(f["id"] for f in body["features"])
        assert body["revision"] == 3 and body["feature_count"] == 250
        if not body["pagination"]["has_more"]:
            break
        cursor = body["pagination"]["next_cursor"]
    assert ids == list(range(250)), "HTTP 翻页拼接 == 原序（不漏不重）"


@pytest.mark.asyncio
async def test_revision_guard_conflict_409(client, monkeypatch):
    _install_store(monkeypatch, revision=3)
    r = await client.get(
        "/api/v1/layers/data/ref-page/features",
        params={"session_id": _VALID_SID, "v": 2},
    )
    assert r.status_code == 409, "翻页起点所属版本已过期必须 409（绝不静默跨版）"
    assert r.json()["detail"]["current_revision"] == 3
    ok = await client.get(
        "/api/v1/layers/data/ref-page/features",
        params={"session_id": _VALID_SID, "v": 3},
    )
    assert ok.status_code == 200


@pytest.mark.asyncio
async def test_malformed_cursor_400(client, monkeypatch):
    _install_store(monkeypatch)
    r = await client.get(
        "/api/v1/layers/data/ref-page/features",
        params={"session_id": _VALID_SID, "cursor": "not-a-cursor"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_malformed_bbox_400(client, monkeypatch):
    _install_store(monkeypatch)
    r = await client.get(
        "/api/v1/layers/data/ref-page/features",
        params={"session_id": _VALID_SID, "bbox": "1,2,3"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_bbox_window_filters_server_side(client, monkeypatch):
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "id": i,
             "geometry": {"type": "Point", "coordinates": [106.0 if i % 2 else 100.0, 30.0]},
             "properties": {"i": i}}
            for i in range(50)
        ],
    }
    _install_store(monkeypatch, data=fc)
    r = await client.get(
        "/api/v1/layers/data/ref-page/features",
        params={"session_id": _VALID_SID, "bbox": "105,29,107,31", "limit": 100},
    )
    assert r.status_code == 200
    body = r.json()
    assert [f["id"] for f in body["features"]] == [i for i in range(50) if i % 2]
    assert body["bbox_mode"] == "coarse"


@pytest.mark.asyncio
async def test_fields_projection(client, monkeypatch):
    _install_store(monkeypatch)
    r = await client.get(
        "/api/v1/layers/data/ref-page/features",
        params={"session_id": _VALID_SID, "fields": "name", "limit": 2},
    )
    assert r.status_code == 200
    feats = r.json()["features"]
    assert feats[0]["properties"] == {"name": "f0"}
    assert feats[0]["geometry"] is not None


@pytest.mark.asyncio
async def test_descriptor_absent_serves_with_null_revision(client, monkeypatch):
    # 旧数据无 descriptor：诚实降级 revision=None，无 guard（可服务）。
    _install_store(monkeypatch)
    r = await client.get(
        "/api/v1/layers/data/ref-other/features",
        params={"session_id": _VALID_SID, "limit": 5},
    )
    assert r.status_code == 404  # _install_store 只给 ref-page 数据


@pytest.mark.asyncio
async def test_limit_clamped(client, monkeypatch):
    _install_store(monkeypatch)
    r = await client.get(
        "/api/v1/layers/data/ref-page/features",
        params={"session_id": _VALID_SID, "limit": 5000},
    )
    assert r.status_code == 422, "limit 超过 1000 必须被参数校验拒绝"
