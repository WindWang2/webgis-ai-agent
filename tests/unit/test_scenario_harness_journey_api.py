"""G05 路由级多候选端到端：propose→execute→compare→adopt 全链（HTTP 面）。

与工具面同链性证据：同一服务函数、同一守卫；compare 结果经 API 透传
（含 baseline move 与影响分级）；adopt 后 overview 反映 adopted 状态。
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.scenario_harness import router
from app.services.gis_world_state import apply_gis_mutation
from app.services.mapspec.intents import UpsertLayerIntent

_fc = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature", "id": f"sch-{i}",
            "geometry": {"type": "Point", "coordinates": [104.03 + i * 0.01, 30.61]},
            "properties": {"id": f"sch-{i}", "students": 400 + i * 60},
        }
        for i in range(4)
    ],
}


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    return app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def _patch_env(app):
    """只覆盖所有权依赖；不 patch session manager —— 引擎单例持有构造期
    manager 引用，patch 模块属性会分裂读写（引擎写全局、harness 读替身）。"""
    from app.services.auth_history_bridge import require_owned_session

    app.dependency_overrides[require_owned_session] = lambda: MagicMock()
    yield


async def _seed(session_id: str) -> None:
    res = await apply_gis_mutation(
        session_id,
        UpsertLayerIntent(
            layer={"id": "schools", "type": "circle", "source": "schools"},
            source_data={"type": "geojson", "inlineData": _fc},
        ),
        origin="agent",
        actor="journey",
    )
    assert not res.is_error, res.error_msg


@pytest.mark.asyncio
async def test_route_propose_execute_compare_adopt_chain(client):
    sid = f"g05-api-{uuid.uuid4().hex[:8]}"
    await _seed(sid)

    # propose（用户面）
    resp = await client.post(
        f"/api/v1/chat/sessions/{sid}/scenario/candidates",
        json={"title": "API 候选 B", "hypothesis": "透明度 0.85",
              "intents": [{"kind": "PatchLayerStyleIntent",
                           "layer_id": "schools", "paint": {"opacity": 0.85}}]},
    )
    assert resp.status_code == 200, resp.text
    cand = resp.json()["candidate"]
    assert cand["created_by"] == "user"
    assert cand["delta"][0]["base_values"], "API 提案同样观察基准值"

    # execute（分支试算）
    resp = await client.post(
        f"/api/v1/chat/sessions/{sid}/scenario/candidates/{cand['candidate_id']}/execute",
        json={"intents": []},
    )
    assert resp.status_code == 200, resp.text
    assert all(r["status"] == "applied" for r in resp.json()["results"])

    # compare（含影响分级）
    resp = await client.get(f"/api/v1/chat/sessions/{sid}/scenario/compare")
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert result is not None
    assert len(result["rows"]) == 1
    row = result["rows"][0]
    assert row["style_fields"] == ["schools.paint.opacity"]
    assert result["impacts"][0]["level"] == "current"

    # adopt（用户决议）
    resp = await client.post(
        f"/api/v1/chat/sessions/{sid}/scenario/candidates/{cand['candidate_id']}/adopt",
        json={"mode": "all"},
    )
    assert resp.status_code == 200, resp.text
    receipt = resp.json()["receipt"]
    assert receipt["applied_count"] == 1

    # overview 反映 adopted
    resp = await client.get(f"/api/v1/chat/sessions/{sid}/scenario/overview")
    body = resp.json()
    states = {c["candidate_id"]: c["state"] for c in body["candidates"]}
    assert states[cand["candidate_id"]] == "adopted"
    assert body["overview"]["adopted"] == 1


@pytest.mark.asyncio
async def test_route_propose_rejects_rollback_family_422(client):
    sid = f"g05-api-{uuid.uuid4().hex[:8]}"
    await _seed(sid)
    resp = await client.post(
        f"/api/v1/chat/sessions/{sid}/scenario/candidates",
        json={"title": "回滚意图", "intents": [{"kind": "RollbackIntent",
                                               "checkpoint_id": "ck"}]},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error"] == "invalid_intents"
