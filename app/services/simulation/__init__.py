"""时空因果推演与动态微观仿真运行时（ADR-0192）。

包式组织（geocompute 同款纪律）：

- 数值核（contracts/state/laws/runtime/models/layers）：纯 numpy + pydantic，
  不 import DB / Redis / Celery / app.tools / app.core.config —— 可解释器内
  裸跑、可离线单测；
- 平台接线（api.py / tasks.py）：Celery durable job 与工具面桥接；
- 工具面：app/tools/simulation_tools.py（run_spatial_simulation）。

数据面红线：本包不得依赖 app.tools（ADR-0096 D1 同款边界，boundary 测试守卫）。
"""

from app.services.simulation.api import run_simulation
from app.services.simulation.contracts import (
    BottleneckSpec,
    ConservationReport,
    GridSpec,
    HydroBoundaryCondition,
    HydroSimulationParams,
    MAX_SLICE_FEATURES,
    MAX_TEMPORAL_FRAMES,
    NetworkEdgeSpec,
    RasterFieldSpec,
    SimulationModelKind,
    SimulationParams,
    SimulationRunConfig,
    SimulationStatus,
    SimulationStep,
    SimulationStepDiagnostics,
    TemporalLayerProduct,
    TrafficBoundaryCondition,
    TrafficSimulationParams,
    parse_params,
)
from app.services.simulation.errors import (
    InvalidSimulationTransition,
    SimulationBudgetError,
    SimulationCancelledError,
    SimulationConfigError,
    SimulationError,
    SimulationStabilityError,
    SimulationStateError,
)
from app.services.simulation.layers import (
    GeoParquetTemporalSink,
    InMemoryTemporalSink,
    LayerDraft,
    TemporalSink,
    build_mapspec_bundle,
)
from app.services.simulation.laws import (
    DynamicPropagationLaw,
    build_law,
)
from app.services.simulation.runtime import (
    SimulationResult,
    SimulationRuntime,
)
from app.services.simulation.state import (
    GraphStateMatrix,
    GraphTopology,
    RasterStateMatrix,
    SpatialStateMatrix,
)

__all__ = [
    "BottleneckSpec",
    "ConservationReport",
    "DynamicPropagationLaw",
    "GeoParquetTemporalSink",
    "GraphStateMatrix",
    "GraphTopology",
    "GridSpec",
    "HydroBoundaryCondition",
    "HydroSimulationParams",
    "InMemoryTemporalSink",
    "InvalidSimulationTransition",
    "LayerDraft",
    "MAX_SLICE_FEATURES",
    "MAX_TEMPORAL_FRAMES",
    "NetworkEdgeSpec",
    "RasterFieldSpec",
    "RasterStateMatrix",
    "SpatialStateMatrix",
    "SimulationBudgetError",
    "SimulationCancelledError",
    "SimulationConfigError",
    "SimulationError",
    "SimulationModelKind",
    "SimulationParams",
    "SimulationResult",
    "SimulationRunConfig",
    "SimulationRuntime",
    "SimulationStabilityError",
    "SimulationStateError",
    "SimulationStatus",
    "SimulationStep",
    "SimulationStepDiagnostics",
    "TemporalLayerProduct",
    "TemporalSink",
    "TrafficBoundaryCondition",
    "TrafficSimulationParams",
    "build_law",
    "build_mapspec_bundle",
    "parse_params",
    "run_simulation",
]
