# 运维控制台 UI V9 —— P0 勘察报告（ops-console-recon）

> 线：`feat/ops-console-v9`（ADR-0142）。基线 `origin/master@8b5b8375`（2026-09-11）。
> 本文是 P0 勘察产出：端点契约表（字段逐字来自 master 后端源码）、死代码审查、指标语义核对、复用边界、以及与本线任务书假设不一致处的事实修正。

---

## 0. 复核纪要（§0.2 产出）

### 0.1 PR / issue / 分支重叠核查

| 检查 | 命令/对象 | 结论 |
| --- | --- | --- |
| PR 历史（200 条，关键词 geocompute/cluster dashboard/runtime inspector/ops/observability UI） | `gh pr list --state all` | 相关合入 PR 全部是**后端线**：#1196（GeoCompute V8）、#1190（workflow-v6 durable cluster + Inspector API）、#1194（platform-v4 统一可观测/错误分类学）、#1163（cluster runtime V6）。**无任何运维控制台前端 PR**，无在开重叠分支（`git branch -a` 对 ops/console/inspector/dashboard 零命中） |
| issue #1213（必查） | vector-pdf 死接口 | 教训采纳：**后端已有观测能力必须全部对齐到产品面**。本线 P2 的 typed client 契约表逐一对照后端路由文件全量端点，不留「能而不见」 |
| issue #1211（必查，已关闭） | Driver 孤儿恢复同步 store 写 | 抽查 `app/services/workflow_runtime/driver.py` `_run_loop`：已全部经 `asyncio.to_thread` 卸载（`get_instance`/`release_run_lease` 均 to_thread）。确认已修，无残留 |
| 死代码现状 | `grep runtime-inspector` | `components/sidebar/workflow/runtime-inspector.tsx` 仍**零非测试引用**（仅 `runtime-inspector.test.tsx` 引用）。本线 P1 负责「接线复活」而非重写 |
| `lib/api/geocompute.ts` 现状 | 文件头 + 全文 | 仅 `runs/{id}/events` + `runs/{id}/cancel` 两个调用；`/cluster/metrics`、`/cluster/workers`、`/cluster/runs/stuck`、`/plans/*`、`/runs` 列表、reset、ledger、drift-check 均未接。与任务书描述一致 |
| admin/健康面板存在性 | `grep -rn "health\|/metrics\|/version" frontend/lib frontend/components` | 无任何 `/health`、`/version`、`/metrics` 前端消费方（组件层命中均为无关子串）。确认无重叠 |

**结论：无重叠，照单执行。**

### 0.2 与任务书假设不一致的事实（及本线处置）

1. **仓库无 msw**（package.json 无依赖，全仓 `msw|setupServer|HttpResponse` 零命中）。仓内测试纪律是 **vitest + 显式 fetch stub / vi.fn() 模块 mock**。
   处置：不引入 msw 新依赖（10 线并发下 `package.json`/lockfile 是高冲突共享面，且违背仓内既有约定）。改为 `frontend/test/fixtures/**` 纯 TS fixture 库 + `frontend/test/fixtures/api-stub.ts` 零依赖路由表式 fetch 拦截器（msw 等价物：每端点 正常/空/错误 三态 handler + 60 分钟合成指标序列）。任务书所称「msw fixtures / 验收旅程（msw 模式）」一律以此等价机制满足，记入 PR 描述。
2. **不存在 workflow rail tab**：nav rail 词表是 `chat/project/data_sources/layers/components/analysis/tasks/results/export_layout`，workflow-runtime V5 的 UI 从无挂载点。任务书 P1 给的两个候选挂载点之一（「workflow tab 内切换」）不成立。
   处置（ADR-0142 D1）：新建 **ops rail tab「运维」**（`LeftTab` union、`MODE_TABS` 三模式词表、`RAIL_GROUPS`、`context-panel` 分支各一行 **append-only**，与 D/F/H/J 线同规则），控制台本体全部在 `frontend/components/sidebar/ops/**` 新目录。
