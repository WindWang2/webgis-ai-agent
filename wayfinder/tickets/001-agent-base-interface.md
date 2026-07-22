# Ticket: Agent 基类接口契约设计

## Question

基于对 Pi (`earendil-works/pi`) 和 OpenCode (`anomalyco/opencode`) 实际源码的分析，为 webgis-ai-agent 设计基类 `Agent` 的接口契约。

### Pi 的实际设计（已读源码）

**Agent 是有状态的** (`agent.ts:171`)：
- 持有 `_state: MutableAgentState`，包含 `tools`、`messages`、`model`、`systemPrompt`
- `tools` 和 `messages` 通过 getter/setter 保护，赋值时自动拷贝数组 (`messages.slice()`)
- 运行时状态：`isStreaming`、`streamingMessage`、`pendingToolCalls`、`errorMessage`

**生命周期钩子**（都是可选的函数属性）：
```typescript
beforeToolCall?: (context: BeforeToolCallContext, signal?) => Promise<BeforeToolCallResult | undefined>
afterToolCall?: (context: AfterToolCallContext, signal?) => Promise<AfterToolCallResult | undefined>
prepareNextTurn?: (context: PrepareNextTurnContext, signal?) => Promise<AgentLoopTurnUpdate | undefined>
shouldStopAfterTurn?: (context: ShouldStopAfterTurnContext) => boolean | Promise<boolean>
```

**队列机制**：
- `PendingMessageQueue` 支持两种模式：`"all"`（排空所有）和 `"one-at-a-time"`（每次只注入最旧的）
- `steeringQueue`：在 agent 运行中注入用户消息（如用户在中途说"换一种方式"）
- `followUpQueue`：在 agent 停止后运行（如用户追加的新消息）
- `prompt()` 启动新对话，`continue()` 从当前 transcript 继续

**StreamFn 抽象**：
```typescript
type StreamFn = (model: Model, context: Context, options?: SimpleStreamOptions) => AssistantMessageEventStream | Promise<AssistantMessageEventStream>
```
这是唯一的 LLM 调用抽象，Agent 不直接依赖任何 LLM SDK。

### OpenCode 的实际设计（已读源码）

- 函数式架构 (Effect-TS)，Agent 是 Service pattern（通过 Effect Context 获取）
- Tool 定义是 Schema-first：`Definition<Input, Output>`，`execute(input, context)` → `Effect`
- Tool 输出是 `Content` union (text/file)，通过 `toModelOutput` 映射到 LLM 显示格式

### 对 webgis-ai-agent 的关键启示

1. **Agent 必须有 mutable state**（Pi 模式），不是无状态的（OpenCode 模式）
2. **State 的 tools/messages 必须是 copy-on-write**（Pi 的 getter/setter 模式）
3. **LLM 调用必须抽象为 StreamFn**，Agent 不依赖具体 SDK
4. **beforeToolCall/afterToolCall 是我们实现 GIS 专属钩子的最佳位置**（如注入 `[环境感知]`）
5. **steering/follow-up 队列对于长时 GIS 分析任务非常重要**（用户可能中途改变意图）

### 需要决策的具体问题

1. **状态管理**：基类持有 `_state`（messages/tools/model），还是通过外部 `Memory` 注入？
   - Pi 的做法：Agent 持有 `_state`，但通过 getter/setter 保护数组拷贝
   - 我们的考虑：GIS 的 ref-based 数据流需要 session 级别的状态管理，Agent 持有 state 更自然

2. **生命周期钩子位置**：GIS 专属钩子（如 `beforeToolCall` 中注入 `[环境感知]`）放在基类还是子类？
   - Pi 的做法：钩子放在基类，通过函数属性配置
   - 我们的考虑：`[环境感知]` 注入是所有 Agent 子类都需要的，应该放在基类的 `beforeToolCall` 中

3. **StreamFn 适配**：现有的 `LLMClient.call_llm_stream` 如何适配 Pi 的 `StreamFn` 接口？
   - Pi 的 StreamFn 返回 `AssistantMessageEventStream`（增量事件流）
   - 我们的 `call_llm_stream` 返回 `(event_type, data)` tuples
   - 需要一层适配器将我们的 tuple 流转换为 Pi 风格的事件流

4. **队列机制**：是否需要 steering/follow-up 队列？
   - Pi 的做法：有，支持 "all" / "one-at-a-time" 模式
   - 我们的考虑：长时 GIS 分析任务（如热力图生成可能需要 30 秒），用户可能中途发送新消息，steering 队列非常必要

请给出基类接口的伪代码定义（不需要完整实现），标注哪些方法在基类、哪些在子类、哪些是抽象方法。

## Reference

- Pi `Agent` class: `/tmp/pi/packages/agent/src/agent.ts` (完整已读)
- Pi `AgentTool` interface: `/tmp/pi/packages/agent/src/types.ts` (完整已读)
- Pi `AgentEvent` type: `/tmp/pi/packages/agent/src/types.ts:422-437`
- 当前 `ChatEngine`: `app/services/chat_engine.py`
- 当前 `SubagentDispatcher`: `app/services/subagent.py`

## Constraints

- 必须保留现有 GIS 模式：ref-based 数据流、环境感知注入、工具三层目录
- 必须兼容现有 FastAPI + asyncio 架构
- 渐进替换要求：新 Agent 必须能与旧 ChatEngine 并存
- Agent 的 `messages`/`tools` 赋值必须 copy-on-write（防止外部突变）
