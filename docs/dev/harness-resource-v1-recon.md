# Harness Resource / Cost / Budget / Backpressure / Admission Governor V1 — 勘察报告（P0）

> 分支：`harness/resource-cost-governor-v1`；基线：`master@580b33e9`。
> 本文是 Governor V1 的入库勘察产物，覆盖现存全部 resource/cost/budget/concurrency 机制的盘点、
> 跨切缺口分析、接入缝（integration seams）、并行线冲突规避与仓库惯例。
> 所有断言均带 `path:line` 证据（行号对齐基线 580b33e9）。

---

## 0. 最重要的一个发现（改变设计前提）

**仓库已经存在一个名为 `ResourceGovernor` 的层级预算树**（global → tenant → project → session → execution），
带原子预留（reserve/charge/release）、跨进程 advisory 计数器、以及生产单例：

- 树实现：`app/services/geocompute/budgets.py:79`（`ResourceGovernor`），全局单例 `GLOBAL_GOVERNOR`（`app/services/geocompute/budgets.py:427`）；
- 生产接线：`app/services/geocompute/api.py:148`（`GOVERNOR = ResourceGovernor(global_limits=BudgetLimits(max_rows=5_000_000, max_bytes=2 GiB), cross_process=shared_counter())`）；
- 层级并发槽：tenant=8 / project=4 / session=4（`app/services/geocompute/api.py:160-163`），执行作用域在 `run_plan_sync` 挂载（`app/services/geocompute/api.py:176` 起）；
- 准入执行点：`app/services/geocompute/executor.py:756`（`_admission_check`：计划级 rows/bytes 估计和 vs `plan.budget`）；执行器默认 `max_workers=2` 线程池（`app/services/geocompute/executor.py:56`）。

**Governor V1 不应另造第二棵预算树**。正确姿态是：复用/扩展现有 `ResourceGovernor`（维度、作用域、跨进程层），
把 harness 面的 token/turn/tool/browser/export 等预算作为**新维度或新客户端**挂进来，而不是平行发明。

其次是：**观测预算面也已存在** —— `app/lib/observability/budgets.py`（`BudgetRegistry` + manifest
`config/perf_budgets.json` + Prometheus breach counter，`app/lib/observability/budgets.py:32-45,86,119`），
明确声明与 `tests/benchmarks/baselines.json`、`tests/fixtures/perf_budget.py` 三真相**不合并**（`app/lib/observability/budgets.py:7-13`）。
Governor 的 SLO 观测必须挂这套注册表，不得新建标签词表。

---

## 1. 预算盘点矩阵（Budget Inventory Matrix）

