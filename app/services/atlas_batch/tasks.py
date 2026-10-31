"""Atlas batch Celery task —— durable_job 薄壳（计算全部在 executor）。

与 ``spatial_tasks.run_ndvi_analysis`` 同模式：``job_id`` 由
``submit_durable_job`` 注入 kwargs；``durable_job`` 上下文负责认领/取消
看门狗/进度节流/终态收敛；任务体只做三件事：

1. ``execute_atlas_job``（typed 失败 = job failed，页级证据已直写
   result_summary）；
2. 成功 → ``finish_job(result=页级终态, result_ref=PDF 文件名)``；
3. 取消/重复投递沿框架既有语义（OperationCancelled → cancelled；
   AlreadyFinished → 跳过）。
"""
from __future__ import annotations

import logging

from app.services.task_queue import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.services.atlas_batch.tasks.run_atlas_export", bind=True)
def run_atlas_export(self, job_id: int | None = None):  # noqa: ANN201
    """批量图册导出（durable job；幂等/取消/重试由框架承载）。"""
    from app.services.atlas_batch.executor import execute_atlas_job
    from app.services.jobs.worker import AlreadyFinished, durable_job, finish_job

    if job_id is None:
        return {"status": "rejected", "message": "atlas export requires a durable job_id"}

    try:
        with durable_job(job_id, celery_task=self) as job:
            result = execute_atlas_job(job_id, job=job)
            job.progress(100, "图册发布完成")
            finish_job(job_id, result=result, result_ref=str(
                (result.get("artifacts") or {}).get("pdf") or ""))
            return result
    except AlreadyFinished:
        # 重复投递（acks_late 重投）：入口守卫已跳过执行
        raise
