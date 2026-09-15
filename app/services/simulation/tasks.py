"""时空仿真的 Celery 任务体（ADR-0192 D6 / spec §6）。

穿透既有 durable-job 运行时（geocompute/tasks.py 同款纪律）：

- 生产派发只经 ``submit_durable_job`` —— worker 侧 ``durable_job()``
  上下文内按 output_stride 上报进度，终态 ``finish_job`` 落有界摘要；
- ``job_id=None`` 仅作为 eager/测试直调路径存在（显式告警，无持久语义）；
- 任务参数是纯 JSON dump（contracts 契约），worker 侧重验证 —— Celery
  边界上的载荷不可信，pydantic 再验一次。

本模块必须登记进 ``app/services/task_queue.py::celery_app`` 的 include
列表（test_task_module_registered_in_celery_include 守卫）。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.services.task_queue import celery_app

logger = logging.getLogger(__name__)


def _bounded_summary(result: Any) -> dict[str, Any]:
    """有界摘要（进 job 行 / 返回值；绝不含状态数组本体）。"""
    return {
        "status": result.status.value,
        "run_id": result.run_id,
        "model_kind": result.model_kind.value,
        "n_steps": result.n_steps,
        "dt_seconds": result.dt_seconds,
        "conservation": (
            result.conservation_report.model_dump(mode="json")
            if result.conservation_report is not None
            else None
        ),
        "final_metrics": dict(result.final_metrics),
        "products": [
            {
                "product_id": p.product_id,
                "tick": p.tick,
                "format": p.format,
                "ref": p.ref,
                "uri": p.uri,
                "feature_count": p.feature_count,
            }
            for p in result.products
        ],
        "product_count": len(result.products),
    }


@celery_app.task(
    name="app.services.simulation.tasks.run_simulation_forecast", bind=True,
)
def run_simulation_forecast(
    self,
    params: dict,
    config: dict,
    session_id: Optional[str] = None,
    job_id: Optional[int] = None,
) -> dict[str, Any]:
    """执行一次时空推演任务（参数为 contracts 契约的 JSON dump）。"""
    from app.services.simulation.contracts import (
        SimulationRunConfig,
        parse_params,
    )
    from app.services.simulation.api import run_simulation

    params_model = parse_params(params)
    config_model = SimulationRunConfig.model_validate(config)

    if job_id is None:
        # 直调（无 durable 语义）只允许 eager 测试路径存在；生产派发必经
        # submit_durable_job。这里仍诚实执行，但无状态机保护。
        logger.warning(
            "[simulation] run_simulation_forecast called without job_id "
            "(eager/test path)"
        )
        result = run_simulation(params_model, config_model)
        return _bounded_summary(result)

    from app.services.jobs.worker import durable_job, finish_job

    with durable_job(job_id, celery_task=self) as job:
        job.progress(5, "仿真任务初始化", phase="init")
        job.ensure_not_cancelled()
        stride = max(int(config_model.output_stride), 1)

        def _progress(tick: int, total: int, phase: str) -> None:
            # 取消令牌在每个检查点生效：被取消的推演立即中断（10 万步
            # 上限的循环不允许跑完才响应取消）。OperationCancelled 穿透
            # runtime（转 cancelled 终态）并由 durable_job 收敛为 cancelled。
            job.ensure_not_cancelled()
            # 进度按 output_stride 节流上报（spec §6 口径；平台侧另有落库节流）
            if tick % stride == 0 or tick == total:
                pct = 5 + int(90.0 * tick / max(total, 1))
                job.progress(pct, f"tick {tick}/{total}", phase=phase)

        result = run_simulation(params_model, config_model,
                                progress_callback=_progress)
        job.ensure_not_cancelled()  # 落终态前的强制检查
        result_ref = result.products[-1].ref if result.products else None
        summary = _bounded_summary(result)
        finish_job(job_id, result=summary, result_ref=result_ref)
        return summary


__all__ = ["run_simulation_forecast"]