3. **`system-settings.tsx` 不是 tabs 容器**，是单块只读面板；`SettingsTab` 词表已有 `'system'`（设置面板 → 系统 → SystemSettings）。
   处置：P5 的「settings 内新 tab」落在 SystemSettings 组件内部——**append 一个 tab 入口**（「常规 / 集群健康」二段式 tab strip），不改 settings-panel.tsx 的 NAV_ITEMS（避免与 G 线文案键冲突）。
4. **geocompute events 是轮询 JSON（`after_id` 游标），不是 SSE**。P4 进度视图用游标轮询通道（复用 use-job-center 纪律模式），不引入 SSE。`use-sse-stream.ts` 是聊天主会话专用重 hook，**不复用**（ADR-0142 D3）。
5. **错误分类 top-N：无 HTTP 端点**（failure_taxonomy/RemediationLedger 是进程内组件，无路由暴露；`/api/v1/metrics/digest` 是工具耗时指标非错误分类）。P5 按任务书显示「能力待后端支持」诚实空态，记协调点。
6. **断路器无独立披露端点**：`engine_breaker` 披露只内嵌在 federation 查询响应载荷中（v6 引擎失败回退 v5 时注入）；`result_cache` 披露只内嵌在缓存命中响应中。`stats()` 无 HTTP 端点。P6 面板按「最近一次披露」客户端留存 + 诚实空态实现（ADR-0142 D5），三态视觉在 fixture 驱动下呈现。
7. **`/healthz` 不存在**；健康面实际形状：`/api/v1/health`（无认证）、`/health/live`、`/ready`（503 body 极简）、`/status/detailed`（JWT，组件级 db/redis/llm/worker/object_store）、`/api/v1/version`（无认证）、`/metrics`（Prometheus 文本、无应用层 token 门禁、不出 OpenAPI）。P5 按此形状适配。
8. **cluster 读面全部 `require_admin`**：403 是一等公民状态，控制台必须给出「需要管理员权限」诚实态而非死按钮。
9. **ADR watermark**：master `docs/adr/` 最大编号 0137，**0142 未被占用**（0138–0141 留给并发线），本线按约使用 ADR-0142。

---

## 1. 路由挂载总表（`app/main.py` L641-682）

| Router 文件 | router 自身 prefix | 真实前缀 |
| --- | --- | --- |
| `app/api/routes/geocompute.py` | `/geocompute` | `/api/v1/geocompute/*` |
| `app/api/routes/workflow_runtime.py` | `/workflow-runtime` | `/api/v1/workflow-runtime/*` |
| `app/api/routes/health.py` | 无 | `/api/v1/health*`、`/api/v1/ready`、`/api/v1/status/detailed` |
| `app/api/routes/version.py` | 无 | `/api/v1/version` |
| `app/api/routes/metrics.py` | 无 | `/api/v1/metrics/digest` |
| `app/api/routes/jobs.py` | `/tasks/jobs` | `/api/v1/tasks/jobs*` |
| `app/api/routes/data_fabric.py` | 无 | `/api/v1/data-fabric/*` |

限流：全局 240 req/min；豁免前缀 `/api/v1/layers/data/`、`/api/v1/health`、`/api/v1/local-data/`。**轮询间隔纪律 ≥3s**，与限流预算相容。

---

## 2. geocompute 端点契约表（P2 typed client 勾选底稿）

### 2.1 `POST /api/v1/geocompute/plans/validate`（可选认证）
- Body：`ExecutionPlanIn`
- 200 → `{plan_id, graph_fingerprint, node_fingerprints: {node_id: str}, waves: list, wired_categories: list[str]}`
- 422 → `detail = GeoComputeError.to_dict()`

plan JSON schema（`ExecutionPlanIn`）：
```
plan_id: str
nodes: list[ExecutionNodeIn] = []
budget: dict[str, Any] = {}
description: str | None = None
```
`ExecutionNodeIn`：`node_id: str`、`category: str`、`operation: str = ""`、`inputs: list[str] = []`、
`dataset_fingerprints: dict[str,str] = {}`、`parameters: dict[str,Any] = {}`、`crs: dict|None`、
`estimate: dict|None`、`policy: str = "in_process"`、`reuse: str = "allow"`、`retry: dict = {}`、
`deadline_s: float|None`、`cancellable: bool = True`、`locality_hint: str|None`、`description: str|None`、
`lineage_inputs: list[{ref_id, kind}] = []`（截断 ≤16）

