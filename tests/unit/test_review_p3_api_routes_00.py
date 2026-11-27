"""P3 审查修复回归测试（模块 app/api/routes）。

每条测试对应 .scratch/review-swarm/fixtasks/P3-app_api_routes-00.md 中的一条
发现；在未修复代码上必须失败。多数通过直接调用路由函数 + 轻量 stub 实现，
避免拉起完整 app/DB。
"""
from __future__ import annotations

import contextlib
import io
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException


# ── atlas_jobs.py:188 ──────────────────────────────────────────────────────


async def test_atlas_submit_runs_off_event_loop(monkeypatch):
    from app.api.routes import atlas_jobs as mod

    async def _verify(*_a, **_k):
        return None

    async def _get_mapspec(_sid):
        return {"type": "FeatureCollection", "features": []}

    async def _hydrate(doc, _sid):
        return doc

    seen: dict = {}

    def _dry_run(_spec, _doc):
        seen["dry_thread"] = threading.current_thread()
        return {"page_count": 0}

    def _submit(_spec, *, origin_session_id=None):
        seen["submit_thread"] = threading.current_thread()
        return {"job_id": "job-1", "status": "submitted"}

    monkeypatch.setattr(mod, "verify_session_owner", _verify)
    monkeypatch.setattr(
        "app.services.mapspec_store.mapspec_store",
        SimpleNamespace(get_mapspec=_get_mapspec),
    )
    monkeypatch.setattr(
        "app.services.publication_export.hydrate_ref_sources", _hydrate)
    monkeypatch.setattr(
        "app.services.atlas_batch.submit.dry_run_plan", _dry_run)
    monkeypatch.setattr(
        "app.services.atlas_batch.submit.submit_atlas_job", _submit)

    body = mod.AtlasSubmitRequest(session_id="sess-atlas-1", atlas={})
    result = await mod.submit_atlas_export_job(
        body, db=MagicMock(), user={"user_id": "u1"}, owner_token=None)

    assert result.job_id == "job-1"
    assert seen["dry_thread"] is not threading.main_thread()
    assert seen["submit_thread"] is not threading.main_thread()


# ── cartogram_authoring.py:151 ─────────────────────────────────────────────


async def test_cartogram_list_layouts_maps_authoring_error(monkeypatch):
    from app.api.routes import cartogram_authoring as mod
    from app.services.cartogram_authoring import AuthoringError

    async def _boom(*_a, **_k):
        raise AuthoringError("layout_not_found", "gone", status_code=404)

    monkeypatch.setattr(mod, "list_layouts", _boom)
    with pytest.raises(HTTPException) as ei:
        await mod.layouts("sess-1", limit=20, conv=MagicMock())
    assert ei.value.status_code == 404


# ── cartographic_assets.py:76 ──────────────────────────────────────────────


def test_cartographic_reference_scan_truncated_is_not_200():
    from app.api.routes import cartographic_assets as mod

    exc = mod._map_catalog_error(
        mod.CartographicAssetError("asset_reference_scan_truncated", "x"))
    assert exc.status_code == 409


# ── chat.py:2719 ───────────────────────────────────────────────────────────


async def test_execute_tool_direct_releases_db_before_dispatch(monkeypatch):
    from app.api.routes import chat as mod
    from app.schemas.chat_schema import ToolExecuteRequest

    db = MagicMock()
    db.close = AsyncMock()

    async def _authz(*_a, **_k):
        return None

    class _Registry:
        def metadata(self, _name):
            return {"tier": 1}

        async def dispatch(self, _name, _args, session_id=None):
            assert db.close.await_count == 1, "db must be released before dispatch"
            return {"success": True}

    monkeypatch.setattr(mod, "get_registry", lambda: _Registry())
    monkeypatch.setattr(
        "app.api.chat_tool_access.authorize_and_audit_tool_execution", _authz)

    req = ToolExecuteRequest(tool="buffer_analysis", arguments={},
                             session_id="sess-1")
    result = await mod.execute_tool_direct(
        req, _user={"user_id": "admin", "role": "admin"},
        owner_token=None, db=db)
    assert result == {"success": True}


# ── config.py:345 ──────────────────────────────────────────────────────────


