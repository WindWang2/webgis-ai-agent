# Harness Resource Governor V1 — 决策日志（Decision Log）

> 分支：`harness/resource-cost-governor-v1`；基线：`origin/master@580b33e9`（#1272 merge）。
> 勘察输入：`docs/dev/harness-resource-v1-recon.md`（P0 全仓勘察，file:line 证据对齐基线）。
> 本文件记录 V1 全部重要架构/取舍决策；执行进度见 `harness-resource-v1-ledger.md`。

---

## D0. 任务对账基线（2026-09-14 执行时）

- `origin/master = 580b33e9`（Merge PR #1272 adaptive-data-supply/v1-master）。
- Open PR（避免重复施工）：#1270 fix/ci-adaptive-hygiene、#1273 quality/review-optimize-loop、
  #1274 harness/pi-typed-tool-surface-v1、#1275 harness/gis-situation-world-model-v1。
- 本地并行分支（未 open PR）：`harness/gis-capability-graph-v1`（已有 Capability/ExecutionGraph
  M1-M5 设计，**占用 ADR-0181**）、`harness/pi-native-kernel-sessionplan-v1`。
- ADR 号占用：master 已至 0179；#1274 与 #1275 的 PR 标题均声称 ADR-0180；capability-graph
  分支占用 0181。**本线使用 ADR-0182**。

## D1. 身份与命名：Harness Governor ≠ geocompute ResourceGovernor

勘察 §0 确认 `app/services/geocompute/budgets.py:79` 已有层级预算树 `ResourceGovernor`
（rows/bytes/nodes/concurrency 维，global→tenant→project→session→execution，原子预留 +
补偿回滚 + 可选跨进程 advisory 计数器）。**V1 绝不复制该树**。

- 新包名：`app/services/governor/`（全新文件，零文件所有权冲突）。
- Facade 类名：`HarnessResourceGovernor`（与 geocompute 的 `ResourceGovernor` 明确区分）。
- 职责分界：geocompute 树是 **geocompute 域内的 L1 权威**；Harness governor 是**跨域协调层**
  ——token / tool-slot / browser-export / wall-time / external-calls / plan-level aggregate
  这些**树里不存在的维度**在这里记账与准入；rows/bytes/nodes 维通过只读视图
  （`usage_full`/`limits_for`，`budgets.py:348,356`）消费树的状态，不重复记账。

## D2. 复用清单（谁已存在 → governor 只消费）

| 能力 | 已有实现 | governor 的姿态 |
|---|---|---|
| 数据获取成本 | `estimate_cost`（`data_fabric/planning/cost_model.py:84`，ADR-0173） | R2 projection 的数据面价格表来源 |
| context token 预算 | `context_budget.py`（BudgetPlan/measure/GisBudgetAdvisor，ADR-0101/0103/0104） | R12 只做 allocation + priority hints，绝不重写 tokenizer |
| geocompute 用量 | `GLOBAL_GOVERNOR.usage_full()` | 只读视图喂 admission |
| provider 健康 | `get_breaker_registry().state()`（circuit_breaker.py:129,195）+ `provider_health.health_tracker` | R11 只读输入，不重建 breaker |
| 协作式取消 | `app/lib/cancellation.py`（CancellationToken/link/checkpoint/registry） | R9 挂靠该主干 |
| SLO 观测 | `observability/budgets.py` BudgetRegistry + `config/perf_budgets.json`（封闭词表，observe-only） | R16 注册纪律对齐；R3 把 breach 作为 admission 输入（G12 的干净机会） |
| 会话公平 | `_SessionWaveGate`（tool_dispatch_service.py:244，ADR-0100） | R6 在其上加全局层 + aging/small-bypass，不替换 |
| VLM judge / export 串行 | visual_evaluator（自限 1 call/20s/1024tok）、ExportBatchQueue（serial 纪律） | 只读记账，不包第二层闸 |
| org 配额 | `org_quota.enforce_for_principal`（org_quota.py:228） | 主体维准入与其合流（V1 只读其判定结果作输入） |

