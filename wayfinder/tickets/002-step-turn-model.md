# Ticket: Step/Turn 模型定义

## Question

基于 Pi (`agent-loop.ts`) 的实际事件流设计，为 webgis-ai-agent 定义 `Step` 和 `Turn` 的数据结构。

### Pi 的实际设计（已读源码）

Pi **没有显式的 `Step` 类型**。它的粒度就是事件流：
```
agent_start → turn_start → [message_start → message_update* → message_end] → 
  tool_execution_start → [tool_execution_update*] → tool_execution_end → 
  turn_end → [agent_end]
```

**Turn** 是隐式的：从 `turn_start` 到 `turn_end` 之间的所有事件构成一个 Turn。
- 一个 Turn = 一次 LLM 请求 + 响应 + 所有 tool call/result
- Turn 之间有 `prepareNextTurn` 钩子可以修改 context/model

**Step** 在 Pi 中不存在作为独立类型。Pi 的 `AgentTool.execute()` 支持 `onUpdate` 回调（partial result），这提供了 act → observe 的细粒度，但不是独立的 Step 对象。

**关键设计点**：
- `message_update` 事件支持增量 token（thinking_start/delta/end, text_start/delta/end, toolcall_start/delta/end）
- `tool_execution_update` 支持 partial result（长时工具如热力图生成可以推送进度）
- `prepareNextTurn` 在 `turn_end` 之后调用，可以返回新的 context/model/thinkingLevel
- `shouldStopAfterTurn` 在 `prepareNextTurn` 之后调用，决定是否停止

### 当前 webgis-ai-agent 的设计

- 扁平的消息列表 + SSE 事件
- `max_rounds=60` 的 FC 循环
- 没有显式的 Step/Turn 分层
- `TaskTracker` 跟踪步骤，支持 cooperative cancellation

### 需要决策的具体问题

1. **是否需要显式 `Step` 类型**？
   - Pi 的做法：不需要，事件流本身就是粒度
   - 我们的考虑：GIS 工具经常是长时的（热力图 30s，遥感分析 2min），`tool_execution_update` 的 partial result 机制足够
   - **建议**：不引入独立 Step 类型，沿用 Pi 的事件流模型

2. **Turn 是否作为第一类对象**？
   - Pi 的做法：Turn 是隐式的（事件区间），不是显式对象
   - 我们的考虑：需要序列化到 DB/Redis，可能需要显式 Turn 对象
   - **建议**：Turn 保持隐式，但在序列化时用 `turn_start`/`turn_end` 事件边界标记

3. **如何将现有的 60 轮 FC 循环映射到新模型**？
   - 当前：`max_rounds=60`，每轮是一个 LLM call + tool dispatch
   - 新模型：每轮 = 一个 Turn（从 turn_start 到 turn_end）
   - 60 轮限制 → 60 Turn 限制，或者改为 token/time budget

4. **可中断/恢复语义（用户要求 B 选项）**：
   - Pi 的做法：`AbortSignal` 传给 StreamFn 和 beforeToolCall/afterToolCall
   - 当前 `TaskTracker._cancelled` 标志在每轮之间检查
   - **建议**：保留 `TaskTracker` 模式，但将 `_cancelled` 检查点移到 Loop 的事件边界（turn_end 时检查）

5. **tool_execution_update 的 partial result**：
   - Pi 的做法：工具执行中可以通过 `onUpdate` 回调推送 partial result
   - 我们的考虑：长时 GIS 工具（热力图、遥感分析）需要推送进度
   - **建议**：保留现有的 SSE `step_result` 事件，对齐 Pi 的 `tool_execution_update`

请给出以下类型定义（Python），以及它们与现有消息列表的关系：
- `AgentEvent`（事件类型 union）
- `AgentContext`（传给 Loop 的上下文快照）
- `AgentLoopConfig`（Loop 配置）

## Reference

- Pi `agent-loop.ts`: `/tmp/pi/packages/agent/src/agent-loop.ts` (完整已读)
- Pi `AgentEvent` type: `/tmp/pi/packages/agent/src/types.ts:422-437`
- Pi `AgentLoopConfig` type: `/tmp/pi/packages/agent/src/types.ts:144-287`
- Pi `BeforeToolCallContext` / `AfterToolCallContext`: `/tmp/pi/packages/agent/src/types.ts:93-118`
- 当前 ChatEngine._call_llm_stream: `app/services/chat_engine.py`
- 当前 SSE helpers: `app/services/chat/sse_helpers.py`
- 当前 TaskTracker: `app/services/task_tracker.py`

## Constraints

- 必须兼容现有的 SSE 流式输出协议（前端已依赖）
- 必须支持 cooperative cancellation（TaskTracker 模式）
- 必须支持 tool_execution_update（长时 GIS 工具进度推送）
- 事件必须可序列化（JSON）用于持久化