async def test_upload_skill_uses_exclusive_create(tmp_path, monkeypatch):
    from pathlib import Path

    from app.api.routes import config as mod
    from app.core.config import settings

    monkeypatch.chdir(tmp_path)
    skills = Path("app/skills")
    skills.mkdir(parents=True)
    existing = skills / "atomic_skill.py"
    existing.write_text("original = 1\n", encoding="utf-8")

    # 模拟 exists() 检查之后、写盘之前被并发上传抢先的竞态窗口。
    monkeypatch.setattr(Path, "exists", lambda _self: False)
    monkeypatch.setattr(settings, "ALLOW_RUNTIME_SKILL_UPLOAD", True)
    monkeypatch.setattr(mod, "load_skills", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "app.tools.skills.approve_runtime_skill", lambda *_a, **_k: True)

    from fastapi import UploadFile

    upload = UploadFile(file=io.BytesIO(b"replacement = 2\n"),
                        filename="atomic_skill.py")
    with pytest.raises(HTTPException) as ei:
        await mod.upload_skill(file=upload, _user={"user_id": "admin", "org_id": None})
    assert ei.value.status_code == 409
    assert existing.read_text(encoding="utf-8") == "original = 1\n"


# ── data_delivery.py:172 ───────────────────────────────────────────────────


async def test_delivery_download_anonymous_gets_401(monkeypatch):
    from app.api.routes import data_delivery as mod

    async def _never(*_a, **_k):
        raise AssertionError("authorization resolver must not be reached")

    monkeypatch.setattr(mod, "resolve_download", _never)
    with pytest.raises(HTTPException) as ei:
        await mod.download_delivery(
            "job-1", None,
            user={"user_id": "anonymous", "role": "anonymous"}, owner_token=None)
    assert ei.value.status_code == 401


# ── data_lifecycle.py:188 ──────────────────────────────────────────────────


class _FakeScalars:
    def all(self):
        return []

    def __iter__(self):
        return iter([])


class _FakeDb:
    def execute(self, *_a, **_k):
        return self

    def scalars(self):
        return _FakeScalars()

    def scalar_one(self):
        return 0


class _FakeSessionCtx:
    state = {"active": 0}

    def __enter__(self):
        _FakeSessionCtx.state["active"] += 1
        if _FakeSessionCtx.state["active"] > 1:
            raise AssertionError("nested SessionLocal (two pool connections)")
        return _FakeDb()

    def __exit__(self, *_exc):
        _FakeSessionCtx.state["active"] -= 1
        return False


def test_data_lifecycle_no_nested_pool_session(monkeypatch):
    from app.api.routes import data_lifecycle as mod

    _FakeSessionCtx.state["active"] = 0
    monkeypatch.setattr("app.core.database.SessionLocal", _FakeSessionCtx)
    monkeypatch.setattr(
        "app.services.data_lifecycle.gc_plan.create_gc_plan",
        lambda *_a, **_k: SimpleNamespace(
            id="plan-1", status="draft", scope={}, candidate_count=0,
            candidate_bytes=0, staging_expires_at=None, created_by="u",
            approved_by=None, executed_by=None, job_id=None, result=None,
            plan_tree={}, created_at=None,
        ),
    )
    user = {"user_id": "u1", "org_id": None, "role": "viewer"}

    page = mod.list_lifecycle_objects(
        limit=10, offset=0, kind=None, tier=None, user=user)
    assert page.total == 0

    out = mod.create_plan(mod.GcPlanCreateRequest(), user=user)
    assert out["success"] is True


# ── extensions_marketplace.py:155 ──────────────────────────────────────────


def test_marketplace_size_check_before_opening_blob(tmp_path, monkeypatch):
    from app.api.routes import extensions_marketplace as mod
    from app.extensions_platform.marketplace.store import BLOB_MAX_BYTES

    blob = tmp_path / "blob.tgz"
    blob.write_bytes(b"x")
    digest = "a" * 64

    class _Store:
        def blob_path(self, _digest):
            return blob

        def iter_blob_stream(self, _digest):
            raise AssertionError("stream must not be opened for over-cap blob")

    record = SimpleNamespace(
        status="active",
        versions={"1.0.0": SimpleNamespace(
            digest=digest, size_bytes=BLOB_MAX_BYTES + 1)},
    )
    service = SimpleNamespace(
        store=_Store(),
        get_package=lambda _pid: record,
    )
    monkeypatch.setattr(mod, "_service_or_404", lambda: service)

    with pytest.raises(HTTPException) as ei:
        mod.download_package("pkg-1", "1.0.0", _user={"user_id": "u"})
    assert ei.value.status_code == 500


