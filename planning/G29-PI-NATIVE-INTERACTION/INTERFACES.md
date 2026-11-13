# G29 INTERFACES — 新增/修改接口清单

## 新增模块（G29 所有权）

### `app/services/chat/pi_ui_protocol.py`（纯协议层）
- `DIALOG_METHODS` / `FIRE_FORGET_METHODS` / `ALL_METHODS`
- `parse_ui_request(obj) -> ParsedUiRequest`（抛 `UiProtocolError{code, detail}`）
- `build_ui_response(native_id, *, value|confirmed|cancelled) -> dict`（三态互斥校验）
- 界：title 160 / message 4000 / options ≤12×160 / text 8000 / widget 24 行 / id 80；
  `MIN_NATIVE_TIMEOUT_MS=3000`。

### `app/services/chat/pi_runtime_interaction.py`（运行中交互服务）
- `get_interaction_service() -> RuntimeInteractionService`（进程单例；`reset_interaction_service()` 测试钩子）
- `make_sink(rpc, bridge)`：stdout `extension_ui_request` 消费入口（sync，零 I/O；
  单向写排入 `_deferred_writes`，由 bridge 心跳 `flush_deferred_writes()` 冲刷）。
- 状态机：`pending → answer_written → resolved | cancelled | timed_out | invalidated`
  （`InteractionState`）。每记录 asyncio.Lock；至多一次有效翻转；重复/迟到提交返回
  `already_answered`/`duplicate` + 当前态。
- `submit_answer(session_id, interaction_id, answer, rpc_resolver)`：白名单校验
  （select 严格 ∈options；input/editor 非空 ≤8000；confirm bool）→ `rpc.send_ui_response`
  单向写出 → `answer_written`。
- `cancel_interaction(...)`：合法 `cancelled:true` 收口。
- `expire_due(session_id)`：原生 timeout+宽限 → `timed_out`（不写 stdin，vendor 已自收口）；
  宿主策略期限（无原生 timeout，如 editor）→ deferred `cancelled:true`。
- `invalidate_turn(session_id, turn_id, reason)`：turn teardown/abort → `invalidated`。
- `observe_turn_activity(turn_id, evidence)` / `mark_resolved_by_callback(iid, evidence)`。
- `waiting_for_user(turn_id)`：WP07 bridge 预算记账钩子。
- `register_envelope_wait(sid, tid, envelope)` + `pop_envelope_by_id`：封套-native 配对。
- fire-and-forget：`_FireProjection`（1s 合并窗；notify ≤5 条 TTL；status/widget/title/
  editor_suggestion last-value）。
- 每会话 pending 上限 `MAX_PENDING_PER_SESSION=3`（超限立即合法取消，不悬挂 vendor promise）。
- 交互 id：`iax-<hex12>`；boot 代际 `WORKER_BOOT_ID`（重启后旧记录只读终止）。

### `app/services/chat/pi_interaction_envelope.py`（受信封套）
- `mint_envelope(*, tool_name, kind, title, ...)`：HMAC(bridge_secret, sha256(canonical))
  签名 + awaiting 登记；per-turn 上限 2（`ENVELOPE_TURN_CAP`）。
  签名 digest = `sha256(JSON sort_keys 递归, separators=(',',':'), ensure_ascii=False)` 的 hex，
  再 HMAC —— 扩展侧 `canonicalJson` 递归排序对齐（嵌套 dict 键序两侧同构）。
- `verify_envelope(token)`（扩展侧对称实现 `app/extensions/webgis-tools/index.mjs`）。
- `current_tool_context() -> (sid, turn_id, tool_call_id)`：dispatch_tool 绑定的已验证上下文
  （`bind_tool_call_id` ContextVar + RuntimeContext）。
- `consume_answer(*, envelope=<记录携带全文>, answer_kind, display, interaction_state,
  record_answer_kind)`：选项表精确反解（不宽松匹配）；可得证据分类（timed_out/cancelled/
  no_answer vs 用户 confirmed false）。返回工具结果 content + domain。

### `app/api/routes/pi_interactions.py`
用户面（`require_owned_session` + 404 防存在性泄漏 + 429 限速）：
- `GET  /chat/sessions/{sid}/runtime-interactions` → pending+recent+fire 投影（水合）
- `POST /chat/sessions/{sid}/runtime-interactions/{iid}/answer` {value|confirmed|cancelled}
- `POST /chat/sessions/{sid}/runtime-interactions/{iid}/cancel`
- `POST /chat/sessions/{sid}/turns/{tid}/messages` {message, mode: adjust|queue}
  → G22 control gate 内「entry 同一性复核 + steer/follow_up issue」原子段 → 真实 disposition；
  用户消息 best-effort 入转录。
