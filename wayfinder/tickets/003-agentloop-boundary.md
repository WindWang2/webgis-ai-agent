# Ticket: AgentLoop 职责边界与执行模型

## Resolution

基于对 Pi (`agent-loop.ts`) 实际源码的逐行分析（155-793 行完整已读），以及当前 `ChatEngine.chat_stream` / `dispatcher` 的完整阅读，设计如下。

---

## 设计决策

### 1. Loop 纯函数化

**决策**：`AgentLoop.run()` 是纯函数，不持有任何状态。所有状态在 Agent 中。

**理由**：
- Pi 的做法：`runAgentLoop(prompts, context, config, emit, signal, streamFn)` — 6 个参数，全是输入
- Loop 只做：stream LLM → execute tools → emit events → check stop conditions
- 状态变更通过 `emit` 回调通知 Agent，由 Agent 更新自己的 `_state`

```python
async def run_agent_loop(
    prompts: list[AgentMessage],
    context: AgentContext,        # 只读快照
    config: AgentLoopConfig,      # 配置对象
    emit: AgentEventSink,         # 事件回调
    signal: Optional[asyncio.Event],  # 取消信号
    stream_fn: StreamFn,          # LLM 调用抽象
) -> list[AgentMessage]:
    """纯函数：不持有状态，通过 emit 通知外部事件。"""
```

### 2. Planning 放在 Loop 之前

**决策**：Planning 是 Loop 前的可选预处理阶段，不是 Loop 内的特殊 Step。

**理由**：
- Pi 没有 planning 阶段（纯 FC loop）
- 当前 `ChatEngine._maybe_plan()` 在 loop 前运行
- 我们的 plan-first 是 GIS 工作流的核心优势，不能丢
- Planning 结果通过 `AgentContext` 传入 Loop（作为 system prompt 或额外的 context 字段）

```
用户消息 → [可选: _maybe_plan() → Plan] → Agent.prompt() → AgentLoop.run()
```

### 3. Tool 执行：parallel/sequential 策略（Pi 模式）

**决策**：保留 Pi 的 `ToolExecutionMode`（`"sequential"` / `"parallel"`），默认 `"parallel"`。

**理由**：
- Pi 的做法：全局 `toolExecution` 模式 + 每个 tool 的 `executionMode` 覆盖
- GIS 工具大多数是独立的（可以 parallel），但有些有依赖关系
- 当前 `ChatEngine` 是隐式 sequential（for 循环逐个执行工具）

**parallel 模式的关键设计**（对齐 Pi）：
1. 所有 tool call 先 prepare（含 beforeToolCall 钩子）
2. 允许的工具并行 execute
3. `tool_execution_end` 按工具完成顺序 emit
4. tool-result 消息按源顺序 emit（保证 LLM 上下文顺序正确）

**sequential 模式**：
1. 每个 tool call 按顺序 prepare → execute → finalize
2. 检查取消信号后才进入下一个

### 4. Self-healing 放在 afterToolCall 钩子中

**决策**：保留 self-healing，但放在 `afterToolCall` 钩子中（与 Pi 的 afterToolCall 覆盖语义对齐）。

**理由**：
- Pi 的做法：`afterToolCall` 可以覆盖 tool result（修改 content/details/isError/terminate）
- 当前的做法：`construct_self_healing_message()` 生成提示注入回 LLM
- 我们的做法：`afterToolCall` 检测错误 → 生成 self-healing hint → 覆盖 tool result 的 content
- LLM 在下一次 turn 中看到自愈提示，自动修正

---

## AgentLoop 伪代码骨架

