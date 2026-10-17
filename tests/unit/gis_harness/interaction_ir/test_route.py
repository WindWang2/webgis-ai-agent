"""Interaction IR V1 端点测试（ADR-0224）：鉴权 / 限速 / ack 信封 / 幂等重发 /
投影读面 / 删除会话 tombstone。模式同 test_map_action_acks.py。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes import interaction_events as _ie_mod
from app.api.routes.interaction_events import router
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


def _url(session_id: str) -> str:
    return f"/api/v1/chat/sessions/{session_id}/interaction-events"


def _event(seq: int, kind: str = "viewport_settled", **extra) -> dict:
    base = {
        "event_id": f"e2e-{seq:08d}",
        "client_instance_id": "tab-e2e0001",
        "client_seq": seq,
        "kind": kind,
        "payload": {"center": [116.0, 39.9], "zoom": 10},
    }
    base.update(extra)
    return base


def _make_limiter(allowed: bool):
    async def _is_allowed(key, max_requests, window_seconds):
        return allowed

    limiter = MagicMock()
    limiter.is_allowed = _is_allowed
    return limiter


@pytest.fixture(autouse=True)
def _stub_rate_limiter(monkeypatch):
    async def _get_stub():
        return _make_limiter(True)

    monkeypatch.setattr(_ie_mod, "get_rate_limiter", _get_stub)


@pytest.fixture(autouse=True)
def _stub_ownership(app):
    """require_owned_session 直通（跨租户语义由既有 auth 测试族覆盖）。"""
    from app.services.auth_history_bridge import require_owned_session

    app.dependency_overrides[require_owned_session] = lambda: MagicMock()


@pytest.fixture
def store():
    return MemorySessionStore()


def _patch_store(store):
    return patch.object(_ie_mod, "session_data_manager", new=store)


@pytest.mark.asyncio
async def test_endpoint_happy_path_and_ack_envelope(client, store):
    with _patch_store(store):
        resp = await client.post(
            _url("sess-ir1"),
            json={"events": [_event(1), _event(2, kind="aoi_drawn",
                                       payload={"geometry_type": "Polygon",
                                                "vertex_count": 4,
                                                "geometry_digest": "abc123"})]},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] == 2
    assert body["duplicates"] == 0 and body["stale"] == 0 and body["rejected"] == 0
    assert body["watermarks"] == {"tab-e2e0001": 2}
    assert [a["verdict"] for a in body["acks"]] == ["accepted", "accepted"]
    assert body["acks"][0]["server_seq"] == 1


@pytest.mark.asyncio
async def test_endpoint_retry_after_ack_loss_idempotent(client, store):
    batch = {"events": [_event(1), _event(2)]}
    with _patch_store(store):
        r1 = await client.post(_url("sess-ir2"), json=batch)
        r2 = await client.post(_url("sess-ir2"), json=batch)
    assert r1.json()["accepted"] == 2
    assert r2.json()["stale"] == 2 and r2.json()["accepted"] == 0
    # ledger 不双计。
    from app.services.gis_harness.interaction_ir.ingest import load_ledger_state

    state = await load_ledger_state(store, "sess-ir2")
    assert len(state.events) == 2


@pytest.mark.asyncio
async def test_endpoint_actor_forgery_rejected(client, store):
    with _patch_store(store):
        resp = await client.post(
            _url("sess-ir3"), json={"events": [_event(1, actor="agent")]},
        )
    assert resp.status_code == 200  # 单条拒绝不 500（fire-and-forget 语义）
    body = resp.json()
    assert body["rejected"] == 1
    assert body["acks"][0]["reason"] == "actor_not_user"


@pytest.mark.asyncio
async def test_endpoint_batch_bounds(client, store):
    with _patch_store(store):
        empty = await client.post(_url("sess-ir4"), json={"events": []})
        too_many = await client.post(
            _url("sess-ir4"), json={"events": [_event(i) for i in range(1, 52)]},
        )
    assert empty.status_code == 422
    assert too_many.status_code == 422


@pytest.mark.asyncio
async def test_endpoint_rate_limited(client, store, monkeypatch):
    async def _get_stub():
        return _make_limiter(False)

    monkeypatch.setattr(_ie_mod, "get_rate_limiter", _get_stub)
    with _patch_store(store):
        resp = await client.post(_url("sess-ir5"), json={"events": [_event(1)]})
    assert resp.status_code == 429


@pytest.mark.asyncio
async def test_endpoint_deleted_session_410(client, store, monkeypatch):
    # _raise_if_deleted 函数体内延迟 import —— patch 源模块属性。
    import app.agent_pi_bridge as bridge_mod

    monkeypatch.setattr(
        bridge_mod, "is_cartographic_session_deleted", lambda sid: True,
    )
    with _patch_store(store):
        resp = await client.post(_url("sess-ir6"), json={"events": [_event(1)]})
    assert resp.status_code == 410


@pytest.mark.asyncio
async def test_projection_endpoint_readonly(client, store):
    with _patch_store(store):
        await client.post(_url("sess-ir7"), json={"events": [_event(1)]})
        resp = await client.get(f"{_url('sess-ir7')}/projection")
    assert resp.status_code == 200
    body = resp.json()
    assert body["schema_version"] == "interaction_ir.v1"
    assert body["projection"]["viewport"]["zoom"] == 10
    assert body["projection"]["event_count"] == 1


@pytest.mark.asyncio
async def test_endpoint_persist_degraded_disclosed(client, store):
    """存储降级：分类照给 + persist_degraded 披露（不 500）；重试由水印收敛。"""

    class DegradedStore(MemorySessionStore):
        async def set_map_state(self, session_id, key, value):
            return False

    with _patch_store(DegradedStore()):
        resp = await client.post(_url("sess-ir8"), json={"events": [_event(1)]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] == 1
    assert body["persist_degraded"] is True


@pytest.mark.asyncio
async def test_projection_endpoint_empty_session(client, store):
    with _patch_store(store):
        resp = await client.get(f"{_url('sess-empty')}/projection")
    assert resp.status_code == 200
    assert resp.json()["projection"]["event_count"] == 0
    assert resp.json()["projection"]["viewport"] is None
