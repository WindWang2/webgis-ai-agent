"""G05 scenario_harness 路由测试：鉴权直通 / 只读投影 / 错误码透传。

模式同 tests/unit/test_situation_views_route.py（require_owned_session 覆盖 +
MemorySessionStore 注入 —— 路由经调用时局部导入取全局 manager，patch 全局属性）。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.scenario_harness import router
from app.services.scenario_harness.contracts import (
    ScenarioBaseline,
    ScenarioCandidate,
)
from app.services.scenario_harness.store import upsert_registry_entry
from app.services.session_data import MemorySessionStore


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
def _patch_session_manager():
    """全局 session_data_manager → 进程内 MemorySessionStore（测试隔离）。"""
    with patch(
        "app.services.session_data.session_data_manager", new=MemorySessionStore()
    ):
        yield


@pytest.fixture(autouse=True)
def _stub_ownership(app):
    from app.services.auth_history_bridge import require_owned_session

    app.dependency_overrides[require_owned_session] = lambda: MagicMock()


def _cand(cid: str, sid: str, state="comparable") -> ScenarioCandidate:
    return ScenarioCandidate(
        candidate_id=cid, session_id=sid,
        branch_session_id=f"{sid}__wif_sc{cid}", branch_id=f"sc{cid}",
        baseline_id="sb-1", state=state, title=f"方案 {cid}",
    )


@pytest.mark.asyncio
async def test_overview_empty_session(client):
    resp = await client.get("/api/v1/chat/sessions/empty-sid/scenario/overview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["schema_version"] == "scenario_overview.v1"
    assert body["overview"] is None
    assert body["candidates"] == []
    assert body["active_locks"] == []


@pytest.mark.asyncio
async def test_overview_with_candidate_and_baseline(client):
    sid = "g05-route"
    await upsert_registry_entry(sid, "_scenario_baselines", ScenarioBaseline(
        baseline_id="sb-1", session_id=sid, mapspec_revision=4,
        mapspec_fingerprint="a" * 64, captured_at="2026-11-25T00:00:00Z",
    ))
    await upsert_registry_entry(sid, "_scenario_candidates", _cand("c1", sid))
    resp = await client.get(f"/api/v1/chat/sessions/{sid}/scenario/overview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["overview"]["total"] == 1
    assert body["overview"]["baseline"]["baseline_id"] == "sb-1"
    assert body["candidates"][0]["candidate_id"] == "c1"
    assert body["candidates"][0]["title"] == "方案 c1"


@pytest.mark.asyncio
async def test_locks_roundtrip_via_route(client):
    sid = "g05-locks"
    resp = await client.post(
        f"/api/v1/chat/sessions/{sid}/scenario/locks",
        json={"kind": "layer_field", "target_id": "L1",
              "field_paths": ["paint.circle-color"], "reason": "配色定死"},
    )
    assert resp.status_code == 200
    lock = resp.json()["lock"]
    assert lock["lock_id"]
    assert lock["field_paths"] == ["paint.circle-color"]

    # 幂等：同 (kind,target,fields) 返回同一把锁
    resp2 = await client.post(
        f"/api/v1/chat/sessions/{sid}/scenario/locks",
        json={"kind": "layer_field", "target_id": "L1",
              "field_paths": ["paint.circle-color"], "reason": "again"},
    )
    assert resp2.json()["lock"]["lock_id"] == lock["lock_id"]

    # 非法 kind → 422（pydantic Literal 校验）
    resp3 = await client.post(
        f"/api/v1/chat/sessions/{sid}/scenario/locks",
        json={"kind": "hacker_kind", "field_paths": ["x"]},
    )
    assert resp3.status_code == 422

    # 释放（仅 user）
    resp4 = await client.delete(
        f"/api/v1/chat/sessions/{sid}/scenario/locks/{lock['lock_id']}?reason=不用了"
    )
    assert resp4.status_code == 200
    assert resp4.json()["lock"]["released_at"]

    # 活跃清单为空（released 不返回）
    resp5 = await client.get(f"/api/v1/chat/sessions/{sid}/scenario/locks")
    assert resp5.json()["locks"] == []


@pytest.mark.asyncio
async def test_compare_no_candidates_returns_reason(client):
    resp = await client.get("/api/v1/chat/sessions/g05-nocand/scenario/compare")
    assert resp.status_code == 200
    body = resp.json()
    assert body["schema_version"] == "scenario_compare.v1"
    assert body["result"] is None
    assert body["reason"] == "no_candidates"


@pytest.mark.asyncio
async def test_adopt_unknown_candidate_maps_to_404(client):
    resp = await client.post(
        "/api/v1/chat/sessions/g05-adopt-404/scenario/candidates/ghost/adopt",
        json={"mode": "all"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "candidate_not_found"


@pytest.mark.asyncio
async def test_delete_unknown_lock_maps_to_404(client):
    resp = await client.delete("/api/v1/chat/sessions/g05-lock-404/scenario/locks/lock-ghost")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "lock_not_found"
