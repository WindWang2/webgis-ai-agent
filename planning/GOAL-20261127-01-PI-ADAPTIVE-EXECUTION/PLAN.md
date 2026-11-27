# PLAN — GOAL-20261127-01 Pi 自适应执行策略

> 目标一句话：让已存在的 Pi 原生宿主在真实 GIS 长任务中**消费既有的模型路由、
> 资源 governor、任务阶段、provider 健康、诊断和费用账本**做一致的、可解释的
> 执行调度 —— 可恢复的继续，不可恢复的诚实终止，不重复有副作用的工具。
> 不新建 router/governor/日志库/classifier/schema 卫生/compaction。

## 已实证的核心缺口（WP1 审计，主 Agent 自读证据）

| # | 缺口 | 证据 |
|---|---|---|
| G1 | Pi turn 期模型应用**不经路由权威**：session_model_service 只做目录+能力门+预算投影，无健康护栏/回退链/软评分；`RouteRequest.prefer_model` 注释明确预期 Pi set_model 消费但未接线 | `model_runtime/routing.py:69`（prefer_model 注释）；`session_model_service.py` 全文无 health/fallback |
| G2 | provider 失败**不回写健康表**：`after_provider_response` 只落 stderr 有界环；`observe_outcome` 仅 execution_engine.py（ChatEngine title/execution/planner）调用 | `extensions/webgis-tools/index.mjs:607-618`；`grep resolve_routed_config` 仅 execution_engine.py:1035-1280 |
| G3 | 重试语义全在 vendor 内部（auto_retry 事件只做人读 note），宿主无 attempt 链路事实：attempt 未与 turn runtime 行、ToolDispatch 幂等键、job ID、usage 收据关联成可核对链条 | `pi_event_mapper.py:447-497`（note+诊断）；`ChatTurnRuntime` 无 attempt 维度 |
| G4 | 长任务阶段（ GIS job 提交→运行→地图 revision）与模型/provider 状态之间无投影：job 完成/失败不反映到路由健康，也不反映到用户可见的阶段事实 | governor/dispatch_adapter.py 只在工具派发面；provider 维度缺席 |

## 设计原则（不可违背）

1. **单一权威复用**：路由=model_runtime.ModelRouter；健康=model_runtime/health +
   provider_health；预算=governor（session_budget/admission/retry_budget）+
   session_model_service M30 准入；账本=usage_ledger（ChatTurnRuntime/ChatUsageRecord）；
   取消=pi_durable_cancel + jobs lifecycle；诊断=pi_surface_metrics/_diag_stats。
   本线只做**接线与投影**，不复制逻辑。
2. **Pi 工具真实可达**：新能力必须经 webgis-tools extension 注册、被 Pi 真实
   RPC 调用；同时提供普通 UI/API 入口，两入口共用同一领域服务。
3. **诚实语义**：unknown 不冒充 0；unsupported/consumed/not_evaluated/failed/partial
   分列；模型切换只在安全点（未启动 turn 或证明无已提交副作用的边界）。
4. **有界性**：receipt/投影字段有界（reason codes 截断、无提示词全文、无空间
   数据、无 key）；429 Retry-After 有界尊重；401/403 不盲重试。

## 分阶段计划（每阶段小步 commit）

### S1 WP1 收尾：契约审计 + fixture 实测（子代理报告 → DEDUP_MATRIX 定稿）
- [x] master/PR/vendor pin 审计（BASELINE.md）
- [ ] 两个 Explore 报告 → 消费矩阵定稿
- [ ] mock HTTP provider fixture 驱动真实 vendored RPC：200/429/5xx/断流，
      采集 route/attempt/usage 证据链现状（WP5 前置）
- 验收：DEDUP_MATRIX.md 每格 present/partial/missing/covered + path:line