```python
async def run_agent_loop(
    prompts: list[AgentMessage],
    context: AgentContext,
    config: AgentLoopConfig,
    emit: AgentEventSink,
    signal: Optional[asyncio.Event],
    stream_fn: StreamFn,
) -> list[AgentMessage]:
    """纯函数 Loop：stream LLM → execute tools → emit events → check stop."""
    new_messages: list[AgentMessage] = list(prompts)
    current_context = AgentContext(
        systemPrompt=context.systemPrompt,
        messages=list(context.messages) + prompts,  # 快照副本
        tools=list(context.tools),
        sessionId=context.sessionId,
    )
    current_config = config
    first_turn = True
    pending_messages = await config.get_steering_messages() or []

    # ── Outer loop: follow-up messages ──────────────────────
    while True:
        has_more_tool_calls = True

        # ── Inner loop: tool calls + steering ───────────────
        while has_more_tool_calls or pending_messages:
            # Turn start
            if not first_turn:
                await emit({"type": "turn_start"})
            else:
                first_turn = False

            # Inject steering messages
            if pending_messages:
                for msg in pending_messages:
                    await emit({"type": "message_start", "message": msg})
                    await emit({"type": "message_end", "message": msg})
                    current_context.messages.append(msg)
                    new_messages.append(msg)
                pending_messages = []

            # ── Stream assistant response ────────────────────
            message = await stream_assistant_response(
                current_context, current_config, signal, emit, stream_fn
            )
            new_messages.append(message)

            # Check abort
            if signal and signal.is_set():
                await emit({"type": "turn_end", "message": message, "toolResults": []})
                await emit({"type": "agent_end", "messages": new_messages, "aborted": True})
                return new_messages

            # Check error/aborted stop reason
            if message.get("stop_reason") in ("error", "aborted"):
                await emit({"type": "turn_end", "message": message, "toolResults": []})
                await emit({"type": "agent_end", "messages": new_messages})
                return new_messages

            # ── Execute tool calls ───────────────────────────
            tool_calls = extract_tool_calls(message)
            tool_results: list[ToolResultMessage] = []
            has_more_tool_calls = False

            if tool_calls:
                # Truncated response → fail all tool calls
                if message.get("stop_reason") == "length":
                    batch = await fail_truncated_tool_calls(tool_calls, emit)
                else:
                    batch = await execute_tool_calls(
                        current_context, message, current_config, signal, emit
                    )
                tool_results.extend(batch.messages)
                has_more_tool_calls = not batch.terminate

                for result in tool_results:
                    current_context.messages.append(result)
                    new_messages.append(result)

            # ── Turn end ─────────────────────────────────────
            await emit({"type": "turn_end", "message": message, "toolResults": tool_results})

            # ── prepareNextTurn hook ─────────────────────────
            next_turn_ctx = {
                "message": message,
                "toolResults": tool_results,
                "context": current_context,
                "newMessages": new_messages,
            }
            next_snapshot = await config.prepare_next_turn(next_turn_ctx, signal)
            if next_snapshot:
                current_context = next_snapshot.context or current_context
                current_config = _update_config(current_config, next_snapshot)

            # ── shouldStopAfterTurn hook ─────────────────────
            if await config.should_stop_after_turn({
                "message": message,
                "toolResults": tool_results,
                "context": current_context,
                "newMessages": new_messages,
            }):
                await emit({"type": "agent_end", "messages": new_messages})
                return new_messages

            # Check for steering messages
            pending_messages = await config.get_steering_messages() or []

        # ── Outer loop: follow-up messages ──────────────────
        follow_up = await config.get_follow_up_messages() or []
        if follow_up:
            pending_messages = follow_up
            continue
        break

    await emit({"type": "agent_end", "messages": new_messages})
    return new_messages
```

### Tool 执行子函数

