# Ticket: AgentLoop 职责边界与执行模型

## Question

基于 Pi (`agent-loop.ts`) 的实际实现，设计 `AgentLoop` 的职责边界。

### Pi 的实际设计（已读源码）

Pi 将 Agent 和 AgentLoop **严格分离**：

**Agent** (`agent.ts:171`)：有状态，持有 `_state`，管理生命周期
- `runWithLifecycle(executor)` 包装执行，管理 `activeRun`、abort controller
- `processEvents(event)` 处理事件，更新 state，通知 listeners
- `createContextSnapshot()` 创建 Loop 需要的不可变快照
- `createLoopConfig()` 将 Agent 配置转换为 Loop 配置

**AgentLoop** (`agent-loop.ts`)：**纯函数**，不持有任何状态
```typescript
async function runAgentLoop(
    prompts: AgentMessage[],
    context: AgentContext,      // 只读快照
    config: AgentLoopConfig,    // 配置对象
    emit: AgentEventSink,       // 事件回调
    signal: AbortSignal | undefined,
    streamFn: StreamFn,         // LLM 调用抽象
): Promise<AgentMessage[]>
```

Loop 的核心逻辑：
1. **外层循环**：处理 follow-up 消息（agent 停止后还有新消息时继续）
2. **内层循环**：处理 tool calls + steering 消息
3. **streamAssistantResponse**：调用 streamFn，增量接收 assistant 消息
4. **executeToolCalls**：支持 sequential/parallel 两种模式
5. **prepareNextTurn**：每轮结束后可以修改 context/model
6. **shouldStopAfterTurn**：每轮结束后决定是否停止

**Tool 执行策略**（Pi 的 `ToolExecutionMode`）：
- `"sequential"`：每个 tool call 按顺序 prepare → execute → finalize
- `"parallel"`：所有 tool call 先 prepare（含 beforeToolCall），然后并行 execute，按源顺序 finalize
- 每个 tool 可以声明自己的 `executionMode` 覆盖全局设置
- `beforeToolCall` 可以阻断工具执行（返回 `{ block: true }`）
- `afterToolCall` 可以覆盖 tool result（修改 content/details/isError/terminate）

**Self-healing**：Pi 不做 self-healing。错误直接作为 tool result 返回（`isError: true`），由 LLM 自行决定如何修正。Loop 继续运行。

### 当前 webgis-ai-agent 的设计

- `ChatEngine` 同时持有状态和执行逻辑
- `_maybe_plan()` 在 loop 内插入 planning 阶段
- `_dispatch_tool()` 在 loop 内执行工具
- `construct_self_healing_message()` 生成错误提示注入回 LLM

### 需要决策的具体问题

1. **Loop 是否纯函数化**？
   - Pi 的做法：是，`runAgentLoop(context, config, emit, streamFn)` 不持有状态
   - 我们的考虑：纯函数化便于测试和替换，但需要 Agent 创建 context snapshot
   - **建议**：是，Loop 纯函数化，所有状态在 Agent 中

2. **Planning 放在哪里**？
   - Pi 的做法：没有 planning 阶段（纯 FC loop）
   - 当前的做法：`_maybe_plan()` 在 loop 前作为可选预处理
   - 我们的考虑：plan-first 是 GIS 工作流的核心优势，不能丢
   - **建议**：Planning 作为 Loop 前的可选阶段，由 `prepareNextTurn` 或单独的 `PlanAgent` 处理

3. **Tool 执行的 parallel/sequential 策略**？
   - Pi 的做法：全局 `toolExecution` 模式 + 每个 tool 的 `executionMode` 覆盖
   - 我们的考虑：GIS 工具大多数是独立的（可以 parallel），但有些有依赖关系（如先 clip 再 analyze）
   - **建议**：保留 Pi 的 parallel/sequential 模式，默认 parallel

4. **Self-healing 放在哪里**？
   - Pi 的做法：不做 self-healing，错误直接返回
   - 当前的做法：`construct_self_healing_message()` 生成提示注入回 LLM
   - 我们的考虑：GIS 工具错误经常是路径/格式问题，self-healing 提示很有价值
   - **建议**：保留 self-healing，但放在 `afterToolCall` 钩子中（与 Pi 的 afterToolCall 覆盖语义对齐）

请给出 `AgentLoop` 的伪代码骨架（Python），标注与 Pi 的异同点。

## Reference

- Pi `runAgentLoop`: `/tmp/pi/packages/agent/src/agent-loop.ts` (155-275 行，完整已读)
- Pi `executeToolCallsParallel`/`Sequential`: `/tmp/pi/packages/agent/src/agent-loop.ts` (411-554 行)
- Pi `beforeToolCall`/`afterToolCall` logic: `/tmp/pi/packages/agent/src/agent-loop.ts` (600-754 行)
- 当前 ChatEngine.chat_stream: `app/services/chat_engine.py`
- 当前 dispatcher: `app/services/chat/dispatcher.py`

## Constraints

- Loop 必须支持 streaming（增量 token 输出）
- Loop 必须支持 abort signal（TaskTracker cancellation）
- 必须兼容现有的 plan-first 模式（可选 planning phase）
- 工具执行错误必须被 loop 捕获并转化为 LLM 可读的 tool result
- parallel 模式下，tool result 消息必须按源顺序返回（与 Pi 一致）
