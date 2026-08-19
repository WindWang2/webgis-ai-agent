"""User chrome presentation mutations (visibility / opacity) — issue #639."""
import shutil
import uuid

import pytest

from app.services.mapspec.lifecycle_engine import (
    InitProjectIntent,
    MapSpecLifecycleEngine,
    PatchLayerPresentationIntent,
    UpsertLayerIntent,
)
from app.services.mapspec.store import BASE_STORAGE_DIR, mapspec_store_instance
from app.services.session_data import session_data_manager


@pytest.fixture
async def clean_session():
    sid = f"pres-session-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    d = BASE_STORAGE_DIR / sid
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


def _point_layer(layer_id="L1"):
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


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_user_hide_layer_commits_layout_visibility(clean_session):
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(clean_session, InitProjectIntent())
    seeded = await engine.apply_mutation(
        clean_session,
        UpsertLayerIntent(layer=_point_layer(), source_data=_geojson()),
    )
    assert seeded.is_error is False

    result = await engine.apply_mutation(
        clean_session,
        PatchLayerPresentationIntent(layer_id="L1", visible=False),
        origin="user",
        expected_revision=seeded.mutation_revision,
    )

    assert result.is_error is False
    assert result.superseded is False
    stored = await mapspec_store_instance.get_mapspec(clean_session)
    layer = next(lyr for lyr in stored["layers"] if lyr["id"] == "L1")
    assert layer["layout"]["visibility"] == "none"


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_user_opacity_commits_paint_opacity(clean_session):
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(clean_session, InitProjectIntent())
    seeded = await engine.apply_mutation(
        clean_session,
        UpsertLayerIntent(layer=_point_layer(), source_data=_geojson()),
    )

    result = await engine.apply_mutation(
        clean_session,
        PatchLayerPresentationIntent(layer_id="L1", opacity=0.4),
        origin="user",
        expected_revision=seeded.mutation_revision,
    )

    assert result.is_error is False
    stored = await mapspec_store_instance.get_mapspec(clean_session)
    layer = next(lyr for lyr in stored["layers"] if lyr["id"] == "L1")
    assert layer["paint"]["opacity"] == 0.4
    assert layer["paint"]["circle-opacity"] == 0.4