### S2 WP2：阶段感知运行策略接线（核心）
- 新模块 `app/services/chat/pi_runtime_strategy.py`（只做桥，不复制 router）：
  - `resolve_turn_model(ctx)`：session preference（显式优先）→ ModelRouter.route
    （硬资格：require_tools/上下文窗；健康护栏；fallback 链）→ 选中 + reason
    codes 进有界 route receipt；保留用户显式选择（除非撤销/不可用 → typed 拒绝，
    绝不静默换）。
  - `observe_provider_failure(...)`：after_provider_response/auto_retry 事件 →
    model_runtime/health.record_failure（breaker/cooldown/rate_limit 分类）→
    下次路由自动规避。
- extension 接线：`after_provider_response` handler 经 RPC 通知宿主（新增
  受 bridge_secret 保护的观测回调，或复用 stderr 结构化行升级为 RPC 事件——
  以 WP1 实测的通道事实定），payload 有界整数化（status/retry_after/attempt）。
- set_model 安全点：沿用 stream_prompt 现有 apply_session_model 位置（turn
  边界）；不在活动工具/部分输出中切换。
- 验收：单测（策略纯函数）+ 真实 RPC fixture（429→健康表 cooldown→下 turn
  路由 reason codes 含 health_deprioritize）+ route receipt 进 SSE/DB。

### S3 WP3：预算/恢复闭环
- turn↔attempt 关联：ChatTurnRuntime 增补（migration additive）或复用
  usage_json 有界键：provider_attempts 数、route receipt ref、durable job IDs、
  dispatch 幂等键引用。**不新建平行账本**。
- 恢复语义：provider 已响应但 SSE 丢失/客户端重连 → 既有 event_resume 链路
  复核 + 补 turn 终态 typed indeterminate（无法证明是否执行时）。
- 预算耗尽：governor session budget（M30 已有 admission）→ 超限 typed 拒绝
  与已保留结果投影（不重复 #2283 诊断）。
- GIS job 完成但模型失败：job 终态 + turn 终态分离呈现，续跑读回状态不重放
  已提交 CAS/job/导出（pi_durable_cancel + JobOrigin 已有，补消费面验证）。
- 验收：取消/恢复/预算负例 + 关联链串证测试（turn→attempt→job→revision→usage）。

### S4 WP4：运行质量投影 + 用户操作
- 聚合投影 API：turn/attempt/job 关联的有界投影端点（复用
  session_usage_summary + health_snapshot + job 状态，不建新库）。
- 前端：模型设置/任务状态入口呈现等待阶段、可恢复条件、已保留结果、降级原因；
  重试/继续操作明确「复用或新任务」。
- 验收：UI 组件 + API 双入口同语义测试。

### S5 WP5：上游兼容 + 故障矩阵回归
- v1.6.1 gitlink 不动（ls-remote 实证无新正式版）；compat matrix 增量行。
- mock HTTP provider（真实 HTTP+真实 vendored RPC）：200/429/5xx/401/断流/
  超时/不支持 schema/usage unknown/异常后再执行矩阵。
- 3 类 GIS 任务恢复/选择/调用链纳入 regression（复用现有 journey 框架）；
  压测 1/2/4 会话（资源允许下）。
- 验收：TEST_MATRIX.md 全绿或如实分列。

### S6 WP6：安全 + R1/R2 + 交付
- 威胁负例：恶意 provider 响应注入、跨 owner 路由账本隔离、重复结算、重连
  重复提交、资源耗尽。
- R1 独立审查（第 3 个子代理或主 Agent 隔离上下文，非作者）→ 修复 → R2。
- 终验 + PR。

## 完成定义（Oracle）

1. DEDUP_MATRIX 定稿且每格有证据。
2. G1–G4 缺口关闭或如实披露（代码+测试+真实入口）。
3. 强制旅程 1–5 有可执行验证记录（TEST_MATRIX.md）。
4. R1/R2 关闭全部 P0/P1/P2。
5. 单 PR 创建（`gh pr create --base master`），正文含全部要求项。
