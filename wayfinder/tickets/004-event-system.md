# Ticket: 事件系统设计

## Question

基于 Pi 的实际事件系统设计，为 webgis-ai-agent 设计新 Agent 的事件系统。

### Pi 的实际设计（已读源码）

**事件类型**（`AgentEvent` union type, `types.ts:422-437`）：
```typescript
type AgentEvent =
    | { type: "agent_start" }
    | { type: "agent_end"; messages: AgentMessage[] }
    | { type: "turn_start" }
    | { type: "turn_end"; message: AgentMessage; toolResults: ToolResultMessage[] }
    | { type: "message_start"; message: AgentMessage }
    | { type: "message_update"; message: AgentMessage; assistantMessageEvent: AssistantMessageEvent }
    | { type: "message_end"; message: AgentMessage }
    | { type: "tool_execution_start"; toolCallId: string; toolName: string; args: any }
    | { type: "tool_execution_update"; toolCallId: string; toolName: string; args: any; partialResult: any }
    | { type: "tool_execution_end"; toolCallId: string; toolName: string; result: any; isError: boolean }
```

**事件回调机制**（`Agent.subscribe`）：
```typescript
subscribe(listener: (event: AgentEvent, signal: AbortSignal) => Promise<void> | void): () => void
```
- Listener 按订阅顺序执行，Promise 被 await
- `agent_end` 是最后一个事件，但 run 要等所有 listener 的 Promise settle 才算 idle
- 每个 listener 收到 active abort signal，可以响应取消

**事件流产生**（`agent-loop.ts`）：
- `emit` 函数是 `AgentEventSink`（`(event: AgentEvent) => Promise<void> | void`）
- Loop 在关键节点调用 `emit`，不持有任何事件状态
- 事件顺序保证：`message_start` → `message_update*` → `message_end` → `tool_execution_start` → `tool_execution_update*` → `tool_execution_end` → `turn_end`

### 当前 webgis-ai-agent 的事件系统

- `sse_event()` 函数生成 SSE 格式字符串
- 事件类型散落在 `ChatEngine`、`dispatcher`、`session_data_manager`
- 前端通过 `frontend/lib/types/agent-events.ts` 消费
- `decision_log.py` 记录结构化 JSONL

### 需要决策的具体问题

1. **事件类型集合**：
   - Pi 的 10 种事件类型是否足够？
   - 是否需要增加 GIS 专属类型？（如 `perception_update`、`layer_toggled`、`plan_proposed`）
   - **建议**：保留 Pi 的 10 种作为核心，GIS 专属事件作为 `message_start/message_end` 的 custom role 或 `tool_execution_update` 的 partialResult 携带

2. **事件总线 vs 事件回调**：
   - Pi 的做法：`subscribe(listener)` 回调模式，简单直接
   - 我们的考虑：需要多个消费者（SSE 输出 + 日志 + 审计）
   - **建议**：保留 Pi 的回调模式，但增加一个 `EventBus` 中间层（subscribe 的 list 包装为 pub/sub）

3. **事件与 SSE 的映射**：
   - Pi 的事件是内部抽象，不直接对应 SSE
   - 我们的情况：前端已经依赖 SSE 协议
   - **建议**：事件系统是内部抽象，SSE 是外部序列化。映射层在 FastAPI route 中处理

4. **事件的持久化**：
   - Pi 的做法：不持久化事件流（通过 session storage 间接保存）
   - 当前的做法：`event_log` 只保留最近 20 条
   - **建议**：不持久化完整事件流，只持久化关键事件（tool_execution_start/end, turn_start/end）到 decision_log

请给出事件类型定义（Python dataclass 或 TypedDict），以及事件总线/回调接口。

## Reference

- Pi `AgentEvent` type: `/tmp/pi/packages/agent/src/types.ts` (422-437 行，完整已读)
- Pi `Agent.subscribe`: `/tmp/pi/packages/agent/src/agent.ts` (243-246 行)
- Pi `emit` in agent-loop: `/tmp/pi/packages/agent/src/agent-loop.ts` (多处)
- 当前 `sse_event`: `app/services/chat/sse_helpers.py`
- 当前 frontend events: `frontend/lib/types/agent-events.ts`
- 当前 session event_log: `app/services/session_data.py`
- 当前 decision_log: `app/services/chat/decision_log.py`

## Constraints

- 必须向后兼容前端 SSE 协议（不能破坏现有前端）
- 事件系统必须支持多个消费者（SSE 输出、决策日志、审计）
- 事件必须可序列化（JSON）用于持久化
- `agent_end` listener 的 Promise settle 语义必须保留（run 才算 idle）
