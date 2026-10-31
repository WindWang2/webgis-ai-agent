"""Map Operation / Display Confirmation 路由测试（M4/M7/M10）。

覆盖：owner 受控提交（敏感操作批准流）、回执只读面、跨会话/未认证
fail-closed（F-A5）、display-ack 新鲜度门（F-A3：旧代次拒绝 / 最新代次
确认 / auto 模式零行为变化）、ack 后 finalizer force 重跑。
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from app.api.routes import display_confirmation as _dc_mod
from app.api.routes.display_confirmation import router as dc_router
from app.api.routes.map_operations import router as mo_router
from app.services.session_data import session_data_manager


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(mo_router, prefix="/api/v1")
    app.include_router(dc_router, prefix="/api/v1")
    return app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def _stub_ownership(app):
    from app.services.auth_history_bridge import require_owned_session

    app.dependency_overrides[require_owned_session] = lambda: MagicMock()


@pytest.fixture(autouse=True)
def _stub_limiter():
    async def _is_allowed(key, max_requests, window_seconds):
        return True

    limiter = MagicMock()
    limiter.is_allowed = _is_allowed
    with patch.object(_dc_mod, "get_rate_limiter", new=MagicMock(
        side_effect=lambda: _make_async(limiter))):
        yield


def _make_async(value):
    async def _get():
        return value
    return _get()


def _sid() -> str:
    return f"mopr-{uuid.uuid4().hex[:10]}"


async def _seed_pending(sid: str) -> str:
    """经真实 service 产一条 confirmation_required 回执。"""
    from app.services.gis_harness.map_operations import MapOperationRequest
    from app.services.gis_harness.map_operations.service import commit_map_operation
    from app.services.mapspec.lifecycle_engine import (
        MapSpecLifecycleEngine,
        UpsertLayerIntent,
    )

    engine = MapSpecLifecycleEngine()
    res = await engine.apply_mutation(sid, UpsertLayerIntent(layer={
        "id": "heatmap", "type": "heatmap", "source": "src-heat",
        "name": "人口热力图", "paint": {"heatmap-opacity": 0.8},
    }, source_data={"type": "geojson", "inlineData": {
        "type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point",
                                             "coordinates": [104.0, 30.6]},
             "properties": {}}]}}))
    assert not res.is_error
    oid = f"mop-pend-{uuid.uuid4().hex[:10]}"
    receipt = await commit_map_operation(sid, MapOperationRequest(
        operation_id=oid, mode="commit",
        effects=[{"kind": "remove_layer", "target": "heatmap"}],
    ))
    assert receipt["status"] == "confirmation_required"
    return oid


# ── map-operations 路由 ─────────────────────────────────────────────────


async def test_list_and_get_receipts(client):
    sid = _sid()
    oid = await _seed_pending(sid)

    resp = await client.get(f"/api/v1/chat/sessions/{sid}/map-operations")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == sid
    assert any(r["operation_id"] == oid for r in body["operations"])

    resp = await client.get(f"/api/v1/chat/sessions/{sid}/map-operations/{oid}")
    assert resp.status_code == 200
    assert resp.json()["receipt"]["status"] == "confirmation_required"

    resp = await client.get(
        f"/api/v1/chat/sessions/{sid}/map-operations/mop-nonexistent")
    assert resp.status_code == 404


async def test_owner_commit_flow_removes_pending_and_applies(client):
    sid = _sid()
    oid = await _seed_pending(sid)

    resp = await client.post(
        f"/api/v1/chat/sessions/{sid}/map-operations/{oid}/commit", json={})
    assert resp.status_code == 200
    receipt = resp.json()["receipt"]
    assert receipt["status"] == "committed"
    assert receipt["effects"][0]["state"] == "applied"

    from app.services.mapspec.store import mapspec_store_instance

    spec = await mapspec_store_instance.get_mapspec(sid)
    assert not any(lyr["id"] == "heatmap" for lyr in spec["layers"])

    # 环内不再 pending；重复批准 → 409
    resp = await client.post(
        f"/api/v1/chat/sessions/{sid}/map-operations/{oid}/commit", json={})
    assert resp.status_code == 409


async def test_commit_non_pending_rejected(client):
    sid = _sid()
    from app.services.gis_harness.map_operations import MapOperationRequest
    from app.services.gis_harness.map_operations.service import commit_map_operation

    oid = f"mop-np-{uuid.uuid4().hex[:8]}"
    receipt = await commit_map_operation(sid, MapOperationRequest(
        operation_id=oid, mode="commit",
        effects=[{"kind": "set_layer_opacity", "target_kind": "layer_ref",
                  "target": "none", "opacity": 0.5}],
    ))
    assert receipt["status"] == "rejected"
    resp = await client.post(
        f"/api/v1/chat/sessions/{sid}/map-operations/{oid}/commit", json={})
    assert resp.status_code == 409


# ── display-confirmation 路由（F-A3）───────────────────────────────────


def _seed_observation(sid: str, seq: int) -> None:
    import asyncio

    asyncio.get_event_loop().run_until_complete(
        session_data_manager.set_map_state(sid, "_cartographic_observation", {
            "source": "frontend_runtime", "session_id": sid,
            "sequence": seq, "mapspec_revision": 3,
        })
    )


def _stored(seq: int):
    async def _fake(sid):
        return {"render_observation_seq": seq, "status": "complete",
                "product_verdict": {"verdict": "READY"}}
    return _fake


async def test_fa3_required_pending_then_confirm(client, monkeypatch):
    sid = _sid()
    monkeypatch.setenv("GIS_FINAL_DISPLAY_CONFIRM", "required")
    await session_data_manager.set_map_state(sid, "_cartographic_observation", {
        "source": "frontend_runtime", "session_id": sid,
        "sequence": 7, "mapspec_revision": 3,
    })
    monkeypatch.setattr(_dc_mod, "_stored_product", _stored(5))

    # first ready 但无确认 → pending，不宣布 confirmed
    resp = await client.get(f"/api/v1/chat/sessions/{sid}/display-confirmation")
    body = resp.json()
    assert body["mode"] == "required"
    assert body["confirmed"] is False
    assert body["pending"] is True
    assert body["required_render_seq"] == 5
    assert body["latest_observation_seq"] == 7

    # 旧代次 ack（≠ 最新观察 7）→ 拒绝
    resp = await client.post(
        f"/api/v1/chat/sessions/{sid}/display-confirmation/ack",
        json={"render_seq": 5},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "stale_ack"

    # owner 当前代次确认 → confirmed
    finalize_calls = []
    from app.services.gis_harness.completion import pipeline as _pipeline

    async def _fake_finalize(session_id, *, reason="", force=False, final_gate=False):
        finalize_calls.append({"reason": reason, "force": force})
        return None

    monkeypatch.setattr(_pipeline, "maybe_finalize_map_product", _fake_finalize)
    resp = await client.post(
        f"/api/v1/chat/sessions/{sid}/display-confirmation/ack",
        json={"render_seq": 7},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] is True
    assert body["confirmed"] is True
    assert finalize_calls and finalize_calls[0]["force"] is True

    # 确认后 GET → pending False
    resp = await client.get(f"/api/v1/chat/sessions/{sid}/display-confirmation")
    assert resp.json()["pending"] is False


async def test_fa3_auto_mode_zero_behavior_change(client, monkeypatch):
    sid = _sid()
    monkeypatch.setenv("GIS_FINAL_DISPLAY_CONFIRM", "auto")
    monkeypatch.setattr(_dc_mod, "_stored_product", _stored(5))

    resp = await client.get(f"/api/v1/chat/sessions/{sid}/display-confirmation")
    body = resp.json()
    assert body["mode"] == "auto"
    assert body["confirmed"] is True
    assert body["pending"] is False

    resp = await client.post(
        f"/api/v1/chat/sessions/{sid}/display-confirmation/ack",
        json={"render_seq": 5},
    )
    body = resp.json()
    assert body["accepted"] is False
    assert body["reason"] == "mode_auto"


async def test_ack_without_verdict_rejected(client, monkeypatch):
    sid = _sid()
    monkeypatch.setenv("GIS_FINAL_DISPLAY_CONFIRM", "required")

    async def _none(sid_):
        return None

    monkeypatch.setattr(_dc_mod, "_stored_product", _none)
    resp = await client.post(
        f"/api/v1/chat/sessions/{sid}/display-confirmation/ack",
        json={"render_seq": 3},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "no_verdict"


async def test_ack_older_than_verdict_rejected(client, monkeypatch):
    sid = _sid()
    monkeypatch.setenv("GIS_FINAL_DISPLAY_CONFIRM", "required")
    await session_data_manager.set_map_state(sid, "_cartographic_observation", {
        "source": "frontend_runtime", "session_id": sid,
        "sequence": 9, "mapspec_revision": 3,
    })
    monkeypatch.setattr(_dc_mod, "_stored_product", _stored(12))

    resp = await client.post(
        f"/api/v1/chat/sessions/{sid}/display-confirmation/ack",
        json={"render_seq": 9},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "stale_verdict"


# ── F-A5：跨会话 / 未认证 fail-closed ──────────────────────────────────


async def test_foreign_session_404(app, client):
    from app.services.auth_history_bridge import require_owned_session

    def _raise():
        raise HTTPException(status_code=404, detail="Session not found")

    app.dependency_overrides[require_owned_session] = _raise
    sid = _sid()
    for method, url in [
        ("get", f"/api/v1/chat/sessions/{sid}/map-operations"),
        ("get", f"/api/v1/chat/sessions/{sid}/map-operations/mop-x"),
        ("post", f"/api/v1/chat/sessions/{sid}/map-operations/mop-x/commit"),
        ("get", f"/api/v1/chat/sessions/{sid}/display-confirmation"),
        ("post", f"/api/v1/chat/sessions/{sid}/display-confirmation/ack"),
    ]:
        if method == "post":
            resp = await client.post(url, json={})
        else:
            resp = await client.get(url)
        assert resp.status_code == 404, (method, url)
