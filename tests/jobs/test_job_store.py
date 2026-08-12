"""Durable job store：原子迁移、竞态、归属隔离、stale 清扫、幂等（ADR-0052）。

竞态用 asyncio.Event / barrier 编排，不用 sleep 猜时序（规范 §42）。
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.db_model import AnalysisTask, Base
from app.services.jobs import (
    DurableJobStore,
    JobKind,
    JobNotFound,
    JobProgress,
    JobStatus,
    coerce_status,
)


def _naive_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest_asyncio.fixture
async def engine(tmp_path):
    eng = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}",
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine):
    return async_sessionmaker(bind=engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def db(session_factory):
    async with session_factory() as s:
        yield s


async def _job(db, **overrides):
    params = {
        "task_type": "ndvi",
        "kind": JobKind.analysis,
        "owner_id": "user-a",
        "session_id": "sess-a",
        "parameters": {"raster_path": "/data/a.tif"},
        # retry 必须能忠实重跑 —— 没有 dispatch 描述符时 store 会拒绝重试
        "dispatch_spec": {
            "task": "app.services.spatial_tasks.run_ndvi_analysis",
            "args": ["/data/a.tif"],
            "kwargs": {},
        },
    }
    params.update(overrides)
    job = await DurableJobStore.create(db, **params)
    await db.commit()
    return job


# ── 创建 / 幂等 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_sets_defaults(db):
    job = await _job(db)
    assert job.id is not None, "BigInteger 主键在 SQLite 上必须自增（dialect variant）"
    assert coerce_status(job.status) is JobStatus.pending
    assert job.attempt == 1
    assert job.retry_count == 0
    assert job.progress == 0
    assert job.job_kind == "analysis"


@pytest.mark.asyncio
async def test_create_is_idempotent_by_key(db):
    """规范 §16：SSE 重连 / 双击 / API retry 不得产生第二个 job。"""
    first = await _job(db, idempotency_key="k-1")
    second = await DurableJobStore.create(
        db, task_type="ndvi", owner_id="user-a", idempotency_key="k-1"
    )
    await db.commit()
    assert second.id == first.id

    rows = (await db.execute(AnalysisTask.__table__.select())).fetchall()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_create_without_key_allows_multiple(db):
    """没有幂等键时不去重 —— 不给所有工具硬编码 hash（规范 §16）。"""
    a = await _job(db)
    b = await _job(db)
    assert a.id != b.id


@pytest.mark.asyncio
async def test_parameters_are_redacted_and_bounded(db):
    """凭据不落库，巨型几何体只留摘要（规范 §35 / §38）。"""
    job = await _job(
        db,
        parameters={
            "token": "super-secret",
            "signed_url": "https://x/y?sig=abc",
            "features": [{"geometry": {}} for _ in range(5000)],
            "raster_path": "/data/a.tif",
        },
    )
    assert job.parameters["token"] == "[REDACTED]"
    assert job.parameters["signed_url"] == "[REDACTED]"
    assert job.parameters["features"]["count"] == 5000
    assert job.parameters["raster_path"] == "/data/a.tif"


# ── 原子迁移 ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_happy_path_transitions(db):
    job = await _job(db)
    assert await DurableJobStore.mark_queued(db, job.id, celery_task_id="celery-1") is True
    assert await DurableJobStore.mark_running(db, job.id) is True
    assert await DurableJobStore.mark_succeeded(db, job.id, result={"mean": 0.4}) is JobStatus.completed
    await db.commit()

    fresh = await DurableJobStore.get(db, job.id)
    assert coerce_status(fresh.status) is JobStatus.completed
    assert fresh.progress == 100
    assert fresh.completed_at is not None
    assert fresh.heartbeat_at is None  # 终态清空心跳，避免被 stale 清扫误判


@pytest.mark.asyncio
async def test_illegal_transition_returns_false_without_writing(db):
    """非法迁移不抛错、不写库 —— 调用方据 False 走幂等分支。"""
    job = await _job(db)
    await DurableJobStore.mark_queued(db, job.id)
    await DurableJobStore.mark_running(db, job.id)
    await DurableJobStore.mark_failed(db, job.id, error=ValueError("bad crs"))
    await db.commit()

    # failed → running 被禁止
    assert await DurableJobStore.transition(db, job.id, JobStatus.running) is False
    fresh = await DurableJobStore.get(db, job.id)
    assert coerce_status(fresh.status) is JobStatus.failed


@pytest.mark.asyncio
async def test_mark_queued_rejects_failed_predecessor(db):
    """failed → queued 必须走 start_retry（递增 attempt），不能被普通入队绕过。"""
    job = await _job(db)
    await DurableJobStore.mark_queued(db, job.id)
    await DurableJobStore.mark_running(db, job.id)
    await DurableJobStore.mark_failed(db, job.id, error="boom")
    await db.commit()
    assert await DurableJobStore.mark_queued(db, job.id) is False


@pytest.mark.asyncio
async def test_transition_on_unknown_job_returns_false(db):
    assert await DurableJobStore.transition(db, 999999, JobStatus.running) is False
    assert await DurableJobStore.transition(db, "not-a-number", JobStatus.running) is False


# ── 取消语义 ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_running_job_goes_to_cancelling(db):
    job = await _job(db)
    await DurableJobStore.mark_queued(db, job.id)
    await DurableJobStore.mark_running(db, job.id)
    await db.commit()

    changed, status = await DurableJobStore.request_cancel(db, job.id)
    await db.commit()
    assert changed is True
    assert status is JobStatus.cancelling
    fresh = await DurableJobStore.get(db, job.id)
    assert fresh.cancel_requested_at is not None


@pytest.mark.asyncio
async def test_cancel_pending_job_terminates_immediately(db):
    """没有 worker 会去确认取消的 job（未 started）直接落 cancelled。"""
    job = await _job(db)
    changed, status = await DurableJobStore.request_cancel(db, job.id)
    await db.commit()
    assert changed is True
    assert status is JobStatus.cancelled


@pytest.mark.asyncio
async def test_double_cancel_is_idempotent(db):
    """规范 Scenario 5。"""
    job = await _job(db)
    await DurableJobStore.mark_queued(db, job.id)
    await DurableJobStore.mark_running(db, job.id)
    await db.commit()

    first = await DurableJobStore.request_cancel(db, job.id)
    second = await DurableJobStore.request_cancel(db, job.id)
    third = await DurableJobStore.request_cancel(db, job.id)
    await db.commit()

    assert first == (True, JobStatus.cancelling)
    assert second == (False, JobStatus.cancelling)
    assert third == (False, JobStatus.cancelling)


@pytest.mark.asyncio
async def test_cancel_completed_job_is_noop(db):
    """终态不得被取消改写 —— completed 永远是 completed。"""
    job = await _job(db)
    await DurableJobStore.mark_queued(db, job.id)
    await DurableJobStore.mark_running(db, job.id)
    await DurableJobStore.mark_succeeded(db, job.id, result={})
    await db.commit()

    changed, status = await DurableJobStore.request_cancel(db, job.id)
    await db.commit()
    assert changed is False
    assert status is JobStatus.completed


@pytest.mark.asyncio
async def test_late_success_does_not_overwrite_cancellation(db):
    """规范 §14 / Scenario 4：cancelled 不能被 worker 的迟到成功改成 completed。"""
    job = await _job(db)
    await DurableJobStore.mark_queued(db, job.id)
    await DurableJobStore.mark_running(db, job.id)
    await DurableJobStore.request_cancel(db, job.id)
    await db.commit()

    # worker 在观察到取消之前跑完了 —— 结果必须被丢弃
    status = await DurableJobStore.mark_succeeded(db, job.id, result={"mean": 0.9})
    await db.commit()
    assert status is JobStatus.cancelled

    fresh = await DurableJobStore.get(db, job.id)
    assert coerce_status(fresh.status) is JobStatus.cancelled
    assert fresh.result_summary is None, "已取消的 job 不应保留结果"


@pytest.mark.asyncio
async def test_late_success_after_confirmed_cancel_is_noop(db):
    job = await _job(db)
    await DurableJobStore.mark_queued(db, job.id)
    await DurableJobStore.mark_running(db, job.id)
    await DurableJobStore.request_cancel(db, job.id)
    await DurableJobStore.confirm_cancelled(db, job.id)
    await db.commit()

    status = await DurableJobStore.mark_succeeded(db, job.id, result={"mean": 0.9})
    await db.commit()
    assert status is JobStatus.cancelled


@pytest.mark.asyncio
async def test_concurrent_cancel_and_complete_yields_single_terminal(session_factory):
    """竞态：API 取消与 worker 完成同时到达（各自独立 session/事务）。

    结果必须是单一确定终态，且绝不是「先 cancelled 后又 completed」。
    用 Event 编排而不是 sleep 猜时序。
    """
    async with session_factory() as setup:
        job = await DurableJobStore.create(
            setup, task_type="ndvi", owner_id="user-a", session_id="sess-a", parameters={}
        )
        await setup.commit()
        await DurableJobStore.mark_queued(setup, job.id)
        await DurableJobStore.mark_running(setup, job.id)
        await setup.commit()
        job_id = job.id

    ready = asyncio.Event()

    async def canceller():
        async with session_factory() as s:
            await ready.wait()
            result = await DurableJobStore.request_cancel(s, job_id)
            await s.commit()
            return result

    async def completer():
        async with session_factory() as s:
            await ready.wait()
            status = await DurableJobStore.mark_succeeded(s, job_id, result={"ok": True})
            await s.commit()
            return status

    task_c = asyncio.create_task(canceller())
    task_d = asyncio.create_task(completer())
    ready.set()
    await asyncio.gather(task_c, task_d, return_exceptions=True)

    async with session_factory() as s:
        fresh = await DurableJobStore.get(s, job_id)
        final = coerce_status(fresh.status)

    # 两种合法收敛：worker 先赢 → completed；取消先赢 → cancelled。
    # 非法的是 cancelling（悬挂）或先 cancelled 再被改成 completed。
    assert final in (JobStatus.completed, JobStatus.cancelled), final


@pytest.mark.asyncio
async def test_duplicate_completion_only_applies_once(session_factory):
    """重复完成（broker 重投 / worker 重试）只能有一次真正写入。

    注意 ``mark_succeeded`` 对「已经是 completed」的 job 返回 completed（幂等读），
    所以不能靠返回值计数 —— 要断言的是「只有一次条件更新命中」以及「结果不被后来
    者改写」。
    """
    async with session_factory() as s:
        job = await DurableJobStore.create(s, task_type="ndvi", owner_id="u", parameters={})
        await s.commit()
        await DurableJobStore.mark_queued(s, job.id)
        await DurableJobStore.mark_running(s, job.id)
        await s.commit()
        job_id = job.id

    async def complete(value):
        async with session_factory() as s:
            # 直接用底层原子迁移，才能观察到「谁真的写成功了」
            won = await DurableJobStore.transition(
                s,
                job_id,
                JobStatus.completed,
                progress=100,
                result_summary={"v": value},
                completed_at=_naive_utc(),
            )
            await s.commit()
            return won

    winners = await asyncio.gather(*(complete(i) for i in range(5)))
    assert winners.count(True) == 1, "并发完成必须只有一个写入者胜出"

    async with session_factory() as s:
        fresh = await DurableJobStore.get(s, job_id)
        assert coerce_status(fresh.status) is JobStatus.completed
        stored = fresh.result_summary["v"]

    # 胜出者写入的结果不得被后续重复投递改写
    async with session_factory() as s:
        assert await DurableJobStore.mark_succeeded(s, job_id, result={"v": 999}) is JobStatus.completed
        await s.commit()
    async with session_factory() as s:
        assert (await DurableJobStore.get(s, job_id)).result_summary["v"] == stored


# ── 进度 ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_progress_updates_active_job(db):
    job = await _job(db)
    await DurableJobStore.mark_queued(db, job.id)
    await DurableJobStore.mark_running(db, job.id)
    await db.commit()

    ok = await DurableJobStore.update_progress(
        db, job.id, JobProgress(progress=42, message="计算中", phase="compute")
    )
    await db.commit()
    assert ok is True
    fresh = await DurableJobStore.get(db, job.id)
    assert fresh.progress == 42
    assert "[compute]" in fresh.progress_message
    assert fresh.heartbeat_at is not None


@pytest.mark.asyncio
async def test_stale_progress_cannot_overwrite_terminal_status(db):
    """规范 §50：迟到的进度写入不得复活已终结的 job。"""
    job = await _job(db)
    await DurableJobStore.mark_queued(db, job.id)
    await DurableJobStore.mark_running(db, job.id)
    await DurableJobStore.request_cancel(db, job.id)
    await DurableJobStore.confirm_cancelled(db, job.id)
    await db.commit()

    ok = await DurableJobStore.update_progress(db, job.id, JobProgress(progress=77, message="late"))
    await db.commit()
    assert ok is False
    fresh = await DurableJobStore.get(db, job.id)
    assert coerce_status(fresh.status) is JobStatus.cancelled
    assert fresh.progress != 77


@pytest.mark.asyncio
async def test_progress_is_clamped_to_check_constraint(db):
    """progress 必须落在 [0,100] —— 否则触发 ck_task_progress 让整个任务失败。"""
    job = await _job(db)
    await DurableJobStore.mark_queued(db, job.id)
    await DurableJobStore.mark_running(db, job.id)
    await db.commit()

    await DurableJobStore.update_progress(db, job.id, JobProgress(progress=500))
    await db.commit()
    assert (await DurableJobStore.get(db, job.id)).progress == 100

    await DurableJobStore.update_progress(db, job.id, JobProgress(progress=-20))
    await db.commit()
    assert (await DurableJobStore.get(db, job.id)).progress == 0


# ── 归属隔离 ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_owner_scoped_read(db):
    """规范 Scenario 9 / §34：user B 看不到 user A 的 job（且 404 语义）。"""
    job = await _job(db, owner_id="user-a")
    got = await DurableJobStore.get_for_owner(db, job.id, owner_id="user-a")
    assert got.id == job.id

    with pytest.raises(JobNotFound):
        await DurableJobStore.get_for_owner(db, job.id, owner_id="user-b")


@pytest.mark.asyncio
async def test_no_ownership_proof_sees_nothing(db):
    """三条归属证明链都缺失时必须是「恒假」，绝不能退化成无过滤全表扫。"""
    await _job(db, owner_id="user-a")
    assert await DurableJobStore.list_for_owner(db) == []
    with pytest.raises(JobNotFound):
        await DurableJobStore.get_for_owner(db, 1)


@pytest.mark.asyncio
async def test_owner_token_proves_ownership_for_anonymous(db):
    """匿名会话靠 owner_token（镜像 SEC-08），没有 creator_id 也能拥有 job。"""
    job = await _job(db, owner_id=None, owner_token="tok-a")
    got = await DurableJobStore.get_for_owner(db, job.id, owner_token="tok-a")
    assert got.id == job.id
    with pytest.raises(JobNotFound):
        await DurableJobStore.get_for_owner(db, job.id, owner_token="tok-b")


@pytest.mark.asyncio
async def test_list_is_scoped_and_bounded(db):
    for i in range(5):
        await _job(db, owner_id="user-a", session_id="sess-a", task_type=f"t{i}")
    for i in range(3):
        await _job(db, owner_id="user-b", session_id="sess-b", task_type=f"u{i}")

    mine = await DurableJobStore.list_for_owner(db, owner_id="user-a")
    assert len(mine) == 5
    assert {j.creator_id for j in mine} == {"user-a"}

    theirs = await DurableJobStore.list_for_owner(db, owner_id="user-b")
    assert len(theirs) == 3

    limited = await DurableJobStore.list_for_owner(db, owner_id="user-a", limit=2)
    assert len(limited) == 2


@pytest.mark.asyncio
async def test_list_limit_is_capped(db):
    """limit 必须有硬上界，防止无界扫描。"""
    await _job(db, owner_id="user-a")
    got = await DurableJobStore.list_for_owner(db, owner_id="user-a", limit=10_000)
    assert len(got) == 1  # 不报错，且内部 clamp 到 MAX_LIST_LIMIT


@pytest.mark.asyncio
async def test_active_only_filter(db):
    active = await _job(db, owner_id="user-a")
    await DurableJobStore.mark_queued(db, active.id)
    await DurableJobStore.mark_running(db, active.id)
    done = await _job(db, owner_id="user-a")
    await DurableJobStore.mark_queued(db, done.id)
    await DurableJobStore.mark_running(db, done.id)
    await DurableJobStore.mark_succeeded(db, done.id, result={})
    await db.commit()

    only_active = await DurableJobStore.list_for_owner(db, owner_id="user-a", active_only=True)
    assert [j.id for j in only_active] == [active.id]


@pytest.mark.asyncio
async def test_verify_celery_owner_is_durable(db):
    """API 重启后（内存 owner map 清空）合法用户仍能凭 durable 行证明归属。"""
    job = await _job(db, owner_id="user-a")
    await DurableJobStore.mark_queued(db, job.id, celery_task_id="celery-xyz")
    await db.commit()

    assert await DurableJobStore.verify_celery_owner(db, "celery-xyz", owner_id="user-a") is True
    assert await DurableJobStore.verify_celery_owner(db, "celery-xyz", owner_id="user-b") is False
    assert await DurableJobStore.verify_celery_owner(db, "celery-xyz") is False
    assert await DurableJobStore.verify_celery_owner(db, "") is False


# ── stale 清扫（worker crash 恢复） ─────────────────────────────────


@pytest.mark.asyncio
async def test_sweep_stale_marks_heartbeat_timeout(db):
    """规范 §25 / Scenario 7：worker 没了，job 不能永远 running。"""
    job = await _job(db)
    await DurableJobStore.mark_queued(db, job.id)
    await DurableJobStore.mark_running(db, job.id)
    await db.commit()

    # 模拟 worker 失联：心跳退回到很久以前
    fresh = await DurableJobStore.get(db, job.id)
    fresh.heartbeat_at = _naive_utc() - timedelta(seconds=900)
    await db.commit()

    swept = await DurableJobStore.sweep_stale(db, stale_after_s=300)
    await db.commit()
    assert swept == 1
    after = await DurableJobStore.get(db, job.id)
    assert coerce_status(after.status) is JobStatus.stale


@pytest.mark.asyncio
async def test_sweep_stale_leaves_healthy_jobs_alone(db):
    job = await _job(db)
    await DurableJobStore.mark_queued(db, job.id)
    await DurableJobStore.mark_running(db, job.id)
    await db.commit()

    assert await DurableJobStore.sweep_stale(db, stale_after_s=300) == 0
    assert coerce_status((await DurableJobStore.get(db, job.id)).status) is JobStatus.running


@pytest.mark.asyncio
async def test_sweep_stale_ignores_terminal_jobs(db):
    """已完成的 job 不能被清扫改成 stale。"""
    job = await _job(db)
    await DurableJobStore.mark_queued(db, job.id)
    await DurableJobStore.mark_running(db, job.id)
    await DurableJobStore.mark_succeeded(db, job.id, result={})
    await db.commit()

    assert await DurableJobStore.sweep_stale(db, stale_after_s=0) == 0
    assert coerce_status((await DurableJobStore.get(db, job.id)).status) is JobStatus.completed


@pytest.mark.asyncio
async def test_sweep_stale_catches_never_heartbeated_job(db):
    """启动很久但一次心跳都没写的 job 也要能被识别。"""
    job = await _job(db)
    await DurableJobStore.mark_queued(db, job.id)
    await DurableJobStore.mark_running(db, job.id)
    await db.commit()
    fresh = await DurableJobStore.get(db, job.id)
    fresh.heartbeat_at = None
    fresh.started_at = _naive_utc() - timedelta(seconds=900)
    await db.commit()

    assert await DurableJobStore.sweep_stale(db, stale_after_s=300) == 1


# ── 重试 ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retry_creates_new_attempt_and_keeps_error(db):
    """规范 §18：重试是新 attempt，不覆盖第一次失败的证据。"""
    job = await _job(db)
    await DurableJobStore.mark_queued(db, job.id)
    await DurableJobStore.mark_running(db, job.id)
    await DurableJobStore.mark_failed(db, job.id, error=ConnectionError("STAC timeout"))
    await db.commit()

    ok, reason = await DurableJobStore.start_retry(db, job.id)
    await db.commit()
    assert (ok, reason) == (True, "requeued")

    fresh = await DurableJobStore.get(db, job.id)
    assert coerce_status(fresh.status) is JobStatus.queued
    assert fresh.attempt == 2
    assert fresh.retry_count == 1
    assert "STAC timeout" in fresh.error_trace  # 首次失败证据保留
    assert fresh.cancel_requested_at is None
    assert fresh.completed_at is None


@pytest.mark.asyncio
async def test_cancelled_job_is_never_retried(db):
    """规范 §17 / Scenario 12：取消绝不能转成 retry。"""
    job = await _job(db)
    await DurableJobStore.mark_queued(db, job.id)
    await DurableJobStore.mark_running(db, job.id)
    await DurableJobStore.request_cancel(db, job.id)
    await DurableJobStore.confirm_cancelled(db, job.id)
    await db.commit()

    ok, reason = await DurableJobStore.start_retry(db, job.id)
    await db.commit()
    assert ok is False
    assert reason == "cancelled_jobs_are_never_retried"
    assert coerce_status((await DurableJobStore.get(db, job.id)).status) is JobStatus.cancelled


@pytest.mark.asyncio
async def test_retry_rejects_running_job(db):
    job = await _job(db)
    await DurableJobStore.mark_queued(db, job.id)
    await DurableJobStore.mark_running(db, job.id)
    await db.commit()
    ok, reason = await DurableJobStore.start_retry(db, job.id)
    assert ok is False
    assert reason == "not_retryable_from_running"


@pytest.mark.asyncio
async def test_retry_respects_max_retries(db):
    job = await _job(db, max_retries=1)
    await DurableJobStore.mark_queued(db, job.id)
    await DurableJobStore.mark_running(db, job.id)
    await DurableJobStore.mark_failed(db, job.id, error="boom")
    await db.commit()

    assert (await DurableJobStore.start_retry(db, job.id))[0] is True
    await db.commit()
    await DurableJobStore.mark_running(db, job.id)
    await DurableJobStore.mark_failed(db, job.id, error="boom again")
    await db.commit()

    ok, reason = await DurableJobStore.start_retry(db, job.id)
    assert ok is False
    assert reason == "max_retries_exhausted"


@pytest.mark.asyncio
async def test_stale_job_can_be_retried(db):
    job = await _job(db)
    await DurableJobStore.mark_queued(db, job.id)
    await DurableJobStore.mark_running(db, job.id)
    await db.commit()
    fresh = await DurableJobStore.get(db, job.id)
    fresh.heartbeat_at = _naive_utc() - timedelta(seconds=900)
    await db.commit()
    await DurableJobStore.sweep_stale(db, stale_after_s=300)
    await db.commit()

    ok, reason = await DurableJobStore.start_retry(db, job.id)
    await db.commit()
    assert (ok, reason) == (True, "requeued")
    assert (await DurableJobStore.get(db, job.id)).attempt == 2


@pytest.mark.asyncio
async def test_concurrent_retry_only_one_wins(session_factory):
    """并发重试不得把 attempt 加两次或产生两次执行。"""
    async with session_factory() as s:
        job = await DurableJobStore.create(
            s,
            task_type="ndvi",
            owner_id="u",
            parameters={},
            dispatch_spec={"task": "t", "args": [], "kwargs": {}},
        )
        await s.commit()
        await DurableJobStore.mark_queued(s, job.id)
        await DurableJobStore.mark_running(s, job.id)
        await DurableJobStore.mark_failed(s, job.id, error="boom")
        await s.commit()
        job_id = job.id

    async def retry():
        async with session_factory() as s:
            ok, reason = await DurableJobStore.start_retry(s, job_id)
            await s.commit()
            return ok

    results = await asyncio.gather(*(retry() for _ in range(4)))
    assert results.count(True) == 1

    async with session_factory() as s:
        fresh = await DurableJobStore.get(s, job_id)
        assert fresh.attempt == 2


# ── worker 侧同步入口 ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_is_cancel_requested_sync_reads_durable_fact(db, engine):
    """worker 从 DB 读取取消 —— 与「取消请求落在哪个进程」无关。"""
    job = await _job(db)
    await DurableJobStore.mark_queued(db, job.id)
    await DurableJobStore.mark_running(db, job.id)
    await DurableJobStore.request_cancel(db, job.id)
    await db.commit()
    job_id = job.id

    # 用同步 sqlite 连接模拟 worker 进程
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    sync_url = str(engine.url).replace("sqlite+aiosqlite", "sqlite")
    sync_engine = create_engine(sync_url)
    Sess = sessionmaker(bind=sync_engine)
    try:
        with Sess() as s:
            assert DurableJobStore.is_cancel_requested_sync(s, job_id) is True
            assert DurableJobStore.is_cancel_requested_sync(s, 99999) is False
    finally:
        sync_engine.dispose()


@pytest.mark.asyncio
async def test_retry_refused_without_dispatch_spec(db):
    """规范 §9：只有 retry 真正可靠时才提供。

    没有 dispatch 描述符就无法忠实重新入队 —— 此时必须明确拒绝，而不是把状态改成
    queued 却永远没有执行体（那样用户会看到一个永不推进的任务）。
    """
    job = await _job(db, dispatch_spec=None)
    await DurableJobStore.mark_queued(db, job.id)
    await DurableJobStore.mark_running(db, job.id)
    await DurableJobStore.mark_failed(db, job.id, error="boom")
    await db.commit()

    ok, reason = await DurableJobStore.start_retry(db, job.id)
    assert ok is False
    assert reason == "no_dispatch_spec"
    assert coerce_status((await DurableJobStore.get(db, job.id)).status) is JobStatus.failed


@pytest.mark.asyncio
async def test_dispatch_spec_with_sensitive_argument_is_rejected(db):
    """凭据绝不落库：带敏感 kwarg 的描述符被标记不可用，因此该 job 不可重试。"""
    job = await _job(
        db,
        dispatch_spec={"task": "t", "args": [], "kwargs": {"api_key": "leak-me"}},
    )
    await db.commit()
    assert "leak-me" not in str(job.dispatch_spec)
    assert job.dispatch_spec["__truncated__"] == "dispatch_spec"

    await DurableJobStore.mark_queued(db, job.id)
    await DurableJobStore.mark_running(db, job.id)
    await DurableJobStore.mark_failed(db, job.id, error="boom")
    await db.commit()
    ok, reason = await DurableJobStore.start_retry(db, job.id)
    assert ok is False
    assert reason == "dispatch_spec_unusable"


@pytest.mark.asyncio
async def test_sweep_converges_cancelling_job_to_cancelled(db):
    """worker 在确认取消前失联：job 必须收敛到 cancelled，绝不能永久停在 cancelling。

    也不能收敛到 stale —— stale 是可重试的，那等于给被取消的任务开了一条重跑后门
    （规范 §17）。
    """
    job = await _job(db)
    await DurableJobStore.mark_queued(db, job.id)
    await DurableJobStore.mark_running(db, job.id)
    await DurableJobStore.request_cancel(db, job.id)
    await db.commit()
    assert coerce_status((await DurableJobStore.get(db, job.id)).status) is JobStatus.cancelling

    fresh = await DurableJobStore.get(db, job.id)
    fresh.heartbeat_at = _naive_utc() - timedelta(seconds=900)
    await db.commit()

    swept = await DurableJobStore.sweep_stale(db, stale_after_s=300)
    await db.commit()
    assert swept == 1
    after = await DurableJobStore.get(db, job.id)
    assert coerce_status(after.status) is JobStatus.cancelled

    ok, reason = await DurableJobStore.start_retry(db, job.id)
    assert ok is False
    assert reason == "cancelled_jobs_are_never_retried"


@pytest.mark.asyncio
async def test_anonymous_owner_id_is_normalized_to_null(db):
    """auth 层的字面量 "anonymous" 不是 users 表里的一行 —— 直接写 creator_id
    会在 PostgreSQL 上违反外键，匿名用户于是完全无法创建 durable job。"""
    job = await _job(db, owner_id="anonymous", owner_token="tok-anon")
    assert job.creator_id is None
    # 归属仍然成立（靠 owner_token）
    got = await DurableJobStore.get_for_owner(db, job.id, owner_token="tok-anon")
    assert got.id == job.id
    # 而 "anonymous" 不能当作一个真实 owner 去匹配别人的行
    assert await DurableJobStore.list_for_owner(db, owner_id="anonymous") == []


# ── 幂等复用策略（round-2 审计） ────────────────────────────────────


@pytest.mark.asyncio
async def test_cancelled_job_does_not_block_resubmission(db):
    """取消后再次请求同样的分析必须能真的跑起来。

    幂等键若无条件复用旧行，提交路径会永远返回「已复用」而什么都不执行 —— 而
    cancelled 又永不可 retry，于是形成一个除了改参数无法脱出的死胡同。
    """
    first = await _job(db, idempotency_key="ndvi:sess-a:abc")
    await DurableJobStore.mark_queued(db, first.id)
    await DurableJobStore.mark_running(db, first.id)
    await DurableJobStore.request_cancel(db, first.id)
    await DurableJobStore.confirm_cancelled(db, first.id)
    await db.commit()

    second = await DurableJobStore.create(
        db, task_type="ndvi", owner_id="user-a", idempotency_key="ndvi:sess-a:abc"
    )
    await db.commit()

    assert second.id != first.id, "取消后的重新提交必须创建新 job"
    # 旧行保留为历史证据，只是让出了幂等键
    old = await DurableJobStore.get(db, first.id)
    assert coerce_status(old.status) is JobStatus.cancelled
    assert old.idempotency_key is None


@pytest.mark.asyncio
async def test_failed_job_does_not_block_resubmission(db):
    first = await _job(db, idempotency_key="k-failed")
    await DurableJobStore.mark_queued(db, first.id)
    await DurableJobStore.mark_running(db, first.id)
    await DurableJobStore.mark_failed(db, first.id, error="boom")
    await db.commit()

    second = await DurableJobStore.create(
        db, task_type="ndvi", owner_id="user-a", idempotency_key="k-failed"
    )
    await db.commit()
    assert second.id != first.id


@pytest.mark.asyncio
async def test_inflight_and_completed_jobs_are_still_reused(db):
    """在飞与已成功的 job 仍然必须被复用 —— 否则双击会跑两次。"""
    running = await _job(db, idempotency_key="k-running")
    await DurableJobStore.mark_queued(db, running.id)
    await DurableJobStore.mark_running(db, running.id)
    await db.commit()
    again = await DurableJobStore.create(
        db, task_type="ndvi", owner_id="user-a", idempotency_key="k-running"
    )
    assert again.id == running.id

    done = await _job(db, idempotency_key="k-done")
    await DurableJobStore.mark_queued(db, done.id)
    await DurableJobStore.mark_running(db, done.id)
    await DurableJobStore.mark_succeeded(db, done.id, result={})
    await db.commit()
    reused = await DurableJobStore.create(
        db, task_type="ndvi", owner_id="user-a", idempotency_key="k-done"
    )
    assert reused.id == done.id


# ── 孤儿 job 清扫（round-2 审计） ───────────────────────────────────


@pytest.mark.asyncio
async def test_orphaned_pending_job_is_swept_to_failed(db):
    """pending/queued 不会心跳 —— 提交路径崩溃留下的 job 必须另有出路。"""
    job = await _job(db)
    fresh = await DurableJobStore.get(db, job.id)
    fresh.created_at = _naive_utc() - timedelta(seconds=7200)
    await db.commit()

    swept = await DurableJobStore.sweep_orphans(db, orphan_after_s=3600)
    await db.commit()
    assert swept == 1
    after = await DurableJobStore.get(db, job.id)
    assert coerce_status(after.status) is JobStatus.failed
    # failed 可重试，因此没有丢掉任何合法结局
    assert after.dispatch_spec is not None


@pytest.mark.asyncio
async def test_orphan_sweep_leaves_recent_and_running_jobs_alone(db):
    recent = await _job(db)
    running = await _job(db)
    await DurableJobStore.mark_queued(db, running.id)
    await DurableJobStore.mark_running(db, running.id)
    await db.commit()

    assert await DurableJobStore.sweep_orphans(db, orphan_after_s=3600) == 0
    assert coerce_status((await DurableJobStore.get(db, recent.id)).status) is JobStatus.pending
    assert coerce_status((await DurableJobStore.get(db, running.id)).status) is JobStatus.running


# ── has_active 不受列表截断影响（round-2 审计） ─────────────────────


@pytest.mark.asyncio
async def test_has_active_is_independent_of_list_limit(db):
    """活跃 job 比一页更旧时，不能误判「无活跃任务」让前端停止轮询。"""
    old_running = await _job(db, owner_id="user-a", session_id="sess-a")
    await DurableJobStore.mark_queued(db, old_running.id)
    await DurableJobStore.mark_running(db, old_running.id)
    await db.commit()

    # 之后再塞一批已完成的 job，把活跃那条挤出第一页
    for _ in range(5):
        done = await _job(db, owner_id="user-a", session_id="sess-a")
        await DurableJobStore.mark_queued(db, done.id)
        await DurableJobStore.mark_running(db, done.id)
        await DurableJobStore.mark_succeeded(db, done.id, result={})
    await db.commit()

    page = await DurableJobStore.list_for_owner(db, owner_id="user-a", limit=2)
    assert all(coerce_status(j.status) is JobStatus.completed for j in page)
    assert await DurableJobStore.has_active_for_owner(db, owner_id="user-a") is True


@pytest.mark.asyncio
async def test_has_active_respects_ownership(db):
    job = await _job(db, owner_id="user-a")
    await DurableJobStore.mark_queued(db, job.id)
    await DurableJobStore.mark_running(db, job.id)
    await db.commit()
    assert await DurableJobStore.has_active_for_owner(db, owner_id="user-a") is True
    assert await DurableJobStore.has_active_for_owner(db, owner_id="user-b") is False
    assert await DurableJobStore.has_active_for_owner(db) is False
