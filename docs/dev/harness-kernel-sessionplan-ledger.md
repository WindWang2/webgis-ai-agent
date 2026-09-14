# Ledger — Pi-native GIS Harness Kernel + SessionPlan

每个 milestone 一节：目标 / 改动文件 / 新改契约 / 测试 / 证据 / 兼容 / 回滚面 / 未解决项。

## M0 — Phase 0 勘察（2026-09-13）

- 基线：`origin/master` = `580b33e9`；worktree `webgis-wt-hk1`；branch `harness/pi-native-kernel-sessionplan-v1`。
- 结论：SessionPlan（ADR-0076）已存在且是 Pi 路径生产事实 → 任务书「K0 冻结契约」调整为「additive 扩展既有契约」。CanonicalPlan 保持 legacy 源真。#1270 不吞并。ADR 占号 0180。
- recon/decisions 两文档已落；Subagent A 广度对账回填中。

## M1 — K0 契约扩展 + K1/K2 kernel 与持久化（2026-09-13）

- 目标：冻结 host-neutral 契约载体；建立 runtime 生命周期与持久化不变量。
- 改动：`app/services/harness_kernel/{__init__,models,projection,runtime,metrics}.py`（新）；`app/services/session_plan.py`（additive 字段 + save 自增 revision/created_at + `apply_tool_result_with_lock` + `SESSION_PLAN_STEP` 常量 + kernel 投影行）。
- 新契约：`PlanStep/StepEvidence/PlanTurnRecord/PlanDecision/PlanRecoveryMetadata/PlanPatch/PatchResult`；envelope v2（`SCHEMA_VERSION=2`，v1 载荷零漂移反序列化）。
- 测试：`tests/unit/test_harness_kernel_runtime.py` 14/14 绿（turn 生命周期/幂等/中断检测/步骤物化与证据/revision 单调/v1 兼容/checkpoint 环/patch/metrics/投影零漂移）。
- 证据：修复两个实现缺陷——① begin_turn 持锁内调 ensure_slot 自锁死锁（改为锁外预创建）；② metrics `host` 关键字参数未进计数键。
- 兼容：`session_plan.py` 既有 29 测试绿（host/route/unit 三文件）；SSE 三个冻结事件名与 payload 未动。
- 回滚面：新增字段全部有默认值；kernel 模块可整体删除不破坏 v1 行为。

## M2 — K3 Pi 生产接线（2026-09-13）

- 改动：`app/agent_pi_bridge.py` —— `stream_prompt`/`prompt` 各加 begin_turn（register_active_pi_turn 后）与 end_turn（settle 后，状态映射 cancelled/failed/completed）；`_dispatch_tool_bound` 加 begin_step（dispatch 前，仅命中计划能力的工具产生写）+ 成功/失败两路 `apply_tool_result` → `runtime.apply_tool_evidence`（含锁竞争重试路径）。
- 证据：既有 `test_pi_session_plan_host.py` 全绿（scripted 链经 kernel 路径跑通）。
- 兼容：所有 kernel 调用 try/except 包裹（增值披露绝不阻断 turn）；Pi-only 缓存/token/取消未动。
- 回滚面：revert bridge 五处插入即回 v1 行为。

## M3 — K4 legacy adapter（2026-09-13）

- 改动：`app/services/harness_kernel/legacy_adapter.py`（新，单向投影）；`app/services/chat/execution_engine.py` 六挂点——`chat()`/`chat_stream()` 的 turn 开始与结算、`_maybe_plan` 尾投影、`_flush_plan` 后镜像。
- 证据：`test_s6_legacy_host_reads_writes_same_contract`（同一 SessionPlan 契约 + host 标记 + legacy_projected 台账）。
- 兼容：`test_chatengine_intent_does_not_write_session_plan` 仍绿（registry dispatch 不写 envelope 的不变量保持——adapter 只在 plan/turn 边界）；所有挂点 try/except。
- 回滚面：删除挂点即回 legacy 独占状态。

## M4 — K5 patch + K6 事件/前端 + K7 checkpoint + K8 metrics（2026-09-13）

- K5：`runtime.patch_plan(PlanPatch) → PatchResult{invalidated_step_ids, revision, events}`——direction-5 稳定协议；invalidated 标记 + 决策台账，无执行引擎。
- K6：`session_plan_step` 第四事件名（additive）；GET 投影 `steps` 可选字段（`SessionPlanStepView`）；前端 `types/session-plan.ts`（StepRow/StepPayload/投影 steps?）、`session-plan-delta.ts`（applyStep upsert + replaced 重置）、`use-sse-stream.ts` 白名单、`session-plan-panel.tsx` 步骤行渲染、zh/en i18n 键。
- K7：checkpoint 环 `session-plan-cp:{0..2}`（turn 结束自动 + 手动入口）；resume 投影行 `[SessionPlan Recovery]`；in-flight 步骤按 turn 终态落定（cancelled→skipped，failed/interrupted→failed）。
- K8：`harness_kernel/metrics.py` 有界计数器（host 分维度）+ HK_METRICS 结构化日志；挂点覆盖 turn/plan/step/checkpoint/resume/duplicate/stale。
- 测试：前端 vitest 7/7（5 既有 + 2 新增）；后端见 M1-M3。

