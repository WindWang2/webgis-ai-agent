# ADR-0215: Worker Placement Envelope 投影与 Inline Scale 准入

- 状态：Proposed（随 H06 PR 提交）
- 日期：2026-09-29
- 关联：ADR-0182（governor v1）、ADR-0213（统一成本模型）、ADR-0214（workflow
  resource scheduler）、ADR-0029（SpatialAnalyzer seam）、ADR-0117（Backend SDK V3 /
  AlgorithmDescriptor）

## 背景

F08（#1499）落地了 workflow 估算 → Celery durable worker 的治理元数据穿透：
driver 经 `dispatch_node(resource_envelope=_estimate.as_dict())` 传递 rg.v1
估算，worker 侧 `_placement_guard` 以 `cap.satisfies(min_cpu, min_mem_mb, gpu)`
做放置第 2 层校验。但两处形状/接线缺口使「resource envelope 从 compiler 到
worker 的真实消费」在生产上不成立（H06 侦察证据）：

1. **形状失配（静默 no-op）**：`ResourceEstimate.as_dict()` 产出 `dims` 嵌套
   结构（`dims.memory_bytes.{certainty,expected,…}`），而守卫读扁平键
   `envelope.get("min_cpu"/"min_mem_mb"/"gpu")` → 恒为 0 → `satisfies(0,0,0)`
   恒真。生产路径上**任何** rg.v1 envelope 都从未真正拒绝过一个放置。
2. **plan-node 估算止步 schema**：geocompute `ExecutionNode.estimate`
   （rows/bytes/memory_mb/cpu_seconds，agent_swarm 与 `build_plan_from_json`
   写入）不产生 durable envelope —— REST/agent_swarm 计划路径派发时
   `resource_envelope=None`，worker 守卫直接跳过。

同批侦察确认 geo_analysis 侧（#1548）脚手架重复使「inline 500ms 红线」
（CODE_REVIEW Invariant 1）没有统一的执行点：22 处 `to_utm_gdf` 前导各自为政，
超大规模输入在没有任何 typed 门槛的情况下直接进入主进程投影/内核。

## 决策

### D1 — envelope 形状投影归一（worker 侧，fail-open）

新模块 `app/services/geocompute/envelope.py` 是唯一投影面：

- **rg.v1 形状**（`dims`/`resource_class` 键存在）→ 守卫键：
  `min_mem_mb = ceil(dims.memory_bytes.expected / MiB)`（expected 缺失按 max
  兜底），`gpu = gpu_required ? 1 : 0`，`min_cpu = 0`（rg.v1 无 CPU 数值维；
  `cpu_class` 是 advisory）。
- **unknown ≠ 约束**（placement 语义）：certainty 为 unknown/unavailable 的维
  不设门槛 —— placement 守卫保持既有 fail-open 姿态（探针缺席放行同理）；
  「unknown≠0 的保守**记账**」仍是 governor 进程内准入的权威（ADR-0182 语义
  分层：记账从严、放置从宽）。
- **cluster 层 `min_*` 扁平形状逐字节透传**（placement 第 1 层兼容）。
- **投影异常 → `{}`（不设约束）**；`WEBGIS_PLACEMENT_ENVELOPE_PROJECTION=0`
  一键关闭投影（默认开）。envelope 只走 task_kwargs，不进幂等键（ADR-0214 D5
  不变）。

### D2 — plan-node 估算到达 worker（executor 侧）

`_execute_durable` / `_execute_durable_partitioned`（含 tile fan-out）在调用方
未传 envelope 时把 `node.estimate` 投影为 dispatch envelope
（`memory_mb → min_mem_mb`，`effective_dispatch_envelope`）。显式 envelope 原样
透传，行为不变。

### D3 — compiler 侧写真实数值（descriptor → plan hint）

`app/services/geocompute/estimate_hints.py`：agent_swarm 操作 → 已注册
descriptor 的**标识映射**（泛化操作不猜身份），descriptor 声明式
`resource_envelope.bytes_per_feature × rows 真值 → plan-node
memory_mb/bytes`；`cpu_seconds` 取 governor `class_prior`（ADR-0213 D1 单一
先验表 —— 不新增任何成本先验表）。未注册/未声明操作逐字段回退旧行为。

### D4 — inline scale 准入（geo_analysis 侧）

`geo_analysis.context.validate_spatial_input`：

- FeatureCollection 原始要素数 > `INLINE_MAX_FEATURES`（200k）→ **解析/投影
  之前**抛 typed `ResourceScaleMismatch`（拒绝是 O(1) 计数，不消耗主进程
  投影/内存预算）；
- 投影后估算帧字节 > 2GiB → 同样 typed 拒绝；
- guidance 固定引导 geocompute durable 计划（worker 隔离 + 排队/降级）。
  阈值是稳定工程常数，不是机器速度断言；typed 错误复用 ScientificError 词表
  （`RESOURCE_SCALE_MISMATCH`），经 ToolRegistry 既有 ValueError 映射到达
  LLM（correction_hint/guidance 通道，ADR-0009 不受影响 —— 未加 `kind`）。

### D5 — 错误分类学统一（消费既有词表，不建新词表）

statistics narrated 失败面升级为 typed（`error_type` = ScientificError
词表，message 逐字保留）；kriging 私有错误类归入 taxonomy
（`KrigingInputError → ScientificError`，`KrigingCrsError → InvalidCRS`，
名称/消息/ValueError 兼容性逐位保留）；`ScientificError` 增加有界 additive
`guidance`（≤4×160）与 `ref`（≤8 键）。

## 后果与不变量

- **修正的是消费，不是语义**：rg.v1 估算数值、governor 准入、placement 分层、
  幂等键、deadline 推导全部沿用 #1499 既有设计；本 ADR 只让数值真正到达
  守卫。
- **保留的放行姿态**：探针缺席、unknown 维、投影异常、开关关闭 —— 四类
  fail-open 路径全部保留（宁可漏放给第 1 层 gating，也不让守卫层自误伤）。
- **memory 真实拒绝的新行为**：估算 expected 内存超过 worker 探测总内存的
  durable 节点将 retry（有界）→ `PLACEMENT_MISMATCH` typed 终态。这是修复
  「静默 no-op」的直接后果；观察期可通过 kill-switch 回退。
- **inline 红线只收窄超大规模**：≤200k 要素（实测 100k 全路径 <15s、峰值内存
  <1.5GiB）的 inline 行为零变化；>200k 是新拒绝面。
- descriptor 数值是**声明面**（粗粒度，`calibrate_governor.py` 证据可回填）；
  实现层硬闸（`ResourceScaleMismatch` 实现级、raster guard）仍是执行权威
  （ADR-0117 语义不变）。

## 不做的事（Out of Scope）

- 不引入 K8s 调度器/外部资源管理器（沿 ADR-0214 非目标）。
- 不给 narrated 工具增加自动 Celery 委托（typed 拒绝 + durable 引导是 v1；
  自动委托需要 dedup/幂等与 SSE 生命周期设计，另行立项）。
- 不合并 rg.v1 与 plan-node 两个 `ResourceEstimate` 模型（桥接而非合并，
  沿 ADR-0213 D1）。
- statistics/terrain/kriging 的完整包拆分（#1559 A5）—— 本批交付
  kernel/adapter seam 与共享契约，机械拆包留后续。
