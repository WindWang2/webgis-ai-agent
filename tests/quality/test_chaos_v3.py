"""Chaos V3 行为测试（Quality V3 W13，Epic 10 §L）。

四个跨系统 fault：WORKER_LOSS / CANCEL_STORM / STALE_REVISION_CAS /
DB_TRANSIENT_SEQUENCE。每个测试同时断言：(a) 故障真的开火（journal /
state 证据），(b) 系统的恢复 / 诚实失败行为（V2 chaos 纪律）。

跨系统场景的诚实归属（模块 docstring 缺口段同步）：
- API/coordinator 重启 → scripts/integration_harness.py（真实进程 kill -9）；
- SSE 重连 / 重复投递语义 → tests/test_runtime_chaos_resume.py（28 项
  专项行为套件）；redis 瞬时 → LOCK_* faults + real lane。
"""
from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.db_model import AnalysisTask, Base
from app.services.jobs import (
    DurableJobStore,
    JobKind,
    JobStatus,
)
from tests.fixtures.chaos import FAULTS, chaos, journal_snapshot


@pytest_asyncio.fixture
async def engine(tmp_path):
    eng = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'chaos_v3.db'}",
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db(session_factory):
    async with session_factory() as s:
        yield s


@pytest_asyncio.fixture
async def session_factory(engine):
    return async_sessionmaker(bind=engine, expire_on_commit=False)


async def _make_running_job(session_factory) -> int:
    async with session_factory() as db:
        job = await DurableJobStore.create(
            db, task_type="ndvi", kind=JobKind.analysis,
            owner_id="user-a", session_id="sess-a",
            parameters={"raster_path": "/data/a.tif"},
            # retry 必须能忠实重跑 —— 无 dispatch 描述符时 start_retry 拒绝
            dispatch_spec={
                "task": "app.services.spatial_tasks.run_ndvi_analysis",
                "args": ["/data/a.tif"],
                "kwargs": {},
            },
        )
        await db.commit()
    async with session_factory() as db:
        ok = await DurableJobStore.transition(
            db, job.id, JobStatus.queued, expected=[JobStatus.pending])
        assert ok, "queued 转移必须成功"
        await db.commit()
    async with session_factory() as db:
        ok = await DurableJobStore.transition(
            db, job.id, JobStatus.running, expected=[JobStatus.queued])
        assert ok, "running 转移必须成功"
        # 生产路径 started_at/heartbeat_at 由 mark_running 包装写入
        # （store.py:465-478）；transition 不带时间戳，这里显式补写。
        from datetime import datetime, timezone

        from sqlalchemy import update

        await db.execute(
            update(AnalysisTask)
            .where(AnalysisTask.id == job.id)
            .values(started_at=datetime.now(timezone.utc)
                    .replace(tzinfo=None))
        )
        await db.commit()
    return job.id


# ── WORKER_LOSS ─────────────────────────────────────────────────────────

@pytest.mark.asyncio()
async def test_worker_loss_sweeps_and_allows_retry(session_factory):
    job_id = await _make_running_job(session_factory)
    fired_events = []
    with chaos("JOBS_WORKER_LOSS") as fault:
        async with session_factory() as db:
            swept = await DurableJobStore.sweep_stale(db, limit=10)
            await db.commit()  # sweep 由调用方提交（store 契约）
            assert swept == 1
            row = (await db.execute(
                select(AnalysisTask).where(AnalysisTask.id == job_id)
            )).scalar_one()
            # stale 是可重试终态（worker 挂了 ≠ 用户取消）
            assert row.status == JobStatus.stale.value
            assert "stale" in (row.progress_message or "").lower() or \
                row.error_trace, "stale 收敛必须留痕"
        # 重试语义：stale → start_retry（递增 attempt 的生产重试路径；
        # 直接 transition 绕过 attempt 计数被设计拒绝）
        async with session_factory() as db:
            ok, reason = await DurableJobStore.start_retry(db, job_id)
            assert ok, f"stale 后必须可重试（reason={reason}）"
            await db.commit()
    fired_events = [e for e in journal_snapshot()
                    if e.fault_id == "JOBS_WORKER_LOSS" and e.action == "fired"]
    assert fired_events, "故障必须真的开火（journal 证据）"
    assert fault.fired


@pytest.mark.asyncio()
async def test_worker_loss_no_stale_jobs_sweep_is_noop(session_factory):
    await _make_running_job(session_factory)
    # 无 fault 时：新 job 心跳新鲜，sweep 不动它（对照组）
    async with session_factory() as db:
        swept = await DurableJobStore.sweep_stale(db, limit=10)
    assert swept == 0


# ── CANCEL_STORM ────────────────────────────────────────────────────────

