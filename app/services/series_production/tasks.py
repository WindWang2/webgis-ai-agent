"""Series Production Celery 薄壳 —— durable_job 上下文（atlas 同模式）。"""
from __future__ import annotations

import asyncio
import logging

from pydantic import JsonValue

from app.services.task_queue import celery_app

logger = logging.getLogger(__name__)


def _run_async(coro):
    """在 Celery 同步任务内驱动协程。

    真实 worker 线程无运行中事件循环 → ``asyncio.run``；eager 模式（测试
    在事件循环内同步执行任务）→ 旁路线程跑独立 loop（会话 store/渲染
    offload 均为 loop 局部资源，跨线程安全）。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import threading

    box: dict = {}

    def _target() -> None:
        box["value"] = asyncio.run(coro)

    thread = threading.Thread(target=_target, name="series-production-eager")
    thread.start()
    thread.join()
    return box["value"]


@celery_app.task(name="app.services.series_production.tasks.run_series_production", bind=True)
def run_series_production(self, job_id: int | None = None) -> dict[str, JsonValue]:  # noqa: ANN201
    """系列出版生产（durable job；幂等/取消/重试由框架承载）。

    执行体是 async（ref 水合/渲染 offload）—— Celery 同步任务内用
    ``asyncio.run`` 驱动（worker 线程无运行中事件循环）。
    """
    from app.services.jobs.worker import AlreadyFinished, durable_job, finish_job
    from app.services.series_production.submit import execute_series_production_job

    if job_id is None:
        return {"status": "rejected", "message": "series production requires a durable job_id"}

    try:
        with durable_job(job_id, celery_task=self) as job:
            result = _run_async(execute_series_production_job(job_id, job=job))
            job.progress(100, "系列生产完成")
            finish_job(job_id, result=result,
                       result_ref=str(result.get("manifest_file") or ""))
            return result
    except AlreadyFinished:
        raise


__all__ = ["run_series_production"]
