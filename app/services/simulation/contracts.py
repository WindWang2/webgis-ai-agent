"""时空动态仿真的线协议契约（ADR-0192 D2 / spec §2）。

本模块只承载 pydantic v2 数据契约：仿真参数、物理边界条件、步记录与
时态图层产物。**不 import numpy / 平台设施** —— Celery json 序列化与
工具面 JSON Schema 都从这里出发。

量纲约定：
- 水文在投影米制下运行（GridSpec.crs 必须显式声明，诚实缺省不伪造）；
- 守恒总量：水文 = 体积（m³，深度×像元面积），交通 = 车辆数（veh）；
- 诊断里的 min/max 是守恒变量的原始量纲（m / veh）。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.services.simulation.errors import SimulationConfigError

#: MapSpec v1.2 图集帧上限（与 mapspec_schema.MAX_SPEC_FRAMES 同口径）。
#: 这里用字面量以保持本模块零平台 import；一致性由 layers.py 的对账测试守卫。
MAX_TEMPORAL_FRAMES = 50

#: 单个时态切片的最大要素行数（超出截断并留痕，与 ref_offload 纪律一致）。
MAX_SLICE_FEATURES = 20_000

#: 守恒对账的 fail-loud 阈值（相对残差）。超过即判定模型/运行时出错。
CONSERVATION_FAIL_REL = 1e-6

#: 守恒报告 ``passed`` 的严格阈值（验收口径，spec §8）。
CONSERVATION_PASS_REL = 1e-9


class SimulationModelKind(str, Enum):
    """可用的微观推演模型（law 注册表的键）。"""

    HYDRO_DIFFUSION = "hydro_diffusion"
    TRAFFIC_PROPAGATION = "traffic_propagation"


class SimulationStatus(str, Enum):
    """仿真任务生命周期（runtime 维护，非法迁移显式拒绝）。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class _ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# ── 栅格几何与场 ──────────────────────────────────────────────────────────


class GridSpec(_ContractModel):
    """规则网格几何（米制投影坐标；origin 为 [0,0] 格元左下角）。"""

    width: int = Field(ge=1, le=4096)
    height: int = Field(ge=1, le=4096)
    cell_size_m: float = Field(gt=0)
    origin_x: float
    origin_y: float
    crs: str = Field(min_length=4, description="投影 CRS（如 EPSG:32650）")

    @property
    def cell_area_m2(self) -> float:
        return self.cell_size_m**2

    @property
    def shape(self) -> tuple[int, int]:
        return (self.height, self.width)


class RasterFieldSpec(_ContractModel):
    """栅格初始/边界场：常数广播或显式二维数组（行优先 [row][col]）。"""

    kind: Literal["constant", "array"] = "constant"
    value: float = 0.0
    values: Optional[list[list[float]]] = None

    @model_validator(mode="after")
    def _check_array(self):
        if self.kind == "array":
            if not self.values:
                raise ValueError("array field requires non-empty values")
            width = len(self.values[0])
            for row in self.values:
                if len(row) != width:
                    raise ValueError("field values must be rectangular")
                if any(v != v or v in (float("inf"), float("-inf")) for v in row):
                    raise ValueError("field values must be finite")
        return self

    def materialize(self, shape: tuple[int, int]) -> Any:
        """广播/还原为 numpy 数组（供 state 层使用；numpy 在此惰性引入）。"""
        import numpy as np

        if self.kind == "constant":
            return np.full(shape, float(self.value), dtype=float)
        arr = np.asarray(self.values, dtype=float)
        if arr.shape != tuple(shape):
            raise ValueError(
                f"field shape {arr.shape} does not match grid {tuple(shape)}"
            )
        return arr


# ── 水文模型参数与物理边界 ────────────────────────────────────────────────


