# GIS Harness Failure Taxonomy

本文是可执行的失败分类索引。判定来源是既有 `GisTraceChain` / `TurnEvidence` / `ToolDispatchService` / `RecoveryPolicy` / `GoalVerification`，以及 G07 的只读 `project_journey_evidence` 投影；不新增第二个失败分类器或 trace store。

## 状态词

- `pass`：该阶段有直接证据；整体通过还要求所有必要阶段及目标所需的 export。
- `partial`：只有部分证据或 at-least-once 重复投递，不能当完整完成。
- `fail`：存在矛盾、冲突或明确失败证据。
- `blocked`：取消、权限、外部依赖或安全策略阻止继续。
- `not_evaluated`：缺少可裁决证据，**既不是 pass，也不是 0 分样本**。

`command accepted`、`tool dispatched`、`tool result validated`、`artifact persisted`、`MapSpec applied`、`map rendered and observed`、`goal satisfied`、`publication/export verified` 是不同声明，不得相互补写。模型文本不构成任何一项的完成证据。

## 边界与 reason codes

| 边界 | 代表 reason codes | 终态/诊断 | 安全恢复资格 |
|---|---|---|---|
| Pi/RPC、turn | `cancelled`、`timeout`、`outcome_failed`、`turn_mismatch`、`noncanonical_stage_order` | cancelled → blocked；失败/身份或顺序矛盾 → fail | 按既有宿主重连/新 turn；迟到 turn 不得写入 |
| Tool surface / dispatch | `tool_unregistered`、`dispatch_refused`、`dangling_tool_result` | 拒绝或悬空 → fail | 仅既有 fallback/重读 schema；禁止平行执行器 |
| GIS algorithm / provider | `postgis_unavailable`、`tool_result_error`、`upstream_server_error`、`rate_limited` | 明确工具失败 → fail；瞬态由 RecoveryPolicy 判 retryable | 只对 retry_safe/幂等读按预算 retry/backoff；外部副作用不自动重试 |
| Artifact / ref | `artifact_persistence_unverified`、`conflicting_tool_result`、`stale_ref`、`cross_session_ref` | 缺持久证据或冲突 → fail；相同重复 → partial | 重新拉取只读元数据/重放幂等操作；跨 session/owner 永不自动恢复 |
| MapSpec / scope | `mapspec_application_unverified`、`scope_ref_mismatch`、`goal_mismatch`、`operation_mismatch` | 未应用/范围或身份不一致 → fail 或 needs_user_data | 只接受新的合法数据/用户澄清；不得换文本掩盖错误城市 |
| Renderer | `render_observation_absent`、`renderer_context_lost`、`tiles_missing` | 缺观察 → not_evaluated；context loss → fail；缺 tiles → partial，task_complete 均不得通过 | `reobserve` 只是建议；真实新观察到达后才重验，不能自动宣称修复 |
| Export / publication | `export_not_verified`、`export_failed` | 未验证 → not_evaluated；失败 → fail | 导出/发布走现有权限与确认，不自动重放副作用 |
| Settlement | `task_complete_without_render_observation`、`lock_lost`、`duplicate_tool_callback` | 伪完成/锁丢失 → fail；相同重复投递 → partial | 取消/锁丢失先停写；完成门和锁实现保持既有权威 |
| Owner / correlation | `session_mismatch`、`correlation_mismatch`、`permission_denied` | fail 或诊断 blocked | 无自动恢复；诊断拒绝不得回显其他 owner ID |

## 恢复实验对照

1. **缺观察**：`diagnose_goal_gap` → `reobserve` 建议 → 新观察后 `reconcile_goal_verification=pass`。
2. **瞬态读失败**：503 → `upstream_server_error` + `retry_with_backoff` → 真实 `ToolDispatchService` 第二次成功。
3. **跨城不一致**：`product_scope_geography=mismatched` → `ask_user_data`，不能盲目重试；合法新 scope/ref 才可重新验证。
4. **持久编辑**：state_mutation 且无可验证幂等键 → `owner_write`，不得自动 replay；取消时零补偿动作。

## 诊断数据最小化

`scripts/harness_journey_diagnose.py` 只接受本地 ReplayTrace JSON（最大 2 MiB），必须同时给 `--session-id` 与 `--turn-id`。报告不含工具参数、token、URL、用户文本或 FeatureCollection；其他 owner 的 ID 不回显。默认输出最多 32 KiB，硬上限 64 KiB，按真实 UTF-8 bytes 计算。CLI 不持久化报告，因此没有新增 retention store 或 TTL 状态。

参考实现与测试：

- `app/lib/harness/replay/journey.py`
- `app/services/gis_harness/recovery_policy/`
- `tests/harness_replay/test_journey_faults.py`
- `tests/unit/gis_harness/recovery_policy/test_reliability_recovery_experiments.py`
- `tests/unit/gis_harness/test_reliability_selection_scope.py`
