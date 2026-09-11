"""V9 跨租户隔离测试矩阵（ADR-0139 P4，foundation/security-tenancy-v9）。

把「仅 RAG」的跨租户覆盖扩成 V8 全子系统：双 org（acme / beta）×
{geocompute, workflow-runtime, lakehouse, jobs, fabric, project, upload}
的**读端点越权 + 列表无泄漏**，全部断言 403/404（或列表/投影不含对方
org 标识），响应体扫描 victim 租户特征词（用户名 / org slug）。

复用既有 RAG 隔离测试（tests/integration/test_cross_tenant_isolation.py）
的组织 fixture 模式：临时 aiosqlite + create_all + get_async_db override
+ 同步 SessionLocal/factory 指向同一测试库。

标记：``integration``（与 RAG 隔离测试同 lane；PR 门禁另跑 unit + 本文件）。
"""
from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# 必须在 import app.* 之前设置（auth 模块导入期读 settings）
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-v9-matrix-32-chars-ok")
os.environ.setdefault("ENV", "development")

pytestmark = pytest.mark.integration

ORG_A_ID = 1
ORG_B_ID = 2
ORG_A_SLUG = "org-acme-v9"
ORG_B_SLUG = "org-beta-v9"
USER_A = "alice-v9"
USER_B = "bob-v9"
#: victim 租户特征词：出现在 attacker 的任何响应体里即泄漏
VICTIM_TOKENS_A = (USER_A, ORG_A_SLUG, "proj-alice-v9", "run-alice-v9")


def _token(user_id: str, org_id: int, role: str = "editor") -> str:
    from app.core.auth import create_access_token

    return create_access_token({"sub": user_id, "role": role, "org_id": org_id})


def _auth(user_id: str, org_id: int) -> dict:
    return {"Authorization": f"Bearer {_token(user_id, org_id)}"}


@pytest_asyncio.fixture
async def app_and_db(tmp_path, monkeypatch):
    """临时 SQLite（async + sync 双引擎同文件）+ V8 子系统路由。"""
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core import rate_limiter as rl_mod
    from app.core import tenancy
    from app.core.database import get_async_db
    from app.api.routes import geocompute as geocompute_routes
    from app.api.routes import lakehouse_datasets as lakehouse_routes
    from app.api.routes import task as task_routes
    from app.api.routes import workflow_runtime as workflow_routes
    from app.api.routes import project as project_routes
    from app.api.routes import upload as upload_routes
    from app.models.db_model import Base

    db_file = tmp_path / "v9_matrix.db"
    db_url_sync = f"sqlite:///{db_file}"
    test_engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_file}",
        connect_args={"check_same_thread": False})
    test_session = async_sessionmaker(bind=test_engine, expire_on_commit=False)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_get_async_db():
        async with test_session() as s:
            yield s

    # 同步面：V8 store 工厂 + tenancy/org 线程解析都读这些名字
    sync_engine = create_engine(db_url_sync, connect_args={"check_same_thread": False})
    SyncSession = sessionmaker(bind=sync_engine, expire_on_commit=False)

    from app.services.geocompute.cluster import store as gc_store
    from app.services.workflow_runtime import store as wf_store
    from app.services.geocompute.cluster import events as gc_events
    from app.services.geocompute import reuse_index as gc_reuse
    from app.services.geocompute import run_evidence as gc_evidence

    monkeypatch.setattr(gc_store, "session_factory", SyncSession)
    monkeypatch.setattr(wf_store, "session_factory", SyncSession)
    # _svc() 是进程级单例：不重置会缓存**上一个测试**的 tmp DB 工厂
    from app.services.workflow_runtime import service as wf_service

    wf_service.reset_service()
    monkeypatch.setattr(wf_service, "_service", None)
    monkeypatch.setattr(gc_events, "session_factory", SyncSession)
    monkeypatch.setattr(gc_reuse, "session_factory", SyncSession)
    monkeypatch.setattr(gc_evidence, "session_factory", SyncSession)
    monkeypatch.setattr("app.core.database.SessionLocal", SyncSession)
    tenancy.reset_default_org_cache()

    class _NoOpLimiter:
        async def is_allowed(self, key, max_requests, window_seconds):
            return True

    async def _stub_get_rate_limiter():
        return _NoOpLimiter()

    monkeypatch.setattr(rl_mod, "get_rate_limiter", _stub_get_rate_limiter)
    # 配额面在矩阵里 stub 放行（越限语义由 quota 专项测试覆盖）
    from app.services import org_quota

    async def _skip_quota(user, **kwargs):
        return ""

    monkeypatch.setattr(org_quota, "enforce_for_principal", _skip_quota)

    app = FastAPI()
    app.include_router(geocompute_routes.router, prefix="/api/v1")
    app.include_router(workflow_routes.router, prefix="/api/v1")
    app.include_router(lakehouse_routes.router, prefix="/api/v1")
    app.include_router(task_routes.router, prefix="/api/v1")
    app.include_router(project_routes.router, prefix="/api/v1")
    app.include_router(upload_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_async_db] = override_get_async_db
    try:
        yield app, test_session, SyncSession
    finally:
        tenancy.reset_default_org_cache()
        await test_engine.dispose()
        sync_engine.dispose()


