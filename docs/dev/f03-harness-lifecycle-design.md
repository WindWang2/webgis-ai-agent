# F03 — Harness Lifecycle Convergence — Design

- Date: 2026-09-26（实现） / 2026-09-27（本文为合并后补记的 design，G06 文档债补齐）
- ADR: `docs/adr/0204-f03-harness-lifecycle-convergence.md`
- Recon: `docs/dev/f03-harness-lifecycle-recon.md`；Review: `docs/dev/f03-lifecycle-review.md`；Ownership: `docs/dev/f03-lifecycle-ownership-matrix.csv`
- 实现基线: `origin/master @ 9e1ad229`；Merge: PR #1506（`0cffb226`）
- 状态: 已合并（merged）。本文全部内容以合并后代码为事实源，可 rg 核对。

## 目标与红线

把 turn 生命周期结算收敛到**单一 seam**，消灭「错误伪装成完成」：#1485 交付的
canonical TurnPhase/event journal 只有 clean 路径跑投影，error/cancel/timeout/
process_died 半途而废；`refused`/`aborted` 词表就绪但无生产发射者；`map_mutated`
只有占位。红线（写入 ADR）：**turn 终态只能由
`app/services/harness_kernel/runtime.py::GISSessionRuntime.end_turn` 写**；
bridge/tracker/V7/StageState 全是投影，投影失真用 parity 观测暴露，不开第二写口。

## 修复的两个 pre-existing P1

1. **未分类异常 → kernel `completed` 假阳性**：non-stream generic `except` 不置
   settle flag、stream 路径根本没有 generic except，任何异常都结算成 `completed`
   而 `rt_ev` 已记 FAILED。
2. **用户/系统 abort 后结算成 `completed`**：`PiBridge.abort()` 发 RPC + 点燃
   turn token，但结算映射不读 token，vendor 正常发 `agent_settled` → kernel
   `completed` 与 tracker cancel 真相矛盾。

## Module map（改动面）

| 模块 | 状态 | 内容 |
|---|---|---|
| `app/agent_pi_bridge.py` | extended | 单结算 seam `_settle_turn_outcome`（stream/non-stream 两个 finally 收敛调用）；`_hk_turn_status` 扩展 `error`/`abort_source` 维度（优先级序 `cancelled 旗标 > abort_source > 失败族 > completed`）；`PiBridge.abort(session_id, *, source)` 记来源到有界 turn 级台账（`_TURN_ABORT_SOURCES`，同步块操作、record→pop 间无 await）；tracker 结算收敛进 seam；`_ABORT_SOURCE_STATUS` 单一定义点 |
| `app/services/chat/pi_post_dispatch.py` | extended | `TurnSettleOutcome`（frozen dataclass）+ `settle_turn_projections` 的 outcome-aware 分支：非 clean 结算**跳过完成度终验与 map_product 披露**（错误不伪装成 completed），但补齐 WorkflowInstance / RuntimeState(turn_settled) / checkpoint / 链 USER_OUTPUT+persist（错误有可回放证据）；`outcome=None ≡ 旧行为`（签名向后兼容）；`log_lifecycle_parity` 在 `_safe_kernel_end_turn` **之后**调用（terminal parity 才有值） |
| `app/services/harness_kernel/runtime.py` | extended | `end_turn`（唯一终态写者，:614）；`record_map_mutation`（:1131，幂等 causal_id=mutation_id，迟到归原 turn `detail.late=true`，绝不重开/推进 phase）；`turn_refusal_candidate`（:1182，纯读）；`_stamp_clarification_turn`（:240）落章 `raised_turn_id`（review P2-1 修复：判定要求提出者==本 turn，防遗留开放澄清导致误降级） |
| `app/services/harness_kernel/models.py` | extended | `map_mutated` 从 `RESERVED_EVENT_KINDS` 占位转正进 `EVENT_KINDS`（现 `RESERVED_EVENT_KINDS = ()`）；TurnStatus/TurnPhase 词表不变 |
| `app/services/harness_kernel/phase_adapter.py` | extended | 投影渲染器：`render_stage_view` / `render_goal_view` / `terminal_parity`（canonical vs V7 实测对账，失真仅计数+日志）；`project_runtime_phase` 既有映射保留 |
| `app/services/gis_world_state/mutation.py` | extended | `apply_gis_mutation` post-success 块发射 `map_mutated`（镜像 `notify_map_mutation` 纪律：never-raise、revision 关联；归因只信 `envelope.turn_id`，无信封静默跳过） |
| `app/services/chat/session_cancellation.py` | extended | 用户取消走 `bridge.abort(source="user")` 真实触发面（session 删除路由=system、no-progress watchdog=policy） |

## 结算语义（normative）

1. **单 seam**：bridge 两个 finally 只调 `_settle_turn_outcome`；clean 路径的
   in-stream/in-try 投影调用保留（SSE 事件序不变），seam 经 `projections_settled`
   幂等去重 —— abort-after-settled / timeout+policy / process-death+user 等角落
   下终态仍诚实（review 确认项 B）。
2. **abort 映射**：`abort_source` 优先于失败族：user→`cancelled`、
   system/policy→`aborted`；fallback 无会话守卫会串账 → 补
   `_current_turn.session_id == session_id` 守卫（review P3-4 修复）。
3. **refused 判据**（ADR-0208「执行前结束、无所失物」的生产化）：clean settle +
   本 turn 零执行活动（零 tool_calls 且零步被本 turn 触碰）+ 未解决澄清**由本
   turn 提出**（`raised_turn_id` 章）。kernel 纯读 `turn_refusal_candidate`，
   单 seam 在 `end_turn` 前降级 completed→refused；无章/别 turn 提出 → 永不降级
   （保守偏向既有 completed 语义）。
4. **错误分支 reduced settle**：跳过完成度终验/map_product 披露，其余投影、
   checkpoint、链证据照常落 —— 终验/证据链不再只属于 clean 路径。
5. **重放不变量**：终态冻结、`event_seq` 单调、重复回调/回调重投幂等、supersede
   迁移 event_seq、restart 中断相位终态化 —— 全部由 property 套件钉住。

## 测试计划

| 文件 | 覆盖 |
|---|---|
| `tests/unit/test_turn_settlement_seam.py` | seam 结算矩阵（clean/error/cancel/timeout/abort×source），abort 参数化断言 `settle_calls==1` |
| `tests/unit/test_f03_kernel_lifecycle_events.py` | `map_mutated` 接线/幂等/迟到归原 turn、refused 降级、stale-clarification FP 回归 |
| `tests/unit/test_harness_lifecycle_properties.py` | seeded generative 游走 + 图 oracle + 幂等 fuzz + 多会话 chaos + 重启终态化 |
| `tests/unit/test_pi_cancellation_unified.py` | 走真实 `bridge.abort`（fake 签名 `source=` 过期属 pre-existing，本 PR 修复后 9/9 绿） |

邻域回归：kernel / pi_post_dispatch / parity / e2e / bridge / cancellation /
gis_world_state / mapspec / map_product。review 终裁：无 P0，2×P2 + 6×P3 全部
处置（修复或记录理由）；F03 三套件 + parity + kernel lifecycle 55 passed。
