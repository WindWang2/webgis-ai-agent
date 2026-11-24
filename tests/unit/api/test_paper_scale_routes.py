"""paper-scale REST 路由测试 —— 验权 / preview / commit / 结构化错误。

走真实 FastAPI TestClient + 真实引擎播种（无 mock），与 G27 路由测试同纪律。
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.services.mapspec.lifecycle_engine import (
    MapSpecLifecycleEngine,
    UpsertLayerIntent,
)
from app.services.mapspec.store import mapspec_store_instance
from app.services.session_data import session_data_manager


@pytest.fixture()
async def session():
    sid = f"pgenapi-{uuid.uuid4().hex[:10]}"
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": "a", "properties": {"name": "a"},
         "geometry": {"type": "Polygon", "coordinates": [
             [[115.0, 38.0], [116.0, 38.0], [116.0, 39.0],
              [115.0, 39.0], [115.0, 38.0]]]}},
        {"type": "Feature", "id": "b", "properties": {"name": "b"},
         "geometry": {"type": "Polygon", "coordinates": [
             [[116.0, 38.0], [117.0, 38.0], [117.0, 39.0],
              [116.0, 39.0], [116.0, 38.0]]]}},
    ]}
    engine = MapSpecLifecycleEngine()
    res = await engine.apply_mutation(sid, UpsertLayerIntent(
        layer={"id": "districts", "type": "fill", "source": "src-districts",
               "paint": {"fill-color": "#4a90d9"}},
        source_data={"type": "geojson", "inlineData": fc}))
    assert not res.is_error, res.error_msg
    yield sid
    session_data_manager._store.pop(sid, None)
    session_data_manager._map_state.pop(sid, None)
    await mapspec_store_instance.discard_mapspec(sid)


@pytest.fixture()
def client(session):
    from app.main import app
    from app.services.auth_history_bridge import require_owned_session

    # 会话归属注入（FastAPI 标准测试缝；鉴权语义由 verify_session_owner
    # 自身的测试域覆盖 —— 路由只验「同一依赖在场」）。
    class _Conv:
        id = session
        user_id = "route-test-user"

    app.dependency_overrides[require_owned_session] = lambda: _Conv()
    yield TestClient(app)
    app.dependency_overrides.pop(require_owned_session, None)


AUTH_HEADERS: dict = {}


class TestPaperScaleRoutes:
    def test_inspect_ok(self, client, session):
        resp = client.get(
            f"/api/v1/chat/sessions/{session}/paper-scale",
            params={"layer": "districts", "denominator_text": "1:50万",
                    "page_text": "A4横版", "purpose": "overview"},
            headers=AUTH_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "ok"
        assert body["target_denominator"] == 500_000

    def test_inspect_bad_scale_400(self, client, session):
        resp = client.get(
            f"/api/v1/chat/sessions/{session}/paper-scale",
            params={"layer": "districts", "denominator_text": "乱写"},
            headers=AUTH_HEADERS)
        assert resp.status_code == 400
        # 全局错误包装（code/message/data 形状；detail 载于 message）
        body = resp.json()
        assert "无法解析比例尺" in str(body.get("message") or body.get("detail"))

    def test_preview_and_commit(self, client, session):
        resp = client.post(
            f"/api/v1/chat/sessions/{session}/paper-scale",
            json={"layer": "districts", "denominator": 500_000,
                  "page_text": "A4横版", "purpose": "overview",
                  "mode": "preview", "operation_id": "op-api-1"},
            headers=AUTH_HEADERS)
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "preview"

        resp = client.post(
            f"/api/v1/chat/sessions/{session}/paper-scale",
            json={"layer": "districts", "denominator": 500_000,
                  "page_text": "A4横版", "purpose": "overview",
                  "mode": "commit", "operation_id": "op-api-1"},
            headers=AUTH_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "committed"
        assert body["artifact_ref"].startswith("ref:")
