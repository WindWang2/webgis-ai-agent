# ADR-0192: 时空因果推演与动态微观仿真运行时（Spatial Causal Simulation Runtime）

**Status:** Accepted（分支 `agent/08-spatial-causal-simulation-runtime`）
**Date:** 2026-09-15
**关联:** ADR-0052（durable job）、ADR-0096（geocompute 数据面边界）、ADR-0047（MVT 数据面）、
ADR-0118（GeoParquet row-group 剪枝）、ADR-0155（MapSpec additive 演进）、规划任务 B011（ST-GCN，前瞻）

## 背景

平台现有分析算子（叠加、缓冲区、核密度、LISA、网络分析）全部面向**静态历史数据切片**：
输入一个时刻的快照，输出一个时刻的图面。城市防灾应急（暴雨内涝）、交通调度（潮汐拥堵）、
城市规划（方案比选）等场景的核心诉求不是"看现状"，而是"向未来推导演变"（Predictive &
Prescriptive GIS）。看板任务 B011（预测性时序网络 ST-GCN）长期停留在规划中，根因是系统
缺乏一个统一的时空演变建模框架——每个预测需求都要从零造轮子，且没有统一的产物形态交付前端。

本 ADR 决定构建独立的 **时空动态仿真运行时（Spatial Simulation Runtime）**，抽象出
统一时空状态机：

```
初始状态 → 空间网络阻抗矩阵 → 时间步长迭代推演（Tick Scheduler） → 时态动态图层（Temporal Layers）
```

## 决策

### D1. 独立子域包 `app/services/simulation/`，纯数值核与平台设施分层

按 geocompute 先例采用包式组织：`contracts.py`（pydantic 线协议）、`state.py`
（SpatialStateMatrix 实现）、`laws.py`（DynamicPropagationLaw 基类与注册表）、
`runtime.py`（SimulationRuntime）、`layers.py`（时态产物）、`errors.py`、`api.py`
（稳定入口）、`tasks.py`（Celery 任务体）、`models/`（具体物理模型）。

**分层红线**：数值核（contracts/state/laws/runtime/models/layers）不 import
DB / Redis / Celery / `app.tools`——可在解释器内裸跑、可离线单测；平台设施只在
`tasks.py` / `api.py` / 工具面接线。数据面不得依赖 `app.tools`（ADR-0096 D1 同款边界）。

### D2. 时空数据抽象契约三件套

- **`SpatialStateMatrix`**（`state.py`）：仿真状态的内存载体。两个实现：
  - `RasterStateMatrix`：规则网格（GridSpec：宽/高/像元尺寸/原点/CRS）+ 命名变量
    二维 numpy 数组（如 `water_depth_m`）；
  - `GraphStateMatrix`：有向路网图（GraphTopology：node/edge 索引、出边/入边邻接、
    egress 判定）+ 命名变量一维数组（如 `queue_veh`）。
  统一职责：守恒总量读取（`total(var)`）、有限性/负值校验（`validate()`）、深拷贝隔离、
  载荷展平（`to_features()` 供产物层消费）。
- **`SimulationStep`**（`contracts.py`）：一个时间步的**不可变记录**——tick 序号、
  模拟时刻（`sim_seconds`，可选 ISO 时间戳）、`SimulationStepDiagnostics`（守恒总量
  实测/期望/残差、极值、有限性、耗时、派生指标）、该步产物引用。诊断是数据，不是日志。
- **`DynamicPropagationLaw`**（`laws.py`）：物理规律接口，模型即插件：
  - `build_initial_state()`：由参数铸初始状态；
  - `advance(state, dt, tick) -> (new_state, StepAccounting)`：推进一步，**必须**
    返回源/汇记账（`sources`/`sinks`：守恒变量 → 本步净增量/净移除量）；
  - `validate_stability(state, dt)`：CFL 型稳定性/模型有效性守卫，违例抛
    `SimulationStabilityError`；
  - `conserved_variable`：守恒变量名；`derived_metrics(state)`：派生指标
    （最大水深、平均拥堵指数等）。
  注册表 `build_law(kind, params)` 按 `SimulationModelKind` 分发，新模型（如 B011 的
  ST-GCN）只需新增一个 law 实现并注册，运行时零改动。

