# F08 — Workflow Resource Scheduler Convergence：Recon（执行时基线）

- 执行时间：2026-09-26
- 基线：`origin/master = 9e1ad22907e99cd7b4721153294448ce6496c717`（2026-09-24 05:00 +0800，merge #1494）
- 分支/worktree：`zcode/f08-workflow-resource-scheduler-20260926-9e1ad229` @ `../wt-webgis-f08-workflow-resource-scheduler-20260926-9e1ad229`
- seed snapshot（2026-09-25）的 SHA 与执行时一致，未发生基线漂移。
- 主 checkout（本地 master @ d5315716 审计批次线）**不含** #1479–#1488；本任务全部读取与实现都在 worktree（origin/master 线）上完成。

## GitHub 状态（执行时读取）

- Open PR：仅 #1489（dependabot docker node 25），与本方向零交集。
- 最近 merged：#1490–#1496 全部 dependabot；最近功能性波次仍是 #1479–#1488（2026-09-21）。
- Open issues：#1436（前端 i18n，无关）、#1377（audit 延期跟踪，无关）。
- 与本方向直接相关的已合 PR：**#1484（unified cost/resource/planning，ADR-0213）** —— 其 PR body 的 Out-of-Scope 显式列明：
  1. workflow_runtime 节点级 ResourceEstimate 接入；
  2. publication_export 串行锁与 governor EXPORT 通道双轨收敛；
  3. mapspec→RenderWorkInput 生产接线（投影已就绪，消费点留白）。
  这正是 F08 的工作包，不构成重复实现，而是前置交付。
- 并发 worktree：f09（trace-replay oracle）、f11（template/component）已从同一基线派生；本任务修改面与其无文件交集（见 Must Not Touch）。

## 现状事实（file:line 均为 worktree @ 9e1ad229）

### 已有（直接复用，不重造）

- rg.v1 契约真相：`governor/contract.py`（ResourceEstimate/ResourceDemand/ResourceReservation/ResourceUsage/ResourceDecision；`CONSERVATIVE_FLOORS`；`RetryClass.WORKFLOW` 已存在）。
- 先验唯一驻留：`governor/estimation.py`（`_TOOL_CLASS_PRIOR` + `class_prior` + `estimate_for_tool`）。
- 计划聚合：`governor/plan_aggregation.py`（critical path / parallel live peak / retry 乘数 / cache 折扣 / unknown 地板 / OPTIONAL+FALLBACK 披露池 / `budget_violations`）。
- 工具面 governor 管线：`governor/dispatch_adapter.py`（`GovernorDispatchAdapter.run`：admit→execute→complete + actual 回填 + CalibrationStore + 短窗重试入账；kill-switch `GOVERNOR_TOOL_SURFACE`）。
- governor 门面：`governor/governor.py`（`admit_and_reserve`/`complete` 幂等认领/`cancel_session`/fail-open 纪律）。
- 背压+公平：`governor/backpressure.py`（session/subsystem-channel/global 三层；heavy/raster/browser/export/external/llm 六通道）+ `governor/fairness.py`（加权公平+aging+small bypass）。
- 校准：`governor/calibration.py`（有界 CalibrationStore 256×64 + `suggest_priors` 离线建议；生产零自修改）。
- render 估工：`governor/render_budget.py`（`RenderWorkInput`/`estimate_render`/`render_input_from_spec_summary`）。
- workflow 状态机：`workflow_runtime/contracts.py`（9 态 + LEGAL_TRANSITIONS 唯一裁决 + journal 封闭词表）；`machine.py`（ready_set/upstream_of）。
- driver：`workflow_runtime/driver.py`（波次循环；`max_concurrency` 硬钳 ≤4；`_run_node_claimed` 内确定性后端选择 plan_executor>cartography>science>dispatcher>geocompute inprocess；`_fail_or_cancel` 统一失败/取消收口 + 退避门）。
- 派发面：`workflow_runtime/dispatch.py`（Local/Durable/AutoDispatcher；`choose_dispatch`；进程级槽位信号量 `GIS_WORKFLOW_DISPATCH_SLOTS=4`；`NoCapableWorker`）；`workflow_runtime/cluster.py`（WorkerRegistry：capabilities `{cpu,mem_mb,gpu,profiles,io_mbps,backends}` + load `{in_flight,queue_depth,mem_used_mb}` + `total_active_slots`）。
- durable 通道：`geocompute/durable.py::dispatch_node` 已有 `resource_envelope`/`budget`/`run_id`/`node_attempt` 穿透管道（task_kwargs，不进幂等键）。
- 重试分类：`workflow_runtime/retry.py`（NON_RETRYABLE 含 `NO_CAPABLE_WORKER`/`RESOURCE_BUDGET_EXCEEDED`/`CANCELLED`；未知码 fail-closed）。

