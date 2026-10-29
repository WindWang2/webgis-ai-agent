"""atlas REST API 测试：提交（typed 400/dry-run/幂等）/详情（属主 404）。

真实路由 + 真实 SQLite（conversations/analysis_tasks）+ eager Celery（2 区
小图册真实渲染落盘）；mapspec_store 边界为内联合成数据（simulated 声明）。
"""
import json
import os

import pytest

from .fixtures import district_document

pytestmark = pytest.mark.cartography

fastapi_testclient = pytest.importorskip("fastapi.testclient")
weasyprint = pytest.importorskip("weasyprint")

EXPECT_USER = "atlas-api-user"
SESSION = "sess-atlas-api"


@pytest.fixture()
def api_env(tmp_path, monkeypatch):
    """真实 app 切面：异步 DB（conversations/jobs）+ 同步 db_session + eager。"""
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.api.routes import atlas_jobs as atlas_routes
    from app.api.routes import jobs as jobs_routes
    from app.core.database import get_async_db
    from app.models.db_model import Base, Conversation
    from app.services import export_paths
    from app.services.task_queue import celery_app

    monkeypatch.setattr(export_paths, "exports_root", lambda: tmp_path)
    monkeypatch.setitem(celery_app.conf, "result_backend", None)
    monkeypatch.setitem(celery_app.conf, "broker_url", "memory://")
    monkeypatch.setitem(celery_app.conf, "task_always_eager", True)

    # 单一 SQLite 文件：异步路由与同步 db_session（submit/worker/executor）
    # 必须看到同一份 job 行。
    db_file = tmp_path / "atlas_api.db"
    aengine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async_factory = async_sessionmaker(bind=aengine, expire_on_commit=False)

    async def override_db():
        async with async_factory() as s:
            yield s

    import contextlib
    from sqlalchemy.orm import sessionmaker

    sync_engine = create_engine(f"sqlite:///{db_file}",
                                connect_args={"check_same_thread": False})
    sync_sess = sessionmaker(bind=sync_engine)

    @contextlib.contextmanager
    def fake_db_session():
        db = sync_sess()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    monkeypatch.setattr("app.tools._utils.db_session", fake_db_session)
    monkeypatch.setattr("app.services.jobs.submit.db_session", fake_db_session)
    monkeypatch.setattr(
        "app.services.jobs.worker._default_session_factory", fake_db_session)

    # 异步库表 + 会话归属行（verify_session_owner 真实查询）
    import asyncio

    async def _seed():
        async with aengine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_factory() as s:
            s.add(Conversation(id=SESSION, user_id=EXPECT_USER, title="atlas api"))
            await s.commit()

    asyncio.run(_seed())

    app = FastAPI()
    from fastapi.exceptions import RequestValidationError
    from starlette.exceptions import HTTPException as StarletteHTTPException

    from app.core.exception import (
        unified_http_exception_handler,
        unified_validation_exception_handler,
    )

    app.add_exception_handler(StarletteHTTPException, unified_http_exception_handler)
    app.add_exception_handler(RequestValidationError,
                              unified_validation_exception_handler)
    app.include_router(atlas_routes.router, prefix="/api/v1")
    app.include_router(jobs_routes.router, prefix="/api/v1")
    from app.core.auth import get_current_user_optional, get_owner_token
    app.dependency_overrides[get_current_user_optional] = lambda: {
        "user_id": EXPECT_USER}
    app.dependency_overrides[get_owner_token] = lambda: None
    app.dependency_overrides[get_async_db] = override_db

    yield {"client": fastapi_testclient.TestClient(app), "exports": tmp_path}
    asyncio.run(aengine.dispose())
    sync_engine.dispose()


@pytest.fixture()
def spec_store(monkeypatch):
    from app.services import mapspec_store as store_mod

    state = {"document": district_document(districts=2)}

    async def fake_get(session_id):
        return state["document"]

    monkeypatch.setattr(store_mod.mapspec_store, "get_mapspec", fake_get)
    return state


def _body(**overrides):
    body = {
        "session_id": SESSION,
        "atlas": {
            "driver": "category", "layerId": "districts",
            "categoryProperty": "district", "pageBudget": 20,
            "includeCover": True, "atlasTitle": "API 图册",
        },
        "classification_scope": "shared",
        "target_dpi": 300,
        "title": "API 提交",
        "simulated_data": True,
    }
    body.update(overrides)
    return body


def test_submit_dryrun_and_eager_completion(api_env, spec_store):
    resp = api_env["client"].post("/api/v1/atlas/jobs", json=_body())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["success"] is True
    assert data["idempotent_reuse"] is False
    assert data["plan"]["page_count"] == 3  # 封面 + 2 区（dry-run 预检）
    job_id = data["job_id"]

    # eager：POST 返回时 job 已完成（真实渲染落盘）
    detail = api_env["client"].get(f"/api/v1/atlas/jobs/{job_id}")
    assert detail.status_code == 200, detail.text
    d = detail.json()
    assert d["job"]["status"] == "completed"
    assert d["job"]["kind"] == "atlas_export"
    assert len(d["pages"]) == 3
    assert all(p["status"] == "rendered" for p in d["pages"])
    assert d["artifacts"]["pdf_url"].startswith("/api/v1/export/download/")
    assert d["artifacts"]["manifest_url"].endswith(".manifest.json")
    pdf = os.path.join(str(api_env["exports"]),
                       os.path.basename(d["artifacts"]["pdf"]))
    assert os.path.isfile(pdf)

    # 任务中心统一视图同样可见（同一 job API）
    jobs = api_env["client"].get(
        f"/api/v1/tasks/jobs?session_id={SESSION}")
    assert jobs.status_code == 200
    kinds = {j["kind"] for j in jobs.json()["jobs"]}
    assert "atlas_export" in kinds


