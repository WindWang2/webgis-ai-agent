# 时空动态仿真运行时技术规范（Spatial Simulation Runtime Spec）

**ADR:** 0192　**Status:** Accepted　**Date:** 2026-09-15
**Owner:** agent/08-spatial-causal-simulation-runtime

本文是 `app/services/simulation/` 的工程规范：模块边界、契约字段、数值格式、
接线点与验收标准。数值方案的推导与理由见 ADR-0192 D3–D5。

## 1. 模块布局与分层红线

```
app/services/simulation/
├── __init__.py        # 公共 API re-export + 分层红线声明
├── contracts.py       # pydantic v2 线协议：参数 / 物理边界 / 步记录 / 时态产物契约
├── state.py           # SpatialStateMatrix 抽象 + Raster/Graph 两实现 + GraphTopology
├── laws.py            # DynamicPropagationLaw ABC + StepAccounting + build_law 注册表
├── runtime.py         # SimulationRuntime：生命周期、Tick Scheduler、守恒对账、检查点
├── layers.py          # TemporalSink / InMemoryTemporalSink / GeoParquetTemporalSink
│                      # + build_mapspec_bundle（MapSpec v1.2 frames）
├── errors.py          # SimulationError(PlatformError) 域错误层次
├── api.py             # run_simulation / enqueue_simulation / 有界工具结果组装
├── tasks.py           # Celery 任务体（durable job；job_id=None 仅 eager/测试）
└── models/
    ├── __init__.py
    ├── hydro_diffusion.py      # DEM + Manning 线性化扩散波淹没模型
    └── traffic_propagation.py  # 水平队列 + 重力转向分配拥堵蔓延模型
```

**红线**：`contracts/state/laws/runtime/models/layers` 不得 import DB/Redis/Celery/
`app.tools`/`app.core.config`。平台接线只在 `tasks.py`/`api.py`/`app/tools/simulation_tools.py`。

## 2. 契约字段（contracts.py，pydantic v2）

### 2.1 参数

| 模型 | 字段 | 约束 |
|---|---|---|
| `GridSpec` | width/height | int 1..4096 |
| | cell_size_m | float > 0 |
| | origin_x/origin_y, crs | 必填（仿真在投影坐标/米制下运行，CRS 必须显式声明） |
| `RasterFieldSpec` | kind=constant | value |
| | kind=array | values 与 GridSpec 形状一致、全部有限（validator 强制） |
| `HydroSimulationParams` | grid, elevation_m, manning_n | manning_n ∈ (0, 1] |
| | initial_water_depth_m | ≥ 0 |
| | boundary: HydroBoundaryCondition | 见 2.2 |
| | diffusivity_calibration | float (0, 100]，默认 1.0 |
| `NetworkEdgeSpec` | edge_id/tail/head | 唯一性由 validator 强制；head≠tail |
| | length_m > 0, capacity_veh_h > 0, free_flow_speed_kmh > 0 | |
| | lanes ≥ 1, geometry(可选 LineString coords) | |
| `TrafficSimulationParams` | edges ≤ 20000 | 边 id 重复 → ValidationError |
| | boundary: TrafficBoundaryCondition | 见 2.2 |
| | tau_ref_seconds ∈ (0,3600]；gravity_gamma > 0；jam_density_veh_per_km 默认 150 | |
| `SimulationRunConfig` | dt_seconds > 0；n_steps 1..100000；output_stride ≥ 1 | |
| | start_time(可选 ISO)；max_wall_seconds 可选；seed 保留 | |
| | **帧数守卫**：产物数 = n_steps//stride + 1 (+1 若末步不对齐) ≤ 50 | 超限构造期 ValidationError |

`SimulationParams`：`model_kind` 判别联合（`hydro_diffusion` / `traffic_propagation`）。

### 2.2 物理边界（Boundary Conditions）

- `HydroBoundaryCondition`：`rainfall_rate_m_per_s: RasterFieldSpec`（≥0）、
  `rainfall_duration_s: float|None`（None=全程降雨）、`drainage_rate_m_per_s ≥ 0`
  （均匀排水，仅湿格元生效）。