### 2.2 `POST /api/v1/geocompute/plans/execute`（强制认证）
- Body：`{plan: ExecutionPlanIn, session_id: str|None}`
- 200 → `ExecutionRun`：`{run_id, plan_id, plan_fingerprint, status: "pending|running|completed|failed|cancelled|preempted", wall_time_s, error_code, error_message, source: null|"snapshot"|"cluster", evidence: {node_id: NodeEvidence}, lineage: list, reproducibility: dict}`
- `NodeEvidence`：`status: "pending|ready|running|completed|reused|failed|cancelled|skipped"`、`attempts`、`duration_s`、`rows_emitted`、`bytes_emitted`、`output_ref`、`output_summary`、`error_code`、`error_message`、`retry_safe`、`fingerprint`、`policy`、`failure_codes[≤4]`、`checkpoint_verified`
- 422 校验/BudgetExceeded；500 GeoComputeError；陌生 session → 404

### 2.3 `POST /api/v1/geocompute/plans/runs`（V6 cluster 提交，202）
- Body：`ExecutePlanRequest` + `priority: int = 5`（coerce {0,5,10}）、`project_id: str|None`、`resource: ResourceRequest|None`
- `ResourceRequest`：`min_mem_mb ≤2_097_152`、`min_cpu ≤1024`、`gpu ≤8`、`zone`（pattern `^[A-Za-z0-9_.-]{1,64}$`）、`required_profiles ≤8`、`fallback_cpu: bool`
- 202 → `{run_id, status, plan_fingerprint, required_profiles, resource, source: "cluster"}`
- 413 PlanSnapshotTooLarge（快照 ≤256KB）；429 ClusterBackpressure（header `Retry-After: 5`）；422 `PLAN_INVALID|RESOURCE_REQUEST_INVALID`
- profile 词表 `EXECUTION_QUEUE_PROFILES`：`light_cpu, heavy_cpu, high_memory, raster, network, external_io`

### 2.4 `GET /api/v1/geocompute/runs`（owner 隔离）
- Query：`status`（逗号分隔多值）、`limit=50`（≤100）、`offset=0`
- 200 → `{runs: ScanProjection[], terminal_snapshots: [{run_id, status, source: "snapshot", created_at}], limit, offset}`
- `ScanProjection`：`run_id, status, owner_scope, plan_fingerprint, session_id, priority, attempts, preempts, lease_epoch, cancel_requested_at, yield_requested_at, error_code, required_profiles, resource_request, created_at, started_at, terminal_at, id, tenant_key, coordinator_id, dispatch_seq, heartbeat_at, lease_expires_at`
- 503 `CLUSTER_UNAVAILABLE`

### 2.5 `GET /api/v1/geocompute/cluster/metrics`（require_admin）
503 `CLUSTER_METRICS_UNAVAILABLE`；200 → `ClusterMetrics.snapshot()` 全字段：

```
runs_by_status: {queued|leased|running|completed|failed|cancelled|preempted: int}
queue_depth: int                     # queued+preempted
inflight: int                        # leased+running
completed / failed / cancelled: int
preempted_total / lease_loss_total: int
cancel_latency: {p50_s, p95_s, samples}
queue_wait:     {p50_s, p95_s, samples}
waiting_by_profile: {profile: int}           # ≤256 行扫描
events_counters: {event_rejected_invalid, event_budget_exhausted_total, event_append_failed_total}
workers: {live, by_role: {worker|coordinator}, profile_slots: {profile: int}, gpu_workers}
leader: {count, ids: list[str](≤8)}
ledger: [{scope_key: "global|t:*|p:*", ...usage/limit}]   # ≤20 scope
resource_rejections: {rows, bytes, units, mem_mb, gpu}
oom_avoided: int
gpu_fallbacks: int
spill: {count, bytes, rehydrate_hits, rehydrate_misses}
# —— ADR-0133 五类（V8 Phase H）——
transfer: {bytes_total}                      # events.bytes 24h 窗口和
cache: {worker_cache_hits}
lineage: {node_completed, node_reused, node_lost, partition_planned, speculative_dispatched, poison_quarantined}
utilization: {reserved_units, capacity_units, ratio: float|None}
quarantine: [{owner_scope(伪名化), fingerprint[:16], failure_count, active, last_error_code}]  # ≤10
```

