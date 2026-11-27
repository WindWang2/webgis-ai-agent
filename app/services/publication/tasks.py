"""只读地图发布 Celery task —— durable_job 薄壳（atlas_batch 同模式）。

``job_id`` 由 submit_durable_job 注入；计算全部在 executor。
"""
from __future__ import annotations

import logging

from pydantic import JsonValue

from app.services.task_queue import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.services.publication.tasks.run_publication_publish", bind=True)
def run_publication_publish(self, job_id: int | None = None) -> dict[str, JsonValue]:  # noqa: ANN201
    """只读地图发布（物化 + CAS published；幂等/取消/重试由框架承载）。"""
    from app.services.jobs.worker import AlreadyFinished, durable_job, finish_job
    from app.services.publication.executor import materialize_publication

    if job_id is None:
        return {"status": "rejected", "message": "publication publish requires a durable job_id"}

    try:
        with durable_job(job_id, celery_task=self) as job:
            result = materialize_publication(str(job.params.get("publication_id", "")))
            job.progress(100, "只读地图发布完成")
            finish_job(job_id, result=result, result_ref=str(result.get("publication_id", "")))
            return result
    except AlreadyFinished:
        raise