### D3. 通量形式守恒差分 + 每步守恒对账（数值完整性是契约不是特性）

- 空间交换一律写成**成对通量**（pairwise flux：一个格元失去的量 = 邻元获得的量），
  内部交换在浮点精度内严格守恒；源（降雨、需求注入）与汇（排水、驶出）必须显式记账。
- runtime 每步对账：`expected_total += Σsources − Σsinks`，实测
  `|total − expected| / max(1, |expected|) > 1e-6` → `SimulationStateError` **fail-loud**
  （状态机转入 failed）。守恒破坏是模型 bug，绝不允许静默发散。
- `validate_stability` 在每步 advance 之前调用（电导随状态变化的模型每步重算）。

### D4. 水文模型：线性化扩散波 + Manning 粗糙度（`models/hydro_diffusion.py`）

基于 DEM 高程梯度与地表粗糙度的暴雨内涝淹没扩散模型：

- 水面高程 `w = Z + h`；相邻格元对 `(i,j)` 的传导率
  `k_ij = K_h_ij / dx²`，`K_h_ij = calib · h_act^{5/3} / n̄`（`n̄` 取 Manning 系数
  均值，`h_act = max(h_i, h_j, h_min)` 允许湿地向干格扩散，标定常数吸收量纲因子）。
- 显式 Euler：`h_i ← h_i + dt·[Σ_j k_ij·(w_j − w_i) + rain_i − drained_i]`。
- **负水深硬保证**：发送方比例限制器——单格全部流出通量按
  `min(1, 0.45·h_i/Σout)` 等比缩放；缩放只作用在"发出多少"，成对守恒**不被破坏**。
  排水受"流出后余量"封顶（`drained ≤ h − Σout·s`），因此
  `h_new = h − Σout·s − drained + rain ≥ rain ≥ 0` 在**排水与出流并发**
  （退水期薄水膜）时依然严格成立。限制器是数值保险丝，不是常态路径。
- 排水仅在湿格元扣减（余量封顶隐含此语义），避免干格被"抽"出负水。
- 稳定域：`dt · max_i Σ_j k_ij ≤ 0.9`（图拉普拉斯显式格式的最大值原理条件），
  违例抛 `SimulationStabilityError`（附 dt_max 证据）。

### D5. 交通模型：水平队列 + 重力模型转向分配（`models/traffic_propagation.py`）

基于路网拓扑图的交通潮汐动态扩散模型：

- **状态变量 = 边上排队车辆数 `q_e`（守恒量）**；拥堵指数
  `c_e = q_e / (C_e · τ_ref)`（饱和度，τ_ref 默认 60s）是派生量，受存储上界
  （`L_e · lanes · jam_density`）自然约束，无需人为截断。
- 每步：① 边界需求注入 `q_e += D_e·dt`（记账 source）；② 服务率
  `o_e = min(q_e, C_e·dt)`（瓶颈 = `BottleneckSpec` 容量折减）；③ 节点转向分配：
  重力权重 `w(er→es) ∝ C_s / (1 + t_s)^γ`（t_s 为下游边行程时间，逐发送者归一化）；
  ④ **溢流节流（spillback）**：下游边接收量超过存储余量时按比例退回——未被放行的
  车辆留在上游边，拥堵由此**物理地**向上游蔓延，这正是"注入瓶颈后拥堵指数按步长
  向周边边蔓延"的机理；⑤ 头节点无出边的边为 egress，服务量计入 `sinks`（驶出）。
- 模型无条件数值稳定（全部 min/比例封顶），`validate_stability` 强制的是
  **模型有效性包络**：dt 不得超过最小行程时间/最小存容服务时间的 1/4（时间分辨率
  不足以分辨队列动力学即拒绝执行，诚实报错而非输出噪声）。