### 2.6 `GET /api/v1/geocompute/cluster/workers`（require_admin，≤256 行）
200 → `{workers: [...], live}`；每 worker：`worker_id, role("worker"|"coordinator"), profiles: {profile: slots}, capability: dict|None（旧 worker 诚实 null）, heartbeat_age_s: float, cache_entries: int, cache_bytes: int`；503 `CLUSTER_UNAVAILABLE`

### 2.7 `GET /api/v1/geocompute/cluster/runs/stuck`（require_admin，≤50）
200 → `{runs: ScanProjection[], count}`（占用 lease 且 `lease_expires_at < now-5s`）；503

### 2.8 `POST /api/v1/geocompute/cluster/runs/{run_id}/reset`（require_admin）
- Body：`{reason: str = "admin reset"}`（≤200）或空
- 200 → `{run_id, outcome: "requeued"|"failed", status, attempts}`；409 `RUN_NOT_RESETABLE`；503

### 2.9 `POST /api/v1/geocompute/cluster/ledger/limits`（require_admin）
- Body：`{scope_key（pattern ^(global|[tp]:[A-Za-z0-9_-]{1,76}$）, limit_rows|limit_bytes|limit_units|limit_mem_mb|limit_gpu: int≥0|None}`（None=解除）
- 200 → 回显 6 字段；500 `LEDGER_LIMITS_FAILED`；503

### 2.10 `GET /api/v1/geocompute/runs/{run_id}`（强制认证）
cluster 行命中 → `{run_id, plan_fingerprint, status, source: "cluster", priority, attempts, preempts, error_code, cancel_requested_at, required_profiles, created_at, started_at, terminal_at, progress?: {settled, done, failed, total}}`（内存命中 → 2.2 的 ExecutionRun 投影）；404 `RUN_NOT_FOUND`；503

### 2.11 `GET /api/v1/geocompute/runs/{run_id}/summary`
200 → `{lines: list[str]}`；404

### 2.12 `GET /api/v1/geocompute/runs/{run_id}/events`（轮询 JSON，非 SSE）
- Query：`after_id=0`（游标）、`limit=200`（≤200）
- 200 → `{run_id, events: EventProjection[], after_id（=末条 id 或入参）, count}`
- 事件：`{id, run_id, event, node_id|None, worker_id|None（伪名化 "w-"+sha1[:12]）, attempt|None, status|None, rows|None, bytes|None, error_code|None, created_at}`
- **EVENT_VOCABULARY（21 值封闭词表）**：`run_started, node_dispatched, node_started, node_output_ready, node_completed, node_reused, node_failed, node_cancelled, node_lost, run_completed, run_failed, run_cancelled, run_preempted, waiting_resource, worker_cache_hit, straggler_detected, gpu_fallback, partition_planned, speculative_dispatch, speculative_resolved, poison_quarantined`
- run 行被 retention 清理后 **404**（诚实披露「事件不可用」）；503

### 2.13 `POST /api/v1/geocompute/runs/{run_id}/cancel`（双别名：`POST /plans/runs/{run_id}/cancel` 同 handler）
内存命中 → `{run_id, cancelled, status, source}`；cluster → `{run_id, cancelled, requested, status, source: "cluster"}`；快照幂等 → `{cancelled: false}`；404；503

### 2.14 `POST /api/v1/geocompute/plans/drift-check`（强制认证）
- Body：`{stored: dict, plan?: ExecutionPlanIn}`
- 200 → `{state: "current"|"stale_runtime"|"degraded_plan"|"unknown", reason, stored_plan_fingerprint, current_plan_fingerprint, stored_runtime_fingerprint, current_runtime_fingerprint}`

---

## 3. workflow-runtime 端点契约表（13 端点，全部强制认证）

错误统一 `detail = "{CODE}: {detail}"[:300]`（**typed client 需解析错误码前缀**）；owner 隔离：他人/未知 → 404 `{"detail": "not found"}`。