- `GET  /chat/sessions/{sid}/turns/{tid}/queue` → vendor get_state 投影 + 本地台账(≤16)
扩展回调面（bridge secret + 签名 turnToken + 活跃 turn）：
- `POST /pi-tools/interaction-resolve` {toolCallId, turnToken, envelopeId, answerKind,
  display} → `consume_answer` → 工具结果 content。

### `app/tools/user_decision_tools.py`
- `webgis_user_decision`（tier-1，前门激活）：kind/title/options/prefill/timeout_s/target；
  返回 `webgis_interaction_request` 封套（随 details 到扩展）。

## 最小接线（现有文件）

- `pi_rpc_client.py`：reader 新分支 `extension_ui_request → sink`；`send_ui_response()`
  单向写出（不登记 pending future）；`_stdin_write_lock` 保证 issue/回答行级原子；
  `_write_stdin_line` 共享写原语。
- `agent_pi_bridge.py`：`rpc` property；`__init__` 挂 sink；`dispatch_tool` 绑
  tool_call_id；stream/非流式两循环：等待记账（stall 豁免 + total 展宽，帽
  `PI_INTERACTION_MAX_WAIT_S=900`）、deferred 冲刷、真实业务事件→续接观察、teardown→失效；
  `_slim_pi_details_payload` overflow 白名单加 `webgis_interaction_request`。
- `pi_event_mapper.py`：4 个纯翻译 handler → SSE `pi_interaction` / `pi_interaction_update`
  / `pi_ui` / `pi_turn_message`（`pi_steer_disposition` 内部合成事件键）。
- `sse_contracts.py`：`CHAT_EVENT_TYPES` +4（反向守卫测试已绿）。
- `cartography_tools.py`：`webgis_layer_upsert` 裸名分支（URL/path 形态零漂移；唯一命中直接
  绑定；同名 ≥2 铸 select 封套，选项含版本日期 as_of tag/范围/要素数；>8 引导缩小）。
- `webgis-tools/index.mjs`：`canonicalJson`/`verifyEnvelope`/`askUserDecision`/
  `postInteractionResolve`/`runInteractionToolResult`；execute 包装在结果携带封套时走
  ctx.ui 闭环；`webgis_user_decision` 入 FALLBACK_NATIVE 前门。
- `main.py`：include 两个 router。

## 前端

- `lib/types/runtime-interaction.ts`、`lib/chat/adapters.ts`（3 个 fail-closed 解析器）、
  `lib/api/chat.ts`（4 个 API fn）、`lib/chat/message-reducer.ts`
  （`runtimeInteractions[]` + upsert/setStatus）、`hooks/use-sse-stream.ts`
  （4 事件分支 + 提交/steer handler + 刷新水合 + turn id 追踪）。
- `components/chat/runtime-interaction-card.tsx`（select/confirm/input/editor；
  IME 安全 Enter；状态机；aria；终态禁用）。
- `chat-tab.tsx`（卡片渲染 + adjust/queue 模式 chips）、`streaming-chat-host.tsx`、
  `context-panel.tsx`（props 下行）。
- i18n：`chat.interaction.*`（zh-CN/en-US）。
- `SSEEventType` union +4。

## SSE 事件契约（新）

| 事件 | 载荷 | 语义 |
|---|---|---|
| `pi_interaction` | `{interaction_id, session_id, turn_id, method, kind, state:"pending", title, options?, message?, placeholder?, prefill?, deadline_at, source{tool,tool_call_id,envelope_id,business}}` | 运行中原生问题卡片（ring 重放覆盖） |
| `pi_interaction_update` | 同上 + `answer_kind/answer_display/resolve_evidence` | 状态翻转 |
| `pi_ui` | `{kind: notify|status|widget|title|editor_suggestion, ...}` | fire-and-forget 只读投影 |
| `pi_turn_message` | `{turn_id, mode, command, disposition, error?}` | 追加指令接纳回执（接纳≠消费） |

## 环境变量（全部有默认）

`PI_UI_MAX_*`（协议界）、`PI_UI_MAX_PENDING_SESSION=3`、`PI_INTERACTION_MAX_WAIT_S=900`。