| subsystem | existing budget/limit mechanism | owner module | unit | hard/soft | runtime enforcement point (file:line) | evidence | duplicate-risk for governor? |
|---|---|---|---|---|---|---|---|
| LLM/context tokens | `ContextBudgetPlan` 分类预算规划 + `BudgetReport` 度量 + `GisBudgetAdvisor` 建议（不截断，只建议）；tool schema 硬上限 35%、输出保留 25%、GIS 分区上限（ADR-0101/0103/0104） | `app/services/chat/context_budget.py` | token | soft（violation 留痕；截断权在历史压缩/schema 投影） | 规划 `context_budget.py:142`；度量 `context_budget.py:188,248`；建议 `context_budget.py:306`；接线 `context_assembler.py:587-690` | `context_budget.py:53`(TOOL_SCHEMA_MAX_FRACTION=0.35), `:63`(GIS_SECTION_CAPS) | 高：governor 若做 token 预算必须复用此 API，不得另写估算器（CJK-aware `_estimate_tokens` 单一语义，`context_budget.py:15-16`） |
| LLM history 软预算 | `HISTORY_TOKEN_BUDGET = 6000`（历史压缩头预算） | `app/services/chat/context/history_compression.py` | token | soft | `history_compression.py:10` | `context_budget.py:254`(history_budget_tokens=6000 对齐) | 中：同一 token 口径，挂靠即可 |
| GIS harness context layers | `apply_context_budget`：域字节上限 → 按淘汰序 prune，violations 留痕 | `app/services/gis_harness/context_layers.py` | bytes | soft（确定性压缩） | `context_layers.py:230`（`apply_context_budget`），调用 `:208` | `context_layers.py:16`（对齐 context_budget 纪律） | 中：bytes 维预算，可成为 governor 的被通知方 |
| LLM request | `LLM_TIMEOUT_S=120`、`LLM_MAX_TOKENS=16384`、`LLM_CONTEXT_WINDOW`（未配置→window_unknown 诚实降级） | `app/core/config.py` | s / token | hard（client 超时） | `config.py:133-138` | `context_budget.py:82-84,159-165` | 低 |
| Chat 引擎 turn | `CHAT_MAX_ROUNDS=60`（LLM 循环轮数）+ `TURN_TOTAL_TIMEOUT_S=900`（整 turn 墙钟） | `app/core/config.py` / `chat/execution_engine.py` | 轮 / s | hard | `execution_engine.py:369`(`max_rounds`), `:375`(`_turn_total_timeout_s`) | `config.py:165-166` | 中：turn 级 deadline 是 governor 层级 deadline 传播的锚点 |
| Pi turn | `PI_TURN_TOTAL_TIMEOUT=300`（整回合）、`PI_EVENT_STREAM_TIMEOUT=120`（连续静默 stall）、`PI_EVENT_DRAIN_TIMEOUT=2`（单事件粒度）、heartbeat 8s；超时分类 `pi_turn_budget` vs `pi_stall` | `app/agent_pi_bridge.py` | s | hard | `agent_pi_bridge.py:86-87,103`（常量）；判定 `:2234,2429,2454,2549` | `:85-90` 注释（nginx 3600 预算、#982） | 高：Pi 面已有 metrics 扩展（并行 PR #1274 `pi_surface_metrics.py`），governor 勿重复布点 |
| Pi worker 池 | `PI_BRIDGE_POOL_SIZE`（默认 1）有界 subprocess 池 + session 亲和路由 | `app/agent_pi_bridge.py` | 进程数 | hard | `agent_pi_bridge.py:2643-2702`（`BoundedSet` 池 + `_active_turns` 表 `:1191`） | `:249-254`（V5-B 池语义） | 高：这就是 Pi 的"会话并发准入"，governor 的多会话准入应挂它旁边 |
| 会话取消（abort 预算） | `ABORT_TIMEOUT_S=5.0`：卡死的 Pi 回合不得扣留调用方（CONC-F7） | `app/services/chat/session_cancellation.py` | s | hard | `session_cancellation.py:36`，`abort_active_pi_turn :39` | 模块 docstring `:1-22`（唯一桥接取消点） | 低：governor 应复用为取消传播路径 |
| Tool dispatch（进程级 wave） | `TOOL_WAVE_CONCURRENCY`（默认 5）`asyncio.Semaphore` + heavy 工具占 2 槽（`_MultiSlotAcquire`） | `app/services/tool_dispatch_service.py` | 并发槽 | hard | `tool_dispatch_service.py:316-317`（semaphore 创建）、`:489-497`（session gate → wave semaphore 获取序） | `:223-242`（`_MultiSlotAcquire`）、`#1062/#909` 注释 | 高：**这是 governor 最自然的工具面接入点**（ADR-0100 会话公平门同址） |
| Tool dispatch（会话公平） | `_SessionWaveGate`：每会话并发 cap=2（`TOOL_WAVE_SESSION_CONCURRENCY`），Condition 唤醒，会话表天然有界 | `app/services/tool_dispatch_service.py` | 并发/会话 | hard | `tool_dispatch_service.py:244-276`（类）、`:320-321`（实例化） | ADR-0100 fairness 注释 `:246-259` | 同上 |
| Tool dispatch（线程面） | `_TOOL_THREAD_LIMIT = max(4, min(16, cpu+4))` loop-aware semaphore（THREAD 工具 to_thread 路径） | `app/tools/registry.py` | 并发线程 | hard | `registry.py:143-167`（`_get_tool_thread_semaphore`） | `:133`（ToolExecutionPolicy 四档 INLINE/ASYNC/THREAD/CELERY） | 中 |
| 单工具墙钟 | `TOOL_TIMEOUT_S=300`（注册元数据 timeout 可覆盖）；INLINE 在事件循环上执行无法抢占（诚实约束） | `app/tools/registry.py` | s | hard（放弃等待，线程跑完为止） | `registry.py:174`（常量）、`:1528-1529`（`asyncio.timeout`） | `:176-178`（泄漏线程计数 `_tool_thread_leaked_count`） | 中 |
| 工具成本先验 | `ToolCost = light/medium/heavy`（156 个存量工具默认 light；heavy 占 2 wave 槽） | `app/tools/registry.py` | 档位 | soft（调度权衡用） | `registry.py:136-142` | `tool_dispatch_service.py:225-230`（heavy 多槽获取） | 低：governor 定价可复用此词表 |
| 工具去重/复用 | 同参去重（`executed_tools` + `_completed_keys` 有界 4096）+ artifact 层 `GIS_ANALYSIS_REUSE` 确定性复用 | `app/services/tool_dispatch_service.py` | 次数 | hard | `tool_dispatch_service.py:331-395`（dedup）、`:383-395`（reuse） | `:283-289`（并发在飞 vs 已完成诚实区分） | 低 |
| Tool metrics（背压） | 有界队列 8192 + 后台 writer 线程，满则丢行（背压），10MB×5 轮转（ADR-0044） | `app/services/tool_metrics.py` | 行/bytes | hard（观测面） | `tool_metrics.py:32-38`（`_MAX_QUEUE` 等） | docstring `:1-10` | 中：观测面注册纪律参照 |
| Subagent 并行 | `SUBAGENT_PARALLEL_CONCURRENCY = 2`（asyncio.Semaphore 有界并行） | `app/services/subagent.py` | 并发 | hard | `subagent.py:52`（常量）、`:677`（semaphore） | `:24` docstring | 中 |
| Data Fabric 结果界 | `DATA_FABRIC_MAX_FEATURES=50k / MAX_RESPONSE_BYTES=256MiB / MAX_PAGES=200 / QUERY_TIMEOUT=30s / TOTAL=120s`；配置地板防"配 0 关保护" | `app/services/data_fabric/limits.py` + `app/core/config.py` | rows/bytes/页/s | hard | `limits.py:19-22`（地板）、`:65`（`enforce_result_bounds`）、`:112`（`enforce_page_bound`）；`config.py:302-306` | `limits.py:1-9` docstring（不信任远端 limit） | 中：governor 的 bytes 维语义与之一致即可 |
| DF 获取成本模型 | `estimate_cost`（rows/bytes/latency/quota 启发式，DS8 待校准）+ `AcquisitionBudget`（max_rows/max_bytes/max_ms/max_quota）+ 计划择优 `_within` | `app/services/data_fabric/planning/cost_model.py` `contracts.py:152` `planning/compiler.py` | rows/bytes/ms/页 | soft（估计）→ 计划选择 hard | `cost_model.py:84`（`estimate_cost`）；`compiler.py:222`（`_within`）；`matrix.py:104`（budget=500 rows 示例） | `compiler.py:125-154`（估行→成本→carrier cap） | **高**：这是"governor 的数据面价格表"的现成来源，勿另写成本模型 |
| DF 并发/连接 | `DATA_FABRIC_SYNC_CONCURRENCY=4`（describe 并发）、V7 连接池有界（1024 entries, idle TTL 1800s） | `app/core/config.py` / `data_fabric/connection_manager.py` | 并发/连接数 | hard | `config.py:316,320-326`；`connection_manager.py:279`（RLock 生命周期） | `circuit_breaker.py:98-105`（≤16 线程并发 describe） | 低 |
| DF 熔断 | per-source `CircuitBreakerRegistry`（threshold=5, cool_down=30s, half-open 单试探，LRU 4096） | `app/services/data_fabric/circuit_breaker.py` | 状态机 | hard（fail-fast） | `circuit_breaker.py:41-95`（`CircuitBreaker`）、`:98-188`（registry + `call`）、进程单例 `:195` | `:79-82`（永久错误不触发）；raster 复用 `geo_raster/remote.py:97-105` | 低：governor 的 provider 健康真相源 |
| DF 降级链 | 声明式 fallbacks（深度 5、条件触发 timeout/5xx/429/quota/empty/circuit_open）+ `reliability.retry_call`（有界指数退避+full jitter、POST 幂等门） | `data_fabric/fallback.py` / `data_fabric/reliability.py` | 跳数/次数 | hard | `reliability.py:33-55`（`is_transient` 单一分类器）、fallback 链执行（ADR-0174） | `docs/adr/0174-ads-v1-fallback-chains.md` | 中：governor 报废/degraded 记账应消费其 `AcquisitionFact` |
| DF streaming 协同 | `batch_size_for(governor)`：按 `ResourceGovernor` 用量余弦折半（有下界）——**已有的 governor↔data_fabric 联动先例** | `app/services/data_fabric/streaming.py` | 批大小 | adaptive | `streaming.py:43`；限额读取走 `budgets.py:356`(`limits_for`) 公开 API | `streaming.py:11` docstring | 低：正面样板 |
| Geocompute 预算树 | `ResourceGovernor` 层级（见 §0）+ `BudgetExceededError` 类型化拒绝 + 补偿回滚 + 跨进程 advisory（fail-open，默认关） | `app/services/geocompute/budgets.py` | rows/bytes/nodes/concurrency | hard（L1 权威） | `budgets.py:167`（`reserve` 原子预留）、`:269`(release)、`:311`(charge)、`:427`(GLOBAL_GOVERNOR) | `budgets.py:232-255`（零增量维度不参与判定，防假性饿死） | **本体**：governor 应扩展而非替代 |
| Geocompute 节点重试 | `RETRYABLE_FAILURE_CLASSES` 白名单 + 有界指数退避（backoff_s×multiplier^(n-1) 封顶 max_backoff_s，deadline 感知拒绝，jitter 可关） | `app/services/geocompute/executor.py` | 次数/s | hard | `executor.py:1203-1211`（`_retry_allowed`）、`:1214-1229`（`_retry_delay_s`） | ADR-0101 D5 | 低 |
| Geocompute 队列画像 | `EXECUTION_QUEUE_PROFILES = raster/high_memory/external_io/heavy_cpu/light_cpu/network` + 默认 `celery`；`queue_for_node` 确定性路由（locality_hint > 类别 > ResourceClass≥4 > 轻向量） | `app/services/geocompute/durable.py` | 队列名 | hard（路由） | `durable.py:44-59`（profiles）、`:64-126`（`queue_for_node`） | `task_queue.py:39-53`（具名队列+路由声明） | 中：**已有按资源画像分队列** —— governor 的排队公平策略必须建立在它之上 |
| Celery 任务硬限 | `task_time_limit=3600` / `task_soft_time_limit=3300`（软超时留清理窗口）、`acks_late=True` + `reject_on_worker_lost=False`（不重投不可逆 GIS 操作）、broker/backend socket 2s 快速降级 | `app/services/task_queue.py` | s | hard | `task_queue.py:81,107-111,89-99` | `:100-106`（ADR-0052 崩溃语义） | 低 |
| Celery 重试 | `DEFAULT_RETRY_POLICY = {max_retries:3, interval_start:5, step:10, max:60}`（提交侧可选） | `app/services/task_queue.py` | 次/s | soft（opt-in） | `task_queue.py:125-130`，`:171-179` | — | 低 |
| Workflow runtime 派发 | 进程级 `GIS_WORKFLOW_DISPATCH_SLOTS`（默认 4）per-event-loop semaphore；批次内 priority 降序 + DAG 序 FIFO 公平；大任务隔离 opt-in（rows>20k 要求 durable worker） | `app/services/workflow_runtime/dispatch.py` | 并发/行数 | hard | `dispatch.py:36`（默认槽）、`:61-71`（`get_slots_semaphore`）、`:38`（隔离阈值） | 模块 docstring `:1-19` | 中 |
| GeoCompute cluster 协调器 | `WEBGIS_COORDINATOR_SLOTS`（默认 2）本地槽 + run lease TTL + 心跳 watchdog + attempt 预算内 reclaim 重派 + fairness 轮转选取 | `app/services/geocompute/cluster/scheduler.py` | 槽/lease | hard | `scheduler.py:145`（slots）、`:84`（TTL 常量）、`:151`（max_run_attempts）、`:330`（`free = slots - inflight`） | docstring `:5-17`（reclaim/dispatch/heartbeat） | 中：**已有 fairness 轮转**，governor 的队列公平不必重做 |
| asyncio 桥背压 | `WEBGIS_BRIDGE_MAX_INFLIGHT=128` BoundedSemaphore（专用 loop 线程 + supervisor 重建 + 30s result timeout） | `app/services/geocompute/_async_bridge.py` | 在飞协程 | hard | `_async_bridge.py:41-45` | docstring `:1-27`（消除 `_SERIAL` 进程级串行） | 低 |
| Raster 打开环境 | `rasterio_env()`：GDAL_CACHEMAX（`RASTER_GDAL_CACHE_MAX_MB`=64）、GDAL_NUM_THREADS=1（窗口顺序处理 §42）、HTTP_TIMEOUT=5、MAX_RETRY=0；SSRF 门禁复用 DF security | `app/lib/geo_raster/env.py` | MB/线程/s | hard | `env.py` docstring `:1-19`；`config.py:66,69`（RASTER_PROCESSING_MEMORY_MB=256 / CACHEMAX） | `env.py:22-40`（每个 open 路径必须持有 env） | 低 |
| Raster 病理防护 | `RasterResourceExceededError`：分辨率/像素/估算 bytes 上界，分配前拦截（degree-meter 混淆防护，ADR-0042） | `app/lib/geo_analysis/raster_guard.py` | px/bytes | hard | `raster_guard.py:1-5`（模块头）+ 异常构造 `:7-27` | ADR-0042 | 低 |
| Raster 远端读预算 | `RemoteReadPolicy{max_requests, max_bytes, backoff_s, max_attempts, jitter}` + 会话累计（requests/bytes_read/retries）+ per-host 熔断 | `app/lib/geo_raster/remote.py` | 次数/bytes | hard | `remote.py:70-82`（policy 字段）、`:89-96`（requests 超限）、`:135`（重试循环）、`:150-156`（bytes 超限） | `:83-91`（`RemoteReadSession` 计数载体） | 中：**自带 per-session 成本累计**，是 governor 记账的样例形态 |
| 远感/STAC | stac_client + ADS 降级链（同 DF 行）；`RemoteReadPolicy` 同样覆盖 /vsi 路径 | `app/services/rs/stac_client.py`、`data_fabric/fallback.py` | — | hard | 同上 | ADR-0174 | 低 |
| 统计/向量计算 | geocompute `ResourceGovernor` 全覆盖（vector 类别 → light_cpu 队列）；DF 统计走 `query/statistics.py` 线程池 | `geocompute/*`、`data_fabric/query/statistics.py` | rows/bytes | hard | `durable.py:113-126`（轻向量→light_cpu） | — | 低 |
| Map compile / render | perf 预算 `map_render` 5s（SLO 观测）；MVT 单飞（`SingleFlightManager` per (session,ref) 共享一次构建）；`MAP_QUALITY_GATE_MAX_FEATURES=5000`；制图修复预算 `MAX_RUNTIME_REPAIR_ITERATIONS`（ADR-0074 repair budget） | `app/lib/observability/budgets.json`(manifest) / `mvt.py` / `cartography_runtime.py` | s/features/迭代 | soft（SLO）/hard（其余） | `config/perf_budgets.json:24-28`；`mvt.py:1703,1804`；`config.py:113`；`cartography_runtime.py:973` | ADR-0074、ADR-0146 | 中 |
| 浏览器/headless | runtime validator 走 Node CLI + chromium；`REQUIRE_BROWSER=1` nightly 车道硬失败；pytest marker `heavy` 自跳 | `app/services/runtime_validator.py` + `pytest.ini` | 进程/车道 | hard（车道级） | `runtime_validator.py:5,49,314`；`pytest.ini:16-17` | issue #532 | 中：浏览器会话无进程内池 —— 是 governor 的空白点 |
| VLM judge | 每证据状态至多 1 次 VLM 调用（(session,fingerprint,截图摘要) 记忆化 + 无重试）；`CARTO_VISUAL_JUDGE_TIMEOUT_S=20`；max_tokens=1024；截图 ≤4MiB；fail-closed not_evaluated | `app/lib/harness/visual_evaluator.py` | 次/s/token | hard | `visual_evaluator.py:213`（timeout）、`:216`（max_tokens）、`:48`（`_MAX_SCREENSHOT_BYTES`）、`:16-18`（限流纪律） | ADR-0158 | 低：**已是自我节制的 judge**，governor 只需读其证据 |
| Export | 批量出图串行队列：`max_concurrency=1` 是纪律常量（导出/浏览器/VLM 属重型资源 §0.4）；`MAX_BATCH_JOBS=200`；每 job `max_retries=2`；断点续传 `resume_state` | `app/services/export_batch_queue.py` | 并发/次 | hard | `export_batch_queue.py:9-17`（设计）、`:25,30`（常量）、`:127`（重试循环） | ADR-0166 | 中 |
| ModelOps / GPU | `BoundedSemaphore(max_concurrent_inferences)`；`MAX_TILES_PER_RUN=65536`；`vram_budget_bytes`→`batch_for_budget`；OOM downshift ≤2 次；extension worker 帧 68MiB 上限、`_ADAPTER_SEMAPHORE=4`；**BudgetLimits 无 GPU 维（模型自持）** | `app/services/modelops/engine.py`、`app/lib/modelops/resources.py` | 并发/tiles/bytes | hard | `engine.py:244`（slots）、`:108`（tiles）、`:565-575`（VRAM→batch）、`:100-102`（帧上限/downshift）；`extension_adapter.py:47` | `resources.py:1-5`（与 governor 记账语义对齐、GPU 维自持） | 高：GPU 维度是 governor 预算模型需要**新增**的维度契约 |
| 存储 / artifact | `MAX_ARTIFACT_BYTES=5GiB` LRU 逐出 + chunk cache 1GiB + advisory 字节计数器；S3 multipart `MAX_PARTS=10_000`；ingest 内联载荷 8MiB 上限 | `app/lib/artifact_cache.py`、`s3_blob_store.py`、`tools/ingest_tools.py` | bytes | hard | `artifact_cache.py:46,219,363,468-476`；`s3_blob_store.py:52`；`ingest_tools.py:23,104` | ADR-0048 | 中 |
| Org 配额（DB 真相） | 存储字节 100GiB / 并发任务 50 / 速率 240/min；per-org 表覆盖 → env 默认；读 fail-open；越限 → `QuotaExceededError`（429 QUOTA 信封）+ 审计 | `app/services/org_quota.py` | bytes/次/min | hard（裁决）+ fail-open（查询故障） | `org_quota.py:43-45`（默认）、`:145`(`check_concurrency`)、`:158`(`check_storage`)、`:187`(`check_rate`)、`:228`(`enforce_for_principal` 路由入口一站式) | `:17-18`（fail-open 纪律） | **高**：org 维准入已有权威裁决点 —— governor 的"主体准入"必须与 `enforce_for_principal` 合流 |
| 速率限制设施 | `RateLimiter` Protocol：Redis 滑窗 + 内存兜底（`_MAX_KEYS=10000` 有界），Redis 不可达自动降级 | `app/core/rate_limiter.py` | 次/窗口 | hard | `rate_limiter.py:14`（Protocol）、`:28`（Redis impl）、`:130-140`（Memory impl + 兜底 `:237`） | — | 低：governor 直接复用 |
| 分布式会话锁 | `session_lock(session_id)`：Redis SET+Lua 释放 + TTL 续约，故障降级进程内 asyncio.Lock（waiter-aware 逐出） | `app/services/distributed_lock.py` | 锁 | hard | 模块 docstring `:1-14` | CONCURRENCY-V2 | 低 |
| 协作式取消 | `CancellationToken`（幂等 cancel + 回调 + asyncio waiter）+ `CURRENT_TOKEN` contextvar（to_thread 复制）+ `checkpoint()`/`cancellable()` + 进程内 LRU registry（持久事实在 DB，worker 用 DurableCancellationProbe） | `app/lib/cancellation.py` | — | — | `cancellation.py:36`（token）、`:138`（contextvar）、`:163`（checkpoint）、`:254`（registry） | `:128`（`link()` 级联：agent task → durable job） | 低：**取消传播的现成主干** |
| 性能预算（SLO 观测） | `BudgetRegistry` 封闭 key 词表 + manifest `config/perf_budgets.json`（6 条：first_response 3s / tool_dispatch 0.5s / data_query 10s / map_render 5s / workflow_scheduling 2s / artifact_transfer 15s）+ Prometheus 直方图与 breach counter；观测绝不阻断业务 | `app/lib/observability/budgets.py` | s | soft（观测+告警接缝） | `budgets.py:119`（`observe`）、`:175`（`observe_budget`）；manifest `config/perf_budgets.json:4-41` | `budgets.py:7-16`（三真相不合并声明、封闭词表） | 高：SLO→准入的升级路径是 governor 的机会，但注册必须走这套 |
| CI perf 门 | `perf/budgets.json`（ADR-0146 quality-e2e-v9；tolerance 10%；4 条：sse_concurrent_50 20s / mvt_tile_p95 50ms / cube_window_p95 500ms / chat_first_token_p95 20ms）+ `scripts/perf/run_budget.py` RED 门 | `perf/budgets.json`、`scripts/perf/run_budget.py` | ms | hard（CI 门） | `run_budget.py:30`（默认路径）、`:40-45`（evaluate）；`perf/budgets.json:1-44` | `perf/budgets.json:4-6`（数量级回归闸哲学） | 低：CI 侧，不与运行时 governor 混同（manifest 自述口径互不合并） |
| 任务队列 Celery | 见上 Celery 三行 | `app/services/task_queue.py` | — | — | — | — | — |