- `BottleneckSpec`：`edge_ids`（必须存在于路网）、`capacity_factor ∈ (0,1)`、
  `start_tick ≥ 1`（默认 1）、`duration_ticks ≥ 1 | None`。
- `TrafficBoundaryCondition`：`demand_veh_h: dict[edge_id, float ≥ 0]`（边入口注入，
  id 必须存在）、`bottleneck: BottleneckSpec|None`。

### 2.3 步记录与时态产物

- `SimulationStepDiagnostics`：`tick`、`sim_seconds`、`timestamp(ISO|None)`、
  `conserved_total/expected_total/mass_error/mass_error_relative`、
  `min_value/max_value/finite`、`stability_dt_max`、`wall_ms`、`metrics: dict[str,float]`。
- `SimulationStep`：tick + 诊断 + `layer: TemporalLayerProduct|None`（不可变记录）。
- `TemporalLayerProduct`：`product_id`（`sim-<run>-t<tick>`）、`tick/sim_seconds/
  timestamp`、`format: "geoparquet"|"memory"`、`ref|None`（提货券）、`uri|None`、
  `feature_count`、`truncated`、`bbox|None`、`layer_kind: "raster_grid"|"network_edges"`、
  `degraded_note|None`。
- `ConservationReport`：`variable/initial_total/final_total/net_sources/net_sinks/
  expected_final/residual/residual_relative/pass`。
- `SimulationResult`：`run_id/model_kind/status/config 摘要/steps/products/
  conservation_report/final_metrics/mapspec_bundle|None`。

## 3. 状态矩阵（state.py）

- `SpatialStateMatrix`（ABC）：`variable_names`、`total(var)`、`minmax(var)`、
  `validate()`（有限性 + 非负域变量检查 → `SimulationStateError`）、`copy()`（深拷贝）。
- `RasterStateMatrix`：`grid: GridSpec` + `variables: dict[str, np.ndarray (H,W)]`。
  `to_features(var, threshold, max_features)` → 活跃格元矩形面 GeoJSON features
  （属性：cell 值 + 中心坐标），行数超限截断。
- `GraphStateMatrix`：`topology: GraphTopology` + `variables: dict[str, np.ndarray (E,)]`。
  `GraphTopology.from_edges(specs)` 预计算：`out_edges_of_node/in_edges_of_node`
  （CSR 风格索引）、`egress_edges`（头节点出度=0）、`storage_veh`（存容）。
  `to_features()` → 逐边折线 features（geometry 取 spec.geometry，缺省退化为
  起终点直线）。

## 4. 物理模型数值格式

### 4.1 水文（hydro_diffusion）

```
w_i            = Z_i + h_i                          # 水面高程
K_h_ij         = calib · h_act^{5/3} / n̄_ij          # h_act = max(h_i,h_j,1e-3)
k_ij           = K_h_ij / dx²                        # [1/s]
Δ_ij           = dt · k_ij · (w_i − w_j)             # 成对通量（i→j 为正）
s_i            = min(1, 0.45·h_i / Σ_j max(Δ_ij,0))  # 发送方限制器
drained_i      = min(max(h_i − Σ_j out_i·s_i, 0), dt·drain)   # 排水受流出后余量封顶
h_i ← h_i + Σ_j in(Δ) − Σ_j out(Δ·s_i) + dt·rain_i(t) − drained_i
```
- 守恒恒等式（每步严格）：`Σh_new = Σh_old + dt·Σrain − Σdrained`（成对通量/限制器
  均不创造或销毁水量；rain/drain 记入 StepAccounting）。
- 稳定域：`dt ≤ 0.9 / max_i Σ_j k_ij`；违例 `SimulationStabilityError`（context 携带 dt_max）。
- 雨强窗口：`rain_i(t) = rate_i · [t·dt < rainfall_duration_s]`。

### 4.2 交通（traffic_propagation）