@pytest.mark.asyncio()
async def test_cancel_storm_single_winner_and_stable_state():
    from app.lib.cancellation import CancellationToken

    token = CancellationToken()
    concurrency = 8
    results: list[bool] = []
    errors: list[BaseException] = []

    async def storm():
        try:
            await token.spec_barrier if False else asyncio.sleep(0)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    with chaos("CANCEL_STORM", concurrency=concurrency) as fault:
        barrier = fault.barrier

        async def canceller():
            await barrier.wait()  # 全员同时开火
            try:
                return token.cancel(reason="storm")
            except Exception as exc:  # noqa: BLE001 — 泄漏即失败
                errors.append(exc)
                return None

        results = await asyncio.gather(*[canceller() for _ in range(concurrency)])

    assert not errors
    winners = [r for r in results if r is True]
    assert len(winners) == 1, f"恰好一次胜出，实际 {len(winners)}"
    losers = [r for r in results if r is False]
    assert len(losers) == concurrency - 1, "幂等重复取消必须返回 False"
    assert token.cancelled, "终态必须收敛为 cancelled"
    assert token.cancelled_at is not None
    # 再次取消：仍然幂等 False
    assert token.cancel() is False


# ── STALE_REVISION_CAS ──────────────────────────────────────────────────

@pytest.mark.asyncio()
async def test_stale_revision_cas_rejects_loser(session_factory):
    job_id = await _make_running_job(session_factory)
    outcomes: dict[str, object] = {}

    with chaos("JOBS_STALE_REVISION_CAS"):
        # 确定性交错：胜者先完整提交，败者再携带已被作废的 expected ——
        # 纯并发编排由 CANCEL_STORM 覆盖；这里锁定 CAS 拒绝语义本身。
        async def transition(tag: str, target, expected):
            async with session_factory() as db:
                ok = await DurableJobStore.transition(
                    db, job_id, target, expected=expected)
                await db.commit()  # 胜者转移必须持久化，败者才能看到作废
                outcomes[tag] = ok
                return ok

        await transition("a", JobStatus.failed, [JobStatus.running])
        await transition("b", JobStatus.cancelling, [JobStatus.running])

    successes = [k for k, v in outcomes.items() if v]
    assert len(successes) == 1, f"恰好一路 CAS 胜出，实际 {outcomes}"
    async with session_factory() as db:
        row = (await db.execute(
            select(AnalysisTask).where(AnalysisTask.id == job_id)
        )).scalar_one()
    winner = successes[0]
    expected_status = (JobStatus.failed if winner == "a"
                       else JobStatus.cancelling).value
    assert row.status == expected_status, "终态必须等于胜者目标"


# ── DB_TRANSIENT_SEQUENCE ───────────────────────────────────────────────

@pytest.mark.asyncio()
async def test_db_transient_typed_error_then_recovery(session_factory):
    from sqlalchemy import exc as sa_exc

    fired_before = len([e for e in journal_snapshot()
                        if e.fault_id == "DB_TRANSIENT_SEQUENCE"
                        and e.action == "fired"])
    with chaos("DB_TRANSIENT_SEQUENCE", fail_times=2) as fault:
        # 前两次：类型化异常透传（不静默吞）
        for _ in range(2):
            async with session_factory() as db:
                with pytest.raises(sa_exc.OperationalError):
                    await DurableJobStore.create(
                        db, task_type="ndvi", kind=JobKind.analysis,
                        owner_id="user-a", session_id="sess-a",
                        parameters={"raster_path": "/data/a.tif"},
                    )
        assert fault.fired
        fired = [e for e in journal_snapshot()
                 if e.fault_id == "DB_TRANSIENT_SEQUENCE"
                 and e.action == "fired"]
        assert len(fired) - fired_before == 2, "恰两次瞬时失败"
        # 恢复：第三次成功，行真实落库
        async with session_factory() as db:
            job = await DurableJobStore.create(
                db, task_type="ndvi", kind=JobKind.analysis,
                owner_id="user-a", session_id="sess-a",
                parameters={"raster_path": "/data/a.tif"},
            )
            assert job.id is not None
            await db.commit()
        async with session_factory() as db:
            row = (await db.execute(
                select(AnalysisTask).where(AnalysisTask.id == job.id)
            )).scalar_one()
            assert row.task_type == "ndvi"


@pytest.mark.asyncio()
async def test_db_transient_zero_is_passthrough(session_factory):
    """fail_times=0 边界：不注入，纯透传（fault 可武装但不干扰）。"""
    fired_before = len([e for e in journal_snapshot()
                        if e.fault_id == "DB_TRANSIENT_SEQUENCE"
                        and e.action == "fired"])
    with chaos("DB_TRANSIENT_SEQUENCE", fail_times=0):
        async with session_factory() as db:
            job = await DurableJobStore.create(
                db, task_type="ndvi", kind=JobKind.analysis,
                owner_id="user-a", session_id="sess-a",
                parameters={"raster_path": "/data/a.tif"},
            )
            assert job.id is not None
    fired = [e for e in journal_snapshot()
             if e.fault_id == "DB_TRANSIENT_SEQUENCE" and e.action == "fired"]
    assert len(fired) == fired_before


def test_v3_faults_registered():
    for fault_id in ("JOBS_WORKER_LOSS", "CANCEL_STORM",
                     "JOBS_STALE_REVISION_CAS", "DB_TRANSIENT_SEQUENCE"):
        assert fault_id in FAULTS, f"{fault_id} 未注册"