其他发现的小型守卫（不单列）：`chinese_maps` 各 provider `Semaphore(6)`（`app/tools/chinese_maps/amap.py:307,374,462`）、`spatial_decision_tools` 外部 API `Semaphore(4)`（`app/tools/spatial_decision_tools.py:205-207`）、extension 流事件上界 `EXTENSION_MAX_STREAM_EVENTS=10000`（`config.py:231-232`）、worker 崩溃隔离 `EXTENSIONS_MAX_WORKER_CRASHES=2`（`config.py:201-202`）、内存 payload store 128MiB spill（`executor.py:250`）、单飞通用件 `SingleFlight`（`app/services/singleflight.py:31`，线程面；asyncio 面在 `mvt.py:1703`）。

---

## 2. 缺口分析（Governor 应填的跨切缺口）

按严重度排序；每条都有证据。

### G1. 预算系统彼此无感知（4+ 套独立真相，互不记账）
存在至少四套互不通气的预算：geocompute `ResourceGovernor`（rows/bytes/nodes/concurrency，进程内）、
`org_quota`（DB 聚合：存储/并发/速率）、`context_budget`（token 分区规划）、`observability/budgets`+`perf/budgets.json`
（SLO 观测/CI 门）。没有任何组件回答"这一回合累计花了多少 token + rows + bytes + 秒 + VLM 调用"。
证据：org 并发只数 `geocompute_runs(queued/leased/running)+workflow running`（`org_quota.py:121-142`），
对 tool wave / Pi turn / VLM / export 完全不可见；而 governor 树只覆盖 geocompute 计划
（`api.py:176` run_plan_sync 才挂链）。harness 一回合可以合法地同时撞 5 套预算而无一全局视图。

