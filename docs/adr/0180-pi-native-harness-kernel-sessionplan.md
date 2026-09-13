# ADR-0180: Pi-native GIS Harness Kernel + host-neutral SessionPlan 契约

- 状态：Accepted
- 日期：2026-09-13
- 关联：ADR-0076（SessionPlan 是 Pi 路径计划真相）、ADR-0077（wrap vendored Pi）、ADR-0055（session 拥有 GIS 世界）、ADR-0006（统一调度）、ADR-0022（Pi dispatch cache）、docs/research/pi-host-seams.md
- 分支：`harness/pi-native-kernel-sessionplan-v1`

## Context

Pi 是 agent host（USE_NEW_AGENT 默认开）；GIS 会话语义（计划、证据、恢复）此前分散在三处：

1. **SessionPlan envelope**（ADR-0076，`app/services/session_plan.py`）：Pi 路径 capability 粒度计划真相，含 Redis 持久化、3 条 SSE、前端 hydrate-then-delta——但**无 turn 身份、无 revision、无步骤级证据、无决策日志、无恢复元数据**；
2. **Pi bridge**：工具回调后的计划写入编排（apply + 重试 + SSE 缓存）寄居在 transport 层；turn 生命周期只有 Pi 域的 token/注册表，GIS 侧无台账；
3. **legacy ChatEngine**：CanonicalPlan（含 revision/steps/依赖）+ plan_* SSE 独占，与 SessionPlan 零共享——同一 GIS 任务在两个 host 上产生互不相通的计划证据。

## Decision

在 `app/services/harness_kernel/` 建立单一 **GIS Harness Kernel** 概念层：

1. **契约载体 = 既有 SessionPlan，additive 扩展（不建第五套 plan class）**。新增字段全部带默认值，旧 v1 信封反序列化零漂移：`schema_version`/`created_at`/`revision`（每次持久化自增）/`turns`（FIFO≤8）/`steps`（FIFO≤48，host-neutral PlanStep+StepEvidence）/`decisions`（FIFO≤24）/`recovery`（checkpoint/resume 事实）。capability 进度仍由 `CapabilityProgress` 行承载——`PlanStep.capability` 只做关联，不复制状态。
2. **GISSessionRuntime 是会话生命周期唯一入口**：hydrate → begin_turn → plan update / evidence attach → checkpoint → end_turn。kernel 只拥有 GIS/Harness 语义；**不复制 Pi 的 tool loop / LLM loop / history loop**；Pi-only 的 RPC client、dispatch cache、turn token HMAC 留在 Pi 域。
3. **单一变更锁域**：工具证据的既有 capability 语义（supersede/replace/失败标记）仍在 `session_plan.apply_tool_result`（唯一真相）；kernel 的 step/turn/decision 增量经 `apply_tool_result_with_lock` 在**同一个** fail-closed 会话锁内组合，一锁两段一落盘。
4. **步骤单层化**：PlanStep 由 gis_chapter 行（data_requirements/analysis_steps）物化，`depends_on` 只是语义标注；完整 ExecutionGraph 调度与 affected-subgraph 重执行**不属于本层**（方向 5 的接口已定：`runtime.patch_plan(PlanPatch) → PatchResult.invalidated_step_ids`）。**patch 协议先行、生产未接线**：本方向没有任何 follow-up 分类调用它（K5 预期挂点属方向 5 的 follow-up 流程），tests 之外的引用为零是设计事实而非遗漏。
5. **Legacy 单向投影**：CanonicalPlan 保持 legacy 源真；`legacy_adapter` 在 `_maybe_plan`/`_flush_plan`/turn 边界把语义镜像进同一 SessionPlan。不删 CanonicalPlan；Pi 永不读它。
6. **事件家族只增不改**：`session_plan_updated/progress/superseded` 冻结；新增第四名 `session_plan_step`（additive），核心字段与 legacy `plan_step_done` 语义对齐；CanonicalPlan 事件名在 Pi 路径维持显式禁令。前端仍是同一个 hydrate-then-delta reducer（新名 upsert 步骤行），不建第二条 stream。

## Consequences

- Pi 与 legacy 两个 host 产生**同一契约**的计划证据（host parity 可观测：`hk_metrics` 按 host 分维度）。
- 恢复语义成立：turn 台账 + recovery 元数据 + checkpoint 环（`session-plan-cp:{0..2}`）使 server restart / Pi 重启 / 断线后续跑可判定"哪些已确认、哪些在飞"；恢复路径**只重建上下文，绝不自动重放已 succeeded 的破坏性副作用**（执行层重复拦截继续由 ToolDispatchService 承担）。
- 寄居在 bridge 的通用 GIS session 编排开始向 kernel 收敛（dispatch 侧已迁移）；`agent_settled` 侧的 finalize/workflow/runtime 四连触发**暂不迁移**（与 dispatch 侧参数化差异大，收益不抵回归风险），留 K9 后续。
- `docs/research/pi-host-seams.md` 的 SessionPlan "Missing" 表述由本次更新修正。

## 明确不做

- 不实现 Capability Graph（方向 3）、动态 typed tool surface（方向 4）、ExecutionGraph scheduler（方向 5）。
- 不 fork Pi；不把 legacy planner 链（classify_followup/should_plan/make_plan）移植到 Pi。
- 不为 plan payload 内联 MapSpec/GeoJSON——artifact 永远是 ref。