# ── feature_edits.py:145 ───────────────────────────────────────────────────


async def test_feature_edit_decide_blocked_maps_409(monkeypatch):
    from app.api.routes import feature_edits as mod
    from app.schemas.feature_edit_schema import PlanDecisionRequest

    plan = {"status": "pending", "committable": True}

    async def _get_plan(*_a, **_k):
        return plan

    async def _commit(*_a, **_k):
        raise mod.fe_executor.FeatureEditBlocked("state_invalid", "nope")

    monkeypatch.setattr(mod.fe_executor, "get_plan", _get_plan)
    monkeypatch.setattr(mod.fe_executor, "commit_plan", _commit)
    monkeypatch.setattr(mod, "plan_expired", lambda _p: False)

    with pytest.raises(HTTPException) as ei:
        await mod.decide_feature_edit_plan(
            "sess-1", "plan-1", PlanDecisionRequest(action="approve"),
            idempotency_key=None, _conv=MagicMock())
    assert ei.value.status_code == 409


# ── field_compute.py:54 ────────────────────────────────────────────────────


def test_field_compute_job_incomplete_maps_409():
    from app.api.routes import field_compute as mod
    from app.services.data_preparation.field_compute.contracts import ExecError

    with pytest.raises(HTTPException) as ei:
        mod._raise_typed(ExecError("JOB_INCOMPLETE", "job not converged"))
    assert ei.value.status_code == 409


# ── field_contracts.py:61 ──────────────────────────────────────────────────


async def test_field_contracts_list_maps_typed_error(monkeypatch):
    from app.api.routes import field_contracts as mod
    from app.services.field_contracts.service import FieldContractError

    async def _boom(*_a, **_k):
        raise FieldContractError("contract_not_found", "gone")

    monkeypatch.setattr(mod.fc_service, "list_contracts", _boom)
    with pytest.raises(HTTPException) as ei:
        await mod.list_contracts("sess-1", ref_id=None, _conv=MagicMock())
    assert ei.value.status_code == 404


# ── georeferencing.py:192 ──────────────────────────────────────────────────


async def test_georef_session_mismatch_has_distinct_code():
    from app.api.routes import georeferencing as mod
    from app.schemas.georeferencing_schema import CreatePlanIn

    body = CreatePlanIn(session_id="sess-body-1", source_id="src-1")
    with pytest.raises(HTTPException) as ei:
        await mod.create_georef_plan(
            body, session_id="sess-query-1", _conv=MagicMock())
    assert ei.value.status_code == 422
    assert ei.value.detail["code"] == "SESSION_ID_MISMATCH"


# ── health.py:170 ──────────────────────────────────────────────────────────


def test_health_discloses_pi_worker_split(monkeypatch):
    from app.api.routes import health as mod

    monkeypatch.setattr(mod, "_live_agent_runtime_mode", lambda: ("pi", "ok"))
    monkeypatch.setattr(mod, "_live_agent_runtime", lambda: "pi")
    monkeypatch.setattr(mod, "_pi_workers_alive", lambda: "1/2")
    monkeypatch.setattr(mod, "_pi_vendor_summary", lambda: "built (release)")
    monkeypatch.setattr("app.core.build_info.version_string", lambda: "test")

    resp = mod.health_check()
    assert resp.pi_workers_alive == "1/2"
    assert resp.pi_vendor == "built (release)"


# ── health.py:46 ───────────────────────────────────────────────────────────


