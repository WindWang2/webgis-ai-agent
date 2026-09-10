# ADR 0131 — Platform V4：生产控制面（Extensions 计量 · 统一可观测 · 错误分类学 · 可靠性收敛）

日期：2026-09-11
状态：Proposed（随 feat/platform-v4-production-control 分支交付）
前置：ADR-0104/0105/0128（扩展平台 V1–V3）、ADR-0118（Quality/Reliability/Security V2）、Quality V3（无独立 ADR，落 docs/quality/）、ADR-0046（性能回归 harness）

## 背景

前九个方向把业务能力推进到 V6–V8，同时 Extensions V3 与 Quality V3/SRE
已经建成了大量平台横切能力。Platform V4 的审计（见
`docs/platform-v4/CROSS_CUTTING_AUDIT.md`）定位出五条"最后一公里"缺口：

1. trace 只在 API 进程内传播——Celery worker 侧断裂，跨进程无法并回同一 trace；
2. 无 span 模型——有 ID 传播、无阶段语义与导出面，观测停留在"日志可 grep"；
3. 错误分类三套并存（HTTP 字符串码 / jobs 状态机 / DiagnosticCode），
   Harness/Workflow 想做正确恢复只能解析字符串；
4. worker 下线靠 60s+300s 被动 sweep 收敛，无主动注销与有界 drain；
5. 身份与预算漂移：/health 版本硬编码与 VERSION 文件不一致、无 /version
   端点、perf 预算三处真相不互通、扩展平台零 Prometheus 计量。

## 决策

### D1 统一 Span 面（不引入 OTel SDK）

- `app/lib/observability/spans.py`：封闭阶段词表
  `SpanStage`（user_request/harness/workflow/tool/geocompute/model/
  cartography/artifact/extension），对齐 GisTraceChain 18 阶段的域语义
  但**不替代**它——chain 是业务证据链，span 是基础设施观测树，两面
  各司其职（无第二事实源：span 不落库、不进 chain 注册表）。
- Span 树挂靠 RuntimeContext（trace_id/span_id 已在 Quality V3 W10 落位）：
  `start_span()` 以 ContextVar 保存当前 span，父子关系自动建立，
  `asyncio.to_thread`/task 复制语义免费获得。
- 导出走既有 Sink 纪律：`SpanExporter` 协议 + RingSpanExporter（有界，
  诊断/测试）+ LoggingSpanExporter（结构化日志行）。字段名 OTel-shaped
  （trace_id/span_id/parent_span_id/start_time/end_time/status/attributes），
  未来接 OTel SDK = 新增一个 exporter，事件源不改。
- span 记录永不阻断业务：导出/绑定任何异常静默吞掉（与 emit_chain 同门）。

### D2 跨进程 Trace 传播（Celery headers，零 DB migration）

- 派发侧（`submit_durable_job`）：从当前 RuntimeContext 生成 traceparent +
  关联字段，放进 `apply_async(headers=...)`。headers 是 broker 消息的一部分，
  与幂等键/业务参数正交。
- worker 侧（`durable_job`）：从 `celery_task.request.headers` 恢复
  trace_id/span_id，与 job 行关联字段合并绑定进 RuntimeContext；无 header
  （旧消息/直接调用/eager 模式）行为不变——纯 additive。
- 日志面：`LOG_FORMAT=json` 可选结构化输出（关联字段 + span 字段进 JSON），
  默认保持人类可读文本格式不变。

### D3 错误分类学（封闭词表 + typed 基类，单一生产路径）

- `app/core/errors.py`：`ErrorCategory` 封闭 enum（validation/permission/
  data_unavailable/crs/resource_exhausted/timeout/cancellation/retryable/
  permanent/dependency_failure/worker_lost/model_failure/render_failure/
  storage_corruption），append-only 纪律同 DiagnosticCode。
- `PlatformError` 基类：category、retryable、degraded_hint、user_safe
  message、context（有界 dict）；`classify_exception(exc)` 把任意异常
  映射到 (category, retryable, http_status, user_message)——包括
  OperationCancelled→cancellation、SQLAlchemy 连接错误→dependency_failure
  (retryable)、httpx.TimeoutException→timeout(retryable) 等标准映射。
- `RetryDecision`/`DefaultRetryPolicy`：`decide(exc, attempt) -> RetryDecision`
  给 Harness/Workflow/jobs 一个**基于类型**的重试判定点，不再解析字符串。
- `app/core/exception.py` 接线：响应体 additive 增加
  `category`/`retryable` 字段（PlatformError 才携带；既有响应形状不变），
  日志记录 category——兼容性：老客户端多收到两个字段，无破坏。

### D4 Worker 生命周期与有界 Drain

- `app/services/jobs/worker_lifecycle.py`：进程内注册表
  （worker 身份、状态 online|draining|offline、active 计数）+
  Celery signal hooks（worker_ready → online；worker_shutting_down →
  draining：停止认领新 job 的意愿标记 + 等待在跑任务，受
  `WORKER_DRAIN_TIMEOUT_S` deadline 约束；worker_shutdown → offline）。
  hooks 导入即注册但全部防御性（无 celery 实例/无信号环境时 no-op），
  eager/测试路径可直接调用函数。
