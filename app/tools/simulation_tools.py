"""时空动态仿真推演工具面（ADR-0192 D9）。

``run_spatial_simulation``：物理机制驱动的多时相数值推演入口。
- 水文内涝（hydro_diffusion）：DEM + Manning 粗糙度，暴雨淹没推演；
- 交通潮汐（traffic_propagation）：路网瓶颈注入，拥堵蔓延推演。

与 ``what_if_simulate``（规则式情景模拟）的边界：本工具是**数值推演**，
产出 T0..TN 多时相图层（GeoParquet/内存提货券 + MapSpec v1.2 frames
时间轴），不做指标规则的 RAG 佐证。

错误契约：域异常一律转 ``std_error_response``（不向 LLM 抛栈）；
多步推演默认走 Celery durable job 异步路径（run_async=False 时进程内
同步执行，仅小规模/测试使用）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal, Optional

from pydantic import BaseModel, Field, ValidationError

from app.services.simulation.api import enqueue_simulation
from app.services.simulation.contracts import (
    SimulationRunConfig,
    parse_params,
)
from app.services.simulation.errors import (
    SimulationConfigError,
    SimulationError,
    SimulationStabilityError,
)
from app.tools.registry import ToolRegistry, tool
from app.tools._utils import std_error_response

logger = logging.getLogger(__name__)

_VALID_MODELS = ("hydro_diffusion", "traffic_propagation")


class SimulationForecastArgs(BaseModel):
    """run_spatial_simulation 的参数契约（显式 args_model）。"""

    model_kind: Literal["hydro_diffusion", "traffic_propagation"] = Field(
        description="推演模型：hydro_diffusion=暴雨内涝淹没；"
                    "traffic_propagation=路网拥堵蔓延",
    )
    params: dict = Field(
        default_factory=dict,
        description="模型参数（json）：hydro→HydroSimulationParams"
                    "（grid/elevation_m/manning_n/boundary...）；"
                    "traffic→TrafficSimulationParams（edges/boundary...）",
    )
    dt_seconds: float = Field(default=60.0, gt=0, description="时间步长 [s]")
    n_steps: int = Field(default=30, ge=1, le=100000, description="推演步数")
    output_stride: int = Field(
        default=5, ge=1, description="每 k 步铸一片时态图层（≤50 片）",
    )
    start_time: Optional[str] = Field(
        default=None, description="模拟时钟起点 ISO；缺省仅用相对秒",
    )
    run_async: bool = Field(
        default=True, description="True=Celery 后台任务；False=进程内同步执行",
    )
    session_id: str = Field(default="", description="会话 id（注册面注入）")


def _validate_params(model_kind: str, params: dict):
    """模型参数校验（Celery 边界同款：入参不可信，再验一次）。"""
    dump = dict(params or {})
    dump["model_kind"] = model_kind
    return parse_params(dump)


def run_spatial_simulation_impl(
    *,
    model_kind: str,
    params: dict,
    dt_seconds: float = 60.0,
    n_steps: int = 30,
    output_stride: int = 5,
    start_time: Optional[str] = None,
    run_async: bool = True,
    session_id: str = "",
) -> dict:
    """工具实现（同步可直调；注册面 wrapper 与单测共用）。"""
    try:
        if model_kind not in _VALID_MODELS:
            raise SimulationConfigError(
                f"unknown model_kind {model_kind!r}",
                context={"kind": str(model_kind)},
            )
        params_model = _validate_params(model_kind, params)
        config_model = SimulationRunConfig(
            dt_seconds=dt_seconds,
            n_steps=n_steps,
            output_stride=output_stride,
            start_time=start_time,
        )
        if run_async:
            envelope = enqueue_simulation(
                params_model, config_model, session_id=session_id or None,
            )
            return {"success": True, "data": envelope}

        from app.services.simulation.api import build_tool_result, run_simulation

        result = run_simulation(params_model, config_model)
        return build_tool_result(result)
    except SimulationStabilityError as exc:
        return std_error_response(
            exc.user_message,
            code="VALIDATION_ERROR",
            error_type=type(exc).__name__,
            correction_hint="减小 dt_seconds，使其落在稳定域内"
                            "（诊断 context 含 dt_max 证据）。",
        )
    except SimulationConfigError as exc:
        errors = (exc.context or {}).get("errors")
        return std_error_response(
            exc.user_message,
            code="VALIDATION_ERROR",
            error_type=type(exc).__name__,
            correction_hint=str(errors) if errors else str(exc.context or {}),
        )
    except ValidationError as exc:
        return std_error_response(
            "仿真参数校验失败",
            code="VALIDATION_ERROR",
            error_type="ValidationError",
            correction_hint=str(exc.errors()[:3]),
        )
    except SimulationError as exc:
        return std_error_response(
            exc.user_message,
            code="TOOL_ERROR",
            error_type=type(exc).__name__,
        )


def register_simulation_tools(registry: ToolRegistry) -> None:
    """注册时空仿真工具（加入 app/tools/__init__.py 的 _TOOL_MODULES）。"""

    @tool(
        registry,
        name="run_spatial_simulation",
        description=(
            "时空动态仿真推演：物理机制驱动的微观推演模型。"
            "hydro_diffusion 基于 DEM 高程梯度 + Manning 粗糙度推演暴雨内涝"
            "淹没扩散（质量守恒）；traffic_propagation 基于路网拓扑推演瓶颈"
            "注入后的拥堵潮汐蔓延（车辆守恒）。\n"
            "✅ 用于：向未来推导演变（预测性/处方性 GIS）、防灾应急情景推演、"
            "多时相动态图层（时间轴播放）。\n"
            "❌ 不要用于：静态现状分析（用 kde/lisa/overlay）；"
            "规则式情景影响速查（用 what_if_simulate）。\n"
            "产物：T0..TN 多时相图层提货券 + MapSpec v1.2 frames 时间轴骨架；"
            "多步推演默认作为后台任务执行（任务中心可查进度/取消）。"
        ),
        tier=3,
        domains=["simulation"],
        capabilities=['scenario_simulation'],
        cost="heavy",
        timeout=600.0,
        args_model=SimulationForecastArgs,
        side_effect="deterministic_compute",
        network=False,
        deterministic=True,
        latency_class="slow",
        memory_class="heavy",
        scale_class="large",
        output_semantic_type="geojson_fc",
        result_size_policy="ref_offload",
        data_mutations=(),
        tags=("仿真", "推演", "内涝", "交通拥堵", "simulation",
              "时空动态", "预测"),
        failure_modes=("invalid_args", "missing_data", "resource_exhausted"),
    )
    async def _run_spatial_simulation(
        model_kind: str,
        params: Optional[dict] = None,
        dt_seconds: float = 60.0,
        n_steps: int = 30,
        output_stride: int = 5,
        start_time: Optional[str] = None,
        run_async: bool = True,
        session_id: str = "",
    ) -> dict:
        payload = dict(params or {})
        if run_async:
            return run_spatial_simulation_impl(
                model_kind=model_kind, params=payload,
                dt_seconds=dt_seconds, n_steps=n_steps,
                output_stride=output_stride, start_time=start_time,
                run_async=True, session_id=session_id,
            )
        # 同步路径放线程池，避免阻塞事件循环
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: run_spatial_simulation_impl(
                model_kind=model_kind, params=payload,
                dt_seconds=dt_seconds, n_steps=n_steps,
                output_stride=output_stride, start_time=start_time,
                run_async=False, session_id=session_id,
            ),
        )
