# G42 INTERFACES — 新增/变更接口清单

## REST（UI 面）— `/api/v1/goal-repair`

| 方法 | 路径 | 语义 | 鉴权 |
|---|---|---|---|
| GET | `/sessions/{sid}/preview` | 版本化补全预览（零副作用；`goalrepair.preview/v1`）| `get_current_user_optional` + `get_owner_token` → `verify_session_owner`（404 语义） |
| POST | `/sessions/{sid}/attempts` (202) | 启动有界补全（幂等单 claim；busy=409 SESSION_BUSY） | 同上；body 只含 `action_ids?`/`acknowledged_losses?`/`project_id?` |
| GET | `/attempts/{id}` | attempt 视图 + 惰性对账（waiting_delivery 收口/runner 丢失） | attempt 归属会话再验（跨租户 404） |
| POST | `/attempts/{id}/cancel` | 两阶段取消（幂等；终态 no-op 披露） | 同上 |

typed 错误码 → HTTP：PLAN_NOT_FOUND/ATTEMPT_NOT_FOUND=404；SESSION_BUSY/ACTIVE_ATTEMPT/NO_PROGRESS/GOAL_SUPERSEDED=409；BUDGET_DENIED=402；INVALID_ACTION_IDS=422。

## Pi 工具（原生面投影，`app/tools/goal_repair_tools.py`）

| 工具 | side_effect | 语义 |
|---|---|---|
| `webgis_goal_repair_preview` | cacheable_read | 同 REST preview（同 service 层，无第二实现） |
| `webgis_goal_repair_start` | state_mutation | **零等待**：幂等建 attempt 即返回；Pi 回调入口 `session_busy_check=False`（当前 turn 即合法上下文）；执行体独立任务经统一派发（不嵌套持 wave 槽/writer，R37） |
| `webgis_goal_repair_status` | cacheable_read | attempt 视图（缺省取会话最近） |
| `webgis_goal_repair_cancel` | state_mutation | 两阶段取消 |

## 服务层公开面（`goal_repair_execution`）

- `build_repair_preview(session_id, ctx)` / `start_repair_attempt(...)` / `get_attempt_status(id)` / `cancel_repair_attempt(id, ctx)` / `reconcile_attempt(view)`。
- `store.*`：`submit_attempt_row`（幂等键 `goal_repair:{session}:{goal}:{plan_version}[:r{n}]`）、`find_active_attempt`、`read/write_summary_patch`、`read_dispatch_spec`（绝不经 API 返回）。
- `runner.run_attempt_job` / `register_runner_task` / `runner_alive`。
- `actions.RepairOwnerContext` + 每动作执行体（map export/delivery/runtime_repair/reobserve/reread/requalify）。

## 既有文件的加性变更

| 文件 | 变更 |
|---|---|
| `app/tools/__init__.py` / `app/main.py` | 注册工具与 router |
| `app/tools/registry.py` | ref 透明解引用 skip 名单补 delivery 工具 `spec`（G30/G38 同款） |
| `app/services/gis_harness/goal_satisfaction/evaluator.py` | export/delivery 缺席分支补 `failed_rule`（既有映射键） |
| `app/services/gis_harness/goal_satisfaction/receipts.py` | `_collect_delivery` 修 `[:N].all()` → `limit(N).all()`（既有生产 bug） |
| `app/tools/mixed_map_export_tools.py` | 工具路径补 db+owner（lineage 与 UI 路径对齐） |
| `app/core/database.py` | aiosqlite connect_args 补 `timeout: 30`（与同步侧对称） |
| `frontend/components/sidebar/chat-tab.tsx` | 非 satisfied 完成卡下挂载 GoalRepairPanel |
| `frontend/components/map/map-panel.tsx` | 监听 `webgis:goal-repair-reobserve` → bump 恢复代次（触发既有 reconcile→观察 POST） |
| `frontend/messages/{zh-CN,en-US}/chat.json` | `chat.goalRepair.*`（双语 parity） |
| `frontend/e2e/fixtures/api-stubs.ts` | goal-repair 桩（有界状态机） |

## 前端类型契约（`lib/types/goal-repair.ts`）

`GoalRepairPreview` / `RepairActionBinding`（disposition: executable|needs_acknowledgment|needs_user_input|unsupported|blocked）/ `GoalRepairAttemptView` / `AttemptActionReceipt`（含 download_url/job_id/evidence_ref）。解析纯函数非法 → null（零漂移）。
