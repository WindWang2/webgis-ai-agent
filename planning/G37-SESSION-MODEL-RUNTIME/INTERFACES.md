# G37 INTERFACES

## 新模块（本任务所有者）

| 模块 | 职责 |
|---|---|
| `app/services/chat/model_runtime/session_catalog.py` | 管理员允许目录（base + `SESSION_MODEL_CATALOG_EXTRA`）、`catalog_generation()`、id 校验 |
| `app/services/chat/model_runtime/session_model_prefs.py` | preference 持久化 + CAS（`RevisionConflict`） |
| `app/services/chat/model_runtime/session_model_service.py` | turn 期应用编排：resolve → 能力门 → admission → set_model → get_state 回读；`ModelApplicationResult`（reason 码：`catalog_item_unavailable/credential_missing/tools_unsupported/context_window_insufficient/session_token_budget_exceeded/pi_apply_failed/pi_readback_mismatch`） |
| `app/services/chat/model_runtime/usage_ledger.py` | message_end 提炼（`extract_usage_fact`）、`TurnUsageCollector`、幂等落盘（`(turn_id, call_seq)` 唯一）、价格表（`SESSION_MODEL_PRICE_TABLE_JSON`，`llm_price.v1`）、`session_usage_summary` |
| `app/api/routes/session_model_views.py` | GET/PUT `/chat/sessions/{id}/model`、GET `/chat/sessions/{id}/usage`（require_owned_session） |
| `app/tools/session_model_tools.py` | `webgis_model_catalog` / `webgis_session_model_status` / `webgis_session_budget_status`（只读，经 webgis_execute 面） |
| `frontend/components/chat/session-model-selector.tsx` + `frontend/lib/store/useModelRuntimeStore.ts` | 会话工作台 UI + model_status SSE 投影 |
| `migrations/versions/g3701_session_model_runtime.py` | 三表：`chat_session_model_prefs` / `chat_turn_runtime` / `chat_usage_records` |

## 共享文件局部接线（加性）

- `pi_rpc_client._spawn_process`：models.json 写**目录全部模型**（settings.json default 仍 = base）；`spawn_epoch`/`runtime_epoch` 每次 start 递增。
- `agent_pi_bridge`：`_apply_session_model_for_turn`（isolate 之后、prompt 之前）+ `_vendor_model_state` 跟踪（new_session/respawn/换会话强制重应用）+ `_persist_turn_runtime_receipt`（finally 收口）+ stream/prompt 双路径 usage 采集器 + `model_status` SSE（成功路径在 task_start 之后，失败路径 model_status→task_error→done）。
- `error_taxonomy`：`model_apply_error`/`session_budget_exceeded` → `policy_reject`（不可重试）。
- `chat.py`：挂载 `_session_model_views_router`。
- `app/tools/__init__.py`：注册 `session_model_tools`。
- `frontend`：`use-sse-stream.ts` 加 `model_status` case；`chat.ts` 事件 union 加 `model_status`；`chat-tab.tsx` 挂 `SessionModelSelector`；zh/en `chat.json` 加 `chat.model.*`。
- `db_model.py`：三表 ORM。

## 关键语义契约（评审重点）

- **优先级**：explicit preference（目录内）> 继承 base（= `resolve_llm_config()` ⊕ 运行时 override；`PI_PROVIDER/PI_MODEL` env 仍只在启动期尽力而为 —— 不新增打架来源）。
- **生效**：仅 turn 边界 `set_model` + `get_state` 回读后；`set_thinking_level` 当前**不发送**（compat.supportsReasoningEffort=False；UI 明示不可控）。requested/effective/responseModel 三分。
- **失败语义**：explicit 应用失败/回读不同模型 → 拒 prompt；inherit 读不回/读不懂 → `degraded_inherit` 披露（默认由 spawn 快照构造保证）；DB 不可达 → `degraded_inherit` + 回执无法持久（面 incomplete）。
- **账本**：message_end 终态 usage（assistant + toolResult nested 各一次）；reasoning⊂output、cacheWrite1h⊂cacheWrite；费用 provider(=totalFromProvider)/estimated/unknown 三态，零价不当免费；`(turn_id, call_seq)` 幂等；finalize 仅 agent_settled/finally 一次。
- **预算**：admission 在 prompt 字节前（`cap=None` 披露 unlimited）；投影 = 绑定消息 token 估计（CJK-aware 单一语义）+ max_output。
- **隔离**：目录/凭证全服务器端；PUT 只接受目录内 id；跨 owner 404；工具只读不扩 allowlist。

## 不变量保留

`_stdin_write_lock`、`_control_gate`、turn lease/单 writer、`agent_settled` 唯一终结、`config_generation` 换代回收、`default_active == NATIVE_TOOL_NAMES`、SSE 首事件 `task_start`。
