# ADR-0188: Specialist Agents Pack — Data Scout & GeoCompute（agent-swarm/04）

- 状态：Proposed（随 `agent/04-specialist-agents-pack-data-compute` 分支评审）
- 日期：2026-09-14
- 关联：ADR-0101 Wave 7（子代理角色档 §30/隔离 §31/层级预算 §32）、
  ADR-0104 决策 7（专家子代理团队 + 并行委派）、ADR-0170（ads-v1 契约
  D1–D4）、ADR-0174（DS4 声明式回退链）、ADR-0096（GeoCompute 执行图）、
  ADR-0052（durable job / Celery First）、ADR-0094（诚实默认）
- 目标书：`goals-agent/04-specialist-agents-pack-data-compute.md`
- 规格：`docs/dev/data-compute-agents-spec.md`

## 背景

通用 Agent 面对数据检索与 GIS 计算时，单一系统提示词既要携带数十个 API
规范，又要记住坐标系纠偏、格式转换与重试规则，提示词臃肿且行为不稳定。
两类高频失效模式：

- **Data Sourcing**：数据发现与清洗横跨 ADS v1（`app/services/data_fabric/`）、
  OSM、高德、政务开放平台等多源适配器；检索失败时通用 Agent 直接停滞，
  不会遍历声明式回退链（`fallback.execute_fallback_chain`），也不会清洗
  异构字段或对齐 CRS；
- **Heavy GeoCompute**：PostGIS / GeoPandas / rasterio 重算必须剥离到
  Celery Worker（`geocompute.tasks.run_geocompute_node` durable 路径）；
  通用 Agent 误在事件循环里内联解析超大 GeoJSON，上下文与内存双爆。

既有资产（Phase 0 勘察，均不缺核心原语，缺的是**专精编排层**）：

1. 子代理运行时：`SubagentDispatcher` + `SubagentRole` 角色档
   （allowed_domains / 预算 / allow_mutation / expected_outputs）+
   `AllowlistDispatchRegistry` dispatch 边界白名单（TOOL_NOT_ALLOWLISTED）；
2. 数据面：D1 `D1DatasetDescriptor`、D3 `FallbackDecision`、D4
   `AcquisitionFact`、`execute_fallback_chain`（熔断 + 重试 + 健康镜像）、
   `source_registry_service`、`LocalFileAdapter` / `StatsApiAdapter`；
3. 计算面：`ExecutionNode`/`ExecutionPlan`/`build_plan_from_json`/
   `validate_plan`、`run_geocompute_node`（durable、input_refs 交接、
   `_bounded_summary` 有界摘要、ref 提货券）、`submit_durable_job`。

## 决策

### D1 — 专家 = 角色档 + 确定性领域编排器，不是第二 agent 框架

遵循 `subagent_roles.py` 既有不变式（Pi/主引擎是宿主，子代理不是第二
agent 框架）：`app/services/agent_swarm/` 不引入新的 LLM 执行循环。
`BaseSpecialistAgent` 是**编排器**：持有专属 `SubagentRole`、专属工具
白名单、专属系统提示词（GIS 实体对齐 / 算子编排纪律），提供**确定性
领域方法**（`DataScoutAgent.scout()` / `GeoComputeAgent.plan_computation()`）
供工具层与派遣器调用；生产 LLM 委派路径复用
`SubagentDispatcher.run(role=...)`，角色经 `agent_swarm.registry` 注册进
`SUBAGENT_ROLES`（spawn 工具描述动态渲染自动纳入，无需改工具层）。

### D2 — RBAC 工具白名单（fail-closed，类目级 + 名单级）

每个专家声明 `TOOL_ALLOWLIST`（真实工具名）与 `FORBIDDEN_TOOL_PATTERNS`
（类目级禁区）：

- **data_scout** 只能访问 ADS/Fetch 面（`connect_data_source`、
  `inspect_data_source`、`list_datasets`、`search_datasets`、
  `search_spatial_catalog`、`describe_dataset`、`profile_dataset`、
  `query_dataset`、`query_federated_*`、`plan_data_query`、
  `materialize_dataset`、OSM/POI/年册检索、`fetch_dem`/`fetch_sentinel`）；
- **geocompute** 只能访问计算分析面（`validate_execution_plan`、
  `execute_execution_plan`、`get_execution_run`、`buffer_analysis`、
  `overlay_analysis`、`spatial_join`、`h3_binning`、`zonal_stats`、
  `network_*`、`reproject_coordinates` 等）；
- 越权（data_scout 调计算算子 / geocompute 调数据源接入或制图工具）
  在 `BaseSpecialistAgent.authorize_tool()` 抛 typed
  `SpecialistToolDeniedError`，dispatch 包装面返回结构化
  `TOOL_NOT_ALLOWLISTED`（复用既有语义，不执行）。名单校验
  fail-closed：白名单里的名字必须存在于运行时 registry（构造期校验，
  未知名字构造即失败），防止白名单腐烂成静默放行。

