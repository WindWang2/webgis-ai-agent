"""统一任务中心 API 端到端（ADR-0052）。

覆盖规范 §41 的回归场景：API 重启恢复（Scenario 6）、跨租户隔离（Scenario 9）、
双重取消幂等（Scenario 5）、取消不 retry（Scenario 12）、旧端点兼容、
以及 durable job 与 agent task 的统一视图。

真实 SQLite + 真实路由 + 真实 auth 依赖，不 mock 掉被测语义。
"""
import os
import uuid
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-job-api-32-chars-okay")
os.environ.setdefault("ENV", "development")


@pytest_asyncio.fixture
async def app_and_db(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.api.routes import chat as chat_routes
    from app.api.routes import jobs as jobs_routes
    from app.api.routes import task as task_routes
    from app.core import rate_limiter as rl_mod
    from app.core.database import get_async_db
    from app.models.db_model import Base
    from app.tools import _utils

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'job_api.db'}"
    engine = create_async_engine(db_url, connect_args={"check_same_thread": False})
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_get_async_db():
        async with session_factory() as s:
            yield s

    @asynccontextmanager
    async def override_async_db_session():
        async with session_factory() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    monkeypatch.setattr(_utils, "async_db_session", override_async_db_session)
    monkeypatch.setattr("app.api.routes.chat.async_db_session", override_async_db_session)

    class _NoOpLimiter:
        async def is_allowed(self, key, max_requests, window_seconds):
            return True

    async def _stub_get_rate_limiter():
        return _NoOpLimiter()

    monkeypatch.setattr(rl_mod, "get_rate_limiter", _stub_get_rate_limiter)

    app = FastAPI()
    # 与 main.py 一致：jobs 必须先注册，否则 /tasks/{task_id} 会吃掉 "jobs"
    app.include_router(jobs_routes.router, prefix="/api/v1")
    app.include_router(task_routes.router, prefix="/api/v1")
    app.include_router(chat_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_async_db] = override_get_async_db
    try:
        yield app, session_factory
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def client(app_and_db):
    app, _ = app_and_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def db(app_and_db):
    _, factory = app_and_db
    async with factory() as s:
        yield s


@pytest_asyncio.fixture
async def tenants(db):
    """两个租户，各有一个 Conversation。归属链：session_id → Conversation.user_id。"""
    from app.core.auth import hash_password
    from app.models.db_model import Conversation, User

    made = {}
    for name in ("a", "b"):
        user = User(
            id=str(uuid.uuid4()),
            username=f"user_{name}",
            email=f"{name}@example.com",
            password_hash=hash_password("pw-12345678"),
            role="editor",
            is_active=True,
        )
        db.add(user)
        conv = Conversation(id=f"sess-{name}", user_id=user.id, title=f"会话 {name}")
        db.add(conv)
        made[f"user_{name}"] = user
        made[f"conv_{name}"] = conv
    await db.commit()
    return made


def _auth(user) -> dict:
    from app.core.auth import create_access_token

    token = create_access_token({"sub": user.id, "username": user.username, "role": user.role})
    return {"Authorization": f"Bearer {token}"}


async def _make_job(db, *, owner, session_id, status=None, **overrides):
    from app.services.jobs import DurableJobStore, JobStatus

    params = {
        "task_type": "ndvi",
        "display_name": "NDVI 植被指数分析",
        "owner_id": (owner.id if owner is not None else None),
        "session_id": session_id,
        "parameters": {"raster_path": "/data/a.tif", "token": "SECRET"},
        "dispatch_spec": {
            "task": "app.services.spatial_tasks.run_ndvi_analysis",
            "args": ["/data/a.tif"],
            "kwargs": {},
        },
    }
    params.update(overrides)
    job = await DurableJobStore.create(db, **params)
    await db.commit()
    if status in (JobStatus.queued, JobStatus.running, JobStatus.completed, JobStatus.failed):
        await DurableJobStore.mark_queued(db, job.id, celery_task_id=f"celery-{job.id}")
    if status in (JobStatus.running, JobStatus.completed, JobStatus.failed):
        await DurableJobStore.mark_running(db, job.id)
    if status is JobStatus.completed:
        await DurableJobStore.mark_succeeded(db, job.id, result={"mean": 0.4}, result_ref="/data/out.tif")
    if status is JobStatus.failed:
        await DurableJobStore.mark_failed(db, job.id, error=ValueError("invalid CRS"))
    await db.commit()
    return job


