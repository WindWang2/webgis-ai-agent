"""TDD for #667: GET /layers/data/{ref_id}/feature/{feature_id}"""
import pytest
from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI

from app.api.routes import layer as _mod
from app.core.auth import require_owned_session
from app.models.db_model import Conversation
from app.services.session_data import MemorySessionStore

_VALID_SID = "session-aaaaaaaaaaaaaaaa"


def _fc_with_ids():
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "id": "feat-1", "geometry": {"type": "Point", "coordinates": [116.0, 39.9]}, "properties": {"name": "A"}},
            {"type": "Feature", "id": "feat-2", "geometry": {"type": "Point", "coordinates": [116.1, 39.9]}, "properties": {"name": "B"}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.2, 39.9]}, "properties": {"id": "prop-3", "name": "C"}},
        ],
    }


@pytest.fixture
def store():
    return MemorySessionStore()


@pytest.fixture
def app(monkeypatch, store):
    async def _noop_verify(session_id, user_id, owner_token=None):
        return None
    monkeypatch.setattr(_mod, "_verify_session_owner", _noop_verify)
    monkeypatch.setattr(_mod, "session_data_manager", store)
    app = FastAPI()
    app.dependency_overrides[require_owned_session] = lambda: Conversation(id=_VALID_SID)
    app.include_router(_mod.router, prefix="/api/v1")
    return app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_feature_endpoint_200_single_feature(client, store):
    ref_id = await store.store(_VALID_SID, _fc_with_ids(), prefix="data")
    # by feature.id
    resp = await client.get(f"/api/v1/layers/data/{ref_id}/feature/feat-1", params={"session_id": _VALID_SID})
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "Feature"
    assert body["id"] == "feat-1"
    # by properties.id fallback
    resp2 = await client.get(f"/api/v1/layers/data/{ref_id}/feature/prop-3", params={"session_id": _VALID_SID})
    assert resp2.status_code == 200
    assert resp2.json()["properties"]["name"] == "C"


@pytest.mark.asyncio
async def test_feature_endpoint_404_unknown_feature(client, store):
    ref_id = await store.store(_VALID_SID, _fc_with_ids(), prefix="data")
    resp = await client.get(f"/api/v1/layers/data/{ref_id}/feature/not-exist", params={"session_id": _VALID_SID})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_feature_endpoint_404_unknown_ref(client):
    resp = await client.get("/api/v1/layers/data/ref:missing/feature/feat-1", params={"session_id": _VALID_SID})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_feature_endpoint_403_wrong_token(client, store):
    await store.set_map_state(_VALID_SID, "owner_token_digest", __import__("hashlib").sha256(b"secret-token").hexdigest())
    ref_id = await store.store(_VALID_SID, _fc_with_ids(), prefix="data")
    resp = await client.get(f"/api/v1/layers/data/{ref_id}/feature/feat-1", params={"session_id": _VALID_SID}, headers={"X-Session-Token": "wrong-token"})
    assert resp.status_code == 403

    # correct token succeeds
    resp2 = await client.get(f"/api/v1/layers/data/{ref_id}/feature/feat-1", params={"session_id": _VALID_SID}, headers={"X-Session-Token": "secret-token"})
    assert resp2.status_code == 200


@pytest.mark.asyncio
async def test_feature_endpoint_does_not_hydrate_full_fc_on_wire(client, store):
    """Wire carries one feature, not the full FC. Patch serialize_geojson to assert payload size."""
    ref_id = await store.store(_VALID_SID, _fc_with_ids(), prefix="data")
    resp = await client.get(f"/api/v1/layers/data/{ref_id}/feature/feat-1", params={"session_id": _VALID_SID})
    assert resp.status_code == 200
    body = resp.json()
    # must NOT be a FeatureCollection
    assert body["type"] == "Feature"
    assert "features" not in body
