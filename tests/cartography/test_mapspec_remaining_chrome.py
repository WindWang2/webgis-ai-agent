"""Remaining desired chrome mutations — issue #642."""
import shutil
import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes import mapspec_mutations as _mod
from app.core.auth import require_owned_session
from app.models.db_model import Conversation

from app.services.mapspec.lifecycle_engine import (
    InitProjectIntent,
    MapSpecLifecycleEngine,
    RemoveLayerIntent,
    ReorderLayersIntent,
    SetLayoutIntent,
    SetTimeIntent,
    UpsertLayerIntent,
)
from app.services.mapspec.store import BASE_STORAGE_DIR, mapspec_store_instance
from app.services.session_data import session_data_manager


@pytest.fixture
async def clean_session():
    sid = f"chrome-session-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    d = BASE_STORAGE_DIR / sid
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


def _layer(layer_id: str):
    return {
        "id": layer_id,
        "source": f"s-{layer_id}",
        "type": "circle",
        "paint": {"circle-color": "#00f"},
    }


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


async def _seed_two_layers(engine, sid):
    seeded = await engine.apply_mutation(sid, InitProjectIntent())
    first = await engine.apply_mutation(
        sid,
        UpsertLayerIntent(layer=_layer("A"), source_data=_geojson()),
        expected_revision=seeded.mutation_revision,
    )
    second = await engine.apply_mutation(
        sid,
        UpsertLayerIntent(layer=_layer("B"), source_data=_geojson()),
        expected_revision=first.mutation_revision,
    )
    return second


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_user_reorder_layers_commits_mapspec_order(clean_session):
    engine = MapSpecLifecycleEngine()
    seeded = await _seed_two_layers(engine, clean_session)
    result = await engine.apply_mutation(
        clean_session,
        ReorderLayersIntent(layer_ids=["B", "A"]),
        origin="user",
        expected_revision=seeded.mutation_revision,
    )
    assert result.is_error is False
    stored = await mapspec_store_instance.get_mapspec(clean_session)
    assert [lyr["id"] for lyr in stored["layers"]] == ["B", "A"]


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_user_remove_layer_does_not_resurrect_on_reload(clean_session):
    engine = MapSpecLifecycleEngine()
    seeded = await _seed_two_layers(engine, clean_session)
    result = await engine.apply_mutation(
        clean_session,
        RemoveLayerIntent(layer_id="A"),
        origin="user",
        expected_revision=seeded.mutation_revision,
    )
    assert result.is_error is False
    stored = await mapspec_store_instance.get_mapspec(clean_session)
    assert [lyr["id"] for lyr in stored["layers"]] == ["B"]


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_user_layout_and_time_commit_to_mapspec(clean_session):
    engine = MapSpecLifecycleEngine()
    seeded = await engine.apply_mutation(clean_session, InitProjectIntent())
    layout = await engine.apply_mutation(
        clean_session,
        SetLayoutIntent(legend={"visible": False, "position": "bottom-left"}),
        origin="user",
        expected_revision=seeded.mutation_revision,
    )
    timed = await engine.apply_mutation(
        clean_session,
        SetTimeIntent(enabled=True, field="ts", current="2020-01-01"),
        origin="user",
        expected_revision=layout.mutation_revision,
    )
    assert timed.is_error is False
    stored = await mapspec_store_instance.get_mapspec(clean_session)
    assert stored["layout"]["legend"]["position"] == "bottom-left"
    assert stored["time"]["field"] == "ts"
    assert stored["time"]["current"] == "2020-01-01"


@pytest.fixture
def app(clean_session):
    application = FastAPI()
    application.dependency_overrides[require_owned_session] = (
        lambda: Conversation(id=clean_session)
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
async def test_user_remove_and_reorder_via_http(client, clean_session):
    engine = MapSpecLifecycleEngine()
    seeded = await _seed_two_layers(engine, clean_session)
    removed = await client.post(
        f"/api/v1/chat/sessions/{clean_session}/mapspec/mutations",
        json={
            "intent": "remove_layer",
            "expected_revision": seeded.mutation_revision,
            "layer_id": "A",
        },
    )
    assert removed.status_code == 200
    reordered = await client.post(
        f"/api/v1/chat/sessions/{clean_session}/mapspec/mutations",
        json={
            "intent": "reorder_layers",
            "expected_revision": removed.json()["mutation_revision"],
            "layer_ids": ["B"],
        },
    )
    assert reordered.status_code == 200
    stored = await mapspec_store_instance.get_mapspec(clean_session)
    assert [lyr["id"] for lyr in stored["layers"]] == ["B"]


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_empty_session_init_project_via_http(client, clean_session):
    resp = await client.post(
        f"/api/v1/chat/sessions/{clean_session}/mapspec/mutations",
        json={"intent": "init_project", "expected_revision": 0},
    )
    assert resp.status_code == 200
    stored = await mapspec_store_instance.get_mapspec(clean_session)
    assert stored["version"] == "1.0"
    assert stored["layers"] == []