# ── 列表 ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_jobs_returns_owner_scoped_jobs(client, db, tenants):
    from app.services.jobs import JobStatus

    await _make_job(db, owner=tenants["user_a"], session_id="sess-a", status=JobStatus.running)

    resp = await client.get(
        "/api/v1/tasks/jobs?session_id=sess-a", headers=_auth(tenants["user_a"])
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["jobs"]) == 1
    job = body["jobs"][0]
    assert job["kind"] == "analysis"
    assert job["name"] == "NDVI 植被指数分析"
    assert job["status"] == "running"
    assert job["cancellable"] is True
    assert job["active"] is True
    assert body["has_active"] is True
    assert body["poll_after_ms"] == 3000


@pytest.mark.asyncio
async def test_no_active_jobs_tells_frontend_to_stop_polling(client, db, tenants):
    """规范 §32：无活跃 job → 前端 0 轮询。后端必须主动告知。"""
    from app.services.jobs import JobStatus

    await _make_job(db, owner=tenants["user_a"], session_id="sess-a", status=JobStatus.completed)
    resp = await client.get(
        "/api/v1/tasks/jobs?session_id=sess-a", headers=_auth(tenants["user_a"])
    )
    body = resp.json()
    assert body["has_active"] is False
    assert body["poll_after_ms"] is None


@pytest.mark.asyncio
async def test_list_response_never_leaks_secrets_or_traceback(client, db, tenants):
    """规范 §35 / §10：参数凭据与 traceback 不得出现在响应里。"""
    from app.services.jobs import JobStatus

    await _make_job(db, owner=tenants["user_a"], session_id="sess-a", status=JobStatus.failed)
    resp = await client.get(
        "/api/v1/tasks/jobs?session_id=sess-a", headers=_auth(tenants["user_a"])
    )
    raw = resp.text
    assert "SECRET" not in raw
    assert "Traceback" not in raw
    job = resp.json()["jobs"][0]
    assert job["error"] == "ValueError: invalid CRS"
    # 统一视图不回传原始参数
    assert "parameters" not in job


@pytest.mark.asyncio
async def test_active_only_filter(client, db, tenants):
    from app.services.jobs import JobStatus

    await _make_job(db, owner=tenants["user_a"], session_id="sess-a", status=JobStatus.running)
    await _make_job(db, owner=tenants["user_a"], session_id="sess-a", status=JobStatus.completed)

    resp = await client.get(
        "/api/v1/tasks/jobs?session_id=sess-a&active_only=true", headers=_auth(tenants["user_a"])
    )
    jobs = resp.json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["status"] == "running"