### Still Missing（= 本任务工作包，证据）

1. **workflow_runtime ↔ governor 零接线**：`grep -r governor app/services/workflow_runtime/` 0 命中；节点执行不经过任何 admission/backpressure/预算记账。
2. **节点无 typed ResourceEstimate**：节点 dict 只有可选 `resources.profile` 字符串（`dispatch.py:84-95`），无数值口径。
3. **driver 未向 dispatcher 传派发元数据**：`driver.py:643-647` 调 `dispatcher.execute(...)` 时 `run_id/node_attempt/node_deadline_s` 全部缺省 —— #1408-3 建好的 durable 管道（事件关联/worker 硬超时）在 driver 侧没有喂入。
4. **worker 槽位只声明不记账**：`cluster.py:275-280 total_active_slots` 只做容量上界；`choose_dispatch` 只问「有没有活 worker」，不问「有没有空槽」；无 occupancy 概念。
5. **NO_CAPABLE_WORKER 与资源饱和不可区分**：只有 NoCapableWorker（不可重试）；「worker 在但槽满」无 typed 表达。
6. **ExportWork 契约不存在**：全仓 0 命中；export 面只有 `export_batch_queue`（无消费方）与 `publication_export._WEASYPRINT_LOCK`（429 不排队），都不走 governor 通道。
7. **plan 级 feasibility 缺席**：`aggregate_plan`/`budget_violations` 存在但 workflow DAG 无消费者；unknown 维地板、critical path、parallel peak 语义未进 workflow 准入。
8. **节点 actual 无回填**：CalibrationStore 键空间只有 `{subsystem}:{tool}`（工具面）；workflow 节点的 estimate-vs-actual 观测为零。

## Overlap / Already Done / Still Missing / Must Not Touch / Integration Seams

| 类别 | 内容 |
| --- | --- |
| Already Done（禁止重复） | rg.v1 契约/先验表/plan_aggregation/backpressure/fairness/calibration/render_budget（#1484 及更早）；durable task_kwargs 穿透（#1408）；NO_CAPABLE_WORKER 语义（#1400）；retry 退避门与分类（V6） |
| Still Missing | 上节 1–8 |
| Must Not Touch | `frontend/`（f09/f11 与 #1436 域）；`gis_harness/workflow_v4/`（编译面，方向 5 热区）；`geocompute/durable.py` 深改（只消费既有 `resource_envelope` 参数）；`publication_export.py`（方向 14 render/export runtime 域）；governor 六通道容量语义（只消费不重定义） |
| Integration Seams | ① `driver._run_node_claimed`（唯一节点执行收口，包裹 `_invoke`）；② `dispatch.choose_dispatch`/`Driver` dispatcher 调用点；③ `dispatch_node(resource_envelope=...)` 既有参数；④ `Governor.admit_and_reserve/complete` 既有门面；⑤ `Subsystem`/`EventKind`/retry 词表的**加法式**扩展；⑥ `render_input_from_spec_summary` 既有投影 |

## 已知失败/flake

- `tests/unit/workflow_runtime/` 与 `tests/governor/` 无 skip/xfail。
- #1484 PR body 披露的存量隔离问题（`test_capability_graph_v8.py::test_ra3_cross_scope_model_no_false_duplicate` 顺序依赖）属于 harness 面文件，与本方向改动面不相交；落地时以干净基线复跑区分。

## 架构决策摘要（详见 ADR-0214）

- rg.v1 是唯一数值真相：workflow 节点估算经新桥 `workflow_runtime/estimate.py` 投影 `estimate_for_tool`/`render_budget`/`export_budget`，禁止第二份先验表；parity（同工具同参数 → dims+resource_class 全等）测试锁定。
- 节点执行统一过 governor：`workflow_runtime/governor_link.py` 在 `_run_node_claimed` 包裹 `_invoke`（admit→execute→complete→calibration），kill-switch `GIS_WORKFLOW_GOVERNOR=0`，一切 governor 异常 fail-open。
- plan 级 feasibility：`plan_feasibility.py` 把 DAG 波次结构投影成 `PlanNode` 序列 → `aggregate_plan` → `budget_violations`（manifest scope=workflow）；provisional observe 默认，limits 登记且 enforce 才拒。
- worker 容量二值化：无合格 worker → `NO_CAPABLE_WORKER`（不可重试，#1400 语义不变）；worker 在但槽满（capabilities × load.in_flight）→ `RESOURCE_EXHAUSTED`（可重试退避）。
- estimate 随 durable job 走既有 `resource_envelope`；driver 补喂 `run_id/node_attempt/node_deadline_s`（由 estimate wall hi × 余量派生，仅 durable 消费）。
- 校准闭环只观测：节点 complete → `CalibrationStore.record_usage("workflow:{kind}:{capability}")`；先验更新仍只能走离线 suggest + 人工 PR。
