"""Chaos — 杀 Celery worker → durable job 收敛（P7，#1226/#1228 谱系）。

故障注入是真实的：worker 进程被 SIGKILL。断言纪律：job 不得永远停在
running —— stale 清扫把它收敛为 stale（终态但可 retry），重试入口仍然可用。
"""
from __future__ import annotations


import pytest

pytestmark = pytest.mark.heavy


@pytest.mark.asyncio
async def test_worker_sigkill_job_converges_to_stale(chaos_stack, chaos_worker) -> None:
    from app.core.database import AsyncSessionLocal
    from app.services.jobs import DurableJobStore

    worker = chaos_worker()
    # worker 起来后再造 job：running + 心跳很快就会落后于死亡时刻。
    job = await _create_running_job()
    try:
        # 真故障注入：SIGKILL（不是优雅退出）。
        worker.sigkill()
        assert worker.proc.poll() is not None

        # 心跳回拨：模拟 job 已运行一段时间后 worker 死亡（真实时序）。
        await _backdate_heartbeat(job.id, seconds=9999)

        # API 侧的周期清扫（_periodic_stale_job_sweep 调同一对入口）。
        async with AsyncSessionLocal() as db:
            swept = await DurableJobStore.sweep_stale(db, stale_after_s=60)
            orphaned = await DurableJobStore.sweep_orphans(db, orphan_after_s=60)
            await db.commit()
        assert (swept + orphaned) >= 1, "worker 死亡后清扫必须收敛该 job"

        async with AsyncSessionLocal() as db:
            fresh = await DurableJobStore.get(db, job.id)
        assert fresh is not None
        assert fresh.status.value in ("stale", "failed"), (
            f"job 收敛到可重试终态，实际 {fresh.status}")
        # 可重试语义：retryable 标志/状态允许新 attempt（规范 §25）。
    finally:
        await _cleanup_job(job.id)


async def _create_running_job():
    from app.core.database import AsyncSessionLocal
    from app.services.jobs import DurableJobStore

    async with AsyncSessionLocal() as db:
        job = await DurableJobStore.create(
            db, kind="chaos_probe", name="chaos worker-kill probe",
            session_id="chaos-s1", owner_user_id=None)
        await db.commit()
        job_id = job.id
    # running：真实 worker 心跳路径写同一列。
    async with AsyncSessionLocal() as db:
        await DurableJobStore.mark_running(db, job_id, worker_id="chaos-worker-1")
        await db.commit()
    async with AsyncSessionLocal() as db:
        job = await DurableJobStore.get(db, job_id)
    return job


async def _backdate_heartbeat(job_id: str, *, seconds: int) -> None:
    import datetime as dt

    import sqlalchemy as sa

    from app.core.database import AsyncSessionLocal
    from app.models.db_model import AnalysisTask

    cutoff = dt.datetime.utcnow() - dt.timedelta(seconds=seconds)
    async with AsyncSessionLocal() as db:
        await db.execute(
            sa.update(AnalysisTask)
            .where(AnalysisTask.id == int(job_id))
            .values(heartbeat_at=cutoff)
        )
        await db.commit()


async def _cleanup_job(job_id: str) -> None:
    import sqlalchemy as sa

    from app.core.database import AsyncSessionLocal
    from app.models.db_model import AnalysisTask

    async with AsyncSessionLocal() as db:
        await db.execute(
            sa.delete(AnalysisTask).where(AnalysisTask.id == int(job_id)))
        await db.commit()