## D3. 集成缝最小化（并行纪律 R20）

**唯一强集成点**：`ToolDispatchService.dispatch`（`app/services/tool_dispatch_service.py:329`）。
- 该文件不属于任何 open PR 的热区（#1274 动 `agent_pi_bridge.py`/`tools/registry.py`/
  `pi_native_surface.py`；#1273+capability-graph 动 `gis_harness/planner.py`/`recipes.py`/
  `tools.py`）。
- 接入形态：dedup 之后（:371-389 语义保持）、session gate（:489）之前插入
  `dispatch_adapter` 的 admit→reserve；finally 段 charge/release/observe。
- **次级只读接线**：`context_assembler.py` 已调用 `measure_assembled_context` 的位置追加
  governor 记账（一行级，context_assembler 不在任何 PR 热区清单）。
- 计划级（R8）：不动 `gis_harness/planner.py`（双热区）——通过
  `planning_hints.ResourceAwarePlanHint` 窄协议 + 独立 estimator 模块旁路提供。

## D4. R1 契约（versioned，未知 ≠ 0）

- `schema_version: "rg.v1"`；类型全部 pydantic（与 geocompute budgets 同惯例）。
- 维度（spec §6 全量）：cpu_class / memory_bytes / gpu_required / gpu_memory_bytes /
  network_bytes / storage_bytes / feature_count / pixel_count / raster_window /
  context_tokens / output_tokens / estimated_llm_cost / wall_time / io_class /
  browser_required / external_service_calls。
- 每维为 `Certainty` × `Range`：`certainty ∈ {known, estimated, unknown, unavailable}`；
  range 为 `{min, expected, max}`（粗而诚实 > 伪精确）。**unknown 绝不当 0 参与预算判定**
  ——准入策略对 unknown 维走保守档（按该维的 conservative floor 计）。
- 核心类型：`ResourceEstimate` / `ResourceDemand` / `ResourceBudget` /
  `ResourceReservation` / `ResourceUsage` / `ResourceDecision`。

## D5. 准入语义（R3）

- 决策词表：`accept / accept_with_limits / degrade / defer / reject`；
  `defer` = 进程内排队/串行化（诚实：不承诺后台异步），排队上限触发后升格 degrade/reject。
- **模式旋钮**：`GOVERNOR_MODE ∈ {enforce, observe}`，默认 `enforce`；
  `observe` = 全决策降级为日志+指标（rollback 用 kill-switch）。
- **fail-open 纪律**（与 org_quota/rate_limiter 同仓规）：governor 内部任何异常绝不阻断
  dispatch —— 记 `governor_error_total` 后放行。
- 决策输入：session 预算余量（R4）、三层背压水位（R5）、公平队列状态（R6）、
  estimate vs 当前全局在飞、SLO breach 计数（G12）、provider 健康只读视图（R11）。

## D6. 背压与公平（R5/R6）

- 三层：per-session / per-subsystem（raster、browser、export、external、llm 五个封闭类别）/
  global。每层有 heavy/light 双档水位；轻量查询走 small-job bypass 通道，永不被 heavy 队头
  阻塞。
- 公平：加权公平轮转（weight ∝ session 最近消费），aging（等待超阈值权重线性升），
  heavy-task cap（单 session同时在飞 heavy 数上限），per-session 并发上限
  （对齐 `_SessionWaveGate` 语义，不重复造门——governor 在其外层）。
- **拒绝无界等待**：每个队列带 `max_wait_s`；超时 → degrade 或 reject（不再 FIFO 无限等）。

## D7. 降级语义（R7）

- 复用既有降级能力（采样/聚合/粗分辨率/简化几何/interactive-first/deterministic judge/
  local fallback/essential views），governor 只产出 **DegradePlan（建议+理由+语义标注）**，
  执行权在调用方（与 GisBudgetAdvisor「建议不落刀」同一纪律）。
- 每个降级决策必须携带 `semantics ∈ {comparable, approximate, non_comparable}` +
  `degraded_reason`（结构化 reason code，进 evidence/trace）——**绝不偷偷改科学语义**。