词表（`workflow_runtime/contracts.py`）：
- NodeState：`PENDING, READY, RUNNING, SUCCEEDED, FAILED, BLOCKED, SKIPPED, CANCELLED, STALE`
- InstanceStatus：`running, succeeded, failed, cancelled, superseded`
- journal kind：`state_transition, instance_cancel_requested, node_cancel_requested, recovery_orphan_reset, recovery_finalize, retry_scheduled, retry_exhausted, compensation, compensation_failed, clone, nodes_requeued`
- 错误码：`PACKAGE_NOT_FOUND, INSTANCE_NOT_FOUND, INSTANCE_BUSY, NODE_NOT_FOUND, NODE_NOT_IN_DAG, NODE_NOT_RETRYABLE, RETRY_EXHAUSTED, NO_TARGET_NODES, INSTANCE_NOT_TERMINAL, PACKAGE_CONFLICT, COMPILE_INPUT_REJECTED`

| # | 端点 | 请求 | 成功响应 | 错误 |
|---|---|---|---|---|
| 1 | `POST /packages/register` | `{query: str(1..400), recipe_id(≤64)="", project_id(≤255)="", profile: dict|None（≤32KB/深≤8）}` | `{success: true, package: …}` | 422 `COMPILE_INPUT_REJECTED`；409 `PACKAGE_CONFLICT` |
| 2 | `POST /packages/{id}/publish` | `{version: str(1..16)}` | `{success, package}` | 404 |
| 3 | `GET /packages` | — | `{success, packages: rows}` | — |
| 4 | `GET /packages/{id}/versions` | — | `{success, versions}` | 404（空也 404） |
| 5 | `POST /instances` | `{package_id(1..64), version(≤16)="", session_id(≤255)="", project_id(≤255)=""}` | `{success, instance}` | 404 `PACKAGE_NOT_FOUND` |
| 6 | `POST /instances/{id}/run` | `{deadline_s=60.0, gt=0, le=300}` | `{run: Driver.run 摘要, instance}` | 409 busy；404/422 |
| 7 | `POST /instances/{id}/cancel` | — | `{cancelled, status}`（终态幂等） | 404 |
| 8 | `POST /instances/{id}/changes` | `{changes: [{dimension(1..24), target_kind(1..24), target(≤64), detail(≤200)}](1..N), dry_run=false}` | dry_run → recompute-plan 形状；否则 `{applied, deferred, reason?}` | 409 busy；404 |
| 9 | `GET /instances/{id}` | — | `{success, instance: 投影+explain}` | 404 |
| 10 | `GET /instances/{id}/recompute-plan` | — | `{success, instance_id, stale, counts, decisions}` / 变体 `{style_only, recompute, reuse, would_mark_stale, explanations[≤6], changed_dimensions}` | 404 |
| 11 | `GET /instances` | — | `{success, instances: rows}`（≤32/次） | — |
| 12 | `GET /instances/{id}/events` | Query `limit=100`(≤200)、`after_id=0`、`kind=""(≤40)` | `{success, instance_id, events: [{id, node_id, kind, from_state, to_state, reason, actor, attempt, payload, created_at}]}` | 404 |
| 13 | `GET /instances/{id}/nodes/{node_id}` | — | `{success, instance: {instance_id, status, run_lease_owner, run_lease_expires_at}, node: row}` | 404 |
| 14 | `POST /instances/{id}/nodes/{node_id}/retry` | Query `force=false` | `{node, state: "READY", attempts, force}` | 409 busy/`RETRY_EXHAUSTED`/`NODE_NOT_RETRYABLE`；404 |
| 15 | `POST /instances/{id}/nodes/cancel` | `{node_ids(1..16), include_descendants=true}` | `{requested, flagged}` | 404；422 `NODE_NOT_IN_DAG` |
| 16 | `POST /instances/{id}/clone` | `{session_id?, only_nodes?(≤16), skip_nodes?(≤16)}` | 新实例投影 | 404；409 `INSTANCE_NOT_TERMINAL`；422 |
| 17 | `GET /instances/{id}/debug` | — | `{success, instance: 投影+explain, recent_events(≤100), children[≤32]}` | 404 |