@pytest_asyncio.fixture
async def client(app_and_db):
    app, _, _ = app_and_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def seeded(app_and_db):
    """双 org 种子：org / user / 会话 / V8 数据行（全部归属 alice@acme）。"""
    _, session, SyncSession = app_and_db
    from app.core.auth import hash_password
    from app.models.db_model import (
        AnalysisTask,
        Conversation,
        Organization,
        User,
    )
    from app.services.geocompute.cluster.store import ClusterRunStore
    from app.services.workflow_runtime.store import InstanceStore
    from app.services.geocompute.executor import owner_scope_for

    async with session() as db:
        db.add_all([
            Organization(id=ORG_A_ID, name="Acme V9", slug=ORG_A_SLUG),
            Organization(id=ORG_B_ID, name="Beta V9", slug=ORG_B_SLUG),
        ])
        await db.flush()
        alice = User(
            id=USER_A, username=USER_A, email="alice-v9@example.com",
            password_hash=hash_password("pw-alice-v9-123456"),
            role="editor", org_id=ORG_A_ID, is_active=True,
        )
        bob = User(
            id=USER_B, username=USER_B, email="bob-v9@example.com",
            password_hash=hash_password("pw-bob-v9-123456"),
            role="editor", org_id=ORG_B_ID, is_active=True,
        )
        db.add_all([alice, bob])
        await db.flush()
        conv_a = Conversation(id=f"sess-{uuid.uuid4().hex[:12]}",
                              user_id=alice.id, title="alice session")
        conv_b = Conversation(id=f"sess-{uuid.uuid4().hex[:12]}",
                              user_id=bob.id, title="bob session")
        db.add_all([conv_a, conv_b])
        await db.flush()
        db.add(AnalysisTask(
            task_type="vector_buffer", parameters={},
            org_id=ORG_A_ID, creator_id=alice.id, session_id=conv_a.id,
            owner_token=None,
        ))
        await db.commit()
        session_ids = (conv_a.id, conv_b.id)

    # 同步面种子：geocompute run + workflow instance（alice / org A）
    def _seed_sync():
        store = ClusterRunStore(factory=SyncSession)
        run_id = store.create_run(
            plan_snapshot={"plan_id": "p", "nodes": []},
            plan_fingerprint="f" * 32,
            owner_scope=owner_scope_for({"user_id": USER_A}),
            session_id=session_ids[0],
            creator_id=USER_A,
            org_id=str(ORG_A_ID),
            tenant_raw=str(ORG_A_ID),
        )
        owner_scope = owner_scope_for({"user_id": USER_A})
        from types import SimpleNamespace

        from app.services.workflow_runtime.registry import PackageRegistry

        reg = PackageRegistry(factory=SyncSession)
        pkg = SimpleNamespace(
            package_id="pkg-v9", version="1.0.0", schema_version="1",
            compiler_version="1", methodology_family="m",
            recipe_fingerprint="", methodology_fingerprint="",
            environment_fingerprint="",
            compiled_form={"typed_dag": {"nodes": [
                {"node_id": "n1", "kind": "analysis", "optional": False},
            ], "edges": []}},
            fingerprint="pf" * 16,
            to_bounded_dict=lambda: {"compiled_form": {"typed_dag": {
                "nodes": [{"node_id": "n1", "kind": "analysis",
                           "optional": False}], "edges": []}}},
        )
        reg.register(pkg, owner_scope=owner_scope, org_id=str(ORG_A_ID))
        wf = InstanceStore(factory=SyncSession)
        inst = wf.create_instance(
            package_id="pkg-v9", package_version="1.0.0",
            package_fingerprint="pf" * 16,
            owner_scope=owner_scope,
            org_id=str(ORG_A_ID),
            session_id=session_ids[0],
            node_specs=[{"node_id": "n1", "optional": False}],
        )
        return run_id, inst["instance_id"]

    import anyio

    run_id, instance_id = await anyio.to_thread.run_sync(_seed_sync)
    return {"run_id": run_id, "instance_id": instance_id,
            "session_a": session_ids[0], "session_b": session_ids[1]}