### D3 — 统一输出契约：D1DatasetDescriptor 与 SpatialProfileRef

- Data Scout 统一产出 **D1 `D1DatasetDescriptor`**（复用
  `data_fabric.contracts`，不造新轮子；CRS/bbox/行数只填已知，
  None=未知，诚实默认）；
- GeoCompute 统一产出 **`SpatialProfileRef`**（新冻结契约，
  `agent_swarm.contracts`）：ref_id 提货券 + 有界摘要（rows/crs/
  plan_digest/体积估算/降级注记）。两者都携带溯源
  （source_id / decisions / fact），降级与换源必须显式披露。

### D4 — Celery First 与 Zero Big Data in Context（两条工程纪律红线）

- **Celery First**：GeoCompute 编排器只做计划构造、`validate_plan`
  校验与提交（`run_geocompute_node` durable 路径 / `submit_durable_job`）；
  任何重算子（缓冲/叠置/可达性/H3/栅格统计）绝不内联在 Agent 事件循环。
  体积估算超过阈值时必须出 durable 节点，估算函数独立可测。
- **Zero Big Data in Context**：`SpatialProfileRef` 序列化字节硬上限
  **8 KB**（`SERIALIZATION_BUDGET_BYTES = 8 * 1024`）。超限处理顺序：
  metadata 有界截断（逐键丢弃超预算键并置 `truncated=true` 诚实标注）
  → 仍超限 → typed `SpatialProfileTooLargeError` 诚实失败。绝不静默
  裁剪载荷、绝不把原始几何/要素数组放进上下文。

### D5 — 数据猎手：声明式回退链 + 轻量元数据探测

`DataScoutAgent.scout()` 全程走 ADS 既有原语：`source_registry_service`
解析源声明 → `resolve_chain` 取声明式回退链 → `execute_fallback_chain`
执行（runner = 适配器 probe/preview 级轻量取数，经
`SourceDefinition.fabric_profile()` 构造 `LocalFileAdapter` /
`StatsApiAdapter`）。熔断 OPEN 的源自动跳过并记录 `circuit_open`
D3 决策；链上每跳经 D3/D4 契约留痕；全链失败 → 诚实失败的
`DataScoutReport`（不虚构数据）。GIS 实体对齐提示词（名称→标准实体、
异构字段映射、GCJ-02/WGS84 纠偏声明）作为角色纪律注入，不写死在代码
分支里。

### D6 — 空间计算专家：任务图编排 + UTM 投影防御

`GeoComputeAgent.plan_computation()` 产出合法 `ExecutionPlan`
（`validate_plan` 前置）：

- **体积自动估算**：从 D1 cost_hint / bbox 面积 / 行数启发式估算
  （`estimate_volume()`），估算结果写入计划 budget 与
  `SpatialProfileRef.volume`；
- **UTM 防御**：bbox 跨度或估算体量超阈值 → 任务图头部自动插入
  `reproject` 节点（UTM zone 由 bbox 中心经度推导，
  `infer_utm_crs()` 纯函数），节点携带 `defense=auto_utm` 注记；
  推导失败（无 bbox）→ 诚实跳过并在摘要披露，绝不猜 EPSG;
- **异步 Celery 调度**：durable 节点经提交缝（可注入，默认
  `submit_durable_job`）派发；提交器可被测试桩替换，编排器不直接
  依赖 Redis/DB。

### D7 — 心跳、日志与超时熔断（基类统一）

`BaseSpecialistAgent` 提供：`heartbeat()`（monotonic 时间戳 + 结构化
日志，长任务循环逐波调用）、`heartbeat_age_s()`（僵死检测观测面）、
`check_deadline()`（墙钟熔断，超时抛 `SpecialistTimeoutError`，默认
取角色 `max_wall_time_s`；时钟可注入以便测试）、领域方法的诚实失败
收敛（异常 → 结构化失败结果，不静默吞）。

## 后果

- 正向：提示词按专精切分（角色 title + expected_outputs 承载边界）；
  数据检索失败自愈（回退链 + 熔断）；重算全部离开事件循环；上下文
  体积有 8 KB 硬闸；权限面从「描述符 side_effect 过滤」升级为
  专家级名单 RBAC。
- 代价/限制：`SpatialProfileRef` 是新契约（本 ADR 冻结 v1，追加字段
  须可选 + 升级测试）；工具白名单是手工名单，构造期存在性校验防止
  腐烂；专家领域方法是确定性编排，LLM 判断仍由子代理 LLM 承担
  （委派路径），不试图在本层解决语义对齐。
- 回滚：`agent_swarm.registry.ensure_subagent_roles_registered()` 是
  唯一全局副作用（幂等注册两个角色）；不注册时其余模块零行为。
