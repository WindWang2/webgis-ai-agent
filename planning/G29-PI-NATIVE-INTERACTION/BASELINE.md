# G29 — Pi 原生运行中交互与用户指令工作台

- TASK_ID: `G29-PI-NATIVE-INTERACTION` / slug `g29-pi-native-interaction`
- Worktree: `../webgis-worktrees/g29-pi-native-interaction-20261112T144716Z`
- Branch: `zcode/g29-pi-native-interaction-20261112T144716Z`
- Base SHA: `ca7b3ec273f2b43336852951e2292a416b2238e6` (= origin/master at start; open PRs: 0)
- vendor/pi pin: `b4afdddf44e059c67ccf5097e5317d913521b470` (v1.5.2, matches upstream stable at research time)
- Head SHA: (updated per commit)

## 预算与配额

- Goal 预算声明 ≥ 320,000,000 tokens（宿主实际额度以运行环境为准，不伪造）。
- Subagents ≤ 3：`Explore`(研究) + `general-purpose`(实现辅助/评审辅助) + 独立 reviewer（不参与核心实现）。
- 重型阶段锁：`$(git common dir)/webgis-heavy-stage.lock`，默认 -j2。
- 不等待线上 CI；不轮询远端 checks。

## 核心源码事实（执行时核对，行号基于 base）

1. **E3 缺口确认** — `app/services/chat/pi_rpc_client.py:672-679`：`_read_responses` 只路由
   `type=response`+`id` 与 `type` 无 `id` 两分支；`extension_ui_request`（有 `type`+`id`）被静默
   丢弃。`issue()` 登记 pending future 且等待回执 —— 不能用于 UI 单向回答（原生协议无回执）。
2. **原生协议**（vendor v1.5.2 `rpc-types.ts` / `rpc-mode.ts:90-160,768-797` / `docs/rpc-extension-ui.md`）：
   - Dialog: `select{options,timeout?}` / `confirm{message,timeout?}` / `input{placeholder,timeout?}` /
     `editor{prefill}`（editor 无原生 timeout）→ 阻塞至 `extension_ui_response{id, value|confirmed|cancelled}`。
   - Fire-and-forget: `notify` / `setStatus` / `setWidget` / `setTitle` / `set_editor_text`，不期待响应。
   - vendor 侧：`timeout`/abort 到期 → resolve(default)（select/input/editor→undefined，confirm→false）
     并删除 pending；迟到 response 对已删 id 静默忽略。`handleInputLine` 优先处理 ui_response。
3. **turn 模型**（`app/agent_pi_bridge.py`）：`_ActiveTurnEntry{session_id,turn_id,run_id,token,bridge}`；
   `register/unregister_active_pi_turn`；`_control_gate` 交接门（校验+写出原子段，abort/clear_queue 用）；
   `PiBridge.abort` 快照作用域；`stream_prompt` 持 turn 锁全程，事件队列 `rpc.events` → `map_event_to_sse`。
4. **预算**：`PI_EVENT_STREAM_TIMEOUT=120s`（连续静默 stall）、`PI_TURN_TOTAL_TIMEOUT=300s`（整 turn）、
   `PI_HEARTBEAT_INTERVAL=8s`。ctx.ui 等待期间 vendor 无事件 —— **必须**把「等待用户」记为可信等待，
   否则 120s 后误判 stall（WP07 核心）。
5. **扩展回调面**：`app/extensions/webgis-tools/index.mjs` —— 工具 execute → `postToBridge` →
   `POST /pi-tools/execute`（X-Pi-Bridge-Secret + turnToken 签名 + 活跃 turn 校验，
   `app/api/routes/pi_tools.py`）。turn token = `issue_turn_token(bridge_secret, sid, turn_id)`，15min。
6. **澄清（不重做）**：`app/services/chat/pi_turn_clarification.py` 在 agent_settled 收口期采集
   RequirementIR 澄清，task_complete 后 done 前发 SSE `clarification`；前端 `clarification-card.tsx`。
   G29 的运行中原生 Promise 生命周期与之不同，需兼容去重（WP10）。
7. **SSE 恢复**：`TurnEventBuffer`+`TurnResumeRegistry`（ring 重放，POST 带 Last-Event-ID = resume）。
8. **运行中消息**：`steer`/`follow_up`/`clear_queue` 属稳定 RPC（`rpc-types.ts:20-44`），响应带
   disposition；当前仓库无任何 steer 发起入口（仅 abort/clear_queue 于 PiBridge.abort）。
9. **前端**：SSE 消费在 `frontend/lib/hooks/use-sse-stream.ts`（if-else 事件链）；卡片模式参考
   `clarification-card.tsx`（radio/键盘可达/状态机/REST answer）。

## 所有权边界（D 节确认）

- 本任务独占：`pi_rpc_client.py` 的 UI 请求路由、新交互领域模块、`pi_event_mapper.py` 最小加性接线、
  `app/extensions/webgis-tools/index.mjs`、bridge turn 控制接缝、受权 REST/SSE 契约、聊天交互组件、
  必要时的 Pi pin 兼容（当前 pin 已满足，无需升级）。
- 复用不复制：G22 turn/恢复、澄清/CAS、权限模型、abort/clear_queue、TurnEventBuffer、G28 结算。
- 不新建：第二 agent loop、通用事件总线、新工具注册表、MapSpec store、新 journal writer。
