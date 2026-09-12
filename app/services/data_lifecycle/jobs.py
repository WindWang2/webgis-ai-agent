"""Data Lifecycle V9 —— GC 计划执行的 durable job 执行体（P5）。

``POST /data-lifecycle/gc/plans/{id}/execute`` 经 ``submit_durable_job``
入队本任务；worker 侧只调 ``gc_plan.execute_plan``（状态机/staging 语义
都在那里）。``gc_plan.job_id`` 回链任务中心。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.services.task_queue import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="data_lifecycle.execute_gc_plan",
    bind=True,
    max_retries=1,
    default_retry_delay=30,
)
def execute_gc_plan_task(
    self,
    job_id: int = 0,
    plan_id: str = "",
    actor: Optional[str] = None,
    **_kwargs: Any,
) -> dict:
    """approved 计划 → staging 执行（幂等；中断后可重入）。"""
    from app.core.database import SessionLocal
    from app.services.data_lifecycle.gc_plan import execute_plan, get_plan

    with SessionLocal() as db:
        plan = get_plan(db, plan_id)
        if plan is None:
            return {"status": "failed", "error": "plan not found"}
        result = execute_plan(db, plan, actor=actor)
        _set_plan_job_link(db, plan_id, str(job_id))
        return {"status": "done" if result.get("ok") else "failed",
                "plan_id": plan_id, **result}


def _set_plan_job_link(db: Any, plan_id: str, job_id: str) -> None:
    try:
        from app.models.data_lifecycle import GcPlan

        plan = db.get(GcPlan, plan_id)
        if plan is not None:
            plan.job_id = job_id[:64]
            db.commit()
    except Exception:  # noqa: BLE001 — 回链失败不影响执行结果
        logger.warning("gc plan %s job link writeback failed", plan_id,
                       exc_info=True)


__all__ = ["execute_gc_plan_task"]
