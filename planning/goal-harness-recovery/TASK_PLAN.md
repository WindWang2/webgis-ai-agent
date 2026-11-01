# TASK_PLAN — GOAL 05

分支：`zcode/goal-gis-harness-recovery-v1`；worktree：`../webgis-wt-goal-harness-recovery`。

## 验收 Oracle（可判定）

1. 所有恢复决策基于唯一 `GISRecoveryDiagnostic.v1`（schema `gis_recovery_diagnostic.v1`），reason codes 封闭（测试钉死全集），无循环自激/无界重试（预算继承 REMEDIATION_POLICY ≤3 + 诊断层不新增执行循环）。
2. Pi 与 fallback ChatEngine 都能拿到同一诊断（经 TDS 单 seam 产出；两边 additive 消费）；不生第二 runtime/trace store（复用 GisTraceChain/ToolDispatchService/SessionPlan）。
3. 安全重试/危险拒绝有本地证据：mutating/paid/export/destructive/cross-session 永不自动重试（策略纯函数测试 + TDS seam E2E）；tier3 仍走既有确认门。
4. `goal_verification.v1` 对账块：L1-L5 ↔ GoalSatisfactionReport ↔ observation ↔ data qualification；渲染观察缺失不能 pass、科学证据不足不能 pass（false-PASS 语料钉死）。
5. 已成功子图保持；重试 directive 只指向 failed/unavailable 能力行与 stale 工作流步骤（消费 #1869 recompute）；跨 session ref fail-closed（测试钉死）。
6. recovery_corpus ≥25 案例（每条含 input_state/allowed_actions/forbidden_actions/expected disposition）全部通过；3 个恢复成功 E2E + 2 个安全终止 E2E 通过；性能/安全回归可复跑两次结果一致。
7. 两轮独立 review 无未解决 P0/P1；PR 创建成功（不合并）。

## 里程碑与产出

| M | 产出 | 测试门 |
|---|---|---|
| M0 | BASELINE/PR_OVERLAP_MATRIX/DECISIONS | 勘察报告落盘（本轮已完成） |
| M1 | `recovery_policy/contracts.py`（diagnostic/disposition/reason 词表）+ `diagnose.py`（工具失败/eligibility/no-progress/mapspec/goal 五族诊断构建，纯函数） | test_contracts（词表封闭/有界）+ test_diagnose（映射矩阵 ≥30 断言组） |
| M2 | `recovery_policy/risk.py` + `policy.py`（decide_recovery 纯决策：风险×处置×预算×确认门；backoff 纯计算不 sleep） | test_policy_guards（危险动作全拒矩阵 + 预算耗尽 + 取消即停语义） |
| M3 | TDS `recovery` 键 + pi_post_dispatch 透传 + bridge 详情 + step_error 扩展 + [GIS Recovery] 投影行 + trace 阶段 11 payload 扩展/16 REPAIR 发射 | test_wiring（两路径可见性 + additive 零破坏既有 216 存量恢复测试） |
| M4 | `app/lib/harness/goal_verification/reconcile.py` + pipeline additive 块 + SSE 投影 | test_reconcile（false-PASS 反例 ≥10：渲染缺失/科学不足/隐藏层 user-wins） |
| M5 | `recovery_policy/continuation.py`（retry directive 纯函数 + 投影披露） | test_continuation（保持已验证/只重试失败子图/跨 session fail-closed） |
| M6 | `recovery_policy/explain.py`（四段解释，进 llm_payload 尾注/SSE/投影） | test_explain（四类区分：没算好/算好没显示/显示未验证/导出失败） |
| M7 | `app/evaluation/recovery_corpus.py` ≥25 案例 + runner（复用 simulate_agent_loop 机制） | test_recovery_corpus（全通过 + 类别覆盖 + 禁止动作从未执行断言） |
| M8 | 性能对照（payload 增量/纯函数 P95/重试风暴上限）+ 安全回归（身份注入/跨会话） | 跑两次结果一致的确定性门 |
| M9 | E2E 5 场景 + 双轮独立 review + ADR-0279 + PR | E2E 全绿 + review 清偿记录 |

## 红线（每轮自检）

- 不改 failure_taxonomy 11 类词表；不改 goal_satisfaction 评估器判定；不新建 trace store/调度引擎/确认入口。
- 恢复动作执行仍归：LLM 自愈（llm_payload）、runtime_repair、finalizer、`webgis_analysis_workflow_recompute`、既有 owner 确认端点。
- 诊断/解释不含跨租户信息、不含 LLM 私密上下文原文；ID 只用本会话已有 ID。
- 不 sleep 阻塞事件循环（backoff 只给 schedule，由既有 vendor/LLM 循环消化）。
- 取消（OperationCancelled/CancelledError）立即中断，不产生任何补偿副作用。
