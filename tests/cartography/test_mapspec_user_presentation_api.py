"""HTTP adapter for user MapSpec presentation mutations — issue #639."""
import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.auth import require_owned_session
from app.models.db_model import Conversation
from app.services.mapspec.lifecycle_engine import (
    InitProjectIntent,
    MapSpecLifecycleEngine,
    UpsertLayerIntent,
)
from app.services.mapspec.store import mapspec_store_instance
from app.api.routes import mapspec_mutations as _mod


def _geojson():
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [0, 0]},
                "properties": {},
            }
        ],
    }


@pytest.fixture
def session_id():
    return f"api-pres-{uuid.uuid4().hex[:8]}"


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


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_user_hide_layer_via_http_commits_mapspec(client, session_id):
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(session_id, InitProjectIntent())
    seeded = await engine.apply_mutation(
        session_id,
        UpsertLayerIntent(
            layer={
                "id": "L1",
                "source": "s1",
                "type": "circle",
                "paint": {"circle-color": "#00f"},
            },
            source_data=_geojson(),
        ),
    )

    resp = await client.post(
        f"/api/v1/chat/sessions/{session_id}/mapspec/mutations",
        json={
            "intent": "patch_layer_presentation",
            "expected_revision": seeded.mutation_revision,
            "layer_id": "L1",
            "visible": False,
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["origin"] == "user"
    stored = await mapspec_store_instance.get_mapspec(session_id)
    layer = next(lyr for lyr in stored["layers"] if lyr["id"] == "L1")
    assert layer["layout"]["visibility"] == "none"


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_user_mutation_requires_expected_revision(client, session_id):
    resp = await client.post(
        f"/api/v1/chat/sessions/{session_id}/mapspec/mutations",
        json={
            "intent": "patch_layer_presentation",
            "layer_id": "L1",
            "visible": False,
        },
    )
    assert resp.status_code == 422


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_stale_user_mutation_is_conflict(client, session_id):
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(session_id, InitProjectIntent())
    seeded = await engine.apply_mutation(
        session_id,
        UpsertLayerIntent(
            layer={
                "id": "L1",
                "source": "s1",
                "type": "circle",
                "paint": {"circle-color": "#00f"},
            },
            source_data=_geojson(),
        ),
    )
    resp = await client.post(
        f"/api/v1/chat/sessions/{session_id}/mapspec/mutations",
        json={
            "intent": "patch_layer_presentation",
            "expected_revision": seeded.mutation_revision - 1,
            "layer_id": "L1",
            "visible": False,
        },
    )
    assert resp.status_code == 409
    stored = await mapspec_store_instance.get_mapspec(session_id)
    layer = next(lyr for lyr in stored["layers"] if lyr["id"] == "L1")
    assert (layer.get("layout") or {}).get("visibility") != "none"