### G2. 无 plan 级聚合预算（harness 计划没有 cost gate）
geocompute 有计划级准入（估计和 ≤ plan.budget，`executor.py:756-788`）、DF 有获取计划择优
（`compiler.py:222`），但 **gis_harness 的 plan_graph/plan_runtime/plan_candidates 没有任何预算面**——
一个多工具计划在派发前无人估算"这个 plan 要多少 token×N 轮 + rows×M 查询"。
`GisBudgetAdvisor` 只面向 prompt 分区（`context_budget.py:306`），不面向执行计划。

### G3. 多会话全局准入缺失（每子系统一个孤立旋钮）
进程级并发旋钮彼此独立且语义不同：tool wave=5（`tool_dispatch_service.py:316`）、workflow slots=4
（`workflow_runtime/dispatch.py:36`）、geocompute 执行器 workers=2（`executor.py:56`）、cluster slots=2
（`cluster/scheduler.py:145`）、subagent=2（`subagent.py:52`）、Pi 池=1（`agent_pi_bridge.py:2702`）、
bridge inflight=128（`_async_bridge.py:42`）。没有"当前进程总算力占用 / 新回合该不该收"的单一裁决。
负载升高时这些旋钮各自排队，无全局背压信号（关键词 backpressure 全仓库仅 15 处命中，多在注释）。

### G4. 重试级联无成本聚合（retry 不入账）
至少 10 处独立重试循环/策略：
LLM connect 3 次 + 内容梯 1 次（`llm_client.py:368-373,449-461,474-492`）；raster 远端读 `max_attempts`+
jitter（`remote.py:135`）；DF `retry_call`（`reliability.py`）+ fallback 链逐跳（ADR-0174）；export job 2 次
（`export_batch_queue.py:127`）；celery 提交侧 policy（`task_queue.py:125-130`）；geocompute 节点白名单重试
（`executor.py:1203-1229`）；cluster 交换 GET（`cluster/exchange.py:216`）；history 3 次
（`history_service_async.py:324,394,460`）；map_product 3 次（`map_product_service.py:179`）；artifact_revisions 2 次
（`artifact_revisions.py:94`）。**没有任何一处把"重试造成的重复成本"计入预算**——evidence 层只数
`tool_retries`（`app/lib/runtime/evidence.py:143,211`），与预算零联动。一个坏源可以触发 DF 重试→工具重试→LLM 重试的三级放大，账面只显示一次调用。

### G5. 取消的资源释放与"废功"记账缺口
- to_thread worker 线程无法终止，泄漏线程只计数不断言（`registry.py:176-178`）；INLINE 工具挂死时预算形同虚设（`registry.py:171-174`）。
- 取消后已预留的 governor concurrency 槽由执行器 settle 释放（`executor.py:790` 起 `_run_ready_set`/`_settle`），但 tool wave 槽、Pi turn、LLM 流的已耗成本无统一"废功（wasted-work）"口径——evidence 有 `deduped_tool_calls`/`tool_retries` 计数（`evidence.py:8,143`）但没有成本折算。
- 取消语义本身已有良好主干（`cancellation.py:128` link 级联、`session_cancellation.py:39`），governor 应挂上去而不是改它。