def test_health_llm_probe_single_flight(monkeypatch):
    from app.api.routes import health as mod
    import httpx

    calls: list[int] = []

    def _head(_url, timeout=3.0):
        calls.append(1)
        time.sleep(0.15)
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(httpx, "head", _head)
    monkeypatch.setattr("app.core.egress.assert_egress_allowed",
                        lambda *_a, **_k: None)
    mod._llm_last_check = 0.0
    mod._llm_last_result = False

    threads = [threading.Thread(target=mod._check_llm) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1


# ── lakehouse.py:465 ───────────────────────────────────────────────────────


async def test_lakehouse_catalog_clamps_pagination(monkeypatch):
    from app.api.routes import lakehouse as mod
    import app.services.lakehouse.catalog_service as catalog_service

    captured: dict = {}

    def _search(_db, **kwargs):
        captured.update(kwargs)
        return {"items": [], "next_offset": None}

    async def _verify(*_a, **_k):
        return MagicMock()

    monkeypatch.setattr(catalog_service, "search_catalog", _search)
    monkeypatch.setattr(mod, "verify_session_owner", _verify)
    monkeypatch.setattr("app.core.database.SessionLocal",
                        lambda: contextlib.nullcontext(object()))

    await mod.search_lakehouse_catalog(
        owner_type="session", owner_id="sess-owner-1", session_id="sess-owner-1",
        kind="", time_from="", time_to="", producer="", include_revoked=False,
        limit=-5, offset=-9, _user={"user_id": "u"}, owner_token=None,
        db=MagicMock())
    assert captured["limit"] == 1
    assert captured["offset"] == 0

    captured.clear()
    await mod.search_lakehouse_catalog(
        owner_type="session", owner_id="sess-owner-1", session_id="sess-owner-1",
        kind="", time_from="", time_to="", producer="", include_revoked=False,
        limit=1_000_000, offset=0, _user={"user_id": "u"}, owner_token=None,
        db=MagicMock())
    assert captured["limit"] == 100


# ── layer.py:1218 ──────────────────────────────────────────────────────────


async def test_terrain_tilejson_malformed_receipt_bbox_degrades(monkeypatch):
    from app.api.routes import layer as mod

    async def _budget(_sid):
        return None

    async def _get_ref_data(_sid, _ref, owner_token=None):
        return SimpleNamespace(success=True, data={
            "kind": "terrain_dem",
            "file_path": "/tmp/dem.tif",
            "receipt": {"bbox_epsg4326": ["not", "a", "number", "pair"]},
        })

    monkeypatch.setattr(mod, "_layer_data_budget", _budget)
    monkeypatch.setattr(
        mod, "session_data_manager",
        SimpleNamespace(get_ref_data=_get_ref_data))
    monkeypatch.setattr(
        "app.services.scoped_raster_paths.resolve_scoped_raster_path",
        lambda _path, session_id=None: "/tmp/dem-safe.tif")
    monkeypatch.setattr(
        mod, "_derive_terrain_header",
        lambda _path: ([-1.0, -1.0, 1.0, 1.0], 30.0))
    monkeypatch.setattr("app.core.signing.signed_terrain_query",
                        lambda *_a, **_k: "")

    request = MagicMock()
    request.state = SimpleNamespace(terrain_capability_exp=None)
    resp = await mod.get_terrain_tilejson(
        request, "ref:terrain-1", session_id="sess-terrain-1", token=None,
        owner_token=None, _conv=SimpleNamespace(owner_token=None),
        if_none_match=None)
    assert resp.status_code == 200
    body = json.loads(resp.body)
    assert body["bounds"] == [-1.0, -1.0, 1.0, 1.0]


# ── map.py:500 ─────────────────────────────────────────────────────────────


def test_render_pdf_cleans_temp_file_on_write_failure(tmp_path, monkeypatch):
    from app.api.routes import map as mod

    monkeypatch.setattr(
        "app.lib.cartography.pdf_renderer.generate_map_pdf",
        lambda **_k: b"%PDF-1.4")
    monkeypatch.setattr(mod, "_exports_dir_write", lambda: str(tmp_path))

    def _fsync(_fd):
        raise OSError("disk full")

    monkeypatch.setattr(mod.os, "fsync", _fsync)

    with pytest.raises(OSError):
        mod._render_pdf_to_file("out.pdf", b"img", None, None, None, None)
    assert list(tmp_path.glob("*.pdf")) == []


# ── map.py:797 ─────────────────────────────────────────────────────────────


async def test_mixed_export_discloses_raster_layer_truncation(monkeypatch):
    from app.api.routes import map as mod
    from app.schemas.map_schema import MixedMapExportRequest

    async def _export(*_a, **_k):
        return object()

    async def _persist(*_a, **_k):
        return {
            "manifest": {
                "mapspec_revision": 1,
                "raster_layers": [{"layer_id": f"r{i}"} for i in range(13)],
            },
            "filename": "out.png",
            "download_url": "/d",
            "manifest_url": "/m",
            "bytes": 10,
            "media_type": "image/png",
        }

    async def _verify(*_a, **_k):
        return None

    async def _principal(*_a, **_k):
        return {"user_id": "u"}

    monkeypatch.setattr(
        "app.services.mixed_map_export.export_mixed_map", _export)
    monkeypatch.setattr(
        "app.services.mixed_map_export.persist_mixed_export", _persist)
    monkeypatch.setattr(
        "app.services.auth_history_bridge.verify_session_owner", _verify)
    monkeypatch.setattr(mod, "_export_principal", _principal)

    body = MixedMapExportRequest(session_id="sess-1", format="png")
    out = await mod.export_map_as_mixed(
        body, _user={"user_id": "u", "role": "viewer", "org_id": None},
        owner_token=None, db=MagicMock())
    assert len(out["raster_layers"]) == 12
    assert out["raster_layers_truncated"] is True


# ── pi_interactions.py:425 ─────────────────────────────────────────────────


async def test_pi_secret_non_ascii_header_is_401(monkeypatch):
    from starlette.requests import Request

    from app.api.routes import pi_interactions as mod

    monkeypatch.setattr("app.core.bridge_secret.get_bridge_secret",
                        lambda: "expected-secret")
    scope = {
        "type": "http", "method": "POST", "path": "/pi/interaction-resolve",
        "headers": [(b"x-pi-bridge-secret", "sécret".encode("latin-1"))],
        "client": ("127.0.0.1", 1), "query_string": b"",
        "server": ("test", 80), "scheme": "http",
    }
    request = Request(scope)
    req = mod.InteractionResolveRequest(
        toolCallId="t", turnToken="tt", envelopeId="e", answerKind="value")
    with pytest.raises(HTTPException) as ei:
        await mod.resolve_interaction(req, request)
    assert ei.value.status_code == 401


# ── project.py:1224/1242 ───────────────────────────────────────────────────


def test_repair_null_operations_and_issue_code_truncation(monkeypatch):
    from app.api.routes import project as mod
    import app.services.data_quality.repair_execution as repair_mod

    captured: dict = {}

    async def _execute_repair(**kwargs):
        captured.update(kwargs)
        fc = {"type": "FeatureCollection", "features": []}
        return {
            "operations_applied": [], "ops_evidence": {}, "logs": [],
            "logs_count": 0, "feature_count": 0, "feature_count_before": 0,
            "repaired_ref": None, "ref_registration_error": None,
            "repair_evidence": {"changed": False}, "repaired_geojson": fc,
            "content_digest_after": "d", "output_crs": "EPSG:4326",
        }

    monkeypatch.setattr(
        mod.ProjectService, "get_project_for_write",
        staticmethod(lambda **_k: SimpleNamespace(id="p1", owner_id="u")))
    monkeypatch.setattr(repair_mod, "execute_repair", _execute_repair)
    monkeypatch.setattr(repair_mod, "persist_repair_lineage",
                        lambda *_a, **_k: "art-1")

    payload = {
        "geojson": {"type": "FeatureCollection", "features": []},
        "operations": None,
        "issue_codes": [f"code-{i}" for i in range(20)],
    }
    out = mod.repair_spatial_dataset(
        "p1", payload, db=MagicMock(), user={"user_id": "u", "org_id": None},
        owner_token=None)
    assert captured["operations"] == ["make_valid", "remove_empty"]
    assert captured["issue_codes"] == [f"code-{i}" for i in range(16)]
    assert out["issue_codes_truncated"] is True


# ── project.py:1927 ────────────────────────────────────────────────────────


@pytest.mark.parametrize("exists,status", [(False, 404), (True, 409)])
async def test_restore_snapshot_error_mapping_uses_verification(
        monkeypatch, exists, status):
    from app.api.routes import project as mod
    from app.schemas.project_schema import WorkspaceSnapshotRestoreRequest

    async def _project(*_a, **_k):
        return object()

    async def _session(*_a, **_k):
        return None

    async def _restore(*_a, **_k):
        return {"error": "snapshot vanished", "verification": {"exists": exists}}

    service = SimpleNamespace(restore_snapshot=_restore)
    monkeypatch.setattr(mod, "_get_project_for_write_offloaded", _project)
    monkeypatch.setattr(mod, "_verify_session_access", _session)
    monkeypatch.setattr(
        "app.services.workspace.snapshot.get_workspace_snapshot_service",
        lambda: service)

    with pytest.raises(HTTPException) as ei:
        await mod.restore_workspace_snapshot(
            "p1", "snap-1",
            WorkspaceSnapshotRestoreRequest(session_id="sess-1"),
            db=MagicMock(), user={"user_id": "u"}, owner_token=None)
    assert ei.value.status_code == status


# ── project_data_refresh.py:196 ────────────────────────────────────────────


async def test_data_refresh_list_proposals_maps_refresh_error(monkeypatch):
    from app.api.routes import project_data_refresh as mod
    from app.services.project_runtime.refresh.service import RefreshError

    async def _auth(*_a, **_k):
        return object()

    async def _boom(**_k):
        raise RefreshError("store_unavailable", "down", status=503)

    monkeypatch.setattr(mod, "_auth_project", _auth)
    monkeypatch.setattr(
        mod, "_svc", lambda: SimpleNamespace(list_proposals=_boom))

    with pytest.raises(HTTPException) as ei:
        await mod.list_proposals(
            "p1", db=MagicMock(), user={"user_id": "u"})
    assert ei.value.status_code == 503


# ── project_runtime.py:66 ──────────────────────────────────────────────────


async def test_auth_project_does_not_share_request_session_across_threads(
        monkeypatch):
    from app.api.routes import project_runtime as mod

    captured: dict = {}

    def _get(*, db, **_kwargs):
        captured["db"] = db
        return SimpleNamespace(id="p1", owner_id="u")

    monkeypatch.setattr(
        mod.ProjectService, "get_project_with_auth", staticmethod(_get))

    caller_db = object()
    project = await mod._auth_project(caller_db, "p1", {"user_id": "u"})
    assert project.owner_id == "u"
    assert captured["db"] is not caller_db


# ── raster_authoring.py:135 ────────────────────────────────────────────────


async def test_raster_descriptor_surfaces_service_value_error(monkeypatch):
    from app.api.routes import raster_authoring as mod

    async def _describe(*_a, **_k):
        raise ValueError("band 9 out of range")

    monkeypatch.setattr(mod, "describe_raster", _describe)
    with pytest.raises(HTTPException) as ei:
        await mod.descriptor(
            "ref-1", session_id="sess-raster-1", histogram_bands="1",
            buckets=None, owner_token=None,
            conv=SimpleNamespace(owner_token=None))
    assert ei.value.status_code == 400
    assert "band 9" in str(ei.value.detail)


# ── raster_sampling.py:137 ─────────────────────────────────────────────────


async def test_raster_sampling_list_plans_maps_typed_error(monkeypatch):
    from app.api.routes import raster_sampling as mod
    from app.services.raster_sampling.contracts import RasterSamplingError

    async def _boom(_sid):
        raise RasterSamplingError(
            "SOURCE_UNAVAILABLE", "plan store down", status_code=503)

    monkeypatch.setattr(mod.service, "list_plans_payload", _boom)
    with pytest.raises(HTTPException) as ei:
        await mod.list_plans("sess-sampling-1", _conv=MagicMock())
    assert ei.value.status_code == 503


# ── security_admin.py:100 ──────────────────────────────────────────────────


async def test_set_org_quota_concurrent_delete_is_409(monkeypatch):
    from sqlalchemy.exc import IntegrityError

    from app.api.routes import security_admin as mod

    async def _admin_org(_admin, _db, org_id):
        return org_id

    class _Result:
        def __init__(self, value):
            self._value = value

        def scalar_one_or_none(self):
            return self._value

    org_exists = _Result(1)
    quota_missing = _Result(None)
    quota_missing_after_race = _Result(None)

    class _CM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[org_exists, quota_missing, quota_missing_after_race])
    db.begin_nested = MagicMock(return_value=_CM())
    db.flush = AsyncMock(
        side_effect=IntegrityError("insert", {}, Exception("dup")))
    db.add = MagicMock()

    monkeypatch.setattr(mod, "_admin_org", _admin_org)
    body = mod.OrgQuotaUpdate(
        max_storage_bytes=1, max_concurrent_tasks=1, rate_limit_per_min=1)
    with pytest.raises(HTTPException) as ei:
        await mod.set_org_quota(
            "1", body, MagicMock(), scope_user={"user_id": "admin"},
            _admin={"user_id": "admin"}, db=db)
    assert ei.value.status_code == 409
