# INTERFACES — G22-PI-STATE-CONTINUITY

## 本任务独占文件（G12 移交 + G22 新增；其他任务勿改）

- `vendor/pi`（gitlink → v1.5.2 目标）、`scripts/build-vendor-pi.sh`
- `app/agent_pi_bridge.py`（turn 收尾/准入/abort seam）
- `app/services/chat/pi_*.py`（rpc_client/event_mapper/native_surface/surface_receipt/
  turn_context/turn_state/compat/durable_cancel/bridge_surface_gate 等）
- `app/services/context_assembly/providers.py`（GisStateRebuildProvider 扩展）
- `app/extensions/webgis-tools/index.mjs`
- `docs/PI_UPSTREAM_COMPAT_MATRIX.md`、`docs/PI_RUNTIME_CONTRACT.md`、pi_doctor
- Pi 专属测试：`tests/integration/test_pi_*`、`tests/test_pi_*`、
  `tests/unit/context_assembly/test_gis_state_rebuild_provider.py`

## 消费的既有契约（只读/最小 additive）

| 契约 | 用法 | 变更 |
|---|---|---|
| MapSpecStore / mapspec_lifecycle_engine / CAS / agent_undo | 权威地图状态读取（revision/frames/label authoring/workbench locks） | 只读投影 |
| `app/contracts/workbench_locks.py` | 用户锁定层/组件读取 | 只读 |
| `app/lib/cartography/label_authoring.py`（G16） | 标注 override（hidden/pinned/offset/text/callout/priority）读取 | 只读 |
| `frame_products/service.py`（G15） | active frame/sync group 状态读取 | 只读（`frame_product_status`） |
| `analysis_tasks` 表 / DurableJobStore / workflow_runtime | 恢复时任务清单与取消态读取 | 只读 |
| `turn_journal`（ledger/resume） | 恢复计划（unsettled turns/hanging steps/last_consistent_revision） | 只读消费 + 摘要投影 |
| `harness_kernel`（turn 台账/settle） | 唯一结算编排 | 不改 |
| ToolRegistry / ToolDispatchService / dispatch 闸序 | 工具执行唯一权威 | 不动闸序 |
| `session_data` / Data Fabric ref 生命周期 | 数据 ref 有效性判定 | 只读 |
| G10 refresh（project_runtime/refresh） | 数据版本/有效性投影 | 只读 |

## 对外暴露（additive）

1. **turn 身份边界**（P1）：abort 意图携带 turn_id；写 RPC 前在 turn 锁下
   原子重验（session_id + turn_id 双匹配）；turn 准入与前一 turn 取消动作
   完成之间的序列化边界。无新 vendor API（不发明 turn-scoped RPC）。
2. **GIS 恢复目录 v2**（P2）：`gis:state-rebuild` 块扩展字段（全部 additive）：
   `locks`（lockedLayerIds/Components）、`label_overrides` 计数+代表项、
   `active_frame`、`undo`（最近 undo 点 revision）、`jobs`（非终态任务
   id/状态/工具名）、`data_refs`（无效 ref 披露）、`constraints_full`（超
   24 层时的全量用户约束层 id 寻址）。域 1600 cap 不变；详情走既有只读
   工具/路由（不新开大 projection）。
3. **恢复状态摘要**（P6）：chat 路由在会话恢复/turn 中断时输出 additive
   SSE 事件或 task 字段（`recovery` 小组件数据源：恢复到的 revision、
   失效引用、任务续跑/取消）。技术 ID 只进诊断。
4. N4：J7 llm_calls 计数观测修复（json.dumps separators 对齐 node 紧凑输出）。
5. Redis `webgis:pi:active_turn:*` 启动对账（重启后清扫陈旧 key）。

## 共享文件触碰清单

- `app/api/routes/chat.py`：恢复摘要 additive 字段/事件；turn 准入处
  （recycle_if_config_stale 旁）接 turn 序列化边界。
- `app/services/chat/session_cancellation.py`：abort 路由带 turn 身份。
- `frontend/components/sidebar/tasks-tab.tsx` / `chat-announcer.tsx`：
  recovery 小组件（不改布局主题）。
- `frontend/lib/hooks/use-sse-stream.ts`：仅 additive 事件分支（若需）。
- ADR：方向命名起草，提交前按 master 最新占下一个空号（0288/0289 已用，
  预计 0290+，提交前重核对）。

## 明确不做

- 不发明 `session_after_compact` 等不存在的 upstream hook。
- 不去掉 `--no-session`（两套持久会话会互相竞争）；不建 fork 产品入口。
- 不接管 G23 job runner、G28 完成裁决、R1 预算/记忆/安全合集、
  goal-harness-recovery 的恢复决策域。
- 不把 selection 当授权；无权威持久源的字段如实披露「不投影」。
