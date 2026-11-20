"""Behavior boundaries for the nine delegated #1860 API findings."""
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request


@pytest.fixture
def db_factory(tmp_path):
    from app.core.database import Base
    import app.models.data_lifecycle  # noqa: F401
    engine = create_engine(f"sqlite:///{tmp_path / 'api-review.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.mark.parametrize("model,kwargs", [
    ("ProjectCreate", {"name": "x", "description": "x" * 16001}),
    ("ProjectUpdate", {"metadata_json": {"large": "中" * 90000}}),
    ("DatasetAttach", {"name": "x", "source_type": "upload", "source_ref": "x" * 256}),
    ("DatasetAttach", {"name": "x", "source_type": "upload", "schema_profile": {"large": "x" * 270000}}),
    ("WorkflowCreate", {"name": "x", "graph_spec": {"steps": []}, "inputs_schema": {"large": "x" * 270000}}),
    ("WorkflowGraphSpec", {"steps": [{"step_id": str(i), "tool_name": "x"} for i in range(201)]}),
    ("WorkflowGraphSpec", {"steps": [{"step_id": "x", "tool_name": "x", "args_template": {"big": "x" * 1100000}}]}),
    ("QualityAuditRequest", {"geojson": {"features": [{}] * 10001}}),
    ("QualityAuditRequest", {"geojson": {"features": [], "large": "x" * 2100000}}),
    ("MapProductVersionCreate", {"artifact_ids": [str(i) for i in range(65)]}),
])
def test_request_budgets_reject_oversized_authoring_fields(model, kwargs):
    from app.schemas import project_schema
    with pytest.raises(ValidationError):
        getattr(project_schema, model).model_validate(kwargs)


@pytest.mark.parametrize("url", ["javascript:alert(1)", "//external.example/x", "http://external.example/x", "/\\external/x"])
def test_template_thumbnail_rejects_active_or_cross_scheme_urls(url):
    from app.schemas.template_schema import CreateTemplateRequest
    with pytest.raises(ValidationError):
        CreateTemplateRequest(name="x", kind="basemap", payload={"providerId": "osm"}, thumbnail_url=url)


@pytest.mark.asyncio
async def test_debug_is_the_only_error_detail_switch_and_legacy_strips_unserializable_context(monkeypatch):
    from app.core import exception as errors
    request = Request({"type": "http", "method": "POST", "path": "/api/v1/check",
                       "headers": [(b"x-error-envelope", b"detail")]})
    monkeypatch.setattr(errors.settings, "DEBUG", False)
    monkeypatch.setattr(errors.settings, "ENV", "staging")
    response = await errors.global_exception_handler(request, RuntimeError("database-password"))
    assert b"database-password" not in response.body and b"traceback" not in response.body
    validation = RequestValidationError([{"type": "value_error", "loc": ("body", "field"), "msg": "invalid",
                                          "input": "private-input", "ctx": {"error": ValueError("private-context")}}])
    response = await errors.unified_validation_exception_handler(request, validation)
    assert json.loads(response.body)["detail"] == "请求参数校验失败"
    monkeypatch.setattr(errors.settings, "DEBUG", True)
    response = await errors.unified_validation_exception_handler(request, validation)
    assert json.loads(response.body)["detail"][0]["msg"] == "invalid"
    assert b"private-input" not in response.body and b"private-context" not in response.body


@pytest.mark.asyncio
async def test_workflow_existence_is_404_and_existing_engine_conflict_is_409(monkeypatch):
    from app.api.routes import project
    from app.schemas.project_schema import WorkflowRunRequest
    monkeypatch.setattr(project, "_get_project_for_write_offloaded", AsyncMock(return_value=object()))
    monkeypatch.setattr(project, "get_tool_registry", lambda: object())
    engine = AsyncMock(side_effect=ValueError("execution state conflict"))
    monkeypatch.setattr(project, "_run_workflow_engine", engine)
    monkeypatch.setattr("app.services.workflow_revision_queries.workflow_belongs_to_project", lambda *args: False)
    with pytest.raises(HTTPException) as missing:
        await project.run_workflow("project", "workflow", WorkflowRunRequest(), {"user_id": "u"})
    assert missing.value.status_code == 404
    engine.assert_not_awaited()
    monkeypatch.setattr("app.services.workflow_revision_queries.workflow_belongs_to_project", lambda *args: True)
    with pytest.raises(HTTPException) as conflict:
        await project.run_workflow("project", "workflow", WorkflowRunRequest(), {"user_id": "u"})
    assert conflict.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["restore", "rerun"])
