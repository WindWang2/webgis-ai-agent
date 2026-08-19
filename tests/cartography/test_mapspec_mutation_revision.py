"""MapSpec mutation origin + expected_revision (ADR-0058, issue #638).

Write-seam tests at MapSpecLifecycleEngine.apply_mutation only.
"""
import shutil
import uuid

import pytest

from app.services.mapspec.lifecycle_engine import (
    InitProjectIntent,
    MapSpecLifecycleEngine,
    SetViewIntent,
)
from app.services.mapspec.store import BASE_STORAGE_DIR, mapspec_store_instance
from app.services.session_data import session_data_manager


@pytest.fixture
async def clean_session():
    sid = f"rev-session-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    d = BASE_STORAGE_DIR / sid
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_user_origin_without_expected_revision_is_rejected(clean_session):
    engine = MapSpecLifecycleEngine()
    seeded = await engine.apply_mutation(clean_session, InitProjectIntent())
    assert seeded.is_error is False
    baseline = await mapspec_store_instance.get_mapspec(clean_session)

    result = await engine.apply_mutation(
        clean_session,
        SetViewIntent(center=[114.3, 30.5], zoom=10),
        origin="user",
    )

    assert result.is_error is True
    assert result.superseded is False
    stored = await mapspec_store_instance.get_mapspec(clean_session)
    assert stored == baseline


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_agent_origin_may_omit_expected_revision_and_still_commit(clean_session):
    engine = MapSpecLifecycleEngine()
    seeded = await engine.apply_mutation(clean_session, InitProjectIntent())
    assert seeded.is_error is False

    result = await engine.apply_mutation(
        clean_session,
        SetViewIntent(center=[114.3, 30.5], zoom=10),
    )

    assert result.is_error is False
    assert result.superseded is False
    stored = await mapspec_store_instance.get_mapspec(clean_session)
    assert stored["view"]["center"] == [114.3, 30.5]
    assert stored["view"]["zoom"] == 10
    assert result.mutation_revision == seeded.mutation_revision + 1


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_stale_expected_revision_is_superseded_and_leaves_mapspec_unchanged(
    clean_session,
):
    engine = MapSpecLifecycleEngine()
    seeded = await engine.apply_mutation(clean_session, InitProjectIntent())
    current = seeded.mutation_revision
    baseline = await mapspec_store_instance.get_mapspec(clean_session)

    result = await engine.apply_mutation(
        clean_session,
        SetViewIntent(center=[116.4, 39.9], zoom=8),
        origin="user",
        expected_revision=current - 1,
    )

    assert result.superseded is True
    assert result.is_error is False
    assert result.mutation_revision == current
    assert result.correction_hint
    stored = await mapspec_store_instance.get_mapspec(clean_session)
    assert stored == baseline
    payload = result.to_dict()
    assert payload["success"] is False
    assert payload["status"] == "superseded"
    assert payload["correction_hint"]
    assert payload["mapspec"] == baseline


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_matching_expected_revision_commits_and_records_origin(clean_session):
    engine = MapSpecLifecycleEngine()
    seeded = await engine.apply_mutation(clean_session, InitProjectIntent())
    current = seeded.mutation_revision

    result = await engine.apply_mutation(
        clean_session,
        SetViewIntent(center=[114.3, 30.5], zoom=10),
        origin="user",
        expected_revision=current,
    )

    assert result.is_error is False
    assert result.superseded is False
    assert result.origin == "user"
    assert result.mutation_revision == current + 1
    stored = await mapspec_store_instance.get_mapspec(clean_session)
    assert stored["view"]["center"] == [114.3, 30.5]
    assert result.to_dict()["origin"] == "user"


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_agent_stale_expected_revision_is_also_superseded(clean_session):
    engine = MapSpecLifecycleEngine()
    seeded = await engine.apply_mutation(clean_session, InitProjectIntent())
    baseline = await mapspec_store_instance.get_mapspec(clean_session)

    result = await engine.apply_mutation(
        clean_session,
        SetViewIntent(center=[0.0, 0.0], zoom=2),
        origin="agent",
        expected_revision=seeded.mutation_revision - 1,
    )

    assert result.superseded is True
    assert result.origin == "agent"
    stored = await mapspec_store_instance.get_mapspec(clean_session)
    assert stored == baseline