- 心跳 + stale sweep（既有兜底）保持不变——主动注销把收敛从
  分钟级降到即时，被动 sweep 仍是唯一真相源。
- `app/main.py` lifespan：drain 阶段落进 deadline 看门狗
  （`SHUTDOWN_DRAIN_DEADLINE_S`，默认 20s；到点记录 warning 并继续关停，
  绝不挂死进程——k8s 的 terminationGracePeriod 是最后防线，但进程自身
  先自觉）。`app_inflight_requests` gauge 由请求中间件维护，drain
  可观测。

### D5 扩展平台计量（有界标签纪律）

- `app/extensions_platform/metrics.py`：`extension_activation_total{result}`
  （result ∈ activated|failed|quarantined|disabled）、
  `extension_quarantine_total{reason}`（reason 封闭词表）、
  `extension_worker_crash_total`、`extension_invocation_duration_seconds`
  （**无 extension_id 标签**——host 级直方图，杜绝高基数；per-extension
  诊断走 host.status_report()）。
- 接线点（host.py）：activate 成功/失败、quarantine、worker crash、
  invoke_model_provider 计时——每处 try/except 包裹，计量失败绝不影响扩展执行。

### D6 性能预算 Manifest（生产观测面）

- `app/lib/observability/budgets.py` + `config/perf_budgets.json`：
  声明式预算（key、limit_s、说明、owner 域）。覆盖目标关键路径：
  agent_first_response / tool_dispatch / data_query / map_render /
  workflow_scheduling / artifact_transfer。
- `observe_budget(key, seconds)`：直方图 `perf_budget_observed_seconds`
  + 超预算 counter `perf_budget_breach_total{budget}`（key 封闭词表，
  manifest 外的 key 拒绝——同 sre_metrics 纪律）+ breach 结构化日志。
- 与测试基线的关系：**不合并**。tests/benchmarks/baselines.json 是 perf
  lane 的持久基线（机器相对语义），perf_budget.py 是测试内联断言助手，
  本 manifest 是**生产运行时观测预算**（SLO 语义）——三者口径不同、
  消费方不同，合并反而制造第二真相源。回归检测 = budget lane 的
  schema/一致性测试 + 既有 perf harness。

### D7 Build 身份与配置导出

- `app/core/build_info.py`：VERSION 文件 + git SHA（env 覆盖 → git 文件
  best-effort）+ python 版本，模块级缓存。
- `GET /api/v1/version`：`{version, commit, python, extensions_api}`，
  极简、无环境细节（防侦察纪律同 SEC-11）；`/health` 的 version 字段
  改从 build_info 取（消除 0.1.3 vs 0.1.0.0 漂移）。
- `scripts/gen_config_schema.py`：pydantic `model_json_schema` 导出
  `docs/quality/generated/config.schema.json` + `--check` 字节闸
  （配置漂移即红）；`scripts/gen_app_inventory.py`：importlib.metadata
  依赖 inventory（可选应用级 SBOM-lite，零新依赖）。

### D8 Chaos 注册表扩展（既有接缝内）

- 新增 3 fault：`ARTIFACT_PARTIAL_WRITE`（publish 拷贝中途失败 → 无半截
  制品可见）、`JOBS_ENQUEUE_FAIL_STORM`（入队连续失败 → 诚实 failed、
  无孤儿 queued 行）、`JOBS_REDELIVERY_STORM`（并发重复认领同一 job →
  恰好一个执行者）。全部走已验证的 monkeypatch/编排接缝，生产零改动；
  注册表文档由 `scripts/gen_chaos_registry.py` 再生（字节闸）。

## 兼容与迁移

- 全部新增面 additive：新模块零调用者即可安全合入；既有响应/日志/行为
  不变（/health version 字段值会从 0.1.3 变为 VERSION 文件值 0.1.0.0——
  这是**修复漂移**，已在测试同步）。
- 无 DB migration、无根 manifest 变更、无依赖版本变更（零新运行时依赖）。
- `apply_async(headers=...)`：Celery 5 标准参数；memory:// broker（eager
  模式）下 headers 语义由 Celery 保持，测试覆盖。

## 性能预算

- span 创建/导出：单 span O(字段数) 内存 + 一次日志行；RingExporter
  默认关闭（显式安装），LoggingExporter 走既有文件 handler。
- 计量/budget 面全部 prometheus-client 原语（既有 /metrics 通道），
  无新增网络调用。
- durable_job headers 恢复：dict.get 数次，纳秒级。

## 风险与回滚

- 每个模块独立成 commit，可单独 revert；无跨模块原子性依赖。
- 回滚后：worker 无主动注销（回到被动 sweep 现状）、trace 跨进程断裂
  回归——均为旧行为，无数据风险。

## 与并行方向的边界

- 拥有：observability 新增面、core 错误分类、jobs worker 生命周期、
  extensions 计量、health/version、config 导出、chaos 注册表。
- 极谨慎触碰：`app/core/exception.py`（additive 两字段）、
  `app/services/jobs/submit.py`（additive headers）、
  `app/services/jobs/worker.py`（additive header 恢复）、
  `app/main.py`（additive deadline 看门狗 + inflight gauge）。
- 不触碰：Harness planner、Workflow 调度算法、Science 算法、
  Cartography layout、Workbench UI、DB migrations、根 manifests。
