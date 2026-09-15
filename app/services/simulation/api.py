"""仿真子域稳定入口（spec §6）。

- :func:`run_simulation`：同步进程内推演（纯数值核；单测与小规模直跑）；
- :func:`_enqueue_simulation`：Celery durable job 提交封装（生产路径，
  多步数值推演必须异步流转，ADR-0192 D6）；
- :func:`build_tool_result`：把 SimulationResult 组装成有界的工具面响应
  （载荷一律走提货券，LLM 只见摘要 + ref，fetch-on-demand 纪律）。

本模块是 simulation 包对平台设施的唯一接线层之一（另一处是 tasks.py）；
数值核模块不得反向 import 本模块。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from app.services.simulation.contracts import (
    SimulationParams,
    SimulationRunConfig,
)
from app.services.simulation.laws import DynamicPropagationLaw
from app.services.simulation.layers import TemporalSink, build_mapspec_bundle
from app.services.simulation.runtime import SimulationResult, SimulationRuntime

logger = logging.getLogger(__name__)


def run_simulation(
    params: SimulationParams,
    config: SimulationRunConfig,
    *,
    law: Optional[DynamicPropagationLaw] = None,
    sink: Optional[TemporalSink] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> SimulationResult:
    """同步执行一次时空推演（T0..TN 多时相产物随 result.products 返回）。"""
    return SimulationRuntime(params, config, law=law, sink=sink).run(
        progress_callback=progress_callback,
    )


def enqueue_simulation(
    params: SimulationParams,
    config: SimulationRunConfig,
    *,
    session_id: Optional[str] = None,
) -> dict[str, Any]:
    """提交 durable job（幂等键 = task_type + session + 规范化参数）。"""
    from app.services.jobs.submit import submit_durable_job
    from app.services.simulation.tasks import run_simulation_forecast

    params_dump = params.model_dump(mode="json")
    return submit_durable_job(
        celery_task=run_simulation_forecast,
        task_type="simulation_forecast",
        display_name="时空动态仿真推演",
        params=params_dump,
        task_kwargs={
            "params": params_dump,
            "config": config.model_dump(mode="json"),
        },
        session_id=session_id,
    )


def build_tool_result(result: SimulationResult) -> dict[str, Any]:
    """SimulationResult → 工具面有界响应（摘要 + 提货券，无大载荷）。"""
    bundle = build_mapspec_bundle(result)
    frames = bundle.get("layout", {}).get("frames", [])
    return {
        "success": True,
        "data": {
            "run_id": result.run_id,
            "status": result.status.value,
            "model_kind": (
                result.model_kind.value
                if hasattr(result.model_kind, "value")
                else str(result.model_kind)
            ),
            "n_steps": result.n_steps,
            "dt_seconds": result.dt_seconds,
            "conservation": (
                result.conservation_report.model_dump(mode="json")
                if result.conservation_report is not None
                else None
            ),
            "product_count": len(result.products),
            "products": [p.model_dump(mode="json") for p in result.products],
            "mapspec_frames": len(frames),
            "final_metrics": dict(result.final_metrics),
        },
    }


__all__ = ["build_tool_result", "enqueue_simulation", "run_simulation"]