### G6. 队列公平性只有局部答案，无 org/主体维公平
会话级公平有 `_SessionWaveGate`（`tool_dispatch_session` gate, `tool_dispatch_service.py:244-276`）、
cluster 有 fairness 轮转（`cluster/scheduler.py:7`）、workflow 有 priority+DAG 序 FIFO
（`workflow_runtime/dispatch.py:16`）。但 **celery 具名队列按资源画像分、不按 org 分**（`durable.py:44-59`，
`task_queue.py:39-53`），单 worker 消费全部队列时一个 org 的 heavy 风暴与另一个 org 排同一物理队列；
org 级公平只在速率桶（240/min，`org_quota.py:45`）上间接存在，无并发份额/权重。

### G7. 降级模式可解释性分散（fail-open 无统一叙述）
大量保护面刻意 fail-open：org quota 读（`org_quota.py:17`）、跨进程 governor（`budgets.py:11-13,88`）、
速率桶（`rate_limiter.py:237`）、perf budget 观测（`budgets.py:127`）。理由都成立（保护性限流不放大故障），
但降级决策散落在审计事件、trace evidence、D3 逐跳落账三处（`org_quota.py:172-184`、`fallback.py`、
`evidence.py`），**用户/运维没有单一"为什么被限流/降级"查询面**（无 reason-code 聚合端点）。

### G8. 内存压力无全局信号
内存守卫彼此独立：raster_guard 像素/字节估计（`raster_guard.py`）、GDAL CACHEMAX 64MB（`env.py`）、
executor payload 128MiB spill（`executor.py:250`）、artifact LRU 5GiB（`artifact_cache.py:46`）、
modelops VRAM budget（`engine.py:565`）。没有进程 RSS/GC 压力信号反馈给任何准入决策——
内存紧张时系统依然按名义限额全速接纳。

### G9. 超时层级不连续（deadline 不传播）
同一回合内：turn 总预算 900s（legacy）/300s（Pi）、TOOL_TIMEOUT_S=300、DF 查询 30/120s、LLM 120s、
celery 3600s、nginx 3600s。各自独立设置，**外层剩余时间不收缩内层预算**——第 290 秒启动的工具仍可独占
300s（`registry.py:174`），第 850 秒派发的 durable job 仍可跑 3600s（`task_queue.py:81`）。
唯一做了 deadline 感知的是 geocompute 节点重试（`executor.py:1214-1229`，来不及完成就拒绝）——应推广为全局机制。

### G10. 跨进程/多副本准入是三种故事
进程内 governor 树是权威（`budgets.py:88`），跨进程 advisory 默认关（`api.py:152-157`，需
`WEBGIS_CROSS_PROCESS_GOVERNOR=1`）；org quota 走 DB 聚合（`org_quota.py:98-142`）；MapSpec 互斥走
Redis 分布式锁（`distributed_lock.py:1-14`）。多副本（k8s replicas:2+HPA，`distributed_lock.py:3-5`）下
"全局还剩多少容量"没有单一事实源，advisory 层也只投影 rows/bytes/nodes 三维（`budgets.py:367-389`），不含 concurrency/token。

### G11. GPU/设备维在预算模型之外
`BudgetLimits` 无 GPU 维（`app/lib/modelops/resources.py:3-5` 明示"ModelOps 自持 device/VRAM 维"）。
VRAM 预算只在 modelops 引擎内部（`engine.py:565-575`），若 harness 同时派发多个含推理节点的计划，
governor 树对 GPU 容量不可见。

### G12. SLO 观测与准入决策之间断裂
`observe_budget` 只打日志+counter，从不拒绝（`budgets.py:119-144` 明示"绝不阻断业务路径"）。
预算持续 breach 时没有任何机制收紧准入（例如 tool_dispatch 0.5s 持续超限 → 收缩 wave cap）。
观测→治理的闭环是 governor V1 最自然的增值位。

---

## 3. 扩展点与集成缝（Governor 应挂的精确位置）

### 3.1 工具派发入口
- **主缝**：`ToolDispatchService.dispatch(tc, session_id, executed_tools)` — `app/services/tool_dispatch_service.py:329`。
  内部已有既定获取序：先 `_SessionWaveGate.acquire`（`:489`）再 `_MultiSlotAcquire(self._wave_semaphore, slots)`（`:491`），
  finally 释放（`:497`）。governor 准入/记账包装应加在 session gate 之前、dedup 之后（dedup 命中在 `:371-389` 提前返回，不计费）。
- 副缝（非 agent 路径，ADR-0014 保留）：`app/tools/registry.py` `_dispatch_impl`（timeout 施加点 `:1528-1529`；
  线程 semaphore `:155`）。governor 不应改 registry（PR #1274 正在动它），经 dispatch service 单点覆盖（ADR-0068 全工具执行经 dispatch service）。
- 成本先验词表：`ToolCost`（`registry.py:136-142`）→ governor 定价矩阵的输入。

### 3.2 geocompute 预算 API
- 树操作：`reserve/release/charge/usage_full/limits_for`（`budgets.py:167,269,311,348,356`）；作用域挂载 `ensure_scope :101`、teardown `:128`。
- 生产单例：`GOVERNOR`（`api.py:148`）；入口 `run_plan_sync`（`api.py:176`，链构造 `:208-232`）。
- 计划级准入：`engine._admission_check`（`executor.py:756`）；执行预算对象 `ResourceBudget`（plan schema，`api.py:137-141` 反序列化）。
- 跨进程 advisory：`resource_counter.CrossProcessCounter`（`app/services/geocompute/resource_counter.py`，duck-typed 注入 `budgets.py:91-99`）。
- **扩维建议**：`BudgetLimits`（`budgets.py:43-53`）加 token/turn/GPU 维，或 governor V1 在树旁挂 harness 维账本并复用 scope 路径格式（`global:root/session:s/execution:x`，`budgets.py:154`）。

### 3.3 Data Fabric 成本估算 API
- `estimate_cost(...)` — `data_fabric/planning/cost_model.py:84`（rows/bytes/latency_ms/quota 四维 `CostEstimate`）。
- `AcquisitionBudget` 契约 — `data_fabric/contracts.py:152`；计划适配 `compiler._within`（`planning/compiler.py:222`）。
- 结果硬界：`enforce_result_bounds`/`enforce_page_bound`（`data_fabric/limits.py:65,112`）。
- governor 联动先例：`batch_size_for(governor, ...)`（`data_fabric/streaming.py:43`）经 `governor.limits_for` 读限额（`budgets.py:356-363`）。

### 3.4 context_budget 公共 API
- `plan_budget(context_window, max_output_tokens)` — `context_budget.py:142`
- `measure_components(items, plan)` — `context_budget.py:188`
- `measure_assembled_context(messages, tools_payload, ...)` — `context_budget.py:248`
- `GisBudgetAdvisor(plan).advise(items)` — `context_budget.py:306,331`
- 接线点：`context_assembler.py:587-690`（度量+建议双路径）。governor 的 token 账本应消费 `BudgetReport.total_est_tokens`/`by_category`，不重复估算。

### 3.5 观测指标注册 API（既有纪律）
- prometheus 原语在模块顶层创建、注册默认 REGISTRY、**封闭标签词表**（无用户数据标签）：`app/lib/observability/metrics.py:20-28`（`app_inflight_requests`、`jobs_worker_active_tasks` gauges + `InflightGaugeMiddleware :43`）。
- perf 预算：`Budget`/`BudgetRegistry.register/load_manifest/observe`（`budgets.py:48,86,95,101,119`）；便捷入口 `observe_budget :175`；未注册 key 零序列（`:122-127`）。
- governor 新指标必须遵守同一纪律：模块级定义、封闭词表、高基数走结构化日志。

