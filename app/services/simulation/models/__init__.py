"""仿真模型插件包（每个模型一个 DynamicPropagationLaw 实现）。"""

from app.services.simulation.models.hydro_diffusion import (
    HydroDiffusionModel,
)
from app.services.simulation.models.traffic_propagation import (
    TrafficPropagationModel,
)

__all__ = ["HydroDiffusionModel", "TrafficPropagationModel"]