async def test_map_product_rerun_errors_do_not_expose_internal_exception(monkeypatch, operation):
    from app.api.routes import project
    from app.schemas.project_schema import MapProductRestoreRequest
    monkeypatch.setattr(project.ProjectService, "get_project_for_write", lambda **kwargs: object())
    monkeypatch.setattr(project.MapProductService, "get_version", lambda *args: SimpleNamespace(version_no=1, workflow_run_id="run"))
    monkeypatch.setattr(project, "get_tool_registry", lambda: object())
    monkeypatch.setattr(project, "_run_workflow_engine", AsyncMock(side_effect=RuntimeError("postgres://private-password@internal")))
    with pytest.raises(HTTPException) as failed:
        if operation == "restore":
            await project.restore_map_product_version("p", 1, MapProductRestoreRequest(session_id="s", mode="full"),
                                                      object(), {"user_id": "u"}, None)
        else:
            await project.rerun_map_product_version("p", 1, object(), {"user_id": "u"})
    assert failed.value.status_code == 409
    assert "private-password" not in str(failed.value.detail)


def test_revision_pagination_is_performed_in_database(db_factory):
    from app.models.project import Project, Workflow, WorkflowRevision
    from app.services.project_service import ProjectService
    with db_factory() as db:
        db.add(Project(id="p", name="p", owner_id="u"))
        db.add(Workflow(id="w", project_id="p", name="w", graph_spec={}))
        for index in range(30):
            db.add(WorkflowRevision(id=f"r{index}", workflow_id="w", revision_no=index,
                                    graph_spec={}, inputs_schema={}, graph_fingerprint=str(index)))
        db.commit()
        sql = []
        def query(conn, cursor, statement, parameters, context, many):
            sql.append(statement)
        event.listen(db.bind, "before_cursor_execute", query)
        rows, total = ProjectService.page_workflow_revisions(db, "p", "w", "u", limit=3, offset=5)
        assert total == 30 and [row.revision_no for row in rows] == [24, 23, 22]
        assert any("LIMIT" in statement and "OFFSET" in statement and "workflow_revisions" in statement for statement in sql)


def test_geocompute_malformed_drift_body_is_422_and_control_failure_is_sanitized():
    from app.api.routes import geocompute
    from app.core.auth import get_current_user
    app = FastAPI()
    app.include_router(geocompute.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "u"}
    response = TestClient(app).post("/geocompute/plans/drift-check", json={"plan": {"nodes": "invalid"}})
    assert response.status_code == 422
    error = geocompute._cluster_failure("CLUSTER_UNAVAILABLE", RuntimeError("internal-database-password"))
    assert error.status_code == 503 and error.detail["code"] == "CLUSTER_UNAVAILABLE"
    assert "password" not in json.dumps(error.detail)


def test_quality_audit_requires_authenticated_identity():
    from app.api.routes import project
    from app.core.database import get_db
    app = FastAPI()
    app.include_router(project.router)
    app.dependency_overrides[get_db] = lambda: None
    response = TestClient(app).post("/projects/p/quality-audit", json={"geojson": {
        "type": "FeatureCollection", "features": []}})
    assert response.status_code == 401


def test_health_detailed_rejects_stale_admin_claim_before_any_probe(monkeypatch):
    from app.api.routes import health
    from app.core.auth import get_current_user_with_version
    app = FastAPI()
    app.include_router(health.router)
    app.dependency_overrides[get_current_user_with_version] = lambda: {
        "user_id": "u", "role": "admin", "user": SimpleNamespace(role="viewer")}
    probe = Mock(side_effect=AssertionError("nonadmin reached probe"))
    monkeypatch.setattr(health, "_collect_sre_snapshot", probe)
    response = TestClient(app).get("/status/detailed")
    assert response.status_code == 403
    probe.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("org,db_role,expected", [(2, "admin", 404), (1, "viewer", 404), (1, "admin", 200)])
async def test_template_delete_is_tenant_scoped_and_uses_live_role(org, db_role, expected):
    from app.api.routes.templates import delete_template
    template = SimpleNamespace(id="t", org_id=org, creator_id="other", is_builtin=False)
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: template)),
                         delete=AsyncMock(), commit=AsyncMock())
    user = {"user_id": "admin", "org_id": 1, "role": "admin",
            "user": SimpleNamespace(id="admin", org_id=1, role=db_role)}
    if expected == 404:
        with pytest.raises(HTTPException) as denied:
            await delete_template("t", user, db)
        assert denied.value.status_code == 404
        db.delete.assert_not_awaited()
    else:
        result = await delete_template("t", user, db)
        assert result.status == "deleted"
        db.delete.assert_awaited_once_with(template)


