# ADR-0204-f03：Harness Lifecycle Convergence —— canonical lifecycle 成为生产唯一生命周期语言

状态：Proposed（随 F03 PR 交付）。上位 ADR：0204-harness-kernel-canonical-turn-lifecycle-and-ownership（#1485）、0204-pi-harness-ownership-boundary（#1481）、0180（K9 遗留）。

## 背景

#1485 建立了 canonical TurnPhase/event journal/ownership matrix，但其 Out of Scope ——
`agent_settled` 多触发结算、`refused`/`aborted` 生产发射者、`map_mutated` 发射缝、
StageState/GoalNodeStatus 投影收敛、error 分支语义 —— 以及勘察新发现的两个 P1
（未分类异常结算成 completed；abort 后 turn 结算成 completed）使 canonical lifecycle
尚未成为生产唯一生命周期语言。本 ADR 给出收敛决策；recon 证据见
`docs/dev/f03-harness-lifecycle-recon.md`，字段级矩阵见
`docs/dev/f03-lifecycle-ownership-matrix.csv`。

## 决策

### D1 单结算 seam（bridge）

`PiBridge._settle_turn_outcome` 是 stream/non-stream × success/error/cancel/timeout/
process_died/abort 全部终点的唯一结算序列：状态映射（`_hk_turn_status` 扩展
`error`/`abort_source` 维度）→ 拒答降级判定 → 投影管线（outcome-aware）→ kernel
`end_turn` → tracker 结算。两个 finally 块只调 seam；clean 路径在 try 内的投影调用
保留（SSE 事件序不变），seam 以 `projections_settled` 幂等去重。幂等性继承 kernel
`end_turn`（重复结算 no-op）与终验幂等门。

### D2 错误语义（不伪装成 completed）

`TurnSettleOutcome`（frozen）区分 clean/cancelled/aborted/failed。非 clean 结算走
reduced settle：**跳过**完成度终验与 map_finalization 披露（完成度奖励只属于 clean
turn），**补齐** WorkflowInstance、RuntimeState(turn_settled)、九域 checkpoint、
证据链 USER_OUTPUT + persist（错误同样有可回放证据）。V7 trigger 维持
`execution_settled`（V7 是任务级 DAG 词表；turn 级诚实终态在 kernel，不扩 V7 词表）。

### D3 aborted / refused 生产语义

- `aborted` = policy/system 发起的运行中止（governor/看门狗/会话删除）。
  `PiBridge.abort(source=...)` 单点记录来源；结算映射中 abort_source 优先于失败族：
  `user→cancelled`、`system/policy→aborted`。与 `failed`（工具/执行错误，可重试）、
  `cancelled`（用户发起）的语义边界与 models.py 词表注释一致。
- `refused` = 执行前结束且无所失物。真实生产判据（零新策略发明）：clean settle +
  本 turn 零执行活动（`tool_calls==0` 且零步骤被本 turn 触碰）+
  `chapter.intent.clarification` 存在未解决问题（与 evaluator
  REQUEST_CLARIFICATION 谓词同源）。kernel 纯读 `turn_refusal_candidate`，仅 clean
  结算允许 completed→refused 降级；失败族/取消族永不降级。

### D4 map_mutated 事件缝

`map_mutated` 从 RESERVED 转正。发射缝唯一：`apply_gis_mutation` /
`apply_gis_mutation_batch` post-success 块（E1 通知平面同位，never-raise，revision
关联）。kernel `record_map_mutation` 以 `causal_id=mutation_id` 幂等；归因只信
`envelope.turn_id`——不猜测活跃 turn（迟到回调归原 turn、detail.late=true，绝不
重开 turn/推进 phase/污染 successor）。dispatch 链路向 store 透传 turn_id 的深化
属 F13 邻域，本方向不做（Out of Scope）。

### D5 投影 adapters 与 parity 观测

phase_adapter 增 `render_stage_view`（TurnStatus→StageState 族）、
`render_goal_view`（TurnStatus→GoalNodeStatus 族）、`terminal_parity`
（canonical 终态 vs V7 实测相位的确定性判定）。settle 管线消费 adapter 输出一条
有界 parity 行（hk.metrics 计数）。失真只观测不修（V7 派生函数的收敛属后续方向），
保持「同一事实多处独立判断」向单一投影面的迁移是**加法**且可回滚。

### D6 重放不变量（测试 oracle）

1. 终态冻结：terminal phase 无出边；终态 status + 运行相位不可共存。
2. `event_seq` 信封内单调；supersede 重建迁移 seq（重放序 = seq 序）。
3. 因果事件按 `event_id` 幂等：锁重试/重复回调/迟到重投不双写。
4. restart：begin_turn 把仍 running 的旧 turn 走表终态化为 interrupted。
5. 迟到事实（tool_late/map_mutated-late）归原 turn，永不重开或推进 successor。

以上由 seeded generative（仓规：不用 hypothesis）+ 转移图 oracle + 多会话 chaos
测试钉住（`tests/unit/test_harness_lifecycle_properties.py`）。

## 兼容性

- `settle_turn_projections` 签名向后兼容（outcome=None ≡ 旧行为逐位一致）；
  既有 clean-parity 测试面不变。
- `abort(source=)` 默认 "user"：现有调用方行为收敛为诚实 `cancelled`（此前误结算
  completed 的修复是有意契约变更，PR 标注 pre-existing P1）。
- `map_mutated` 为增量事件词（旧读者按 unknown-kind 透传）；RESERVED 清空记档。
- V7/StageState/GoalNodeStatus 零写入面改动。

## Out of Scope

- agent_settled 之后 Pi vendor 侧行为（loop 所有权不动）。
- prompt 组装切换 HarnessTurnContext（#1485 已声明，另行窗口）。
- dispatch→store 链路 turn_id 透传深化（F13 邻域）。
- V7 derive_runtime_state 与 canonical 的派生逻辑合并（仅 parity 观测）。
- Governor admission 语义（F08 热区）。
