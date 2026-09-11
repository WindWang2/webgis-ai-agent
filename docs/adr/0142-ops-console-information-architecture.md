# ADR-0142: Ops Console V9 — 运维控制台信息架构与数据通道决策

## 状态（Status）

Accepted（2026-09-12，与 feat/ops-console-v9 同枝落地；P0 勘察依据见 `frontend/docs/ops-console-recon.md`）

## 背景（Context）

Geocompute V8（ADR-0133：分区/溢出/重水化记账、投机窗口、五类观测指标
transfer/cache/lineage/utilization/quarantine、毒任务检疫）与 Workflow V6
durable 集群运行时（两级租约/journal/恢复/重试 + Inspector API）后端均已
就绪，但运维视角前端几乎不可见：

1. `components/sidebar/workflow/runtime-inspector.tsx` 已写好但零生产调用方
   （仅测试引用，fetcher 注入式）——死代码。
2. 集群健康（workers 心跳/利用率/检疫）、stuck runs 干预、plan 校验/提交/
   执行——只能 curl；cluster 读面全部 `require_admin`。
3. `/health`、`/version`、`/status/detailed`、`/metrics` 无任何前端消费方。
4. fabric engine breaker 披露（`engine_breaker`）只内嵌在 federation 查询
   响应载荷，无独立端点；结果缓存披露同理。

约束：本线纯前端（禁改 `app/**` 与 `migrations/**`）；10 线并发，共享
共享文件只允许 append-only 注册行；仓库无 msw，测试纪律是 vitest + 显式
stub。#1213（vector-pdf 死接口）教训入线：**后端已有观测能力必须全部对齐
到产品面**；#607 教训入线：无生产者的面板必须诚实空态，禁止假数据。

## 决策（Decision）

### D1 信息架构与挂载点

运维控制台是独立信息面（集群 / 计划 / 运行时 / 系统 / 断路器 / 大屏值守），
不适合塞入任何既有 tab（「workflow tab」并不存在——nav rail 词表是
chat/project/data_sources/layers/components/analysis/tasks/results/export_layout）。

- 新增 rail tab `ops`「运维」（`LeftTab` union、`MODE_TABS` 三模式词表、
  `RAIL_GROUPS`、`context-panel` 渲染分支各**一行 append-only**，与
  D/F/H/J 线同规则）。
- 控制台本体全部落在 `frontend/components/sidebar/ops/**` 新目录；
  `components/sidebar/workflow/` 内只动 `runtime-inspector.tsx`（接线所需
  的最小增量）。
- P5 系统健康落在设置面板 `system` tab（`SystemSettings`）内部：append 一个
  tab 入口（「常规 / 集群健康」二段式），不改 `settings-panel.tsx` 的
  NAV_ITEMS（与 G 线文案键零接触）。

### D2 runtime-inspector 复活（重接线，非重写）

组件本体契约不动（props/数据形状与后端 `instance_projection` 子集对齐，
勘测确认无漂移）。复活方式：ops「运行时」分区
`GET /workflow-runtime/instances` 实例列表 → 选中实例 → 注入
`fetcher = (id) => getWorkflowInstance(id)`（`GET /instances/{id}` 自带
`explain`；`/recompute-plan` 不带 explain，不可作 fetcher）。周边补 V6
干预动作（node retry / instance cancel / debug 面板）。原三用例测试保留。

### D3 数据通道：游标轮询 JSON，不引 SSE

勘测事实：`GET /geocompute/runs/{id}/events` 是 `after_id` 游标轮询 JSON
（页上限 200），workflow-runtime events 同为 `after_id` 游标；全子系统
无 SSE 观测通道。`use-sse-stream.ts` 是聊天主会话专用 hook（位置参数重
签名），不复用。

轮询纪律逐条复刻 `use-job-center.ts` 六条：① 无活跃对象→0 轮询；② tab
隐藏→暂停、可见→立即补拉；③ 连续失败 ≥3 → 停止并暴露错误；④
generation+epoch 双守卫丢弃陈旧响应；⑤ 卸载/切换 abort 在飞请求；⑥ 尊重
服务端建议间隔。全局轮询间隔下限 3s（后端限流 240 req/min 预算内）。