class HydroBoundaryCondition(_ContractModel):
    """水文物理边界：降雨源 + 均匀排水汇（均只作用于湿格元的量纲口径见 spec §4.1）。"""

    rainfall_rate_m_per_s: RasterFieldSpec = Field(
        default_factory=lambda: RasterFieldSpec(kind="constant", value=0.0),
        description="雨强 [m/s]（常数或逐格数组）",
    )
    rainfall_duration_s: Optional[float] = Field(
        default=None, ge=0, description="降雨时长；None = 全程降雨",
    )
    drainage_rate_m_per_s: float = Field(
        default=0.0, ge=0, description="均匀排水速率 [m/s]（仅湿格元生效）",
    )


class HydroSimulationParams(_ContractModel):
    """DEM + Manning 粗糙度的线性化扩散波淹没模型参数。"""

    model_kind: Literal[SimulationModelKind.HYDRO_DIFFUSION] = (
        SimulationModelKind.HYDRO_DIFFUSION
    )
    grid: GridSpec
    elevation_m: RasterFieldSpec = Field(description="DEM 高程 [m]")
    manning_n: RasterFieldSpec = Field(description="Manning 粗糙度系数 (0,1]")
    initial_water_depth_m: RasterFieldSpec = Field(
        default_factory=lambda: RasterFieldSpec(kind="constant", value=0.0),
        description="初始水深 [m]",
    )
    boundary: HydroBoundaryCondition = Field(
        default_factory=HydroBoundaryCondition,
    )
    #: 水力扩散标定系数（吸收量纲常数，见 ADR-0192 D4）。
    diffusivity_calibration: float = Field(default=1.0, gt=0, le=100.0)

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    @field_validator("manning_n")
    @classmethod
    def _manning_in_physical_range(cls, v: RasterFieldSpec) -> RasterFieldSpec:
        vals = [v.value] if v.kind == "constant" else [
            x for row in (v.values or []) for x in row
        ]
        if any(not (0 < x <= 1) for x in vals):
            raise ValueError("manning_n must lie in (0, 1]")
        return v

    @field_validator("initial_water_depth_m")
    @classmethod
    def _depth_nonnegative(cls, v: RasterFieldSpec) -> RasterFieldSpec:
        vals = [v.value] if v.kind == "constant" else [
            x for row in (v.values or []) for x in row
        ]
        if any(x < 0 for x in vals):
            raise ValueError("initial water depth must be >= 0")
        return v

    @model_validator(mode="after")
    def _rain_nonnegative_and_shapes_match(self):
        rain = self.boundary.rainfall_rate_m_per_s
        vals = [rain.value] if rain.kind == "constant" else [
            x for row in (rain.values or []) for x in row
        ]
        if any(x < 0 for x in vals):
            raise ValueError("rainfall rate must be >= 0")
        shape = self.grid.shape
        for name, spec in (
            ("elevation_m", self.elevation_m),
            ("manning_n", self.manning_n),
            ("initial_water_depth_m", self.initial_water_depth_m),
            ("rainfall_rate_m_per_s", rain),
        ):
            if spec.kind == "array":
                if len(spec.values) != shape[0] or len(spec.values[0]) != shape[1]:
                    raise ValueError(
                        f"field '{name}' shape does not match grid {shape}"
                    )
        return self


# ── 路网模型参数与物理边界 ────────────────────────────────────────────────


class NetworkEdgeSpec(_ContractModel):
    """有向路边（拓扑 + 通行能力属性；geometry 可选 LineString 坐标）。"""

    edge_id: str = Field(min_length=1)
    tail: str = Field(min_length=1, description="起点节点 id")
    head: str = Field(min_length=1, description="终点节点 id")
    length_m: float = Field(gt=0)
    capacity_veh_h: float = Field(gt=0, description="通行能力 [veh/h]")
    free_flow_speed_kmh: float = Field(gt=0)
    lanes: int = Field(default=1, ge=1)
    geometry: Optional[list[list[float]]] = Field(
        default=None, description="LineString 坐标 [[x,y],...]；缺省用示意布局",
    )

    @model_validator(mode="after")
    def _geometry_shape(self):
        if self.geometry is not None:
            if len(self.geometry) < 2:
                raise ValueError("geometry needs >= 2 coordinates")
            if any(len(p) != 2 for p in self.geometry):
                raise ValueError("geometry coordinates must be [x, y] pairs")
        return self


