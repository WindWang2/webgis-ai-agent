"""动态传播规律接口与模型注册表（ADR-0192 D2 三抽象之三）。

``DynamicPropagationLaw`` 是"物理规律"的代码形态：模型即插件。运行时
（runtime.py）只面向本接口编程，对新模型零改动 —— ST-GCN（B011）等
后续模型只需新增一个 law 实现并在 :func:`build_law` 注册。

契约要点：
- ``advance`` 必须返回 **新状态**（不原地改写）+ 本步源/汇记账
  （:class:`StepAccounting`）—— 守恒对账（runtime 每步执行）依赖记账
  与 :meth:`SpatialStateMatrix.total` 的量纲一致；
- ``validate_stability`` 返回当前状态允许的最大稳定步长 dt_max，并在
  ``dt > dt_max`` 时抛 :class:`SimulationStabilityError`（附证据）；
- 模型必须确定性：同参数同步数逐元素一致（检查点续跑等价性的前提）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

import numpy as np

from app.services.simulation.errors import SimulationConfigError
from app.services.simulation.state import SpatialStateMatrix

if TYPE_CHECKING:
    from app.services.simulation.contracts import SimulationParams


@dataclass
class StepAccounting:
    """一步的源/汇记账（守恒变量的自然量纲，与 total() 一致）。

    sources：本步净新增量（如降雨体积、注入车辆）；sinks：本步净移除量
    （如排水体积、驶出车辆）。成对通量（内部交换）**不进记账** —— 它们
    只挪动质量，不创造/销毁质量。
    """

    sources: dict[str, float] = field(default_factory=dict)
    sinks: dict[str, float] = field(default_factory=dict)

    def net(self) -> float:
        return sum(self.sources.values()) - sum(self.sinks.values())


class DynamicPropagationLaw(ABC):
    """物理规律接口：初始状态铸造 + 一步推演 + 稳定域守卫。"""

    #: 守恒变量名（runtime 守恒对账的目标）。
    conserved_variable: ClassVar[str]
    #: 产物变量名与活跃阈值（时态切片展平用）。
    product_variable: ClassVar[str]
    product_threshold: ClassVar[float] = 0.0
    #: 产物图层类型（MapSpec layer type 的选择依据）。
    layer_kind: ClassVar[str] = "raster_grid"

    def __init__(self, params: "SimulationParams"):
        self.params = params

    @abstractmethod
    def build_initial_state(self) -> SpatialStateMatrix:
        """由参数铸初始状态（T0）。"""

    @abstractmethod
    def advance(
        self,
        state: SpatialStateMatrix,
        dt_seconds: float,
        tick: int,
    ) -> tuple[SpatialStateMatrix, StepAccounting]:
        """推进一步（tick 从 1 开始），返回 (新状态, 记账)。"""

    @abstractmethod
    def validate_stability(
        self, state: SpatialStateMatrix, dt_seconds: float
    ) -> float:
        """稳定域守卫：返回当前状态允许的 dt_max；dt 超限抛
        :class:`SimulationStabilityError`（context 携带 dt/dt_max 证据）。"""

    def feature_extras(
        self, state: SpatialStateMatrix
    ) -> dict[str, np.ndarray]:
        """产物 features 的附加属性列（与变量数组对齐）；缺省无。"""
        return {}

    def derived_metrics(self, state: SpatialStateMatrix) -> dict[str, float]:
        """派生指标（诊断 metrics；缺省无）。"""
        return {}


def build_law(params: "SimulationParams") -> DynamicPropagationLaw:
    """按 model_kind 分发到具体模型（注册表；惰性 import 防环）。"""
    from app.services.simulation.contracts import SimulationModelKind

    kind = params.model_kind
    if kind is SimulationModelKind.HYDRO_DIFFUSION:
        from app.services.simulation.models.hydro_diffusion import (
            HydroDiffusionModel,
        )

        return HydroDiffusionModel(params)
    if kind is SimulationModelKind.TRAFFIC_PROPAGATION:
        from app.services.simulation.models.traffic_propagation import (
            TrafficPropagationModel,
        )

        return TrafficPropagationModel(params)
    raise SimulationConfigError(
        f"no propagation law registered for model_kind={kind!r}",
        context={"kind": str(kind)},
    )


__all__ = ["DynamicPropagationLaw", "StepAccounting", "build_law"]