### D4 fixture 策略：零依赖路由表拦截器（msw 等价物）

仓库无 msw（勘测核实），10 线并发下不引入新依赖（package.json/lockfile
是高冲突共享面）。`frontend/test/fixtures/api-stub.ts` 提供路由表式 fetch
拦截：`stubApi(routes)` → 每端点 `ok / empty / error` 三态 handler +
`metric-series.ts` 60 分钟合成指标序列生成器。验收旅程（集群 → stuck →
干预 → 回执；校验 plan → 提交 → 瀑布）以 vitest 集成测试呈现。合成序列
只存在于 `test/`，生产 bundle 零引用（§6 自审项）。

### D5 断路器与缓存面板：披露留存型只读视图

勘测事实：`engine_breaker.disclosure()`（state/consecutive_failures/
failure_threshold/cool_down_s/total_fallbacks）与 `result_cache` 披露
（basis=ttl+fingerprint[+distributed]）只内嵌于 data-fabric 查询响应载荷，
无独立轮询端点，`stats()` 无 HTTP 面。

前端建 `data-fabric-disclosure.ts` 留存模块：收到 data-fabric 响应即留存
披露快照（载荷+时间戳，LRU 有界）。面板展示「最近一次披露」；从未收到
披露 → 诚实空态（标注数据通道与协调点）。closed/open/half_open 三态视觉
由 fixtures 驱动呈现并做 DOM 快照。**协调点：请后端补独立 breaker/cache
stats 只读端点（可挂 require_admin），届时面板切换为轮询。**

### D6 系统健康数据面（按 master 实际形状）

`/healthz` 不存在，采用：`/api/v1/health`（无认证基础卡）+
`/api/v1/version`（构建/扩展 API 信息）+ `/api/v1/status/detailed`
（JWT，组件级 db/redis/llm/worker/object_store + stuck_jobs）。
durable 队列深度**双口径明确标注**：全局=cluster/metrics 的
queue_depth/inflight/waiting_by_profile（require_admin）；owner 域=
`/tasks/jobs?active_only=true` 计数。错误分类 top-N 无端点 → 诚实空态卡
（#607 原则），协调点。

### D7 权限态是一等公民

cluster 读面全部 `require_admin`：403/401 不是错误分支而是常态 UI 态
（「需要管理员权限」诚实态 + 引导），面板永不因权限缺失渲染死按钮。

## 事实源边界

- 端点契约唯一依据：master `app/api/routes/geocompute.py`（835 行）、
  `app/api/routes/workflow_runtime.py`、`app/api/routes/health.py`、
  `version.py`、`metrics.py`、`jobs.py`、`app/services/data_fabric/**`。
  全表见 `frontend/docs/ops-console-recon.md`（字段逐字）。
- 前端不复制后端词表之外的状态值；EVENT_VOCABULARY（21 值）、NodeState
  （9 值）、breaker 三态均为封闭词表映射。

## 兼容与回滚

纯增量：新目录 `components/sidebar/ops/**`、新 `lib/api/workflow-runtime.ts`、
新 `lib/hooks/use-cluster-*.ts`、新 `test/fixtures/**`、新
`docs/adr/0142-*`；存量改动仅 `lib/api/geocompute.ts`（扩展，原导出不动）、
`runtime-inspector.tsx`（接线最小增量）、`system-settings.tsx`（append tab
入口）、4 处 rail 注册 append-only 行。回滚 = revert 单 PR。

## 风险

- 共享注册行（LeftTab/MODE_TABS/RAIL_GROUPS/context-panel）与并发线冲突 →
  逐行 append、rebase 按 §8 契约解。
- require_admin 面板在非管理员下大面积空态 → D7 诚实态 + 文案引导，不视为
  缺陷。
- 断路器留存面板的披露新鲜度依赖用户实际发起联邦查询 → 面板标注「最近一次
  披露」时间戳；独立端点落地后切轮询（协调点）。