- 选型理由：相比对拥堵指数直接做图卷积扩散（有界但守恒性被 clamp 破坏、无物理
  溢流语义），水平队列以车辆数为守恒量，守恒严格、蔓延机理内生，且拥堵指数可从
  状态无损派生。ST-GCN（B011）可作为第三个 law 在同一运行时上落地，训练侧不在本 ADR 范围。

### D6. 算力隔离：多步推演必须经 Celery 异步流转（durable job 模式）

- `tasks.py` 定义 `run_simulation_forecast`（`bind=True`），生产派发只经
  `submit_durable_job`；worker 侧 `durable_job()` 上下文内按 output_stride 上报
  `job.progress`，终态 `finish_job(result=有界摘要, result_ref=载荷引用)`。
  `job_id=None` 仅作为 eager/测试直调路径（geocompute/tasks.py 先例，显式告警）。
- 任务模块登记进 `task_queue.py` 的 include 列表；无 Redis 时 eager 语义由
  平台统一保证。仿真本体是同步纯计算，不感知 broker——**运行时不 import Celery**。

### D7. 时态产物：按时间片切分的提货券 + MapSpec v1.2 frames 兼容

- 每 `output_stride` 步（含 T0 与末步）由 `layers.py` 铸一条
  **`TemporalLayerProduct`**：GeoJSON 展平载荷（栅格取值>阈值的活跃格元矩形面、
  路网逐边折线）→ `features_to_arrow` + `table_to_geoparquet` 落盘，登记
  `ref:fabric-parquet/<id>` 族提货券；**pyarrow 缺席时诚实降级**为内存记录
  （`format="memory"` + degraded 注记，绝不虚构 parquet 路径）。
- 每切片行数封顶（默认 20000），超出截断并置 `truncated=True`（与 ref_offload 纪律一致）。
- **MapSpec 兼容**：产出 `MapSpecTemporalBundle`——v1.2 文档骨架，逐切片一个
  `geojson` source + layer，时间轴用 `layout.frames` 表达（每帧 `title` 为模拟时刻、
  `layerOverrides` 只点亮当期图层）。`SimulationRunConfig` 校验
  `ceil(n_steps/stride)+1 ≤ MAX_SPEC_FRAMES(50)`，超限在**构造期**报配置错误，
  不允许跑到一半才发现前端放不下。
- MVT 交付留给后续 ADR（mvt.py 服务已就位，sink 接口已为其预留）。

### D8. 错误处理：域错误层次 + 工具面不抛异常

`errors.py`：`SimulationError(PlatformError)` 域基类 + 稳定 `code`；派生
`SimulationConfigError`（参数/配置，validation）、`SimulationStabilityError`
（数值稳定域违例，含 dt_max 证据）、`SimulationStateError`（守恒/有限性破坏，
fail-loud）、`SimulationCancelledError`（取消语义）。工具面捕获域异常转
`std_error_response`（code=VALIDATION_ERROR 等），绝不让 LLM 看到栈。

### D9. 工具面：`run_spatial_simulation` 注册进统一能力面

`app/tools/simulation_tools.py`，`args_model` 显式契约；`tier=3, cost=heavy,
domains=["simulation"]`；默认走 durable job 异步路径，`run_async=False` 时进程内
同步执行（小规模推演/测试）。与既有 `what_if_simulate`（规则式情景模拟）边界：
本工具是**物理机制驱动的数值推演**，产出多时相图层，不做指标规则的 RAG 佐证。

## 影响

- **正向**：B011（ST-GCN）及后续预测/推演需求有了统一承载；防灾/交通场景获得
  原生的"向未来推演"能力；时态图层让前端时间轴播放有了数据面契约。
- **代价/风险**：显式格式受 CFL 约束，长时程需要多步迭代（由 n_steps 上限与
  max_wall_seconds 预算约束）；新依赖为零（numpy/pydantic/celery 全部既有；
  pyarrow 仍保持可选）。
- **非目标**：不等价填洼（hydrology 填洼已有 terrain.py 先例）、二维浅水方程全动力
  波、交通分配的均衡求解（UE/SO）、GPU 加速。