## M5 — E2E 验收场景（2026-09-13）

- `tests/test_harness_kernel_e2e_scenarios.py`：7 场景全链路（真 dispatch seam、无 LLM/Pi 子进程），**21/21 绿**（7 E2E + 14 单测）。
- 确定性注记：poi/boundary 依赖本机数据导入（同既有 host 测试按条件处理）；能力命中/失败重试链用 heatmap_data inline geojson。
- 过程中修复的实现缺陷：① begin_turn 持锁内 ensure_slot 自锁；② registry 命中但章节未规划的能力无步骤 → `_settle_step` 按需建步（镜像 `_mark_progress` append）；③ 计划外步骤随 replace 丢弃（镜像 `_merge_progress`）；④ chapter 行在 data_requirements+analysis_steps 重复时步骤去重；⑤ `resumed_from_turn_id` 挂起标记使恢复提示在续跑 turn 内可见。
- 测试自身缺陷修复：E2E 直调 dispatch 无活跃 turn → `register_active_pi_turn` 走真实关联；helper 二次 take SSE 缓存导致断言空串。

## M6 — K9 收敛 + 文档（2026-09-13）

- ADR-0180 已落（占号核验：当时最高 0179）；`docs/research/pi-host-seams.md` 修正 SessionPlan 状态 + 补 kernel 层。
- 决策记录：bridge `agent_settled` 侧四连触发**不迁移**（与 dispatch 侧参数差异大，回归风险>收益，留后续）。

## M7 — 回归 + 预存失败归因（2026-09-13）

- **关键修复**：engine 的 `session_lock` 即 `session_lock_registry.lock`（非重入）——legacy 挂点在锁作用域内自取锁，每挂点 30s 争用（planning 套件假死根因）。修复：runtime/adapter 全入口 lock 透传 + engine ContextVar `bind_engine_lock`；子代理引擎门控（否则误标父 turn interrupted）。修复后 planning 套件 400s+ 假死 → 9.24s 全绿。
- 回归矩阵（--no-cov 串行）：
  - session-plan 族 + pi event mapper/turn context/bridge lock：57 过（route 契约 pin 显式纳入 `steps` 后 6/6）
  - planning 族（orchestrator/mode/engine-planning/planner）：80 过
  - kernel + E2E + route + unit：45 过
  - bridge pool v5b/cancellation + kernel + E2E：36 过
  - pi e2e/integration/compat/concurrency/leak/dispatch adapters：65 过
  - dispatch cache eviction/dynamic surface/status fail-closed/issue685/adversarial SSE：45 过
- **master 预存失败对照**（干净 master 复跑同败，非本任务回归）：
  - `tests/test_pi_integration.py::TestPiBridgeSubprocessFlow::test_stream_prompt_emits_heartbeats_during_silence`（本机时序依赖）
  - frontend `use-sse-stream.test.ts`（`next-intl` 无法在本机 vitest 解析——环境问题；session-plan 族 7/7 绿）
- ruff：全部改动文件清零。

## M8 — 独立 review（四轴）与修复（2026-09-13）

- 结论：无 P0；2 P1 + 9 P2。**P1 全部修复**：
  - S1 stream finally 先释放 lease 再 end_turn → 并发下一 turn 误标 interrupted：end_turn 移至 lease 释放**之前**（`_safe_kernel_end_turn`：shield+5s 预算+吞异常，R5 同修）。
  - S2 supersede 重建信封丢弃 turns/decisions/recovery → 在飞 turn 永不结算：新信封携带旧信封的 turn 台账/决策/恢复事实（session_plan.py supersede 分支）。
- **P2 修复**：S4 三个死指标（plan_superseded 改由 capability 层 superseded 事件驱动 + journal；stale_write_refused 随 S5 guard 落地；host_parity Pi 侧补打点）；S5 `_save_if_fresh` stale-revision guard 进 kernel 全部变更 save（D-004 修订）；R4 越界赋值截断（materialize/adapter 双路径，防下次 load 整信封作废）；R5 bridge/engine 四处 end_turn shield 化；R8 `_hk_lock_token` 先初始化（防 NameError 掩盖原始异常）；A5 session_plan 模块 docstring 与 ADR-0180 对齐。
- **已接受不修**（理由可验证）：
  - S3 patch_plan 生产未接线：协议先行为方向 5 留口，ADR 已明示"零生产引用是设计事实"；
  - R6 engine 降级锁透传削弱 fail-closed：engine 路径本就运行在该语义下（非本分支引入）；S5 guard 已兜住跨 pod 竞速时的静默回写；
  - R7 end_turn 步骤落定事件不入 SSE：end_turn 在 finally 无 toolCallId rendezvous 可挂；状态经既有 GET 水合收敛（下次面板水合即正确）；
  - P1(perf) 每 dispatch 写放大（映射工具最多 3 次 envelope RMW + turn 末 checkpoint）：有界无 O(n²)；journal 合并进 capability 层属后续优化。

## M7 — 独立 review + 修复 + PR

（待填）