## D8. RetryBudget（R10）

- 形态：per-(scope, retry_class) 令牌桶 + 进程级总量上限；暴露
  `retry_allowed(scope, class) -> bool` 与 `retry_charged(scope, class)`。
- 对既有重试点（recon §3.7 全表 ≥10 处）V1 **不逐一改造**（避免热区扩散）；第一接线点为
  governor 自己管理的 dispatch 重试建议与 `RetryPolicy` 消费方；已有 DF reliability /
  geocompute retry 保持 owner 不变，governor 通过记账暴露级联乘数（观测先行）。
- 硬保证：user cancel / session teardown 后该 scope 的 retry token 清零（retry 停止）；
  retry 与 fallback 不双发（degrade 决策排斥同请求 retry 路径）。

## D9. 取消（R9）

- 取消源词表：`user_cancel / session_replaced / plan_invalidated / timeout / client_disconnect`。
- `CancellationCoordinator`：link 到 `app/lib/cancellation.CancellationToken` 主干；cancel 后
  （a）pending 不启动（reservation 取消路径）、（b）reservation 立即 release、
  （c）retry token 清零、（d）cancelled 状态写 ledger（防 stale 提交 MapSpec 的判定依据）。
- V1 不侵入 `tools/registry.py` 的 to_thread 不可抢占语义（#1274 热区；诚实约束留痕）。

## D10. R13-R15 预算面

- Render work budget（R13）：纯函数估工模型（layers/features/labels/pixels/DPI/chart/
  floating 加权），与 V11 quality 轴完全分离；输出 `ResourceEstimate`，供 admission 用。
- Browser/Export slots（R14）：bounded slot 语义挂在 governor 的 subsystem 层
  （browser/export 各自独立 slot + max_wait），dispatch 层按工具分类消费。
- Storage pressure（R15）：只读消费 `artifact_cache` 统计 + org_quota 存储判定，产出
  pressure 等级喂 admission；不新建存储子系统、不建第二份 GC。

## D11. 可观测性（R16）

- Prometheus 指标：封闭标签词表（**无 session-id/tenant 标签**——高基数走结构化日志）：
  admission 决策计数、queue wait 直方图、execution 时长、estimate/actual 误差比、
  retry/fallback 计数、各层在飞水位 gauge、governor 内部错误计数。
- 结构化日志行前缀 `[resource-governor]`，携带 decision/reason codes/estimate 摘要。

## D12. 校准与测试（R17-R19）

- synthetic workload 生成器（vector/raster/acquisition/cartography/export/long-context ×
  small/medium/large/extreme），全部 descriptor/synthetic payload，零真实大数据。
- 校准脚本 `scripts/perf/calibrate_governor.py`：跑 corpus → 输出 provisional 预算
  （`config/governor_budgets.json` 种子 + 证据文件），**不用武断常数起家**。
- Chaos：fake executor 注入 slow/hang/pressure/storm/unavailable/cancel，断言不死锁/
  不雪崩/不泄漏/预算全释放。多 session 压测 1/4/8/16 用 fake executor（不开真 Next/browser），
  产出 p50/p95/queue delay/fairness/starvation 指标。
- 测试纪律：focused pytest `--no-cov`（xdist 不可用则 serial）；迭代期不开 next build +
  full pytest + browser golden 并行。

## D13. 无 DB 迁移、无 Redis 硬依赖

V1 全进程内（threading/asyncio 原语），不新增 migration；跨进程聚合不在 V1 范围
（geocompute 的 cross-process advisory 默认关闭是先例）。理由：先让单进程语义正确 +
可回滚，跨进程是 V2 演进。

## D14. 交付纪律

- 不等待线上 CI；不自动 merge；PR 停在 open 状态。
- master 基线 `580b33e9` 上跑通：scoped tests → governor integration → concurrency →
  perf synthetic → 受影响全域 regression（如触前端则 next build；V1 预计零前端改动）。