### 3.6 取消传播路径
- `CancellationToken`（`cancellation.py:36`）→ `CURRENT_TOKEN` contextvar（`:138`，to_thread 复制）→ `checkpoint()`（`:163`）/`cancellable()`（`:179`）。
- 级联：`token.link(child)`（`:128`，agent task→durable job）；进程内 registry（`:193-254`）；持久事实=DB cancel_requested_at（`:197-199`），worker 经 DurableCancellationProbe 轮询。
- Pi 面：`abort_active_pi_turn`（`session_cancellation.py:39`，5s 预算 `:36`）+ bridge `_active_turns` 表（`agent_pi_bridge.py:1191`）。
- governor 的"取消即全额 release"应挂在 executor settle 路径（`executor.py:835` `_settle`，完成/失败/取消/跳过四态统一归还，`budgets.py:280-289` 注释）。

### 3.7 重试位置全表（乘数分析）
| 位置 | 次数 | 退避 | 说明 |
|---|---|---|---|
| `llm_client.py:474-492` | 3 次（`_RETRY_ATTEMPTS` `:368`） | 0.5×2^n（`_connect_backoff :372-373`） | 仅 connect/pool 阶段；流中/发送后**刻意不重试**（`:181-188`，防双执行） |
| `llm_client.py:449-461` | 内容梯 1 次 | 无 | 超窗→确定性重裁（重 trim 不重放） |
| `geo_raster/remote.py:135-166` | `policy.max_attempts` | `[0, backoff_s]` 均匀 jitter | 瞬态才重试（`is_transient_remote_error`）；取消令牌可中断等待 `:166-176` |
| `data_fabric/reliability.py`（`retry_call`） | policy 有界 | 指数+full jitter | POST 需显式幂等标记；`is_transient` 单一分类器 `:33-55` |
| `executor.py:1203-1229` | 白名单类+backoff_s×multiplier^(n-1) 封顶 max_backoff_s | deadline 感知（来不及→拒绝） | jitter 可关（确定性重放） |
| `cluster/exchange.py:216` | `GET_RETRIES+1` | — | artifact 读取 |
| `export_batch_queue.py:127` | 2（`DEFAULT_MAX_RETRIES :30`） | 无 | job 失败不中断批次 |
| `task_queue.py:125-130` | 3 | 5s 起 +10 步进 封顶 60 | 提交侧 opt-in broker 重试 |
| `history_service_async.py:324,394,460` | 3 | — | DB 竞态 |
| `map_product_service.py:179`、`artifact_revisions.py:94` | 3 / 2 | — | 版本插入 |
| `evaluation/failure_corpus.py:230` | max_attempts+1 | — | 评测面 |
**乘数分析**：最坏路径（LLM 3×connect × 内容梯 2 × 工具 DF fallback 链 5 跳 × 每跳 retry_call N 次）在无全局计数下可达
数十次外呼/数十倍 token——G4 的量化依据。

### 3.8 provider 健康 / 熔断状态源
- 在线地图 provider：`ProviderHealthTracker`（`app/services/provider_health.py:34` 起；60/min、错误阈值 5、恢复 300s `:56-60`）；进程单例 `health_tracker`；fabric 源经 `FabricHealthBridge` 镜像（ADR-0174 §3）。
- 数据源熔断：`get_breaker_registry()`（`data_fabric/circuit_breaker.py:195`）/ `state(source_key)` `:129`（governor 可只读查询，作为准入因子）。
- raster 主机级熔断复用同 registry（`geo_raster/remote.py:97-105`，键 `raster:{host}`）。

### 3.9 artifact / ref 存储路径
- artifact 缓存与窗口：`app/lib/artifact_cache.py:46`（`WEBGIS_ARTIFACT_CACHE_BYTES` 默认 5GiB）、`:219`（chunk cache 1GiB）、`:468-476`（advisory 超限→全量扫描逐出）。
- 公共 API：`app/lib/artifacts.py`；注册/修订/生命周期：`app/services/artifact_registry.py`、`artifact_revisions.py`、`ref_lifecycle.py`、`artifact_lifecycle.py`。
- blob：`s3_blob_store.py:52`（multipart 上限）、`durable_blob_store.py`。
- ref 载荷缓存：`app/services/ref_payload_cache.py`；tool 结果 ref 卸载：`tool_dispatch_service.py`（Fetch-on-Offload，模块 docstring `:12-14`）。

### 3.10 浏览器会话池
- Pi subprocess 池：`agent_pi_bridge.py:2643-2702`（`PI_BRIDGE_POOL_SIZE` 默认 1；session 亲和；`_active_turns` `:1191`）。
- headless 验证浏览器：`app/services/runtime_validator.py`（Node CLI + chromium；无进程内池——V1 可只做计数/排队）。
- 截图消费：`app/lib/harness/visual_evaluator.py`（≤4MiB，`:48`）。

### 3.11 render / export 入口
- MapSpec 编译协调：`app/services/mapspec_compile_coordinator.py`；图层管线 `mapspec_layer_pipeline.py`；SVG 渲染 `mapspec_to_svg.py`；MVT `app/services/mvt.py`（`encode_tile` + `single_flight :1804`）。
- 运行时探针（截图/验证）：`app/services/runtime_validator.py`、`runtime_asset_assembly.py`。
- 出版导出：`app/services/publication_export.py` + 串行队列 `export_batch_queue.py`（`run_batch`）。

### 3.12 任务队列 / durable 提交面
- `TaskQueueService.submit_task`（`task_queue.py:160-185`，owner LRU `:138-148`）。
- durable 节点派发：`durable.dispatch_node`（幂等键/心跳/WORKER_LOSS 复用，`task_queue.py:54-59` 注释）；workflow 侧 `workflow_runtime/dispatch.py`（`get_slots_semaphore :67`）。

---

## 4. 并行线冲突规避报告

对四条并行线做 `git diff --stat $(git merge-base master <branch>) <branch>`（app/frontend/config/scripts 范围）：

### PR #1274 — `harness/pi-typed-tool-surface-v1`（tip 9289c3bd，merge-base = 本基线 580b33e9）
```
app/agent_pi_bridge.py                  | 107 +++++
app/api/routes/metrics.py               |  10 +
app/services/chat/pi_input_gate.py      | 317 +++++（新增：Pi 输入门）
app/services/chat/pi_native_surface.py  | 123 +
app/services/chat/pi_surface_metrics.py | 113 +++++（新增：Pi surface 指标）
app/tools/registry.py                   |  10 +
```
**规避**：勿改 `agent_pi_bridge.py`、`tools/registry.py`、`chat/pi_native_surface.py`；勿另建 Pi 面指标模块
（`pi_surface_metrics.py` 已存在——governor 的 turn/token 指标若与 Pi 相关，消费它而不是并列第二套）。
`pi_input_gate.py` 是工具面之外的另一道门，governor V1 文档中须显式声明与它的分工（输入侧 vs 资源侧）。

### PR #1275 — `harness/gis-situation-world-model-v1`（tip ccdfce63，merge-base = 本基线）
```
app/api/routes/chat.py                  |  48 +-
app/services/chat/pi_turn_context.py    |  21 +-
app/services/gis_situation/**（新包，9 文件 2400+ 行：compiler/projection(445)/contract/diff/...）
app/services/ws_service.py              |  20 +
scripts/situation_inspect.py            | 145 +
```
**规避**：勿改 `chat.py` 路由体、`pi_turn_context.py`、`ws_service.py`；`gis_situation.projection` 自称
"bounded projection"（预算相邻概念），governor 文档须说明资源预算与态势投影边界的分工，避免术语碰撞。

### PR #1273 — `quality/review-optimize-loop`（tip da0e5867，merge-base 17c77c73** 早于本基线**）
```
app/lib/cartography/{component_registry,render_scene,semantic_checks,symbology,thematic_spec}.py
app/services/gis_harness/{completion/pipeline,components,map_critique,planner,recipes,
                          runtime_state_machine,tool_surface,tools,workflow_instance}.py   （14 文件，+91/-20）
```
**规避**：制图修复循环（repair budget ADR-0074 域）与 qc 门是它的领地。governor 若要对
"critique/repair 迭代次数"设预算，只能以只读方式消费其 attempts 记录（`cartography_runtime.py:868-973`），
不得改 `gis_harness/map_critique.py`、`planner.py`、`recipes.py`、`tools.py`（与下一条重叠加压）。