```python
async def execute_tool_calls(
    context: AgentContext,
    assistant_message: AgentMessage,
    config: AgentLoopConfig,
    signal: Optional[asyncio.Event],
    emit: AgentEventSink,
) -> ExecutedToolCallBatch:
    """根据 toolExecution 模式选择 parallel 或 sequential。"""
    tool_calls = extract_tool_calls(assistant_message)
    
    # Check if any tool requires sequential execution
    has_sequential = any(
        context.tools and next((t for t in context.tools if t["name"] == tc["name"]), {}).get("executionMode") == "sequential"
        for tc in tool_calls
    )
    
    if config.tool_execution == "sequential" or has_sequential:
        return await execute_sequential(tool_calls, context, assistant_message, config, signal, emit)
    return await execute_parallel(tool_calls, context, assistant_message, config, signal, emit)


async def execute_sequential(
    tool_calls: list[dict],
    context: AgentContext,
    assistant_message: AgentMessage,
    config: AgentLoopConfig,
    signal: Optional[asyncio.Event],
    emit: AgentEventSink,
) -> ExecutedToolCallBatch:
    """顺序执行：prepare → execute → finalize，每个工具完成后才下一个。"""
    finalized_calls: list[FinalizedToolCallOutcome] = []
    
    for tc in tool_calls:
        await emit({"type": "tool_execution_start", "toolCallId": tc["id"], "toolName": tc["name"], "args": tc["arguments"]})
        
        preparation = await prepare_tool_call(context, assistant_message, tc, config, signal)
        if preparation.kind == "immediate":
            finalized = FinalizedToolCallOutcome(toolCall=preparation.toolCall, result=preparation.result, isError=preparation.isError)
        else:
            executed = await execute_prepared_tool_call(preparation, signal, emit)
            finalized = await finalize_executed_tool_call(context, assistant_message, preparation, executed, config, signal)
        
        await emit_tool_execution_end(finalized, emit)
        await emit_tool_result_message(finalized, emit)
        finalized_calls.append(finalized)
        
        if signal and signal.is_set():
            break
    
    return ExecutedToolCallBatch(messages=[create_tool_result_message(f) for f in finalized_calls], terminate=should_terminate(finalized_calls))


async def execute_parallel(
    tool_calls: list[dict],
    context: AgentContext,
    assistant_message: AgentMessage,
    config: AgentLoopConfig,
    signal: Optional[asyncio.Event],
    emit: AgentEventSink,
) -> ExecutedToolCallBatch:
    """并行执行：所有工具先 prepare，然后并行 execute，按源顺序 finalize。"""
    finalized_calls: list[FinalizedToolCallEntry] = []
    
    for tc in tool_calls:
        await emit({"type": "tool_execution_start", "toolCallId": tc["id"], "toolName": tc["name"], "args": tc["arguments"]})
        
        preparation = await prepare_tool_call(context, assistant_message, tc, config, signal)
        if preparation.kind == "immediate":
            finalized = FinalizedToolCallOutcome(toolCall=preparation.toolCall, result=preparation.result, isError=preparation.isError)
            await emit_tool_execution_end(finalized, emit)
            finalized_calls.append(finalized)
            if signal and signal.is_set():
                break
            continue
        
        # 延迟执行：先收集所有 preparation，然后并行执行
        finalized_calls.append(lambda p=preparation: execute_and_finalize(p, context, assistant_message, config, signal, emit))
        if signal and signal.is_set():
            break
    
    # 并行执行所有延迟的工具
    ordered_results = await asyncio.gather(*[
        entry() if callable(entry) else asyncio.create_task(asyncio.sleep(0, result=entry))
        for entry in finalized_calls
    ])
    
    messages = []
    for finalized in ordered_results:
        await emit_tool_result_message(finalized, emit)
        messages.append(create_tool_result_message(finalized))
    
    return ExecutedToolCallBatch(messages=messages, terminate=should_terminate(ordered_results))
```

### Hook 集成点

```python
async def prepare_tool_call(context, assistant_message, tool_call, config, signal):
    """准备工具调用：查找工具定义 → 校验参数 → beforeToolCall 钩子。"""
    tool = find_tool(context.tools, tool_call["name"])
    if not tool:
        return ImmediateOutcome(kind="immediate", result=error_result(f"Tool {tool_call['name']} not found"), isError=True)
    
    try:
        validated_args = validate_arguments(tool, tool_call["arguments"])
        if config.before_tool_call:
            hook_result = await config.before_tool_call({
                "assistantMessage": assistant_message,
                "toolCall": tool_call,
                "args": validated_args,
                "context": context,
            }, signal)
            if hook_result and hook_result.get("block"):
                return ImmediateOutcome(kind="immediate", result=error_result(hook_result.get("reason", "Blocked")), isError=True)
        return PreparedOutcome(kind="prepared", toolCall=tool_call, tool=tool, args=validated_args)
    except Exception as e:
        return ImmediateOutcome(kind="immediate", result=error_result(str(e)), isError=True)


async def finalize_executed_tool_call(context, assistant_message, preparation, executed, config, signal):
    """最终化工具结果：原始结果 → afterToolCall 钩子覆盖。"""
    result = executed.result
    is_error = executed.isError
    
    if config.after_tool_call:
        try:
            hook_result = await config.after_tool_call({
                "assistantMessage": assistant_message,
                "toolCall": preparation.toolCall,
                "args": preparation.args,
                "result": result,
                "isError": is_error,
                "context": context,
            }, signal)
            if hook_result:
                result = {**result, "content": hook_result.get("content", result["content"]), "details": hook_result.get("details", result.get("details")), "isError": hook_result.get("isError", is_error), "terminate": hook_result.get("terminate", result.get("terminate", False))}
                is_error = hook_result.get("isError", is_error)
        except Exception as e:
            result = error_result(str(e))
            is_error = True
    
    return FinalizedToolCallOutcome(toolCall=preparation.toolCall, result=result, isError=is_error)
```

---

## 与当前 ChatEngine 的逐行映射

