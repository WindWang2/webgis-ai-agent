# Goal Satisfaction Evaluator — Milestone Ledger

每个 milestone 追加：目标 / 改动文件 / 新·改契约 / 测试 / 证据 / 兼容 / 回滚面 / 未解决项。

## M0 — Phase 0 勘察（2026-09-14）

- 基线 SHA：`580b33e923e992cd6706659d455dfd72ef55d033`；worktree `../webgis-goal-eval`；branch `harness/goal-satisfaction-evaluator-v1`。
- 产出：recon / decisions（ADR-0183 预订）两文档；重叠矩阵（recon §4）。
- open PR 复核：#1270 CI hygiene（不吞）；#1273-#1279 harness 并行线（无任务语义层 owner，待 Subagent A 报告确认细节）。
- 未解决项：Subagent A review comments 归档；确认 #1273-#1279 实际触碰文件清单。

## M1 — G1+G2 契约层（goal_requirement.v1 + evidence registry）

- 目标：GoalRequirement/GoalContract schema + EvidenceRegistry 纯投影。
- 文件：`app/services/gis_harness/goal_satisfaction/`（新包：`contracts.py` `requirements.py` `evidence.py`）。
- 契约：`goal_requirement.v1`、`goal_evidence.v1`；全序列化、有界。
- 测试：schema round-trip、派生确定性、边界截断。
- 状态：DONE

## M2 — G3+G4+G7+G8 评估器（deterministic + multi-goal + quality 消费 + explainability）

- 目标：`evaluate_goal_satisfaction` 纯函数；逐 requirement 证据裁决；全局 verdict + harness_signal；explainability 投影。
- 文件：`app/services/gis_harness/goal_satisfaction/evaluator.py`（含 signal + explain）。
- 测试：单 req fulfilled/partial/blocked/not_evaluated/failed 全路径；multi-goal 逐项 ledger；visual PASS 不救 missing evidence；stale artifact 不 PASS。
- 状态：DONE

## M3 — G5 生产接线

- 目标：evaluator 进入 default Pi path（非测试孤儿）。
- 接线点：`completion/pipeline.py` `map_product_block` additive 键 `goal_satisfaction`（单一评估点）；`maybe_finalize_map_product` 传 `_cartographic_review` 证据 + goal 信号=replan 路由既有 `request_replan`；`read_stored_map_product` additive 键；`finalization_sse_payload` + task_complete SSE additive 键；`format_session_plan_projection` `[GIS Goal]` 行（5 条 return 路径）；`MapCompletionResult.goal_satisfaction` 透传字段（不入 to_dict）。
- 测试：`test_goal_satisfaction_wiring.py` 7 例；被接线面既有 120 例零漂移。
- 关键决策：task_complete 折叠语义**不变**（advisory，D-007）。
- 状态：DONE

## M4 — G6 反作弊语料

- 目标：任务书 8 类注入场景全部落语料，false-PASS 必须为 0。
- 文件：`app/evaluation/goal_satisfaction_corpus.py`（`GC-cf1..cf8` 命名案例 + `must_not_pass` 指标）。
- 结果：8/8 命名反事实案例在语料内锁定；语料驱动出 4 个真实 evaluator 语义修复（PASS_CAPABLE 强制、BLOCKED_BY_DATA token 归因、块上 cartography 摘要读取、阈值纯分数语义）。
- 状态：DONE

## M5 — G9 验收语料 ≥100 + 指标

- 结果：**101 案例**（14 族：matrix/product/data/export/fallback/scope-filter/multi-goal/language/user/contract/counterfactual/quality/honesty/spec-edges/combinations），`false_pass_rate == 0` 测试锁死；zh/en、single/multi-goal 全覆盖。
- 测试：`test_goal_satisfaction_corpus.py` 4 例（≥100、G6 命名案例在位、全绿、指标形状）。
- 状态：DONE

## M6 — ADR + 回归 + 独立 review + PR

- ADR-0183 落盘。
- 本地回归（2026-09-14，Windows/GitBash）：`tests/unit/gis_harness/` 全量 1341 passed / 2 failed / 4 skipped。**失败归因（干净 origin/master `580b33e9` 对照复跑同样失败）**：① `test_benchmark_harness.py::test_golden_cases_no_semantic_regression`（G4 语义回归）；② `test_component_lifecycle.py::TestUserRemoveWinsOverRepair::test_user_removed_title_not_resurrected[trio]`。**两者均为 master 预存失败，与本任务无关（本任务不触碰 planner/runner/component lifecycle 路径）；本任务回归 = 0。**
- Subagent B 四轴独立 review + P0/P1 修复（单独 commit）。
- 状态：IN_PROGRESS