class BottleneckSpec(_ContractModel):
    """瓶颈注入：对指定边做容量折减（时间窗口可选）。"""

    edge_ids: list[str] = Field(min_length=1)
    capacity_factor: float = Field(gt=0, lt=1, description="折减系数 (0,1)")
    start_tick: int = Field(default=1, ge=1)
    duration_ticks: Optional[int] = Field(default=None, ge=1)


class TrafficBoundaryCondition(_ContractModel):
    """交通物理边界：边入口需求注入 + 可选瓶颈容量折减。"""

    demand_veh_h: dict[str, float] = Field(
        default_factory=dict, description="edge_id → 入口需求 [veh/h]（≥0）",
    )
    bottleneck: Optional[BottleneckSpec] = None

    @field_validator("demand_veh_h")
    @classmethod
    def _demand_nonnegative(cls, v: dict[str, float]) -> dict[str, float]:
        if any(x < 0 for x in v.values()):
            raise ValueError("demand must be >= 0")
        return v


class TrafficSimulationParams(_ContractModel):
    """水平队列 + 重力转向分配的交通潮汐模型参数（spec §4.2）。"""

    model_kind: Literal[SimulationModelKind.TRAFFIC_PROPAGATION] = (
        SimulationModelKind.TRAFFIC_PROPAGATION
    )
    edges: list[NetworkEdgeSpec] = Field(min_length=1, max_length=20_000)
    boundary: TrafficBoundaryCondition = Field(
        default_factory=TrafficBoundaryCondition,
    )
    tau_ref_seconds: float = Field(
        default=60.0, gt=0, le=3600,
        description="饱和度参考时间（拥堵指数派生量纲）",
    )
    gravity_gamma: float = Field(default=1.0, gt=0)
    jam_density_veh_per_km: float = Field(
        default=150.0, gt=0, description="阻塞密度 [veh/km/lanes]",
    )

    @model_validator(mode="after")
    def _topology_integrity(self):
        ids = [e.edge_id for e in self.edges]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate edge_id in network")
        for e in self.edges:
            if e.tail == e.head:
                raise ValueError(f"edge {e.edge_id} is a self-loop")
        known = set(ids)
        for ref in list(self.boundary.demand_veh_h):
            if ref not in known:
                raise ValueError(f"demand references unknown edge {ref!r}")
        if self.boundary.bottleneck is not None:
            for ref in self.boundary.bottleneck.edge_ids:
                if ref not in known:
                    raise ValueError(f"bottleneck references unknown edge {ref!r}")
        return self


SimulationParams = Union[HydroSimulationParams, TrafficSimulationParams]


# ── 运行配置 ──────────────────────────────────────────────────────────────


class SimulationRunConfig(_ContractModel):
    """时间步长调度配置（Tick Scheduler 的输入）。"""

    dt_seconds: float = Field(gt=0)
    n_steps: int = Field(ge=1, le=100_000)
    output_stride: int = Field(default=1, ge=1, description="每 k 步铸一片时态产物")
    start_time: Optional[datetime] = Field(
        default=None, description="模拟时钟起点；None 时仅用相对秒",
    )
    max_wall_seconds: Optional[float] = Field(default=None, gt=0)
    seed: int = Field(default=0, description="随机种子（当前模型均为确定性，保留）")

    @property
    def expected_slice_count(self) -> int:
        emitted = self.n_steps // self.output_stride + 1
        if self.n_steps % self.output_stride:
            emitted += 1
        return emitted  # T0 + 对齐切片 (+ 末步不对齐时补一片)

    @model_validator(mode="after")
    def _frames_must_fit_mapspec(self):
        if self.expected_slice_count > MAX_TEMPORAL_FRAMES:
            raise ValueError(
                f"temporal slices ({self.expected_slice_count}) exceed "
                f"MapSpec frame cap ({MAX_TEMPORAL_FRAMES}); increase output_stride"
            )
        return self


