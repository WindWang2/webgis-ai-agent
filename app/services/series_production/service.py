"""Series Production 提交入口 —— durable job 幂等提交（WP5 工具/REST 共用）。"""
from __future__ import annotations

from typing import Any, Dict, Optional

from app.services.series_production.contract import SeriesFreezePlan

SERIES_DISPLAY_NAME = "系列出版生产"


def submit_series_production(
    plan: SeriesFreezePlan,
    *,
    freeze_payload: Dict[str, Any],
    origin_session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """提交 durable job（幂等键含 freeze 载荷）。

    ``freeze_payload``（WP3 freeze 原始 dict）随 params 落库 —— 执行体从
    job 行重建计划（不依赖提交时的活对象）。freeze 计划只有 pin/指纹/
    字段名（无数据载荷、无凭证），可安全入账。
    """
    from app.services.jobs.lifecycle import JobKind
    from app.services.jobs.submit import submit_durable_job
    from app.services.series_production.submit import (
        SERIES_TASK_TYPE,
        job_params,
    )
    from app.services.series_production.tasks import run_series_production

    params = job_params(plan)
    params["freeze_plan"] = freeze_payload
    return submit_durable_job(
        celery_task=run_series_production,
        task_type=SERIES_TASK_TYPE,
        display_name=f"{SERIES_DISPLAY_NAME} · {plan.name or plan.series_id[:12]}",
        params=params,
        kind=JobKind.series_production,
        session_id=origin_session_id,
        idempotent=True,
    )


__all__ = ["SERIES_DISPLAY_NAME", "submit_series_production"]
