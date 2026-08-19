"""Explicit MapSpec.view framing vs gesture camera — issue #640."""
import shutil
import uuid

import pytest

from app.services.mapspec.lifecycle_engine import (
    InitProjectIntent,
    MapSpecLifecycleEngine,
    SetViewIntent,
    UpsertLayerIntent,
)
from app.services.mapspec.store import BASE_STORAGE_DIR, mapspec_store_instance
from app.services.session_data import session_data_manager


@pytest.fixture
async def clean_session():
    sid = f"view-session-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    d = BASE_STORAGE_DIR / sid
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_set_view_marks_mapspec_view_as_explicit_framing(clean_session):
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(clean_session, InitProjectIntent())
    result = await engine.apply_mutation(
        clean_session,
        SetViewIntent(center=[114.3, 30.5], zoom=10),
        origin="user",
        expected_revision=1,
    )
    assert result.is_error is False
    stored = await mapspec_store_instance.get_mapspec(clean_session)
    assert stored["view"]["center"] == [114.3, 30.5]
    assert stored["view"]["zoom"] == 10
    assert stored["view"]["framed"] is True


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_layer_upsert_does_not_overwrite_explicit_framing(clean_session):
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(clean_session, InitProjectIntent())
    framed = await engine.apply_mutation(
        clean_session,
        SetViewIntent(center=[114.3, 30.5], zoom=10),
        origin="user",
        expected_revision=1,
    )
    result = await engine.apply_mutation(
        clean_session,
        UpsertLayerIntent(
            layer={
                "id": "L1",
                "source": "s1",
                "type": "circle",
                "paint": {"circle-color": "#00f"},
            },
            source_data={
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [0, 0]},
                        "properties": {},
                    }
                ],
            },
        ),
        origin="agent",
        expected_revision=framed.mutation_revision,
    )
    assert result.is_error is False
    stored = await mapspec_store_instance.get_mapspec(clean_session)
    assert stored["view"]["center"] == [114.3, 30.5]
    assert stored["view"]["zoom"] == 10
    assert stored["view"]["framed"] is True


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_viewport_hint_does_not_change_mapspec_view(clean_session):
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(clean_session, InitProjectIntent())
    await engine.apply_mutation(
        clean_session,
        SetViewIntent(center=[114.3, 30.5], zoom=10),
        origin="user",
        expected_revision=1,
    )
    await session_data_manager.set_map_state(
        clean_session,
        "viewport",
        {"center": [0.0, 0.0], "zoom": 2, "bearing": 0, "pitch": 0},
    )
    stored = await mapspec_store_instance.get_mapspec(clean_session)
    assert stored["view"]["center"] == [114.3, 30.5]
    assert stored["view"]["zoom"] == 10
    hint = (await session_data_manager.get_map_state(clean_session)).get("viewport")
    assert hint["center"] == [0.0, 0.0]
