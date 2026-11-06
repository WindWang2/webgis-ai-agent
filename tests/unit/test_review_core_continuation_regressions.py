"""Behavioral regressions for the resumed all-issues core review."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def test_resume_kernel_lease_survives_old_metadata(tmp_path, monkeypatch):
    from app.services.project_runtime import store as mod

    monkeypatch.setattr(mod, "runtime_snapshots_dir", lambda _: tmp_path)
    first, second = mod.ProjectRuntimeStore(), mod.ProjectRuntimeStore()
    assert first.acquire_resume_lease("p", "prs-lock")[0]
    path = first._lease_path("p", "prs-lock")
    path.write_text(json.dumps({"pid": 999999, "acquired_at": 0}))
    assert not second.acquire_resume_lease("p", "prs-lock")[0]
    first.release_resume_lease("p", "prs-lock")
    assert second.acquire_resume_lease("p", "prs-lock")[0]
    second.release_resume_lease("p", "prs-lock")


def test_bad_geometry_cannot_shift_next_feature_area():
    from app.services.simulation.whatif.spatial_diff_engine import diff_layers

    def feature(fid, geometry):
        return {"type": "Feature", "properties": {"id": fid}, "geometry": geometry}

    bad = feature("bad", {"type": "Polygon", "coordinates": []})
    small = feature("small", {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]})
    large = feature("large", {"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 0]]]})
    result = diff_layers({}, {"green": {"type": "FeatureCollection", "features": [bad, small, large]}})["green"]
    assert [f["properties"]["id"] for f in result.added_features] == ["bad", "small", "large"]
    assert result.added_area_m2 > 2.5e10


def test_no_evidence_has_no_recommendation_and_zero_cost_has_no_roi():
    from app.services.simulation.whatif.prescriptive_advisor import BranchDiffResult, PrescriptiveAdvisor
    from app.services.spatial_decision.models import MetricDeltaV2

    advisor = PrescriptiveAdvisor()
    empty = advisor.advise({}, [BranchDiffResult(branch_id="empty")])
    assert empty.recommended_branch_id is None and empty.confidence == 0
    metric = MetricDeltaV2(metric_key="facility_count", metric_name="facilities", baseline=1,
                           simulated=2, delta_abs=1, delta_pct=100, unit="count", missing_baseline=False)
    known = advisor.advise({}, [BranchDiffResult(branch_id="known", metric_deltas=[metric], cost_proxy=0)])
    assert known.roi_sensitivity[0]["roi_index"] is None


def test_durable_simulation_slice_is_resolvable_and_scoped(tmp_path, monkeypatch):
    from app.core.config import settings
    from app.services.simulation.layers import DurableGeoJSONTemporalSink, LayerDraft

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    sink = DurableGeoJSONTemporalSink("owned-session")
    product = sink.emit(LayerDraft(product_id="p1", tick=1, sim_seconds=1,
                                  timestamp=None, layer_kind="network_edges", crs="EPSG:4326",
                                  features=[], bbox=None, truncated=False))
    assert product.ref is None and product.format == "geojson"
    assert json.loads((tmp_path / "owned-session/simulation/p1.geojson").read_text())["features"] == []
    assert product.uri == str(tmp_path / "owned-session/simulation/p1.geojson")


def test_array_budget_is_checked_before_float_parsing():
    from pydantic import ValidationError
    from app.services.simulation.contracts import RasterFieldSpec

    with pytest.raises(ValidationError, match="262144"):
        RasterFieldSpec(kind="array", values=[[object()] * 1000] * 300)


@pytest.fixture
def gc_db(tmp_path, monkeypatch):
    from app.models.data_lifecycle import GcPlan, LifecycleObject, LifecyclePolicy
    from app.services.data_lifecycle import gc_plan, adapters

    engine = create_engine(f"sqlite:///{tmp_path / 'gc.sqlite'}")
    for model in (GcPlan, LifecycleObject, LifecyclePolicy):
        model.__table__.create(engine)
    monkeypatch.setattr(gc_plan, "STAGING_DIR", tmp_path / "staging")
    for name, directory in (("_artifact_root", "artifacts"), ("_cog_root", "cog"),
                            ("_data_root", "data"), ("_spill_root", "spill")):
        root = tmp_path / directory
        root.mkdir()
        monkeypatch.setattr(adapters, name, lambda root=root: root)
    with sessionmaker(engine, expire_on_commit=False)() as db:
        yield db
    engine.dispose()


def test_purge_is_durable_and_cannot_claim_rollback(gc_db, tmp_path):
    from app.models.data_lifecycle import GcPlan
    from app.services.data_lifecycle.gc_plan import GcPlanError, purge_staging, rollback_plan

    plan = GcPlan(status="done", scope={}, plan_tree={}, staging_expires_at=datetime.now(timezone.utc) - timedelta(hours=1))
    gc_db.add(plan)
    gc_db.commit()
    staging = tmp_path / "staging" / plan.id
    staging.mkdir(parents=True)
    (staging / "lost.txt").write_text("gone")
    result = purge_staging(plan, db=gc_db)
    gc_db.expire_all()
    assert plan.result["purged_at"] and plan.result["rollback_available"] is False
    assert result["purged"] and "staging_dir" not in result
    with pytest.raises(GcPlanError, match="purged"):
        rollback_plan(gc_db, plan)


def test_spill_and_artifact_sidecar_roundtrip_use_recorded_lane(gc_db, tmp_path):
    from app.models.data_lifecycle import GcPlan
    from app.services.data_lifecycle.gc_plan import execute_plan, rollback_plan

    spill = tmp_path / "spill/s1/data.json"
    spill.parent.mkdir()
    spill.write_text("payload")
    (tmp_path / "artifacts/a.tif").write_bytes(b"image")
    (tmp_path / "artifacts/a.meta").write_text("metadata")
    import os
    from app.models.data_lifecycle import LifecycleObject, LifecyclePolicy
    old = datetime.now(timezone.utc) - timedelta(days=365)
    for path in (spill, tmp_path / "artifacts/a.tif", tmp_path / "artifacts/a.meta"):
        os.utime(path, (old.timestamp(), old.timestamp()))
    for kind, object_id in (("fabric_materialization", "s1/data.json"), ("artifact_cache", "a.tif")):
        gc_db.add(LifecycleObject(kind=kind, object_id=object_id, owner_scope="", last_used_at=old, tier="cold"))
        gc_db.add(LifecyclePolicy(name=kind, kind=kind, enabled=True, action="stage_delete"))
    gc_db.commit()
    plan = GcPlan(status="approved", scope={"staging_hours": 72}, plan_tree={"objects": [
        {"kind": "fabric_materialization", "object_id": "s1/data.json", "lane": "spill"},
        {"kind": "artifact_cache", "object_id": "a.tif"},
    ]})
    gc_db.add(plan)
    gc_db.commit()
    result = execute_plan(gc_db, plan)
    assert result["moved_count"] == 2 and not spill.exists()
    assert plan.staging_expires_at.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc) + timedelta(hours=71)
    restored = rollback_plan(gc_db, plan)
    assert restored["restored_count"] == 3
    assert spill.read_text() == "payload"
    assert not (tmp_path / "data/s1/data.json").exists()
    assert (tmp_path / "artifacts/a.meta").read_text() == "metadata"


def test_terrain_capability_is_short_lived_and_ref_session_bound(monkeypatch):
    from types import SimpleNamespace
    from urllib.parse import parse_qs
    from app.core import signing

    clock = SimpleNamespace(time=lambda: 1000)
    monkeypatch.setattr(signing, "time", clock)
    query = parse_qs(signing.signed_terrain_query("session-a", "ref:terrain-a")[1:])
    assert "token" not in query
    exp, sig = query["exp"][0], query["sig"][0]
    assert 540 <= int(exp) - clock.time() <= 600
    assert signing.verify_signature(signing.terrain_capability_path("session-a", "ref:terrain-a"), exp, sig)
    assert not signing.verify_signature(signing.terrain_capability_path("session-b", "ref:terrain-a"), exp, sig)
    assert not signing.verify_signature(signing.terrain_capability_path("session-a", "ref:terrain-b"), exp, sig)
    clock.time = lambda: int(exp) + 1
    assert not signing.verify_signature(signing.terrain_capability_path("session-a", "ref:terrain-a"), exp, sig)


@pytest.mark.asyncio
async def test_quota_guard_serializes_check_and_committed_write(monkeypatch):
    from app.services import org_quota
    from app.core import database, tenancy

    @asynccontextmanager
    async def sessions():
        yield object()

    monkeypatch.setattr(database, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(tenancy, "effective_org_id", AsyncMock(return_value="same-org"))
    active = 0
    peak = 0

    async def request():
        nonlocal active, peak
        async with org_quota.serialized_admission({"user_id": "u"}):
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1

    await asyncio.gather(request(), request(), request())
    assert peak == 1


@pytest.mark.asyncio
async def test_upload_rejects_foreign_session_before_read(monkeypatch):
    from fastapi import HTTPException
    from app.api.routes import upload

    @asynccontextmanager
    async def sessions():
        yield object()

    monkeypatch.setattr(upload, "async_db_session", sessions)
    monkeypatch.setattr(upload, "verify_session_owner", AsyncMock(side_effect=HTTPException(status_code=404)))
    file = SimpleNamespace(filename="dem.tif", read=AsyncMock())
    with pytest.raises(HTTPException) as exc:
        await upload.upload_files(files=[file], session_id="foreign-session", crs=None,
            dedup=True, register_ref=False, vertical_datum=None, vertical_unit=None,
            owner_token=None, x_session_id=None, _user={"user_id": "u"}, request=None)
    assert exc.value.status_code == 404
    file.read.assert_not_called()


def test_profiler_preserves_nested_property_samples_and_long_inline_json():
    from app.services.spatial_meta_profiler import profile_geojson_source

    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 1]},
         "properties": {"tags": ["a"], "metadata": {"key": "x"}, "long": "x" * 5000}}
    ]}
    profile = profile_geojson_source(json.dumps(fc))
    assert profile["fields"]["tags"]["sampleValues"] == [["a"]]
    assert profile["fields"]["metadata"]["sampleValues"] == [{"key": "x"}]


def test_export_duration_observed_when_renderer_fails(monkeypatch):
    from app.services import publication_export
    from app.core import sre_metrics
    from unittest.mock import Mock

    observe = Mock()
    monkeypatch.setattr(sre_metrics, "observe_export_duration", observe)
    monkeypatch.setattr(publication_export, "_render_publication_pdf", Mock(side_effect=ValueError("render failed")))
    with pytest.raises(ValueError, match="render failed"):
        publication_export.render_publication_pdf({})
    assert observe.call_count == 1
    assert observe.call_args.args[0] >= 0


def test_signed_task_identity_cannot_be_retargeted_or_forged(monkeypatch):
    from app.services.geocompute import task_identity
    from app.services.geocompute.errors import GeoComputeError

    monkeypatch.setattr(task_identity.time, "time", lambda: 1000)
    kwargs = {"caller": {"user_id": "a", "org_id": "org-a"}, "session_id": "sa",
              "node": {"node_id": "n", "params": {"table": "owned"}}, "owner_scope": "owner-a"}
    signature = task_identity.sign_task_identity(kwargs)
    task_identity.verify_task_identity(kwargs, signature)
    for mutation in ({"caller": {"user_id": "admin"}}, {"session_id": "other"},
                     {"node": {"node_id": "n", "params": {"table": "foreign"}}}):
        with pytest.raises(GeoComputeError, match="unverified"):
            task_identity.verify_task_identity({**kwargs, **mutation}, signature)
    monkeypatch.setattr(task_identity.time, "time", lambda: 100000)
    with pytest.raises(GeoComputeError, match="unverified"):
        task_identity.verify_task_identity(kwargs, signature)


def test_corrupt_redis_log_records_do_not_hide_good_neighbors():
    from app.services.session_redis_maintenance import decode_json_objects

    values = [b'{"event":"a"}', b'not-json', b'\xff', b'[]', None, b'{"event":"b"}']
    assert decode_json_objects(values, source="regression") == [{"event": "a"}, {"event": "b"}]


@pytest.mark.asyncio
async def test_history_context_rejects_different_live_user(monkeypatch):
    from app.services.history_service_async import AsyncHistoryService

    conv = SimpleNamespace(user_id="owner", owner_token="secret", messages=[])
    svc = AsyncHistoryService(db=object())
    monkeypatch.setattr(svc, "_get_or_create_with_messages", AsyncMock(return_value=conv))
    denied = await svc.load_context("session", user_id="intruder")
    assert denied.raw_conversation is None and denied.owner_token is None
    assert denied.llm_messages == []


def test_wrapped_feature_page_bbox_and_malformed_event_bbox():
    from app.services.feature_pages import parse_bbox_param, _feature_intersects_bbox
    from app.services.llm_result_formatter import slim_event_result

    box = parse_bbox_param("170,-10,-170,10")
    def point(lon):
        return {"geometry": {"type": "Point", "coordinates": [lon, 0]}}
    assert _feature_intersects_bbox(point(175), box)
    assert _feature_intersects_bbox(point(-175), box)
    assert not _feature_intersects_bbox(point(0), box)
    assert "bbox" not in slim_event_result({"bbox": "bad,2,3,4", "success": True})
    assert "bbox" not in slim_event_result({"bbox": "0,1,NaN,3", "success": True})


@pytest.mark.asyncio
async def test_geocode_bad_provider_index_preserves_valid_neighbor():
    from app.services.geocode_strategy import GeocodeProviderStrategy

    async def provider(addresses, **kwargs):
        return {"results": [{"index": 100, "lon": 100, "lat": 30},
                            {"index": -1, "lon": 100, "lat": 30},
                            {"index": 0, "lon": 110, "lat": 20}],
                "errors": [{"index": "oops"}]}
    result, _ = await GeocodeProviderStrategy().geocode_addresses(["address"], batch_geocode=provider)
    assert result[0].status == "ok" and result[0].lon == 110


def test_webhook_limit_precedes_json_validation():
    from fastapi import FastAPI, APIRouter
    from fastapi.testclient import TestClient
    from app.api.bounded_webhook import BoundedWebhookRoute

    router = APIRouter(route_class=BoundedWebhookRoute)
    @router.post("/webhook")
    async def endpoint(payload: dict):
        return payload
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        # Malformed JSON would normally be 422. The byte cap runs first.
        assert client.post("/webhook", content=b"x" * 8193).status_code == 413
        assert client.post("/webhook", json={"ok": True}).json() == {"ok": True}


@pytest.mark.asyncio
async def test_rejecting_committed_visual_repair_is_conflict(monkeypatch):
    from fastapi import HTTPException
    from app.api.routes.visual_repairs import reject_visual_repair
    from app.schemas.visual_repair_schema import VisualRepairRejectRequest
    from app.services.gis_harness.visual_observation import repair_bridge, repair_decisions

    monkeypatch.setattr(repair_bridge, "load_proposal", AsyncMock(return_value={"proposal_id": "proposal-123"}))
    monkeypatch.setattr(repair_decisions, "load_decisions", AsyncMock(return_value=[{"proposal_id": "proposal-123", "decision": "approved"}]))
    save = AsyncMock()
    monkeypatch.setattr(repair_bridge, "save_proposal", save)
    with pytest.raises(HTTPException) as error:
        await reject_visual_repair("visual-session", VisualRepairRejectRequest(proposal_id="proposal-123"), _conv=object())
    assert error.value.status_code == 409
    save.assert_not_called()


def test_lost_slim_result_cannot_be_resolved_as_real_tool_input():
    from app.services.planning.deps import MissingRefError, resolve_arg_refs

    lost = {"s1": {"__slim__": True, "ref": "ref:lost", "keys": ["data"]}}
    for ref in ("${s1}", "${s1.keys}", "${s1.ref}"):
        with pytest.raises(MissingRefError):
            resolve_arg_refs(ref, lost)


def test_cluster_resource_hint_cannot_be_zero_or_exceed_int64():
    from app.services.geocompute.cluster.store import _estimate_from_snapshot, ClusterBackpressureError
    from sqlalchemy.dialects import postgresql
    from app.models.db_model import GeoComputeClusterRun, GeoComputeResourceUsage

    rows, bytes_ = _estimate_from_snapshot({"nodes": [{"parameters": {"features": [{}, {}]}, "estimate": {"rows": 0, "bytes": 0}}]})
    assert rows >= 2 and bytes_ >= 1024
    with pytest.raises(ClusterBackpressureError, match="64-bit"):
        _estimate_from_snapshot({"nodes": [{"estimate": {"bytes": 2**63}}]})
    for table, column in ((GeoComputeClusterRun, "reserved_bytes"), (GeoComputeResourceUsage, "usage_rows")):
        assert table.__table__.c[column].type.compile(dialect=postgresql.dialect()) == "BIGINT"


@pytest.mark.asyncio
async def test_anonymous_export_has_owner_check_and_resource_scoped_download(tmp_path, monkeypatch):
    from io import BytesIO
    from fastapi import FastAPI, UploadFile, HTTPException
    from fastapi.testclient import TestClient
    from app.api.routes import map as route
    from app.core.database import Base, Engine, SessionLocal, AsyncSessionLocal
    from app.models.db_model import Conversation

    Base.metadata.create_all(Engine)
    with SessionLocal() as db:
        db.merge(Conversation(id="anon-export-regression", owner_token="owner-export-secret"))
        db.commit()
    monkeypatch.setattr("app.services.export_paths.exports_root", lambda: tmp_path)
    monkeypatch.setattr(route, "_record_lineage", AsyncMock(return_value=None))
    async with AsyncSessionLocal() as db:
        with pytest.raises(HTTPException):
            await route._export_principal({"user_id": "anonymous"}, "anon-export-regression", "wrong", db)
        from PIL import Image
        image = BytesIO()
        Image.new("RGB", (1, 1)).save(image, format="PNG")
        png = image.getvalue()
        response = await route.upload_map_export(UploadFile(BytesIO(png), filename="map.png"),
            title=None, render_diagnostics=None, session_id="anon-export-regression", dpi=72,
            _user={"user_id": "anonymous"}, owner_token="owner-export-secret", db=db)
    assert "sig=" in response.url and "owner-export-secret" not in response.url
    app = FastAPI()
    app.include_router(route.router, prefix="/api/v1")
    with TestClient(app) as client:
        assert client.get(response.url).content == png
        assert client.get(response.url.split("?")[0]).status_code == 401


def test_publication_timeout_reaps_disposable_worker():
    import multiprocessing
    from app.services.publication_render_worker import render_publication_bounded, _SLOT

    before = {process.pid for process in multiprocessing.active_children()}
    with pytest.raises(TimeoutError):
        render_publication_bounded({}, timeout_s=0.001)
    assert {process.pid for process in multiprocessing.active_children()} == before
    assert _SLOT.acquire(blocking=False)
    _SLOT.release()