def test_submit_idempotent_reuse(api_env, spec_store):
    r1 = api_env["client"].post("/api/v1/atlas/jobs", json=_body()).json()
    r2 = api_env["client"].post("/api/v1/atlas/jobs", json=_body()).json()
    assert r1["job_id"] == r2["job_id"]
    assert r2["idempotent_reuse"] is True


def test_submit_typed_failures(api_env, spec_store):
    # local scope + category 驱动 → typed 400
    resp = api_env["client"].post(
        "/api/v1/atlas/jobs",
        json=_body(classification_scope="local"))
    assert resp.status_code == 400
    assert (resp.json().get("data") or {}).get("code") == "atlas_local_requires_frames"

    # category 无属性 → dry-run 规划 typed 400
    bad = _body()
    bad["atlas"] = {**bad["atlas"], "categoryProperty": ""}
    resp = api_env["client"].post("/api/v1/atlas/jobs", json=bad)
    assert resp.status_code == 400
    assert (resp.json().get("data") or {}).get("code") in (
        "atlas_category_property_missing", "atlas_policy_invalid")


def test_missing_spec_404(api_env, monkeypatch):
    async def none(*_a, **_k):
        return None

    monkeypatch.setattr(
        "app.services.mapspec_store.mapspec_store.get_mapspec", none)
    resp = api_env["client"].post("/api/v1/atlas/jobs", json=_body())
    assert resp.status_code == 404
    assert (resp.json().get("data") or {}).get("code") == "atlas_spec_missing"


def test_detail_owner_scoped_404(api_env, spec_store):
    r = api_env["client"].post("/api/v1/atlas/jobs", json=_body()).json()
    job_id = r["job_id"]
    # 换一个用户 → 404（不泄露存在性）
    from app.core.auth import get_current_user_optional
    api_env["client"].app.dependency_overrides[get_current_user_optional] = \
        lambda: {"user_id": "someone-else"}
    resp = api_env["client"].get(f"/api/v1/atlas/jobs/{job_id}")
    assert resp.status_code == 404


def test_submit_unowned_session_404(api_env, spec_store):
    body = _body(session_id="sess-someone-else")
    resp = api_env["client"].post("/api/v1/atlas/jobs", json=body)
    assert resp.status_code == 404  # verify_session_owner：不泄露存在性


def test_manifest_download_content_matches(api_env, spec_store):
    r = api_env["client"].post("/api/v1/atlas/jobs", json=_body()).json()
    detail = api_env["client"].get(
        f"/api/v1/atlas/jobs/{r['job_id']}").json()
    mfile = os.path.join(str(api_env["exports"]),
                         os.path.basename(detail["artifacts"]["manifest"]))
    manifest = json.load(open(mfile, encoding="utf-8"))
    assert manifest["atlas"]["classification_scope"] == "shared"
    assert manifest["disclosures"]["simulated_data"] is True
    assert manifest["qa"]["page_count_expected"] == 3


def test_submit_requires_authenticated_owner(api_env, spec_store):
    """R1-P2：匿名提交 → 401 typed（产物下载走属主通道，不产出无人可下载的 PDF）。"""
    from app.core.auth import get_current_user_optional

    api_env["client"].app.dependency_overrides[get_current_user_optional] = \
        lambda: {"user_id": "anonymous"}
    resp = api_env["client"].post("/api/v1/atlas/jobs", json=_body())
    assert resp.status_code == 401
    assert (resp.json().get("data") or {}).get("code") == "atlas_owner_required"


def test_submit_strict_policy_conversion(api_env, spec_store):
    """R1-P2：类型强转/未知键 → typed 400（不静默吞、不 500）。"""
    bad = _body()
    bad["atlas"] = {**bad["atlas"], "featuresPerPage": "abc"}
    resp = api_env["client"].post("/api/v1/atlas/jobs", json=bad)
    assert resp.status_code == 400
    assert (resp.json().get("data") or {}).get("code") == "atlas_policy_invalid"

    unknown = _body()
    unknown["atlas"] = {**unknown["atlas"], "rogueKey": 1}
    resp = api_env["client"].post("/api/v1/atlas/jobs", json=unknown)
    assert resp.status_code == 400
    assert "rogueKey" in (resp.json().get("data") or {}).get("message", "")

    fake_bool = _body()
    fake_bool["atlas"] = {**fake_bool["atlas"], "includeCover": "false"}
    resp = api_env["client"].post("/api/v1/atlas/jobs", json=fake_bool)
    assert resp.status_code == 400