# ── 步记录与产物契约 ──────────────────────────────────────────────────────


class SimulationStepDiagnostics(_ContractModel):
    """单步诊断（守恒对账是数据，不是日志）。"""

    model_config = ConfigDict(frozen=True)

    tick: int
    sim_seconds: float
    timestamp: Optional[str] = None
    conserved_total: float
    expected_total: float
    mass_error: float
    mass_error_relative: float
    min_value: float
    max_value: float
    finite: bool = True
    stability_dt_max: Optional[float] = None
    wall_ms: float = 0.0
    metrics: dict[str, float] = Field(default_factory=dict)


class TemporalLayerProduct(_ContractModel):
    """一个时间片的动态图层产物（提货券语义，见 ADR-0192 D7）。"""

    model_config = ConfigDict(frozen=True)

    product_id: str
    tick: int
    sim_seconds: float
    timestamp: Optional[str] = None
    format: Literal["memory", "geoparquet"] = "memory"
    ref: Optional[str] = None
    uri: Optional[str] = None
    feature_count: int = 0
    truncated: bool = False
    bbox: Optional[list[float]] = None
    layer_kind: Literal["raster_grid", "network_edges"] = "raster_grid"
    degraded_note: Optional[str] = None


class SimulationStep(_ContractModel):
    """一个时间步的不可变记录（ADR-0192 D2 三抽象之一）。"""

    model_config = ConfigDict(frozen=True)

    tick: int
    sim_seconds: float
    timestamp: Optional[str] = None
    diagnostics: SimulationStepDiagnostics
    layer: Optional[TemporalLayerProduct] = None


class ConservationReport(_ContractModel):
    """全程守恒对账报告（验收口径 residual_relative ≤ 1e-9）。"""

    model_config = ConfigDict(frozen=True)

    variable: str
    initial_total: float
    final_total: float
    net_sources: float
    net_sinks: float
    expected_final: float
    residual: float
    residual_relative: float
    passed: bool


def parse_params(dump: dict) -> SimulationParams:
    """按 model_kind 判别还原参数模型（Celery 载荷入口）。"""
    kind = (dump or {}).get("model_kind")
    try:
        if kind == SimulationModelKind.HYDRO_DIFFUSION.value:
            return HydroSimulationParams.model_validate(dump)
        if kind == SimulationModelKind.TRAFFIC_PROPAGATION.value:
            return TrafficSimulationParams.model_validate(dump)
    except ValidationError as exc:
        # 携带字段级错误摘要（紧凑串，PlatformError.context 会做有界化）：
        # 调用方（LLM/工具面 correction_hint）需要可行动的修正信息。
        brief = "; ".join(
            "{}: {}".format(
                ".".join(str(p) for p in err.get("loc", ())),
                str(err.get("msg", "")),
            )
            for err in exc.errors()[:5]
        )
        raise SimulationConfigError(
            "simulation params failed validation",
            context={"reason": "pydantic_validation", "errors": brief},
        ) from exc
    raise SimulationConfigError(
        f"unknown simulation model_kind: {kind!r}",
        context={"kind": str(kind)},
    )


__all__ = [
    "CONSERVATION_FAIL_REL",
    "CONSERVATION_PASS_REL",
    "MAX_SLICE_FEATURES",
    "MAX_TEMPORAL_FRAMES",
    "BottleneckSpec",
    "ConservationReport",
    "GridSpec",
    "HydroBoundaryCondition",
    "HydroSimulationParams",
    "NetworkEdgeSpec",
    "RasterFieldSpec",
    "SimulationModelKind",
    "SimulationParams",
    "SimulationRunConfig",
    "SimulationStatus",
    "SimulationStep",
    "SimulationStepDiagnostics",
    "TemporalLayerProduct",
    "TrafficBoundaryCondition",
    "TrafficSimulationParams",
    "parse_params",
]
