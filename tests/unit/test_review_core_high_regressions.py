"""Behavioral regressions for the 2026-11-05 high-priority core review fixes."""
from __future__ import annotations

import asyncio
import math
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def test_geographic_resolution_does_not_become_metres():
    from app.services.gis_harness.capability_resolution import build_situation

    geographic = build_situation(profile={"crs": "EPSG:4326", "resolution": 0.001})
    assert geographic.crs_is_geographic is True
    assert geographic.resolution_m_per_px == 0
    projected = build_situation(profile={"crs": "EPSG:3857", "resolution": 30})
    assert projected.resolution_m_per_px == 30
    feet = build_situation(profile={"crs": "EPSG:2263", "resolution": 30})
    assert feet.resolution_m_per_px in (None, 0)


def test_requirement_answer_cannot_persist_unreadable_document():
    from app.services.gis_harness.intent import resolve_map_request_intent
    from app.services.gis_harness.requirement_ir import build_document
    from app.services.gis_harness.requirement_ir.patch import _v_str, PatchValueInvalid
    from app.services.gis_harness.requirement_ir.service import _doc_from

    with pytest.raises(PatchValueInvalid):
        _v_str("aoi.name", "x" * 500)
    document = build_document("成都的小学分布", resolve_map_request_intent("成都的小学分布"), turn=1)
    raw = document.model_dump(mode="json")
    raw["intent"]["aoi"]["name"] = "x" * 1000
    restored = _doc_from(raw)
    assert len(restored.intent.aoi.name) < 1000


@pytest.mark.asyncio
async def test_client_perception_cannot_replace_server_ledgers(monkeypatch):
    from app.services.chat import execution_engine

    writes = AsyncMock()
    monkeypatch.setattr(execution_engine.session_data_manager, "set_map_state", writes)
    await execution_engine.ChatExecutionEngine._persist_map_state(None, "review-sid", {
        "viewport": {"zoom": 5}, "viewport_seq": 7, "layers": ["fake"],
        "_mission_binding": {"org_id": "victim"}, "_gis_memory_org": "victim",
        "_gis_memory_user": "victim-user", "_cartographic_mutation_revision": 900,
    })
    assert writes.await_count == 1
    assert writes.await_args.args == ("review-sid", "viewport", {"zoom": 5})
    assert writes.await_args.kwargs == {"seq": 7}


@pytest.mark.asyncio
async def test_targeted_cartography_state_read_persists_action(monkeypatch):
    from app.services import cartography_runtime
    from app.services.session_data import session_data_manager

    context = {"session_id": "review-action-sid", "mutation_revision": 2, "map_actions": []}

    async def read(sid, key):
        return {"_cartographic_harness_context": context, "_cartographic_mutation_revision": 2}.get(key)

    monkeypatch.setattr(session_data_manager, "get_state_field", read)
    write = AsyncMock(return_value=True)
    monkeypatch.setattr(session_data_manager, "set_map_state", write)
    await cartography_runtime._persist_cartographic_issued_action(
        "review-action-sid", {"action_id": "a", "command": "fly_to"}, session_lock_held=True,
    )
    assert write.await_args.args[2]["map_actions"][0]["action_id"] == "a"


def test_source_profile_rejects_filesystem_path(tmp_path):
    from app.services.spatial_meta_profiler import profile_geojson_source

    private = tmp_path / "private.geojson"
    private.write_text('{"type":"FeatureCollection","features":[]}')
    with pytest.raises(ValueError):
        profile_geojson_source(private)
    with pytest.raises(ValueError):
        profile_geojson_source(str(private))


@pytest.mark.asyncio
async def test_multi_slot_acquisition_cannot_hold_and_wait_deadlock():
    from app.services.tool_dispatch_gates import _MultiSlotAcquire

    semaphore = asyncio.Semaphore(3)
    entered = []

    async def work(n):
        async with _MultiSlotAcquire(semaphore, 2):
            entered.append(n)
            await asyncio.sleep(0)

    await asyncio.wait_for(asyncio.gather(*(work(n) for n in range(4))), timeout=1)
    assert sorted(entered) == [0, 1, 2, 3]


def test_large_closed_polygon_is_not_reported_unclosed():
    from app.services.data_quality.rule_functions import RuleEvalContext, rule_geometry_validity

    ring = [[math.cos(i * math.tau / 600), math.sin(i * math.tau / 600)] for i in range(600)]
    ring.append(ring[0])
    result = rule_geometry_validity(RuleEvalContext(features=[{
        "type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
    }]))
    assert result.status == "pass"
    assert result.metric["unclosed"] == 0
    assert result.metric["rings_skipped_over_cap"] == 0
    assert result.metric["self_intersected"] == 0


@pytest.mark.asyncio
async def test_live_identity_uses_database_organisation(monkeypatch):
    from app.core import auth
    from fastapi.security import HTTPAuthorizationCredentials

    monkeypatch.setattr(auth, "auth_bypass_enabled", lambda: False)
    monkeypatch.setattr(auth, "_load_live_user_state", AsyncMock(return_value=("ok", (0, True, "viewer", 42))))
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=auth.create_access_token({"sub": "u", "org_id": 1}))
    assert (await auth.get_current_user(credentials))["org_id"] == 42
    assert (await auth.get_current_user_optional(credentials))["org_id"] == 42


@pytest.mark.asyncio
async def test_spatial_event_rejects_unowned_binding_before_ingest(monkeypatch):
    from fastapi import HTTPException
    from app.api.routes import mission_runtime, spatial_events

    monkeypatch.setattr(spatial_events.flags, "runtime_enabled", lambda: True)
    monkeypatch.setattr(spatial_events, "_org", lambda user: "o")

    def reject(*args, **kwargs):
        raise HTTPException(status_code=404, detail="session_not_found")

    monkeypatch.setattr(mission_runtime, "_assert_create_bindings", reject)
    with pytest.raises(HTTPException) as caught:
        await spatial_events.ingest_event(spatial_events.IngestBody(
            kind="dataset.updated", subject_type="dataset", subject_key="x", session_id="victim",
        ), user={"user_id": "attacker", "org_id": "o"})
    assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_chat_project_rejects_other_owner(monkeypatch):
    from fastapi import HTTPException
    from app.api.routes.chat import _guard_body_project
    from app.services.project_service import ProjectService

    monkeypatch.setattr(ProjectService, "get_project_with_auth", lambda *args, **kwargs: None)

    async def run_sync(fn):
        return fn(object())

    with pytest.raises(HTTPException) as caught:
        await _guard_body_project(SimpleNamespace(run_sync=run_sync), "victim-project", {"user_id": "a", "org_id": 1})
    assert caught.value.status_code == 404