| Pi `agent-loop.ts` 行号 | Pi 代码 | 当前 `ChatEngine` | 新设计 |
|------------------------|---------|-------------------|--------|
| 155-162 | `runLoop(prompts, context, config, emit, signal, streamFn)` | `chat_stream()` 整个方法 | `AgentLoop.run()` 纯函数 |
| 163-167 | `currentContext = initialContext; config = initialConfig` | 无显式 context 对象 | `Agent.createContextSnapshot()` + `Agent.createLoopConfig()` |
| 170-174 | `while(true) { while(hasMoreToolCalls \|\| pendingMessages)` | `for round_index in range(max_rounds)` | 双层循环，外层 follow-up，内层 tool calls |
| 193 | `streamAssistantResponse()` | `self._call_llm_stream()` + SSE yield | `stream_fn(model, context, options)` |
| 196-200 | `if stopReason === "error"/"aborted"` | `if tracker.is_cancelled()` | signal.is_set() + stop_reason 检查 |
| 203-216 | `executeToolCalls()` | `self._dispatch_tool()` + for loop | `execute_tool_calls()` + parallel/sequential |
| 224 | `emit({type: "turn_end"})` | `yield sse_event("step_result")` | `emit({"type": "turn_end"})` |
| 226-244 | `config.prepareNextTurn?.()` | `self._maybe_plan()` (在 loop 前) | `ChatAgent.prepareNextTurn` (在 turn_end 后) |
| 247-257 | `config.shouldStopAfterTurn?.()` | `round_index >= max_rounds` | `ChatAgent.shouldStopAfterTurn` |
| 259 | `pendingMessages = getSteeringMessages?.()` | 无 | `steeringQueue.drain()` |
| 263-268 | `followUpMessages = getFollowUpMessages?.()` | 无 | `followUpQueue.drain()` |
| 411-426 | `executeToolCalls()` → sequential/parallel | 隐式 sequential | 显式 parallel/sequential |
| 600-663 | `prepareToolCall()` | `dispatcher.dispatch_tool()` | `prepare_tool_call()` + `Agent._dispatch_tool()` |
| 709-754 | `finalizeExecutedToolCall()` | 无显式 finalize | `finalize_executed_tool_call()` + `afterToolCall` |
| 663-707 | `executePreparedToolCall()` | `self._dispatch_tool()` | `Agent._dispatch_tool()` |
| 663-707 | `onUpdate` callback | 无 | `emit({"type": "tool_execution_update"})` |

---

## 与当前系统的差异对比

| 方面 | Pi | 当前 ChatEngine | 新设计 | 差异 |
|------|----|-----------------|--------|------|
| Loop 状态 | 纯函数 | 方法内 state | 纯函数 | ✅ 对齐 Pi |
| Tool 执行 | parallel/sequential | 隐式 sequential | parallel/sequential | ✅ 对齐 Pi |
| Hook 点 | beforeToolCall/afterToolCall | 无显式 hook | 4 个 hook | ✅ 对齐 Pi |
| 停止条件 | shouldStopAfterTurn | max_rounds | shouldStopAfterTurn + max_rounds | 🟡 保留 max_rounds 作为兜底 |
| Planning | 无 | loop 前预处理 | loop 前 + prepareNextTurn | 🟡 扩展 Pi 模式 |
| Self-healing | 无 | 构造提示注入 | afterToolCall hook | 🟡 扩展 Pi 模式 |
| SSE 输出 | 事件回调 | SSE yield | emit → Agent → SSE | ✅ 解耦 |
| Steering | 有 | 无 | 有 | ✅ 对齐 Pi |
| Context | 快照 + 可变 | 直接修改 list | 快照 + 副本 | ✅ 对齐 Pi |

---

## 关键约束满足检查

- ✅ **Loop 纯函数化**：不持有状态，所有状态在 Agent 中
- ✅ **Streaming**：`StreamFn` 是 async generator，支持增量 token
- ✅ **Abort signal**：`signal.is_set()` 在关键边界检查
- ✅ **Plan-first**：Planning 在 Loop 前作为可选阶段
- ✅ **Parallel/Sequential**：tool execution mode 支持两种策略
- ✅ **Self-healing**：在 `afterToolCall` 钩子中实现
- ✅ **GIS 工具执行**：`Agent._dispatch_tool()` 子类实现，保留现有 dispatcher 逻辑
- ✅ **前向兼容**：emit 事件序列与现有 SSE 事件一一对应

## Reference

- Pi `runAgentLoop`: `/tmp/pi/packages/agent/src/agent-loop.ts` (155-275 行，完整已读)
- Pi `executeToolCallsParallel`/`Sequential`: `/tmp/pi/packages/agent/src/agent-loop.ts` (411-554 行)
- Pi `prepareToolCall`/`finalizeExecutedToolCall`: `/tmp/pi/packages/agent/src/agent-loop.ts` (600-754 行)
- Pi `failToolCallsFromTruncatedMessage`: `/tmp/pi/packages/agent/src/agent-loop.ts` (381-406 行)
- 当前 ChatEngine.chat_stream: `app/services/chat_engine.py` (540-803 行)
- 当前 dispatcher: `app/services/chat/dispatcher.py`
