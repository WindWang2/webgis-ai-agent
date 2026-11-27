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
    from app.services.jobs.store import DurableJobStore
    from app.services.jobs.worker import AlreadyFinished, durable_job, finish_job
    from app.services.publication.executor import materialize_publication
    from app.tools._utils import db_session

    if job_id is None:
        return {"status": "rejected", "message": "publication publish requires a durable job_id"}

    with db_session() as db:
        record = DurableJobStore.get_sync(db, job_id)
        params = record.parameters if isinstance(record.parameters, dict) else {}

    try:
        with durable_job(job_id, celery_task=self) as job:
            # 物化固定在专用线程执行：Celery worker 线程本无 loop；eager 模式
            # （无 Redis）下任务体内联在路由事件循环 —— executor 的 run_sync
            # 水合在有 running loop 的线程上会 fail-closed，专用线程两种模式
            # 语义一致（同有界 worker 纪律）。
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(
                    materialize_publication, str(params.get("publication_id", ""))
                ).result()
            job.progress(100, "只读地图发布完成")
            finish_job(job_id, result=result, result_ref=str(result.get("publication_id", "")))
            return result
    except AlreadyFinished:
        raise
