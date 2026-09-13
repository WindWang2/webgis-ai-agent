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
- 接线点：`completion/pipeline.py` `map_product_block` additive 键 `goal_satisfaction`；`read_stored_map_product` additive 键；`runtime_state_machine` advisory 投影。
- 测试：接线后 task_complete 既有语义零漂移（回归锁）；additive 键存在性。
- 状态：DONE

## M4 — G6 反作弊语料（counterfactual）

- 目标：任务书列出的 8 类注入场景全部落语料，false-PASS 必须为 0。
- 文件：`app/evaluation/goal_satisfaction_corpus.py`（counterfactual 分片）。
- 状态：DONE

## M5 — G9 验收语料 ≥100 cases + 指标

- 目标：zh/en、single/multi-goal、矩阵全覆盖；`false_pass_rate == 0`、`not_evaluated_policy` 锁定。
- 状态：DONE

## M6 — 独立 review + 修复 + PR

- 四轴（Spec/Architecture/Reliability/Performance+Security）复核；P0/P1 全修；review 修复单独 commit；PR 创建不合并。
- 状态：PENDING