`instance_projection`（`projection.py` 逐字）：`instance_id, package_id[:64], package_version[:16], package_fingerprint[:64], status, revision, cancel_requested: bool, nodes[], counts: {STATE: n}, decisions[≤16], pending_changes[≤16], error_code, error_detail`，可选 `methodology_family[:40], compiler_version[:16]`。节点投影：`node_id[:64], state, attempts, error_code[:64], bound_ref[:96], output_ref[:96], reused: bool, reuse_evidence: dict, binding_violations: [code[:48]](≤8)`。`explain`：`{why_recomputed[≤8], why_reused[≤8], blocked: [{node, codes}](≤8)}`。

---

## 4. 健康 / 版本 / 指标端点

| 端点 | 认证 | 响应 |
| --- | --- | --- |
| `GET /api/v1/health` | 无 | `{status: "healthy", timestamp, service: "WebGIS AI Agent", version, agent_runtime: "pi"|"chatengine", pi_workers_alive: "alive/total"|null}` |
| `GET /api/v1/health/live` | 无 | `{status: "alive"}` |
| `GET /api/v1/ready` | 无 | 200/503 `{ready: bool}` |
| `GET /api/v1/status/detailed` | JWT | 200/503 `{status: "ok"|"degraded"|"down", components: {db|redis|llm|worker|object_store: {status, latency_ms, detail}}, stuck_jobs, refresh_age_s}`（TTL 10s 缓存） |
| `GET /api/v1/version` | 无 | `{version, commit, python, extensions_api, timestamp}` |
| `/metrics`（根路径） | 无应用层门禁 | Prometheus 文本（`include_in_schema=False`，NetworkPolicy 白名单兜底） |
| `GET /api/v1/metrics/digest` | require_admin | `{success, tool_metrics: {p50/p90/p95/p99/max_ms, count, result_bytes}, spatial_cache, harness_enabled, harness_metrics}` —— **非错误分类** |
| `/healthz` | — | **不存在** |

---

## 5. data-fabric 断路器与缓存披露（P6 数据源现状）

- **无独立披露端点**。`EngineFallbackBreaker.disclosure()`（`app/services/data_fabric/fabric/engine_breaker.py` L125-134）字段：`state: "closed"|"open"|"half_open"`、`consecutive_failures`、`failure_threshold`（默认 3，env `DATA_FABRIC_V8_ENGINE_BREAKER_THRESHOLD`）、`cool_down_s`（默认 60.0）、`total_fallbacks`。
- 注入点：`federation.py` L1969/L2073 —— 仅当 v6 引擎非 typed 崩溃回退 v5 时，查询响应携带 `engine: "v5_fallback"` + `engine_breaker` 块。
- 结果缓存披露（`fabric/result_cache.py`）：本地命中 `{hit: true, age_s, ttl_s, basis: "ttl+fingerprint", key[:16]}`；分布式命中 `{..., basis: "ttl+fingerprint+distributed", age_s: None}`；`stats()`（entries/hits/misses/…）**无 HTTP 端点**。
- 结论（ADR-0142 D5）：P6 面板做**披露留存型**只读面板——前端每次收到 data-fabric 查询响应时留存 `engine_breaker`/`result_cache` 披露（时间戳+载荷），面板展示「最近一次披露」；从未收到披露时显示诚实空态。三态视觉由 fixtures 驱动呈现。**协调点：请求后端补独立 breaker/cachestats 只读端点。**

---

## 6. 错误分类 top-N 与队列深度口径

- 错误分类 top-N：**无端点**（`failure_taxonomy.py`/`RemediationLedger` 进程内；unified_findings 随 completion 载荷；无路由引用）。→ P5 诚实空态卡 + 协调点。
- durable 队列深度近似口径（P5 卡片标注口径）：
  - 全局：`/geocompute/cluster/metrics` 的 `queue_depth` / `inflight` / `waiting_by_profile`（require_admin）
  - owner 域：`/tasks/jobs?active_only=true` 的 jobs 计数（`{jobs: JobView[], has_active, poll_after_ms}`）
  - `/status/detailed` 的 `stuck_jobs`（≤100 有界）
  - 配额端点**不存在**（ledger 限额只有写入侧，读侧仅 cluster/metrics.ledger ≤20 scope）