@pytest.mark.asyncio
async def test_list_requires_proven_session_ownership(client, db, tenants):
    """Scenario 9：拿别人的 session_id 查列表必须 404，不能返回对方任务。"""
    from app.services.jobs import JobStatus

    await _make_job(db, owner=tenants["user_a"], session_id="sess-a", status=JobStatus.running)
    resp = await client.get(
        "/api/v1/tasks/jobs?session_id=sess-a", headers=_auth(tenants["user_b"])
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_without_session_scopes_to_authenticated_user(client, db, tenants):
    """不带 session_id 时按 creator_id 收敛，绝不返回别人的 job。"""
    from app.services.jobs import JobStatus

    await _make_job(db, owner=tenants["user_a"], session_id="sess-a", status=JobStatus.running)
    await _make_job(db, owner=tenants["user_b"], session_id="sess-b", status=JobStatus.running)

    resp = await client.get("/api/v1/tasks/jobs", headers=_auth(tenants["user_a"]))
    assert resp.status_code == 200
    jobs = resp.json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["session_id"] == "sess-a"


# ── 单条读取 ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_job_by_owner(client, db, tenants):
    from app.services.jobs import JobStatus

    job = await _make_job(
        db, owner=tenants["user_a"], session_id="sess-a", status=JobStatus.completed
    )
    resp = await client.get(f"/api/v1/tasks/jobs/{job.id}", headers=_auth(tenants["user_a"]))
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(job.id)
    assert body["status"] == "completed"
    assert body["result_ref"] == "/data/out.tif"
    assert body["progress"] == 100


@pytest.mark.asyncio
async def test_get_job_cross_tenant_is_404(client, db, tenants):
    from app.services.jobs import JobStatus

    job = await _make_job(
        db, owner=tenants["user_a"], session_id="sess-a", status=JobStatus.running
    )
    resp = await client.get(f"/api/v1/tasks/jobs/{job.id}", headers=_auth(tenants["user_b"]))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_nonexistent_job_is_404(client, tenants):
    resp = await client.get("/api/v1/tasks/jobs/999999", headers=_auth(tenants["user_a"]))
    assert resp.status_code == 404
    resp = await client.get("/api/v1/tasks/jobs/not-a-number", headers=_auth(tenants["user_a"]))
    assert resp.status_code == 404


# ── 取消 ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_running_job_reports_cancelling(client, db, tenants):
    """规范 §30：后端未终态时状态必须是 cancelling，而不是直接 cancelled。"""
    from app.services.jobs import JobStatus

    job = await _make_job(
        db, owner=tenants["user_a"], session_id="sess-a", status=JobStatus.running
    )
    resp = await client.delete(f"/api/v1/tasks/jobs/{job.id}", headers=_auth(tenants["user_a"]))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "cancelling"
    assert body["cancel_requested"] is True
    assert body["cancelling"] is True


@pytest.mark.asyncio
async def test_double_cancel_is_idempotent(client, db, tenants):
    """Scenario 5：重复取消 200 且不报错，cancel_requested 转为 False。"""
    from app.services.jobs import JobStatus

    job = await _make_job(
        db, owner=tenants["user_a"], session_id="sess-a", status=JobStatus.running
    )
    first = await client.delete(f"/api/v1/tasks/jobs/{job.id}", headers=_auth(tenants["user_a"]))
    second = await client.delete(f"/api/v1/tasks/jobs/{job.id}", headers=_auth(tenants["user_a"]))
    assert first.status_code == second.status_code == 200
    assert first.json()["cancel_requested"] is True
    assert second.json()["cancel_requested"] is False
    assert second.json()["status"] == "cancelling"


@pytest.mark.asyncio
async def test_cancel_persists_durable_fact(client, db, tenants):
    """取消必须落库 —— 这是 worker 在另一个进程也能观察到取消的前提。"""
    from app.services.jobs import DurableJobStore, JobStatus

    job = await _make_job(
        db, owner=tenants["user_a"], session_id="sess-a", status=JobStatus.running
    )
    await client.delete(f"/api/v1/tasks/jobs/{job.id}", headers=_auth(tenants["user_a"]))

    async with db.begin_nested():
        pass
    fresh = await DurableJobStore.get(db, job.id)
    await db.refresh(fresh)
    assert fresh.cancel_requested_at is not None


@pytest.mark.asyncio
async def test_cancel_cross_tenant_is_404_and_does_not_cancel(client, db, tenants):
    """Scenario 9：user B 不能取消 user A 的任务。"""
    from app.services.jobs import DurableJobStore, coerce_status

    job = await _make_job(
        db, owner=tenants["user_a"], session_id="sess-a", status=None
    )
    from app.services.jobs import JobStatus

    await DurableJobStore.mark_queued(db, job.id)
    await DurableJobStore.mark_running(db, job.id)
    await db.commit()

    resp = await client.delete(f"/api/v1/tasks/jobs/{job.id}", headers=_auth(tenants["user_b"]))
    assert resp.status_code == 404

    fresh = await DurableJobStore.get(db, job.id)
    await db.refresh(fresh)
    assert coerce_status(fresh.status) is JobStatus.running
    assert fresh.cancel_requested_at is None


@pytest.mark.asyncio
async def test_cancel_completed_job_is_noop(client, db, tenants):
    from app.services.jobs import JobStatus

    job = await _make_job(
        db, owner=tenants["user_a"], session_id="sess-a", status=JobStatus.completed
    )
    resp = await client.delete(f"/api/v1/tasks/jobs/{job.id}", headers=_auth(tenants["user_a"]))
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"
    assert resp.json()["cancel_requested"] is False


# ── 重试 ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retry_failed_job_reenqueues_and_creates_new_attempt(
    client, db, tenants, monkeypatch
):
    """retry 必须**真的**把任务重新交给 worker。

    只把状态改成 queued 而不入队会留下一个永不推进的 job —— 比不提供 retry 更糟。
    这里断言 send_task 被调用，且带上了同一个 job_id（新 attempt 复用同一逻辑 job）。
    """
    from app.services.jobs import JobStatus

    sent: list[dict] = []

    class _Result:
        id = "celery-retry-1"

    def fake_send_task(name, args=None, kwargs=None, **_):
        sent.append({"name": name, "args": args, "kwargs": kwargs})
        return _Result()

    monkeypatch.setattr("app.api.routes.jobs.celery_app.send_task", fake_send_task)

    job = await _make_job(
        db, owner=tenants["user_a"], session_id="sess-a", status=JobStatus.failed
    )
    resp = await client.post(
        f"/api/v1/tasks/jobs/{job.id}/retry", headers=_auth(tenants["user_a"])
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["retried"] is True
    assert body["status"] == "queued"
    assert body["attempt"] == 2

    assert len(sent) == 1, "retry 必须真的重新入队"
    assert sent[0]["name"] == "app.services.spatial_tasks.run_ndvi_analysis"
    assert sent[0]["kwargs"]["job_id"] == int(job.id)

    from app.services.jobs import DurableJobStore

    fresh = await DurableJobStore.get(db, job.id)
    await db.refresh(fresh)
    assert fresh.celery_task_id == "celery-retry-1", "新 attempt 的 celery id 必须回填"


@pytest.mark.asyncio
async def test_retry_without_dispatch_spec_is_refused(client, db, tenants):
    """无法忠实重跑时明确拒绝，而不是把 job 改成 queued 后永远挂着。"""
    from app.services.jobs import JobStatus

    job = await _make_job(
        db,
        owner=tenants["user_a"],
        session_id="sess-a",
        status=JobStatus.failed,
        dispatch_spec=None,
    )
    resp = await client.post(
        f"/api/v1/tasks/jobs/{job.id}/retry", headers=_auth(tenants["user_a"])
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["retried"] is False
    assert body["reason"] == "no_dispatch_spec"
    assert body["status"] == "failed"


@pytest.mark.asyncio
async def test_cancelled_job_retry_is_refused(client, db, tenants):
    """Scenario 12 / 规范 §17：取消绝不能被 retry。"""
    from app.services.jobs import JobStatus

    job = await _make_job(
        db, owner=tenants["user_a"], session_id="sess-a", status=JobStatus.running
    )
    await client.delete(f"/api/v1/tasks/jobs/{job.id}", headers=_auth(tenants["user_a"]))

    from app.services.jobs import DurableJobStore

    await DurableJobStore.confirm_cancelled(db, job.id)
    await db.commit()

    resp = await client.post(
        f"/api/v1/tasks/jobs/{job.id}/retry", headers=_auth(tenants["user_a"])
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["retried"] is False
    assert body["reason"] == "cancelled_jobs_are_never_retried"
    assert body["status"] == "cancelled"


@pytest.mark.asyncio
async def test_retry_cross_tenant_is_404(client, db, tenants):
    from app.services.jobs import JobStatus

    job = await _make_job(
        db, owner=tenants["user_a"], session_id="sess-a", status=JobStatus.failed
    )
    resp = await client.post(
        f"/api/v1/tasks/jobs/{job.id}/retry", headers=_auth(tenants["user_b"])
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_agent_task_retry_is_rejected(client, tenants):
    resp = await client.post(
        "/api/v1/tasks/jobs/task-abc12345/retry", headers=_auth(tenants["user_a"])
    )
    assert resp.status_code in (400, 404)


# ── Scenario 6：API 重启恢复 ────────────────────────────────────────


@pytest.mark.asyncio
async def test_job_survives_api_restart(client, db, tenants):
    """Scenario 6 / 规范 §26：清空全部进程内状态后，合法 owner 仍能查询与取消。

    这里显式清空 CancellationRegistry 与 TaskQueueService._task_owners —— 它们是
    ADR-0052 之前唯一的「事实源」。现在事实在 DB，所以清空后语义不变。
    """
    from app.services.jobs import JobStatus, registry as cancellation_registry
    from app.services.task_queue import TaskQueueService

    job = await _make_job(
        db, owner=tenants["user_a"], session_id="sess-a", status=JobStatus.running
    )

    # 模拟 API 重启：进程内状态全部丢失
    cancellation_registry.clear()
    TaskQueueService._task_owners.clear()

    got = await client.get(f"/api/v1/tasks/jobs/{job.id}", headers=_auth(tenants["user_a"]))
    assert got.status_code == 200, "重启后合法 owner 必须仍能查询自己的任务"
    assert got.json()["status"] == "running"

    cancelled = await client.delete(
        f"/api/v1/tasks/jobs/{job.id}", headers=_auth(tenants["user_a"])
    )
    assert cancelled.status_code == 200, "重启后合法 owner 必须仍能取消自己的任务"
    assert cancelled.json()["cancel_requested"] is True

    # 其他用户仍然 404
    other = await client.get(f"/api/v1/tasks/jobs/{job.id}", headers=_auth(tenants["user_b"]))
    assert other.status_code == 404


@pytest.mark.asyncio
async def test_celery_status_endpoint_survives_api_restart(client, db, tenants):
    """旧 `/tasks/status/{celery_task_id}` 端点也必须扛住重启。

    ADR-0052 之前它唯一的归属事实源是进程内 dict —— 重启后合法用户也 404。
    """
    from app.services.jobs import JobStatus
    from app.services.task_queue import TaskQueueService

    job = await _make_job(
        db, owner=tenants["user_a"], session_id="sess-a", status=JobStatus.running
    )
    celery_id = f"celery-{job.id}"

    TaskQueueService._task_owners.clear()  # 模拟重启

    resp = await client.get(
        f"/api/v1/tasks/status/{celery_id}", headers=_auth(tenants["user_a"])
    )
    assert resp.status_code == 200
    body = resp.json()
    # durable 行补充的字段：即使 Celery result backend 不可用也能读到进度
    assert body["job_id"] == str(job.id)
    assert body["durable_status"] == "running"

    denied = await client.get(
        f"/api/v1/tasks/status/{celery_id}", headers=_auth(tenants["user_b"])
    )
    assert denied.status_code == 404


@pytest.mark.asyncio
async def test_celery_revoke_requests_durable_cancel(client, db, tenants):
    """旧 revoke 端点现在也走「先协作取消、后 revoke」。"""
    from app.services.jobs import DurableJobStore, JobStatus, coerce_status

    job = await _make_job(
        db, owner=tenants["user_a"], session_id="sess-a", status=JobStatus.running
    )
    celery_id = f"celery-{job.id}"

    resp = await client.delete(
        f"/api/v1/tasks/status/{celery_id}", headers=_auth(tenants["user_a"])
    )
    assert resp.status_code == 200

    fresh = await DurableJobStore.get(db, job.id)
    await db.refresh(fresh)
    assert coerce_status(fresh.status) is JobStatus.cancelling
    assert fresh.cancel_requested_at is not None


# ── 旧端点兼容（expand-contract） ───────────────────────────────────


@pytest.mark.asyncio
async def test_legacy_task_list_endpoint_still_requires_session_id(client, tenants):
    """审计 S33 的契约不能被本次改动放宽。"""
    resp = await client.get("/api/v1/tasks", headers=_auth(tenants["user_a"]))
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_legacy_and_unified_routes_coexist(client, db, tenants):
    """`/tasks/jobs` 不能被 `/tasks/{task_id}` 吃掉（路由注册顺序）。"""
    resp = await client.get(
        "/api/v1/tasks/jobs?session_id=sess-a", headers=_auth(tenants["user_a"])
    )
    assert resp.status_code == 200
    assert "jobs" in resp.json()
