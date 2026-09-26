# F03 — 独立 Review 记录（Subagent C）

Review 对象：`9e1ad229..HEAD`（本分支全部提交）。Review 维度：单一真相/第二机制、
seam 正确性、并发与锁、幂等/重放、迟到归因、有界性、双路径 parity、兼容性、
测试质量（green-by-construction 检查）、user-wins 与数据隔离。Review 全文结论：
**无 P0；2×P2、6×P3**；"核心收敛扎实，P2 修复后可 PR"。

## Review 发现与处置

| # | 级别 | 发现（file:line 证据见 review 原文） | 处置 |
|---|---|---|---|
| 1 | P2 | `turn_refusal_candidate` 对遗留未解决澄清存在误降级：代码库无任何 `status="resolved"` 写者，旧章节的开放问题永久存活 → 后续任意零执行 turn 会被 completed→refused 误降级 | **已修复**：intent 路径落章时盖 `raised_turn_id` 章（`_stamp_clarification_turn`），判定要求提出者 == 本 turn；无章/别 turn 提出 → 永不降级（保守偏向既有 completed 语义）。新增 4 个回归用例（含 stale FP 回归） |
| 2 | P2 | `log_lifecycle_parity` 在 `end_turn` 之前被 settle 管线调用 → terminal parity 恒为 None，生产路径死代码 | **已修复**：parity 行移入单结算 seam 的 `_safe_kernel_end_turn` 之后；settle 管线内的调用移除；调用方契约写入 docstring |
| 3 | P3 | `map_mutated` 加剧 24 行 journal 环争用（地图编辑密集 turn 挤压 tool_* 证据行） | **记录为 follow-up**（每 turn 折叠/独立环界/抬高 MAX_DECISIONS 三选项留后续；滚动部署期旧 pod 校验上限约束仍在，不动界） |
| 4 | P3 | `abort()` fallback 无会话守卫，可能给别的会话的 turn 记结算来源 | **已修复**：fallback 补 `_current_turn.session_id == session_id` 守卫（与 token 解析同款） |
| 5 | P3 | abort-vs-clean 竞态窄窗：abort 在内联门读取后、seam pop 前到达 → clean 投影已跑而终态记 aborted（诊断/投影奖励面，kernel 终态仍诚实） | **记录不修**（窗口为几个 await；终态正确性不受影响；seam 内再检一次的复杂度收益比不佳） |
| 6 | P3 | aborted 终态的 failure_class 四处字串漂移 | **部分修复**：rt_ev 两侧归一为 `pi_aborted`；seam 的 `TurnSettleOutcome.failure_class` 保留更细 `pi_abort_<source>`（单一定义点 `_ABORT_SOURCE_STATUS` 邻接，docstring 注明差异面）；tracker 的 aborted fail detail 改为 "turn aborted (system/policy stop)" |
| 7 | P3 | 台账 64 界极端并发下可回退到旧行为 | **记录不修**（每进程 64 个同时在飞未结算 abort turn 不可达；UUID turn id 碰撞不现实） |
| 8 | P3 | cancel 恰好落在内联 settle await 内时链侧可能双记 USER_OUTPUT（各腿幂等，链 dup guard 按 (turn_id, total_records) 键控） | **记录不修**（罕见 + 有界；属既有 trace_store 语义） |
| 9 | P3 | 测试缺口：(a) client-cancel 优先级用例记录键错误（session id ≠ turn uuid，断言空转）；(b) abort 路径未断言 settle 恰跑一次；(c) 无 stale-clarification 用例 | **已修复**：(a) 重写为走真实 `bridge.abort`；(b) abort 参数化用例补 `len(settle_calls)==1`；(c) 由 #1 修复新增用例覆盖 |

## Review 确认的关键面（摘）

- `end_turn` 仍是唯一终态化器；无第二事件总线/第二状态权威（A）。
- 优先级序 `cancelled 旗标 > abort_source > 失败族 > completed` 在 abort-after-settled、
  timeout+policy、process-death+user 等角落下语义正确（B）。
- `_TURN_ABORT_SOURCES` 全部同步块操作、record→pop 间无 await；`record_map_mutation`
  锁纪律与 `record_late_callback` 同款、锁序不倒置（C）。
- `end_turn` 重复结算 no-op；`event_seq` 单调保持并由 property 套件钉住（D）。
- 兼容性：`settle_turn_projections` 全部既有调用方 `(sid, turn_id)` 位置式，
  `outcome=None` 逐位还原旧行为；`map_mutated` 为增量词，旧读者透传（H）。
- user-wins：emit 块位于 user-presentation 守卫之后、仅 success 且非
  superseded/duplicate；`origin=user → host="unknown"` 合法（J）。
- 既有失败归属：`test_lock_degraded_maps_to_stale_not_500` 在纯净基线
  9e1ad229 同样失败，与本分支无关。

## 修复后回归

- F03 三套件 + parity + kernel lifecycle：55 passed（`-n 0 --no-cov`）。
- 邻域（kernel/e2e/bridge/cancellation/gis_world_state/mapspec/map_product）：
  全绿；仅上表所列 master 既有失败保持原样。
- ruff 全绿。
