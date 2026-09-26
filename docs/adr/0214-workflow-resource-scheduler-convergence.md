# ADR-0214: Workflow Resource Scheduler Convergence（节点级 ResourceEstimate 与背压）

- 状态：Accepted（direction F08）
- 关联：ADR-0213（unified cost/planning，本方向是其 Out-of-Scope 的直接续篇）、
  ADR-0182（governor v1）、ADR-0184（副作用纪律）、#1484（estimate bridge +
  plan aggregation）、#1408（durable budget/deadline 穿透）、#1400
  （NO_CAPABLE_WORKER）
- 勘察：`docs/dev/f08-workflow-resource-scheduler-recon.md`

## Context

#1484 统一了 planner/dispatch 数值估算、计划聚合与 loop budgets，但明确未覆盖
workflow_runtime 节点级资源治理。勘察实证（基线 9e1ad229）：

1. `workflow_runtime` 与 governor **零互引用**：节点执行不经过任何
   admission/backpressure/预算记账；`RetryClass.WORKFLOW` 无消费方。
2. workflow 节点没有 typed ResourceEstimate；`resources.profile` 只是字符串，
   同一计算在 plan 面（rg.v1 数值）与 workflow 执行面（无数值）是两套口径。
3. driver 调 `dispatcher.execute` 时从不传 `run_id/node_attempt/node_deadline_s`
   —— #1408 建好的 durable 治理管道在 driver 侧断头：durable 节点无 worker
   硬超时、run_events 关联不上。
4. worker 槽位（`cluster.capabilities.profiles`）只声明不记账；「无合格 worker」
   （确定性，不可重试）与「worker 在但槽满」（瞬时，应退避重试）共用一个
   不可区分的失败面。
5. ExportWork 契约不存在；render 估工（`render_budget`）无 workflow 消费方。
6. 节点 actual 用量零回填 —— workflow 域的 estimate-vs-actual 校准盲区。

## Decision

1. **D1 节点级 typed estimate（单一真相）**：新增
   `workflow_runtime/estimate.py::resource_estimate_for_workflow_node`。
   数值先验只来自 `governor.estimation`（`estimate_for_tool` + `class_prior`），
   render 面经 `render_budget.render_input_from_spec_summary`，export 面经新
   `governor/export_budget.py::estimate_export`。**不新增任何先验表**。
   节点声明覆盖（`resources.memory_class/latency_class/estimated_*`）按
   estimate_bridge 同款「同表切片」语义。parity 不变式（测试锁定）：同一
   工具/参数经 workflow 节点桥与经 dispatch `_build_demand` 产出**逐维全等**
   的 dims 与相同 resource_class（subsystem 归因不同：workflow 节点诚实标注
   `Subsystem.WORKFLOW`，新增枚举成员为加法式扩展）。
2. **D2 节点执行统一过 governor**：新增 `workflow_runtime/governor_link.py`
   （`NodeGovernorLink`），在 `driver._run_node_claimed` 包裹执行体：
   estimate → `admit_and_reserve`（subsystem=WORKFLOW，priority 由节点
   priority 确定性映射 ExecutionPriority，attempt>1 走 `RetryClass.WORKFLOW`
   预算）→ 执行 → `complete`（actual 回填 + CalibrationStore）。
   - **释放完备性**：success/fail/cancel/timeout/deadline-abandon（任务被
     cancel → CancelledError）/未预期异常全路径恰好一次释放（BaseException
     兜底，对齐 dispatch adapter RUN-10 修复纪律）；
   - reject+enforce → typed `RESOURCE_BUDGET_EXCEEDED`（既有不可重试词表）；
   - kill-switch `GIS_WORKFLOW_GOVERNOR=0`；governor 任何异常 fail-open
     （绝不阻断 workflow 执行，与工具面同纪律）。
   不动六通道容量语义 —— workflow 是背压通道的**第二个生产消费方**，
   互动 turn/小工具/长分析/导出渲染的公平由既有 FairScheduler（aging +
   small bypass）天然承载。
3. **D3 计划级 feasibility（provisional observe）**：新增
   `workflow_runtime/plan_feasibility.py`：DAG 波次结构（拓扑层内 PARALLEL、
   层间 SEQUENTIAL，与 driver `max_concurrency` 语义一致）→ #1484 的
   `aggregate_plan`（critical path / parallel live peak / retry 乘数 /
   cache / unknown 保守地板）→ `budget_violations` 对 manifest
   scope=`workflow` 的显式上限。limits 未登记 → 零违规 → 行为不变
   （provisional 纪律：先观测后拦截）；limits 登记且 governor=enforce 且
   kill-switch 开 → 实例快速失败 `RESOURCE_BUDGET_EXCEEDED` + journal
   `PLAN_ADMISSION` 事件；否则 observe 披露。资源裁决绝不越过
   capability/permission/data qualification 硬门（只在全部资格门之后）。
4. **D4 worker 容量二值化**：`choose_dispatch` 在 durable/isolate 要求下：
   - 无覆盖 profile 的活跃 worker → `NO_CAPABLE_WORKER`（语义不变，不可重试）；
   - worker 在但 `Σ load.in_flight ≥ Σ profile 槽位`（capabilities × 心跳
     load 双输入，registry 缺 load 时 fail-open 放行给队列）→ 新 typed
     `RESOURCE_EXHAUSTED`（可重试，走既有退避门）。
5. **D5 estimate 随 durable job 穿透**：driver 补喂 `run_id`/`node_attempt`/
   `node_deadline_s`（None 时由 estimate wall max × 2 + 30s 派生，有界
   [30, 3600]s），节点 ResourceEstimate 经既有
   `dispatch_node(resource_envelope=...)` 参数进 task_kwargs（不进幂等键，
   #1408 同款先例）—— inprocess 与 durable worker 对同一节点的预算语义一致。
6. **D6 校准只观测**：节点 complete → `CalibrationStore.record_usage(
   "workflow:{kind}:{capability}")`；先验更新仍只能走 `suggest_priors`
   离线建议 + 人工 PR（生产零自修改，ADR-0213 D4 纪律延续）。
7. **D7 render/export 窄 seam**：workflow 节点可声明
   `resources.render`（→ RenderWorkInput 投影）与 `resources.export`
   （→ 新 `ExportWork` 契约 + `estimate_export`，`export_formula.v1`
   provisional 系数）；二者与普通节点走**同一** governor 通道（render →
   BROWSER/MEDIUM class、export → EXPORT class 背压通道）。render runtime
   本体不动（方向 13/14 的消费 seam 就绪）。

## Non-goals

- 不做通用 Kubernetes scheduler；不重定义 geocompute 队列路由真相。
- 成本分数不越过 capability/permission/data qualification 硬门。
- render engine 实现不进 scheduler（方向 13/14）。
- 不引入生产自动改先验（校准只观测）。

## Consequences

- workflow 节点从「无预算裸跑」变为与工具派发同一 rg.v1 预算语义；
  provisional manifest 未登记 workflow 上限时行为完全不变（可灰度）。
- durable 节点获得 worker 硬超时与 run 事件关联（#1408 管道闭环）。
- 「无 worker」与「槽满」可观测、可区分、重试语义各自诚实。
- 新增词表：`Subsystem.WORKFLOW`、`EventKind.PLAN_ADMISSION`、错误码
  `RESOURCE_EXHAUSTED`（RETRYABLE）、`ExportWork`（governor 契约）。