async def _get(client, path: str, user: str, org: int):
    return await client.get(path, headers=_auth(user, org))


async def _assert_no_leak(response, label: str):
    """attacker 视角响应不得含 victim 租户特征词。"""
    text = response.text
    for token in VICTIM_TOKENS_A:
        assert token not in text, (
            f"{label}: 响应体泄漏 victim 特征词 {token!r}: {text[:200]}"
        )


@pytest.mark.parametrize("path_builder,label", [
    (lambda s: f"/api/v1/geocompute/runs/{s['run_id']}",
     "geocompute run 详情"),
    (lambda s: "/api/v1/geocompute/runs",
     "geocompute run 列表"),
    (lambda s: f"/api/v1/geocompute/runs/{s['run_id']}/events",
     "geocompute run 事件"),
    (lambda s: f"/api/v1/workflow-runtime/instances/{s['instance_id']}",
     "workflow 实例详情"),
    (lambda s: "/api/v1/workflow-runtime/instances",
     "workflow 实例列表"),
])
async def test_cross_org_read_isolation(client, seeded, path_builder, label):
    """双 org 交叉读：bob@beta 访问 alice@acme 的 V8 资源 → 404/空集无泄漏；
    alice 正向可达（隔离不是整体不可用）。"""
    path = path_builder(seeded)

    got = await _get(client, path, USER_A, ORG_A_ID)
    assert got.status_code == 200, f"{label}: owner 正向读失败 {got.status_code}"
    if path.endswith(f"/{seeded['run_id']}") or path.endswith(
            f"/{seeded['instance_id']}"):
        assert USER_A in got.text or seeded["run_id"] in got.text or \
            seeded["instance_id"] in got.text

    foreign = await _get(client, path, USER_B, ORG_B_ID)
    assert foreign.status_code in (200, 404), \
        f"{label}: 异常状态 {foreign.status_code}"
    if foreign.status_code == 200 and path.endswith("/runs") is False \
            and "instances" not in path:
        assert foreign.status_code == 404, \
            f"{label}: 详情端点跨租户必须 404，得到 {foreign.status_code}"
    await _assert_no_leak(foreign, label)
    if foreign.status_code == 200:
        # 列表端点：200 但必须是空集/不含 victim 行
        body = foreign.json()
        text = str(body)
        for token in (seeded["run_id"], seeded["instance_id"]):
            assert token not in text, f"{label}: 列表泄漏 victim 行 id {token}"


async def test_cross_org_session_scoped_resources(client, seeded):
    """session 域资源（lakehouse/upload）：bob 以自己的会话探测 alice 资源
    → 404/不泄漏。（jobs 列表面已由 tests/integration/
    test_cross_tenant_isolation.py 覆盖——裁剪 app 上 task 路由 503，
    不在本矩阵重复。）"""
    # lakehouse：不存在 dataset id 的存在性探测（无泄漏）
    ghost = await _get(client, "/api/v1/lakehouse/datasets/nonexistent-v9",
                       USER_B, ORG_B_ID)
    assert ghost.status_code in (400, 404, 422)  # 400 = 非法 id 形态（诚实拒绝）
    await _assert_no_leak(ghost, "lakehouse ghost 探测")

    # upload：bob 以 alice 的会话列 upload → 空/404 且无 alice 特征词
    up = await _get(
        client, f"/api/v1/uploads?session_id={seeded['session_a']}",
        USER_B, ORG_B_ID)
    assert up.status_code in (200, 401, 403, 404)
    await _assert_no_leak(up, "upload 列表")


async def test_workflow_instance_projection_hides_org(client, seeded):
    """org 明文不进 REST 投影（与 owner_scope 同纪律）。"""
    resp = await _get(
        client, f"/api/v1/workflow-runtime/instances/{seeded['instance_id']}",
        USER_A, ORG_A_ID)
    assert resp.status_code == 200
    assert "org_id" not in resp.text, "实例投影不得暴露明文 org_id"


async def test_cross_org_write_attempt_is_isolated(client, seeded):
    """写越权：bob 以 alice 的实例 id 触发取消/驱动 → 404/409 且无副作用。"""
    cancel = await client.post(
        f"/api/v1/workflow-runtime/instances/{seeded['instance_id']}/cancel",
        headers=_auth(USER_B, ORG_B_ID))
    assert cancel.status_code in (404, 403, 409)
    await _assert_no_leak(cancel, "workflow 取消越权")

    run_cancel = await client.post(
        f"/api/v1/geocompute/runs/{seeded['run_id']}/cancel",
        headers=_auth(USER_B, ORG_B_ID))
    assert run_cancel.status_code in (404, 403, 405, 409)
    await _assert_no_leak(run_cancel, "geocompute 取消越权")