### 本地分支 — `harness/gis-capability-graph-v1`（tip f4208e23，merge-base = 本基线；已有 Capability/ExecutionGraph 设计：M1-M5 完成态，ADR-0181）
```
app/services/gis_harness/capability_graph.py      | 392 +++++
app/services/gis_harness/capability_resolution.py | 584 +++++（新增）
app/services/gis_harness/planner.py               |  96 +
app/services/gis_harness/{qualification_v8,recipes,registry_validation,tools}.py
app/services/chat/plan_orchestrator.py            |  36 +-
app/lib/gis/capability_registry.py                |  13 +
app/lib/quality/artifact_graph.py                 |  18 +
scripts/gen_capability_catalog.py                 | 140 +++++
```
**确认**：该分支确有 Capability/ExecutionGraph 设计（capability_graph 扩 392 行 + 独立 resolution 模块 + planner 集成 + ADR-0181）。
**规避**：`gis_harness/planner.py`、`capability_graph.py`、`chat/plan_orchestrator.py` 是双线热区（#1273 也碰 planner/recipes/tools）。
governor 的 plan 级预算（G2）应作为**计划消费方的旁路估算器**（输入 ExecutionGraph/plan 图谱，输出 BudgetPlan），
以新模块 + 窄 Protocol 接入，不进入 planner 内部。

### 汇总：governor V1 的文件策略
- **只新增**：`app/services/governor/`（或 `app/lib/resource_governor/`）新包 + `config/governor_budgets.json`（仿 `config/perf_budgets.json` manifest 模式）+ 独立测试。
- **只读消费**：`geocompute/budgets.py` API、`context_budget.py` API、`planning/cost_model.py`、`circuit_breaker.state`、`observability/budgets.py`、evidence counters。
- **最小侵入接线点**（如必须改存量，逐条评审）：`tool_dispatch_service.py`（会话公平门旁）、`org_quota.enforce_for_principal` 调用链、executor settle 路径——三者当前均不在任何并行线 diff 内（已核对上述四份 diff --stat）。
- **禁改**（本 V1）：`agent_pi_bridge.py`、`tools/registry.py`、`chat/pi_*`、`api/routes/chat.py`、`ws_service.py`、`gis_harness/planner.py`、`gis_harness/capability_*`、`gis_situation/**`、`lib/cartography/*`。

---

## 5. 仓库惯例（services / ADR / docs / 测试 / 门禁）

### 5.1 service 模块结构（样例：tool_dispatch_service、org_quota、cancellation）
- 模块 docstring 开头写**决策史与职责边界**（引用 ADR/audit 编号），如 `tool_dispatch_service.py:1-40`、`org_quota.py:1-19`。
- 依赖注入两态：可测服务走构造函数显式注入（`ToolDispatchService(registry=..., fire_broadcast=...)`，`tool_dispatch_service.py:296-304`）；基础设施走模块级惰性单例 + `get_*()`/`reset_*_for_tests()` 对（`observability/budgets.py:147-172`、`circuit_breaker.py:192-205`）。
- 端口用 `Protocol` + 内存替身：`SessionDataProtocol`（ADR-0004）、`RateLimiter`（`rate_limiter.py:14`）、`HistoryStoreProtocol`。
- 保护性限额统一 fail-open + 类型化异常 + 可行动 suggestions：`BudgetExceededError(..., suggestions=[...], details={...})`（`budgets.py:246-255`）、`QuotaExceededError`（429 QUOTA 信封，`org_quota.py:48-58`）。
- 常量词表封闭：`ScopeKind` 枚举删除未接线成员（`budgets.py:28-40` 决策记录）、观测标签封闭词表（`budgets.py:15-16`）。
- 线程安全显式声明：锁的持有范围、TOCTOU 补偿（`budgets.py:167-267` reserve 的 admit→charge 补偿回滚）。
- `__all__` 导出白名单（`limits.py:121-129`、`budgets.py:180-188`）。

### 5.2 ADR 格式（`docs/adr/NNNN-slug.md`，当前至 0181）
头部：`# ADR-NNNN: 标题` + `- 状态: Accepted` + `- 日期` + `- 线: <branch/线名>` + `- 关联: ADR-xxxx`；
正文：`## 1. 背景（缺口）` → `## 2. 决策一：...` → 决策二/三（每条带"原语复用不重写"论证），见样例 `docs/adr/0174-ads-v1-fallback-chains.md:1-40`。近期 ADR 与资源直接相关：0042（raster 资源守卫）、0043（工具执行策略）、0044（metrics 队列 writer）、0045（工具缓存单飞）、0046（性能回归 harness）、0096（geocompute 层级治理 D6）、0100/0101（wave 公平门/预算规划）、0131（perf 预算 D4-D6）、0139（org 配额 P5）、0166（导出串行纪律）、0174（降级链）。

### 5.3 docs/dev 惯例
勘察报告（recon）样例：`docs/dev/ac-01-intent-recon.md` —— 头部引用分支与基线 commit，正文用
`file:line` 表格 + 缺口清单，作为后续 PR 的 P0 锚点。本文遵循同一格式。

### 5.4 测试与门禁
- `pytest.ini`：`asyncio_mode=auto`、超时 60s（thread）、`--cov=app`；markers：`heavy`（geopandas/numpy/rasterio，浏览器需 `REQUIRE_BROWSER=1` 进 nightly 车道）、`perf`（确定性 perf harness，基线 `tests/benchmarks/baselines.json`，`PERF_UPDATE_BASELINES=1` 再生）、`cartography`（发布阻断闭环门）、`real_services`（真 Postgres/Redis/celery，`REAL_SERVICES=1`）。
- CI：`.github/workflows/production.yml:65-73`（`ruff check app/ tests/ main.py manage.py`，E4/E7/E9+F，E501 不选，跨模块 re-export 白名单见 `pyproject.toml:65-90`）；`:655-668`（bandit `-ll -ii` 为 release-blocking 安全门）；contract.yml（API 兼容/schemathesis 门）；quality-e2e.yml（chaos + perf 预算门 `scripts/perf/run_budget.py`）。未启用 mypy（CI 无 mypy 步骤）。
- 新模块要求：纯函数核心 + 注入时钟/锁缝（`circuit_breaker.py:9-10` 注入时钟先例）、无网络的真实内存替身测试、`# noqa: BLE001` 带理由。

---

## 附录 A. 关键词命中统计与代表性 file:line

统计口径：`grep -rni <kw> app frontend/lib scripts --include=*.py --include=*.ts`（基线 580b33e9）。

