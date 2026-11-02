# 0279. GIS Harness 任务级诊断、受控恢复与目标验收对账

**Date:** 2026-11-02
**Status:** Proposed
**Branch:** `zcode/goal-gis-harness-recovery-v1`
**Baselines:** ADR-0248（统一失败分类/remediation）· ADR-0183（goal satisfaction）· ADR-0119 D5（durable recovery ledger）· ADR-0213（loop token）· ADR-0278（map operation 事务）

## Context

V5/V6 已交付工具级失败分类（`HarnessFailureClass` 11 类 + 有界 remediation）、
渲染观察闭环、goal satisfaction 评估（已接生产 finalize）。但跨真实执行阶段
仍有四个无人回答的问题：**为什么没完成**（429/5xx/权限/数据资格/no-progress/
MapSpec 未收敛在粗分类里折叠成 unknown 或错预算）、**哪些结果可信**
（task_complete 与渲染观察/科学证据无对账面）、**下一步允许自动补救什么**
（无风险×处置的副作用守卫统一裁决）、**什么时候必须停下问人**（无
needs_user_data/needs_user_approval 处置词表）。`harness_failure` payload 无
程序化消费者；legacy step_error 用第三套 planning FailureClass；reliability
语料无恢复场景。

## Decisions

1. **D1 三层正交词表，全部封闭**：粗类（既有 `HarnessFailureClass` 11 值，
   不动）→ 诊断 reason（`GISRecoveryReason` 27 码，新增细粒度：rate_limited/
   upstream_server_error/permission_denied/cross_session_ref/crs_ambiguous/
   eligibility_*/goal_gap_* 等，逐一映射回粗类）→ 处置
   （`RecoveryDisposition` 6 值：retryable/repairable/needs_user_data/
   needs_user_approval/unsafe_to_retry/terminal）。词表双射由测试钉死
   （registry ↔ enum；11 粗类全覆盖；terminal 仅 cancelled/budget/no_progress）。
2. **D2 RecoveryPolicy 纯决策**：`decide_recovery(diagnostic, risk, …)` 零 IO/
   零执行/零 sleep —— 自动重试仅限 `SideEffectClass.retry_safe`（复用既有
   descriptor 事实源）或携带幂等键的 state_mutation；tier-3/外部副作用/未分类
   绝不自动重试并挂封闭确认门类别（owner_write/paid_call/export/destructive，
   路由到既有确认入口）；退避只给确定性 schedule（无 jitter —— 可回放），
   等待由既有 vendor/LLM 循环消化；取消立即让路零补偿动作；预算耗尽只终结
   retryable 处置（用户路径不消耗预算语义）。
3. **D3 单 seam 接线**：诊断+裁决在 ToolDispatchService 统一错误分支
   （`harness_failure` 之后）以 additive `recovery` 键产出；LLM 单行 note
   （≤240 字符）双引擎同源（Pi HTTP 回包 / legacy tool_result 消息）；
   step_error SSE 透传；SessionPlan 失败分支落账 `last_recovery`（封闭词表
   形状门：未知 reason 拒绝落账）+ `[GIS Recovery]` 投影行；trace TOOL_RESULTS
   错误 payload 增扁平 recovery 键；runtime_repair 补 GisTraceChain 阶段 16
   发射。不建第二 trace store / 第二 runtime。
4. **D4 GoalVerification 对账层（非第二 oracle）**：`reconcile_goal_verification`
   只读 ADR-0183 报告 + observation_health + workflow roles/data_qualifications
   + cartographic review 在场性，产出 8 slot × 4 态（pass/fail/warning/
   not_evaluated）；false-PASS 红线：渲染观察缺失/科学证据不足永不 pass、
   goal satisfied 无渲染证据记 contradiction、task_complete 与 failing slot
   矛盾披露；user-wins：显式隐藏层至多 warning + 说明未展示，绝不代用户显现；
   不适用 slot（需求族不存在）`counts_toward_overall=False`（诚实缺席 ≠ 拖低）。
   随 map_product 块持久化（additive）+ task_complete/map_finalization SSE 投影。
5. **D5 多轮重试指令走披露不走路由**：`resolve_retry_directive` 纯函数从
   last_recovery + 能力行给出「只重试 failed/unavailable 行、保持已验证
   caps/refs、workflow 章节指向 #1869 recompute 工具」的指令行；ref 归属
   不可证明或属他会话 → fail-closed 且不回显他会话 ID；执行归 agent +
   既有工具，不建调度引擎。
6. **D6 恢复语料三 oracle 族**：`recovery_corpus` 29 案例 × 21 类别，每条含
   input_state/allowed_actions/forbidden_actions，驱动生产纯函数（非影子
   实现）；E2E 门 3 恢复成功 + 2 安全终止走真实 dispatch seam。
7. **D7 顺带清偿的三处既有缺陷**（E2E 门暴露）：粗分类器 429/5xx 落
   unknown（预算 1）/budget_exhausted（预算 0，首错即 abort）——并入
   `_TIMEOUT_MARKERS`（词汇未扩，语义对齐类文档"远端不可达"）；TDS 记忆
   候选读错键 `failure_class`（实际 `class`，恒 unknown）；continuation
   回显他会话 ID（移除）。

## Consequences

- 兼容：全部 additive 键（`recovery`/`last_recovery`/`goal_verification`/
  `recovery_explanation`/SSE 字段/投影行），旧读者忽略；成功路径零新键；
  旧失败形状零漂移（无 recovery 键 → 不落账、不投影）。
- 安全：诊断/LLM note/指令行不含跨租户 ID 与用户原文；身份不取自
  map_state（#1841-#1843 信任边界不复制）；破坏性/付费/导出/写操作永不
  自动重试（语料+策略+seam 三层钉死）。
- 性能：诊断+裁决纯函数中位 <2ms / p95 <8ms（M8 门，双跑一致）；payload
  序列化 <2.5KB；LLM note ≤240 字符。
- 不做：第二 oracle（ADR-0183 判定唯一）、第二 trace store、新调度引擎、
  新确认入口、failure_taxonomy 词表变更。