```
注入     q_e += D_e·dt_h                               # source 记账 [veh]
服务     o_e = min(q_e, C_e(t)·dt_h)                   # C_e(t)=容量×瓶颈折减
转向     w(er→es) ∝ C_s/(1+t_s)^γ，逐发送者归一化        # 重力模型
节流     I_s 超出 (storage_s − q_s) 时按比例退回          # spillback：拥堵上溯
驶出     egress 边服务量计入 sinks                      # [veh]
派生     c_e = q_e/(C_e·τ_ref)；v_e = v_f/(1+0.8·c_e)   # 拥堵指数/速度（BRP 线性化）
```
- 守恒恒等式（每步严格）：`Σq_new + exited_cum = Σq_old + injected_cum`。
- 存容硬上界：`storage_e = L_e·lanes·jam_density`；节流保证 `q_e ≤ storage_e` 严格成立。
- 稳定性守卫（模型有效性包络）：`dt ≤ 0.25·min_e(L_e/v_f_e)` 且
  `dt ≤ 0.25·min_e(storage_e/C_e)`。

## 5. 运行时（runtime.py）

- 生命周期：`pending → running → completed | failed | cancelled`（非法迁移抛
  `SimulationStateError`）。
- Tick Scheduler：`validate_stability → advance → 守恒对账（残差 > 1e-6 相对 →
  fail-loud）→ diagnostics → 产物发射（tick%stride==0 或末步）→ 进度回调/取消检查`。
- 检查点：`to_checkpoint() / from_checkpoint()`（numpy tolist + 契约 dump），
  断点续跑与连续跑在相同步数下**逐元素一致**（确定性要求）。
- 预算：`max_wall_seconds` 超时 → `SimulationError`（failed），诚实终止不输出半截结果。

## 6. 接线点

| 接线 | 位置 | 内容 |
|---|---|---|
| Celery include | `app/services/task_queue.py` | `"app.services.simulation.tasks"` |
| 工具注册 | `app/tools/__init__.py::_TOOL_MODULES` | `("app.tools.simulation_tools", "register_simulation_tools")` |
| 工具定义 | `app/tools/simulation_tools.py` | `run_spatial_simulation`（tier=3, cost=heavy, domains=["simulation"]，args_model 显式） |

`api.py`：`run_simulation()`（同步进程内入口）、`enqueue_simulation()`（submit_durable_job
封装）、`build_tool_result()`（有界 LLM 摘要：状态/步数/守恒报告/产物 ref/frames 数）。

## 7. 测试计划（tests/unit/test_simulation_runtime.py）

| 组 | 用例 | 断言核心 |
|---|---|---|
| Contracts | 参数/边界校验、帧数守卫、字段形状 | ValidationError |
| State | 守恒总量读取、深拷贝隔离、非有限拒绝、拓扑邻接 | |
| Hydro | 静水面不流动；坡面汇水；洼地积聚；粗糙度延缓；守恒（rain+drain 恒等式 ≤1e-9）；无负值/无发散；CFL 拒绝 | 数值稳定性契约 |
| Traffic | 瓶颈注入后拥堵向上游逐边蔓延且有序；车辆质量守恒（≤1e-9）；存容上界；自由流稳态；重力权重归一化；有效性包络拒绝 | 蔓延机理 + 守恒 |
| Runtime | 生命周期；T0..TN 多时相产物；检查点续跑等价；守恒破坏 fail-loud；取消；进度回调 | 状态机 + 时序产物 |
| Products | 内存产物载荷；MapSpec v1.2 `MapSpecDocument` 真实校验；帧数=产物数；GeoParquet 往返（pyarrow 在场时） | MapSpec 兼容 |
| Celery | eager 直调（job_id=None）；include 列表接线守卫 | 异步流转 |
| Tool | 注册进 registry；非法参数走 std_error_response；inline 小规模推演成功 | 能力面 |

## 8. 验收标准

1. `pytest tests/unit/test_simulation_runtime.py -v` 全绿（离线、无 DB/Redis）。
2. `ruff check app/services/simulation/ app/tools/simulation_tools.py tests/unit/test_simulation_runtime.py` 零告警。
3. 守恒类测试残差阈值 ≤ 1e-9（相对）——数值完整性可复核。
4. MapSpec 兼容以 `MapSpecDocument.model_validate(bundle)` 真实 schema 校验为准。
5. 不新增环境变量键（test_env_hygiene 守卫保持绿）。
