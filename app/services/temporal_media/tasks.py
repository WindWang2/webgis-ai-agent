"""G35 Celery 薄壳：复用 G23 ``durable_job`` 的 claim/fencing/heartbeat/取消。

任务体只负责把 ``media_job_id`` 交给领域 runner，并把终态交回平台；
取消由框架 ``OperationCancelled`` 收敛（迟到成功不得覆盖 cancelled）。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.services.task_queue import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.services.temporal_media.tasks.run_temporal_media_job",
                 bind=True)
def run_temporal_media_job(
    self,
    *,
    media_job_id: str,
    principal: str,
    job_id: Optional[int] = None,
) -> dict[str, Any]:
    from app.lib.cancellation import OperationCancelled
    from app.services.jobs.worker import AlreadyFinished, durable_job, finish_job
    from .runner import execute_media_job

    if job_id is None:
        return {"status": "rejected",
                "message": "temporal media requires a durable job_id"}
    try:
        with durable_job(job_id, celery_task=self) as job:
            def cancelled() -> bool:
                try:
                    job.ensure_not_cancelled()
                except OperationCancelled:
                    return True
                return False

            def progress(value: Any = None, message: Any = None, **kwargs: Any) -> None:
                job.progress(value, message, **kwargs)

            receipt = execute_media_job(
                principal, media_job_id, job_handle=job,
                external_cancel=cancelled, external_progress=progress)
            if receipt.get("status") == "cancelled":
                raise OperationCancelled("temporal media job cancelled")
            job.progress(100, "动画与逐帧证据已完成", phase="completed")
            finish_job(
                job_id,
                result={"status": "completed", "job_id": media_job_id,
                        "resources": receipt.get("resources") or {}},
                result_ref=str(((receipt.get("artifacts") or {}).get("animation") or {})
                               .get("filename") or ""),
            )
            return receipt
    except AlreadyFinished:
        raise


__all__ = ["run_temporal_media_job"]
