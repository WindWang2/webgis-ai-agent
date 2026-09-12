"""Data Quality V9 —— durable job 执行体注册（Celery 任务）。

大数据集评估路径（任务书 P1）：REST ``POST /data-quality/reports`` 经
``submit_durable_job`` 入队本任务；worker 侧只做一件事 —— 调
``engine.run_quality_evaluate``（幂等 + 落库 + job 回写都在那里）。

注册点：``app.services.task_queue.celery_app.include`` 增加本模块
（USE_REDIS=false 时 eager 模式原地执行，测试路径同形）。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.services.task_queue import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="data_quality.evaluate_session_ref",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
)
def data_quality_evaluate_task(
    self,
    job_id: int = 0,
    session_id: str = "",
    ref: str = "",
    project_id: Optional[str] = None,
    rule_defs: Optional[list] = None,
    created_by: Optional[str] = None,
    **_kwargs: Any,
) -> dict:
    """session ref 载荷 → 质量评估 → 落库（幂等；重试安全）。"""
    from app.services.data_quality.engine import run_quality_evaluate

    return run_quality_evaluate(
        int(job_id),
        session_id=str(session_id or ""),
        ref=str(ref or ""),
        project_id=project_id,
        rule_defs=rule_defs,
        created_by=created_by,
    )


__all__ = ["data_quality_evaluate_task"]
