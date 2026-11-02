# feat(harness): 任务级诊断、受控恢复与目标验收对账 —— GISRecoveryDiagnostic.v1 + RecoveryPolicy + GoalVerification（GOAL 05 / ADR-0279）

## 目标

给 Pi Agent / GIS Harness 补上跨执行阶段的四个可判定回答：**为什么没完成**（统一诊断）、**哪些结果可信**（完成对账）、**下一步允许自动补救什么**（副作用守卫裁决）、**什么时候必须停下问人**（六处置词表）。地图渲染成功只是必要条件；数据资格/科学质量/导出交付仍须有证据。

- master 起点/终点：`6c5e71c2cb04afe31b40bf66ca96e6dd6480b4e7`（无前进，无需 rebase）
- 分支：`zcode/goal-gis-harness-recovery-v1`（独立 worktree，15 commits）

## 真实差异（新增 vs 复用）

**新增**（`app/services/gis_harness/recovery_policy/` + `app/lib/harness/goal_verification/`）：
- M1 `GISRecoveryDiagnostic.v1`：27 个封闭 reason 码（429/5xx/权限/跨会话 ref/CRS 歧义/eligibility/no-progress/MapSpec 未收敛/goal 差距…）全部映射回既有 11 粗类；六处置（retryable/repairable/needs_user_data/needs_user_approval/unsafe_to_retry/terminal）为 registry 驱动 property（单一事实源）。
- M2 `RecoveryPolicy` 纯决策：自动重试仅限 retry_safe 工具（复用既有 SideEffectClass）或幂等键 state_mutation；tier-3/付费/导出/写永不自动重试并挂封闭确认门类别；确定性退避 schedule（无 sleep、无执行）。
- M4 `GoalVerification.v1` 对账层：8 slot×4 态；渲染观察缺失/科学证据不足永不 pass；goal satisfied 无渲染证据记 contradiction；user-wins 隐藏层至多 warning 且不代用户显现。
- M5 `RetryDirective`：只重试 failed/unavailable 能力行、保持已验证 caps/refs、指向 #1869 recompute seam；ref 归属不可证明即 fail-closed。
- M6 四段解释（what_failed/safe_to_try/needs_you/already_done）+ 四类处境。
- M7 `recovery_corpus`：29 案例×21 类别，每条含 input_state/allowed/forbidden 动作，三 oracle 族驱动生产纯函数；RC- 注册进语料 manifest。
- M8/M9 性能安全回归 + E2E 5 场景（真实 ToolDispatchService seam）。

**复用**（不重建）：HarnessFailureClass 11 类词表、RecoveryLedger、repair_planner/runtime_repair、ADR-0183 goal_satisfaction（只新增消费投影）、analysis_workflows 选择性重算、GisTraceChain（additive payload + 补 runtime_repair 侧 REPAIR 发射）、SessionPlan（additive `last_recovery`）、SideEffectClass、既有确认入口。

**最小挂接**：tool_dispatch_service（错误分支 + 存量键名 bug 修复）、pi_post_dispatch 语义（llm_payload/raw_result 同源）、execution_engine step_error、session_plan 投影行、completion/pipeline（goal_verification 块 + SSE 投影 + user-wins 来源）。

## 顺带清偿的既有缺陷（均有失败测试先证）

1. 粗分类器 429/5xx 落 `unknown`（预算 1）/`budget_exhausted`（预算 0，首错即 abort）——并入 timeout 词汇（词边界+语境双门防 "1500 features" 类误报）。
2. TDS 记忆候选读错键 `failure_class`（实际 `class`，恒 unknown）。
3. continuation fail-closed 理由回显他会话 ID（跨租户泄漏面）。
4. policy 预算门误终结 needs_user 路径。

## 测试

- 新增 **207** 用例全绿（recovery_policy 6 文件 191 + goal_verification 21 语料/对账门等）
- 存量回归：tests/unit/gis_harness + tests/unit/lib + tests/unit/evaluation + tests/cartography = **7461 passed / 7 failed**——7 个失败全部为基线存量（git stash 双向复现核对，见 EVIDENCE.md）
- M8 性能/安全门双跑一致；E2E 3 恢复成功 + 2 安全终止
- 未跑项：真实远端 429/5xx 集成（fixture 覆盖）、真实 Pi 子进程（TDS 同源覆盖）、前端 UI 渲染（SSE additive 字段未改前端）

## Review

两轮独立 review（第一轮 0P0/5P1/8P2/5P3 → 全部清偿于 6dd206593；第二轮复审记录见 INDEPENDENT_REVIEW.md），无未解决 P0/P1。

## 风险与边界

- 不改 02 协议 adapters / 03 时间轴 / 04 比例尺表达核心；open PR #1876/#1877/#1878 无文件交集（已核对）
- R7：Pi 子进程端到端未起真实进程（双引擎收敛于 TDS rendezvous，如实披露）
- 手工验证：`.venv/bin/python -m pytest tests/unit/gis_harness/recovery_policy -q --no-cov`