def test_mission_quota_cannot_exceed_org_limits_and_zero_is_a_real_limit(db_factory):
    from app.models.db_model import OrgQuota
    from app.services.mission_runtime.service import MissionRuntimeService
    from app.services.mission_runtime.store import MissionStore, TransitionRejected
    service = MissionRuntimeService(store=MissionStore(factory=db_factory))
    with db_factory() as db:
        db.add(OrgQuota(org_id="1", max_storage_bytes=100, max_concurrent_tasks=2, rate_limit_per_min=3))
        db.commit()
    quota = service.effective_quota("1", {"tokens": 1e30, "tool_calls": 999, "bytes": 999})
    assert quota["tokens"] == 200000 and quota["tool_calls"] == 3
    assert quota["bytes"] == 100 and quota["concurrent_tasks"] == 2
    mission = service.create(org_id="1", user_id="u", quota={**quota, "tokens": 0})
    service.start(mission.mission_id, worker_id="w")
    record = service.store.get_mission(mission.mission_id)
    with pytest.raises(TransitionRejected, match="BUDGET_EXHAUSTED"):
        service.resources.reserve(mission.mission_id, lease_epoch=record.lease_epoch, owner="w", dim="tokens", amount=1)


def test_mission_resume_obeys_disabled_switch(monkeypatch):
    from app.api.routes import mission_runtime
    monkeypatch.setattr(mission_runtime, "mission_runtime_enabled", lambda: False)
    runtime = Mock()
    monkeypatch.setattr(mission_runtime, "get_mission_runtime", runtime)
    with pytest.raises(HTTPException) as denied:
        mission_runtime.resume_mission("m", mission_runtime.WorkerRequest(), {"user_id": "u"})
    assert denied.value.status_code == 503
    runtime.assert_not_called()


@pytest.mark.asyncio
async def test_mission_old_admin_jwt_uses_database_viewer_role(monkeypatch):
    from app.core import auth
    from app.api.routes import mission_runtime
    monkeypatch.setattr(auth, "auth_bypass_enabled", lambda: False)
    principal = SimpleNamespace(role="viewer", org_id=1, token_version=0, is_active=True)
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: principal)))
    token = auth.create_access_token({"sub": "u", "role": "admin", "org_id": 1})
    user = await auth.get_current_user_with_version(HTTPAuthorizationCredentials(scheme="Bearer", credentials=token), db)
    assert user["role"] == "viewer" and mission_runtime._scope_user(user) == "u"


def _gc_fixture(factory, tmp_path, monkeypatch):
    from app.models.data_lifecycle import GcPlan, LifecycleObject, LifecyclePolicy
    root = tmp_path / "cog"
    root.mkdir()
    path = root / "old.tif"
    path.write_bytes(b"raster")
    old = datetime.now(timezone.utc) - timedelta(days=20)
    os.utime(path, (old.timestamp(), old.timestamp()))
    monkeypatch.setattr("app.services.data_lifecycle.adapters._cog_root", lambda: root)
    monkeypatch.setattr("app.services.data_lifecycle.gc_plan.STAGING_DIR", tmp_path / "staging")
    with factory() as db:
        db.add(LifecyclePolicy(name="review", kind="cog_output", action="stage_delete", enabled=True,
                               tier_thresholds={"warm_after_s": 60, "cold_after_s": 120}))
        db.add(LifecycleObject(kind="cog_output", object_id="old.tif", owner_scope="user:u", tier="cold", last_used_at=old))
        plan = GcPlan(status="approved", created_by="u", scope={"staging_hours": 0}, plan_tree={"objects": [
            {"kind": "cog_output", "object_id": "old.tif", "owner_scope": "user:u", "tier": "cold"}]})
        db.add(plan)
        db.commit()
        return plan.id, path


@pytest.mark.parametrize("revive", ["file", "ledger"])
def test_gc_rechecks_frozen_candidate_and_keeps_revived_content(db_factory, tmp_path, monkeypatch, revive):
    from app.models.data_lifecycle import GcPlan
    from app.services.data_lifecycle.gc_plan import execute_plan
    from app.services.data_lifecycle.policy import revive_object
    plan_id, path = _gc_fixture(db_factory, tmp_path, monkeypatch)
    with db_factory() as db:
        if revive == "file":
            os.utime(path, None)
        else:
            assert revive_object(db, "cog_output", "old.tif")
        result = execute_plan(db, db.get(GcPlan, plan_id), actor="admin")
        assert result["ok"] and result["skipped_count"] == 1 and result["staged_count"] == 0
        assert result["skipped"][0]["reason"] == "revived_hot" and path.exists()


def test_gc_concurrent_execute_only_one_claims_and_keeps_one_staging_manifest(db_factory, tmp_path, monkeypatch):
    from app.models.data_lifecycle import GcPlan
    from app.services.data_lifecycle.gc_plan import execute_plan, GcPlanError
    plan_id, path = _gc_fixture(db_factory, tmp_path, monkeypatch)
    barrier = threading.Barrier(2)

    def execute():
        with db_factory() as db:
            plan = db.get(GcPlan, plan_id)
            barrier.wait(timeout=5)
            try:
                return execute_plan(db, plan, actor="admin")
            except GcPlanError:
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: execute(), range(2)))
    assert sum(result is not None for result in results) == 1 and not path.exists()
    with db_factory() as db:
        plan = db.get(GcPlan, plan_id)
        assert plan.result["staged_count"] == 1 and len(plan.result["staged"]) == 1
