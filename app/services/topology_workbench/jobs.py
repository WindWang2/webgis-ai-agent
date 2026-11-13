"""Topology Workbench —— durable job 执行体（celery task 注册）。

与 data_quality.jobs 同模式：REST 提交经 submit_durable_job 入队本任务；
worker 只做一件事 —— 调 ``runner.execute_topology_run``（幂等 + 状态落库在
那里）。注册点：``app.services.task_queue.celery_app.include``（USE_REDIS=false
时 eager 原地执行，测试路径同形）。

取消语义（T18）：durable job 取消 → cancellation registry token + DB
``cancel_requested_at`` 探针；worker 在规则间/批间查询。GEOS 原生调用不可
中断 —— 单步规模受预算上限约束，取消延迟 = 当前单规则批的上限（见
planning/G31 PLAN）。任务不会在 cancelled 后发布新结果（findings 只在终态
事务内落库；执行体返回后不再有任何写）。
"""
from __future__ import annotations

import logging
from typing import Any

from app.services.task_queue import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="topology_workbench.run",
    bind=True,
    max_retries=1,
    default_retry_delay=10,
)
def topology_run_task(self, job_id: int = 0, run_id: str = "", **_kwargs: Any) -> dict:
    """run 行 → 拓扑检查执行（幂等；terminal 直接返回）。"""
    from app.services.jobs.cancellation import registry as cancellation_registry
    from app.services.jobs.progress import JobProgress, ProgressReporter
    from app.services.topology_workbench.runner import execute_topology_run

    if not run_id:
        logger.error("[topology-run] missing run_id job=%s", job_id)
        return {"status": "failed", "code": "invalid_params"}

    def _sink(snapshot: JobProgress) -> None:
        """进度 → durable job 行（analysis_tasks.progress；失败不阻断执行）。"""
        if not job_id:
            return
        try:
            from sqlalchemy import update

            from app.core.database import SessionLocal
            from app.models.db_model import AnalysisTask

            with SessionLocal() as db:
                db.execute(
                    update(AnalysisTask)
                    .where(AnalysisTask.id == int(job_id))
                    .values(
                        progress=max(0, min(100, int(snapshot.progress or 0))),
                        progress_message=str(snapshot.as_message() or "")[:255],
                    )
                )
                db.commit()
        except Exception:  # noqa: BLE001 — 进度是可观测性，不是执行语义
            logger.debug("[topology-run] progress write failed job=%s", job_id, exc_info=True)

    reporter = ProgressReporter(_sink) if job_id else None
    if reporter is not None:
        reporter.report(5, "解析源快照")

    # 取消探测：进程内 registry token（同进程 API 取消立即感知）+ DB
    # cancel_requested_at 探针（跨进程/重启后仍感知；节流查询）。
    import time as _time

    _last_db_probe = 0.0
    _db_cancelled: bool | None = None

    def _should_cancel() -> bool:
        nonlocal _last_db_probe, _db_cancelled
        if job_id and cancellation_registry.is_cancelled(int(job_id)):
            return True
        if not job_id:
            return False
        now = _time.monotonic()
        if _db_cancelled is None or now - _last_db_probe > 2.0:
            _last_db_probe = now
            from app.services.jobs.store import DurableJobStore
            from app.tools._utils import db_session

            try:
                with db_session() as db:
                    _db_cancelled = DurableJobStore.is_cancel_requested_sync(
                        db, int(job_id)
                    )
            except Exception:  # noqa: BLE001 — 探针失败不阻断执行（registry 兜底）
                _db_cancelled = _db_cancelled if _db_cancelled is not None else False
        return bool(_db_cancelled)

    result = _run_coro(execute_topology_run(run_id, should_cancel=_should_cancel))

    if reporter is not None:
        if result.get("status") == "completed":
            reporter.flush(100, "完成")
        elif result.get("status") == "cancelled":
            reporter.flush(100, "已取消")
    return result


def _run_coro(coro):
    """协程 → 同步结果。

    celery worker（无事件循环）经 run_sync；eager 模式（TestClient 的运行
    中循环内原地执行）在独立线程的新循环上跑 —— 两条路径语义一致。
    """
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        from app.core.async_runner import run_sync

        return run_sync(coro)

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


__all__ = ["topology_run_task"]