| keyword | hits | 最重要的命中（file:line） |
|---|---|---|
| budget | 1579 | `geocompute/budgets.py:79`（ResourceGovernor）；`geocompute/api.py:148`（GOVERNOR）；`chat/context_budget.py:142`（plan_budget）；`lib/observability/budgets.py:86`（BudgetRegistry）；`geocompute/executor.py:756`（admission）；`config/perf_budgets.json:1`；`data_fabric/contracts.py:152`（AcquisitionBudget） |
| cost | 1237 | `data_fabric/planning/cost_model.py:84`（estimate_cost）；`tools/registry.py:139`（ToolCost 先验）；`tools/tool_metrics.py`（聚合器）；`data_fabric/planning/compiler.py:138`（成本择优）；`lib/modelops/resources.py:12`（ResourceEstimate） |
| quota | 217 | `services/org_quota.py:43-45`（默认三限）；`org_quota.py:228`（enforce_for_principal）；`core/rate_limiter.py:14`（Protocol）；`data_fabric/planning/cost_model.py:124`（quota 维）；`errors.py`（QUOTA→429 信封 `org_quota.py:48`） |
| limit | 2269 | `data_fabric/limits.py:19-22`（保护地板）；`tools/registry.py:143`（线程上限）；`tools/registry.py:174`（TOOL_TIMEOUT_S）；`config.py:165`（CHAT_MAX_ROUNDS）；`workflow_runtime/dispatch.py:36`（slots=4）；`config.py:113`（MAP_QUALITY_GATE_MAX_FEATURES） |
| timeout | 1354 | `agent_pi_bridge.py:86-87,103`（Pi 三预算）；`config.py:133`（LLM_TIMEOUT_S）；`config.py:166`（TURN_TOTAL_TIMEOUT_S）；`task_queue.py:81,111`（celery 硬/软限）；`data_fabric/limits.py:37-42`（查询单次/总）；`lib/harness/visual_evaluator.py:213`（judge 20s）；`chat/session_cancellation.py:36`（abort 5s） |
| semaphore | 49 | `tool_dispatch_service.py:316-321`（wave+session gate）；`tools/registry.py:152-167`（线程 semaphore）；`geocompute/_async_bridge.py:44-45`（inflight 128）；`services/subagent.py:677`（并行 2）；`modelops/engine.py:244`（推理槽）；`tools/chinese_maps/amap.py:307`（provider 6）；`spatial_decision_tools.py:207`（外部 API 4） |
| lock | 4140 | `services/distributed_lock.py:1-14`（Redis 会话锁）；`geocompute/budgets.py:98,114`（树锁）；`data_fabric/circuit_breaker.py:115`（registry 锁）；`tool_dispatch_service.py:287`（dedup 锁）；`connection_manager.py:279`（RLock）；`singleflight.py`（event 协调） |
| queue | 686 | `services/task_queue.py:39-53`（画像队列声明）；`geocompute/durable.py:44-59`（profiles）；`tools/tool_metrics.py:33`（有界观测队列）；`services/export_batch_queue.py:9`（导出串行队列）；`agent_pi_bridge.py:7-9`（事件排水队列语义） |
| backpressure | 15 | `geocompute/_async_bridge.py:34-37`（无背压悬挂协程教训）；`tools/tool_metrics.py:8`（满则丢行背压）；`geocompute/budgets.py:47`（max_concurrency 作调度背压源）；`workflow_runtime/dispatch.py:12`（爆炸半径上界） |
| concurrency | 73 | `geocompute/budgets.py:53`（max_concurrency 维）；`geocompute/api.py:160-163`（tenant8/proj4/sess4）；`tools/subagent.py:52`；`config.py:316`（DF sync 4）；`geocompute/cluster/scheduler.py:145`（slots 2）；`modelops/engine.py:244` |
| memory | 1025 | `lib/geo_analysis/raster_guard.py:1-5`（病理防护）；`lib/geo_raster/env.py`（GDAL CACHEMAX）；`lib/artifact_cache.py:46,219`（5GiB/1GiB）；`geocompute/executor.py:250`（128MiB spill）；`config.py:66`（RASTER_PROCESSING_MEMORY_MB）；`modelops/engine.py:565`（VRAM budget） |
| token | 2930 | `chat/context_budget.py:53,63`（分区上限）；`chat/context/history_compression.py:10`（6000）；`agent_pi_bridge.py:216`（VLM max_tokens 1024 在 visual_evaluator）；`lib/runtime/evidence.py:8`（work 计数）；`auth/jwt`（无关高占比来源：认证 token） |
| context | 1980 | `chat/context_budget.py`（全文件）；`chat/context_assembler.py:587-690`（接线）；`gis_harness/context_layers.py:230`（apply_context_budget）；`chat/context/history_compression.py`（压缩头）；`agent_pi_bridge.py:1162-1164`（pool-safe 上下文表） |
| bytes | 2300 | `lib/json_size.py`（节点预算估算器 `tools/registry.py:205-215` 引用）；`data_fabric/limits.py:29-30`（256MiB 界）；`geocompute/budgets.py:50-51`（max_bytes 维）；`lib/artifact_cache.py:46`；`tools/ingest_tools.py:23`（8MiB 载荷）；`lib/harness/visual_evaluator.py:60`（截图 4MiB） |
| feature_limit | 4 | `data_fabric/limits.py:25`（max_features 地板）；`config.py:302`（DATA_FABRIC_MAX_FEATURES=50k）；`data_fabric/planning/compiler.py:153`（TIER_EXPORT_FEATURES cap）；`config.py:113`（质量门 5000） |
| window | 1709 | `lib/geo_raster/windowed.py`（§42 顺序窗口）；`lib/geo_raster/env.py`（warp 线程=1）；`chat/context_budget.py:82-96`（context window 未知诚实化）；`geocompute/partitioning.py`（分区窗口）；`data_fabric/query/…`（谓词窗口）；高占比另一来源=时间窗口（滑窗限流 `provider_health.py:31-35`） |
| worker | 1813 | `agent_pi_bridge.py:2643-2702`（Pi 池）；`geocompute/executor.py:56`（workers=2）；`jobs/worker.py`（durable worker + heartbeat）；`modelops/providers/extension_adapter.py:47`（适配器信号量）；`data_fabric/query/statistics.py`（统计线程池）；`tools/tool_metrics.py:22`（writer 线程） |
| GPU | 233 | `lib/modelops/resources.py:3-5`（BudgetLimits 无 GPU 维声明）；`modelops/engine.py:565-575`（VRAM→batch）；`resources.py:31`（device_index 多卡亲和）；`engine.py:883,1323`（OOM downshift ≤2） |
| CPU | 494 | `tools/registry.py:143-145`（GIL 下核数上限论证）；`geocompute/durable.py:122-125`（heavy_cpu/light_cpu 队列）；`geocompute/executor.py:56`；`mvt.py`（编码 CPU 热点 perf 预算 `perf/budgets.json:29-35`） |
| resource | 821 | `geocompute/budgets.py`（全文件）；`lib/geo_analysis/raster_guard.py:1`；`lib/modelops/resources.py`；`geocompute/resource_counter.py:1-9`（advisory 计数器）；`config.py:66-69`（RASTER_* 资源常量） |
| retry | 762 | §3.7 全表（`llm_client.py:368-373`；`geo_raster/remote.py:135`；`executor.py:1203-1229`；`export_batch_queue.py:127`；`task_queue.py:125`；`cluster/exchange.py:216`；`history_service_async.py:324`；`data_fabric/reliability.py`） |
| rate_limit | 119 | `services/provider_health.py:56-60`（60/min、阈值 5、恢复 300s）；`services/org_quota.py:187-208`（org 速率桶）；`core/rate_limiter.py:28,130`（Redis/内存实现）；`data_fabric/reliability.py`（429→SourceRateLimitedError 瞬态分类）；`data_fabric/planning/cost_model.py:120-121`（配额延迟罚项） |

---

## 附录 B. Governor V1 设计输入速记（由本勘察直接推出）

1. **定位**：harness 层"总账 + 联合准入 + 层级 deadline 传播"，不是第二套子系统限额。四个可复用地基：
   geocompute `ResourceGovernor`（记账树）、`observability/budgets`（SLO 注册表）、`org_quota`（主体准入）、
   `planning/cost_model`（价格表）。
2. **最小侵入**：新包 + manifest；接线优先级 = tool dispatch（:329）> run_plan_sync 链构造（:176）>
   enforce_for_principal（org_quota.py:228）> export/mvt 入口。Pi 面 V1 只读（池大小 + turn 预算已自洽）。
3. **必须新增的维**：token（回合累计）、turn/plan 数、GPU/VRAM（与 modelops DevicePlan 对齐）、VLM 调用次数、
   重试放大系数（G4）。
4. **必须打通的闭环**：SLO breach（observability）→ 准入收紧（governor）→ 降级 reason-code 统一出口（G7/G12）。
5. **deadline 传播**：以 turn 总预算为根，派生 tool/query/LLM 子预算（先例：`executor.py:1214-1229` deadline 感知重试）。
