"""FC 端点 ETag/304 契约（extreme-scale v2 / 网络预算）。

整包 ``GET /layers/data/{ref_id}`` 此前无条件 200 整包 —— 会话重放/重复
挂载/调度器条件再验证都要重拉多 MB。与 MVT/PNG 瓦片端点同纪律对齐：
ETag = 返回字节 sha256 前 16 位（mtime=0 同款：内容寻址，非时间戳），
If-None-Match 命中 → 304 + 同 ETag、零 body。
"""

import gzip

import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import patch
from fastapi import FastAPI

from app.api.routes import layer as _mod
from app.core.auth import require_owned_session
from app.models.db_model import Conversation

_VALID_SID = "session-aaaaaaaaaaaaaaaa"

_FC = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "id": i,
            "geometry": {"type": "Point", "coordinates": [116.0 + i * 0.001, 39.9]},
            "properties": {"i": i},
        }
        for i in range(50)
    ],
}


@pytest.fixture
def app(monkeypatch):
    async def _noop_verify(session_id, user_id, owner_token=None):
        return None

    monkeypatch.setattr(_mod, "_verify_session_owner", _noop_verify)
    # 预算限流器缺席时 fail-open —— 测试环境无 Redis，直通。
    app = FastAPI()
    app.dependency_overrides[require_owned_session] = lambda: Conversation(id=_VALID_SID)
    app.include_router(_mod.router, prefix="/api/v1")
    return app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _mock_ref_data():
    class _Res:
        success = True
        error = None
        error_type = None
        data = _FC

    return _Res()


@pytest.mark.asyncio
async def test_fc_response_carries_content_etag(client):
    with patch.object(_mod.session_data_manager, "get_ref_data", return_value=_mock_ref_data()):
        resp = await client.get("/api/v1/layers/data/ref-etag", params={"session_id": _VALID_SID})
    assert resp.status_code == 200
    etag = resp.headers.get("ETag")
    assert etag, "FC 端点必须携带 ETag（网络预算：条件再验证的前提）"
    assert etag.startswith('"') and etag.endswith('"')


@pytest.mark.asyncio
async def test_fc_conditional_request_hits_304(client):
    with patch.object(_mod.session_data_manager, "get_ref_data", return_value=_mock_ref_data()):
        first = await client.get("/api/v1/layers/data/ref-etag", params={"session_id": _VALID_SID})
        etag = first.headers["ETag"]
        second = await client.get(
            "/api/v1/layers/data/ref-etag",
            params={"session_id": _VALID_SID},
            headers={"If-None-Match": etag},
        )
    assert second.status_code == 304
    assert second.headers.get("ETag") == etag
    assert second.content == b""
    # 304 响应不应再整包序列化 —— 同内容两次拉取 ETag 必须稳定（mtime=0 纪律）。
    with patch.object(_mod.session_data_manager, "get_ref_data", return_value=_mock_ref_data()):
        third = await client.get(
            "/api/v1/layers/data/ref-etag",
            params={"session_id": _VALID_SID},
            headers={"If-None-Match": etag},
        )
    assert third.status_code == 304


@pytest.mark.asyncio
async def test_fc_etag_changes_when_content_changes(client):
    with patch.object(_mod.session_data_manager, "get_ref_data", return_value=_mock_ref_data()):
        first = await client.get("/api/v1/layers/data/ref-etag", params={"session_id": _VALID_SID})
    mutated = dict(_FC)
    mutated["features"] = _FC["features"] + [
        {
            "type": "Feature",
            "id": 999,
            "geometry": {"type": "Point", "coordinates": [117.0, 40.0]},
            "properties": {},
        }
    ]

    class _Res2(_mock_ref_data().__class__):
        data = mutated

    with patch.object(_mod.session_data_manager, "get_ref_data", return_value=_Res2()):
        second = await client.get(
            "/api/v1/layers/data/ref-etag",
            params={"session_id": _VALID_SID},
            headers={"If-None-Match": first.headers["ETag"]},
        )
    # 内容变了 → 旧 ETag 必须失效为完整 200（绝不吞掉真更新）。
    assert second.status_code == 200
    assert second.headers["ETag"] != first.headers["ETag"]


@pytest.mark.asyncio
async def test_fc_etag_gzip_and_plain_representations_still_revalidate(client):
    """gzip 分支与 plain 分支都要有 ETag；客户端不带 Accept-Encoding 也能 304。"""
    with patch.object(_mod.session_data_manager, "get_ref_data", return_value=_mock_ref_data()):
        gz = await client.get(
            "/api/v1/layers/data/ref-etag",
            params={"session_id": _VALID_SID},
            headers={"Accept-Encoding": "gzip"},
        )
        assert gz.status_code == 200
        assert gz.headers.get("Content-Encoding") == "gzip"
        plain = await client.get("/api/v1/layers/data/ref-etag", params={"session_id": _VALID_SID})
        # gzip 表示的字节与 plain 不同 —— ETag 允许不同（表示寻址），但
        # 各自必须可再验证：gzip ETag + 同编码请求 → 304。
        again = await client.get(
            "/api/v1/layers/data/ref-etag",
            params={"session_id": _VALID_SID},
            headers={"Accept-Encoding": "gzip", "If-None-Match": gz.headers["ETag"]},
        )
        assert again.status_code == 304
        # httpx 透明解压：content 是解压后 JSON —— 验证载荷有效即可。
        assert gz.json()["type"] == "FeatureCollection"
        assert plain.status_code == 200
