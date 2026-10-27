"""G3D05：用户场景 mutation HTTP 通道（ADR-0275 D3）.

用户场景控制器与 Agent set_map_scene 汇入同一 SetSceneIntent 事务
管线：origin=user、幂等键、409 superseded、非法 shape 400 —— 与既有
presentation mutation 同语义（不造第二套）。
"""
import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.services.auth_history_bridge import require_owned_session
from app.models.db_model import Conversation
from app.services.mapspec.lifecycle_engine import (
    InitProjectIntent,
    MapSpecLifecycleEngine,
)
from app.services.mapspec.store import mapspec_store_instance
from app.api.routes import mapspec_mutations as _mod


@pytest.fixture
def session_id():
    return f"g3d05-scene-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def app(session_id):
    application = FastAPI()
    application.dependency_overrides[require_owned_session] = (
        lambda: Conversation(id=session_id)
    )
    application.include_router(_mod.router, prefix="/api/v1")
    return application


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _init_revision(session_id: str) -> int:
    engine = MapSpecLifecycleEngine()
    res = await engine.apply_mutation(session_id, InitProjectIntent())
    return res.mutation_revision


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_user_set_scene_commits_with_user_origin(client, session_id):
    rev = await _init_revision(session_id)
    resp = await client.post(
        f"/api/v1/chat/sessions/{session_id}/mapspec/mutations",
        json={
            "intent": "set_scene",
            "expected_revision": rev,
            "scene": {
                "mode": "3d",
                "camera": {"pitch": 50, "bearing": -15},
            },
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["origin"] == "user"
    stored = await mapspec_store_instance.get_mapspec(session_id)
    assert stored["scene"]["mode"] == "3d"
    assert stored["scene"]["camera"]["pitch"] == 50


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_user_clear_scene(client, session_id):
    rev = await _init_revision(session_id)
    set_resp = await client.post(
        f"/api/v1/chat/sessions/{session_id}/mapspec/mutations",
        json={
            "intent": "set_scene",
            "expected_revision": rev,
            "scene": {"mode": "2.5d"},
        },
    )
    assert set_resp.status_code == 200
    body = set_resp.json()
    clear_resp = await client.post(
        f"/api/v1/chat/sessions/{session_id}/mapspec/mutations",
        json={
            "intent": "set_scene",
            "expected_revision": body["mutation_revision"],
            "scene": None,
        },
    )
    assert clear_resp.status_code == 200
    stored = await mapspec_store_instance.get_mapspec(session_id)
    assert "scene" not in stored or stored.get("scene") is None


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_invalid_scene_shape_rejected(client, session_id):
    rev = await _init_revision(session_id)
    resp = await client.post(
        f"/api/v1/chat/sessions/{session_id}/mapspec/mutations",
        json={
            "intent": "set_scene",
            "expected_revision": rev,
            "scene": {"mode": "4d"},  # 词表外
        },
    )
    assert resp.status_code == 400
    stored = await mapspec_store_instance.get_mapspec(session_id)
    # last-known-good 不变
    assert stored.get("scene") in (None,)


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_dangling_terrain_source_rejected(client, session_id):
    rev = await _init_revision(session_id)
    resp = await client.post(
        f"/api/v1/chat/sessions/{session_id}/mapspec/mutations",
        json={
            "intent": "set_scene",
            "expected_revision": rev,
            "scene": {
                "mode": "3d",
                "terrain": {"source": "no-such-dem", "exaggeration": 1.5},
            },
        },
    )
    # pre-compile 阻塞（SCENE_TERRAIN_SOURCE_REF 双闸同口径）
    assert resp.status_code == 400


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_stale_revision_conflict(client, session_id):
    rev = await _init_revision(session_id)
    first = await client.post(
        f"/api/v1/chat/sessions/{session_id}/mapspec/mutations",
        json={
            "intent": "set_scene",
            "expected_revision": rev,
            "scene": {"mode": "3d"},
        },
    )
    assert first.status_code == 200
    stale = await client.post(
        f"/api/v1/chat/sessions/{session_id}/mapspec/mutations",
        json={
            "intent": "set_scene",
            "expected_revision": rev,  # 旧代
            "scene": {"mode": "2d"},
        },
    )
    assert stale.status_code == 409


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_client_mutation_id_idempotent_replay(client, session_id):
    rev = await _init_revision(session_id)
    mutation = {
        "intent": "set_scene",
        "expected_revision": rev,
        "scene": {"mode": "3d", "camera": {"pitch": 60}},
        "client_mutation_id": "g3d05-replay-0001",
    }
    first = await client.post(
        f"/api/v1/chat/sessions/{session_id}/mapspec/mutations", json=mutation)
    assert first.status_code == 200
    replay = await client.post(
        f"/api/v1/chat/sessions/{session_id}/mapspec/mutations", json=mutation)
    assert replay.status_code == 200
    # 幂等重放不二次推进 revision
    assert replay.json()["mutation_revision"] == first.json()["mutation_revision"]