---

## 7. 前端复用边界

| 资产 | 复用方式 | 边界 |
| --- | --- | --- |
| `lib/api/transport.ts` | `apiFetch<T>`（401 一次刷新重试、`ApiError{status,body,retryable}`、`openStream`） | typed client 全部走它；错误码从 `ApiError.body.detail` 字符串前缀解析（workflow-runtime）或 `body.code`（geocompute） |
| `lib/hooks/use-job-center.ts` 纪律模式 | 有界轮询六纪律：无活跃→0 轮询 / 隐藏→暂停+可见补拉 / 连错≥3 停 / generation+epoch 陈旧丢弃 / abort 清理 / `poll_after_ms` 服务端建议 | 新 `use-cluster-*.ts` hooks 逐条复刻；轮询间隔 ≥3s（限流预算 240 req/min） |
| `use-sse-stream.ts` | **不复用**（聊天主会话专用，位置参数重签名） | geocompute 事件是游标轮询 JSON，无 SSE 通道 |
| `components/chat/chart-core.tsx` | `ChartCore({chart: ChartData})`，`type:'line'|'timeseries'|'bar'|'kpi_card'`；主题 token 经 `themeColor()`（data-theme/.dark 感知） | 指标时序图喂 `ChartData{type:'line', series}`；图表 chrome（卡片/标题/时间范围选择器）由 ops 面板自组 |
| `status-badge / empty-state / loading-state / inline-notice / section-title` | 直接复用 | 断路器三态用 `StatusBadge` + 自绘状态环 |
| `test/visual/capture.mjs`（Playwright） | SURFACES 增补 ops 面（最终门禁一次） | 明/暗主题截图取证 |
| vitest 视觉 | 组件三态 DOM snapshot（breaker closed/open/half_open） | 不引入图片 diff 新依赖 |

## 8. runtime-inspector.tsx 死代码审查（P1 接线依据）

- props：`{instanceId: string; fetcher?: (instanceId) => Promise<RuntimeInstance>}`，数据获取全由宿主注入；无 fetcher 时渲染「加载运行时」按钮（点击进 loading 后无下文）。
- 数据形状与后端 `instance_projection` **子集对齐**，无契约漂移；`STATE_BADGE` 九态与后端 NodeState 词表一一对应。
- 漂移/缺口：① 只读，未消费 V6 干预端点（retry/cancel/clone/debug）；② 无实例发现能力（宿主须给 instanceId）；③ `explain` 只在 `GET /instances/{id}` 与 `/debug` 附带，fetcher 必须打这两个之一；④ `binding_violations` 是 `code[:48]` 截断数组，组件取 `[0]`。
- 测试注入：`fetcher = vi.fn().mockResolvedValue(instance)`，三用例 happy/error/empty —— **保留并新增挂载路径测试**。
- 接线方案（ADR-0142 D2）：ops 控制台「运行时」分区内 `GET /workflow-runtime/instances` 列表 → 选中实例 → `fetcher = (id) => getWorkflowInstance(id)`（带 explain）注入 `RuntimeInspector`；周边补 V6 干预动作（node retry / instance cancel / debug 面板）。组件本体保持最小修改（复活的钩子在外部，契约不动）。

## 9. ADR-0142 决策索引

- D1 挂载点：新 ops rail tab（append-only 注册），控制台在 `components/sidebar/ops/**`。
- D2 runtime-inspector 复活：实例列表 + fetcher 注入（`getWorkflowInstance`），保留原测试。
- D3 数据通道：游标轮询 JSON（`after_id`），不引 SSE；轮询纪律复刻 use-job-center 六条。
- D4 fixture 策略：零依赖 `test/fixtures/api-stub.ts` 路由表拦截器等价 msw；三态 + 60 分钟合成序列。
- D5 断路器/缓存：披露留存型只读面板 + 诚实空态；协调点请求独立端点。
- D6 健康面形状：`/health` + `/version` + `/status/detailed`（无 `/healthz`）；队列深度双口径标注（全局 admin / owner 域）。
- D7 错误分类 top-N：诚实空态卡（#607 原则），协调点。
