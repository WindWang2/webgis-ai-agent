# Ticket: Step/Turn 模型定义

## Resolution

基于对 Pi (`agent-loop.ts`) 实际事件流的逐行分析，以及当前 `TaskTracker` / SSE helpers 的完整阅读，设计如下。

---

## 设计决策

### 1. 不引入显式 `Step` 类型

**决策**：沿用 Pi 的事件流模型，不引入独立的 `Step` 类型。

**理由**：
- Pi 没有显式 Step 类型，事件流本身就是粒度
- GIS 工具的长时进度通过 `tool_execution_update` 的 partial result 机制推送
- 引入 Step 会增加不必要的序列化/反序列化开销

**进度推送机制**：
```python
# 工具执行中可以通过 onUpdate 回调推送 partial result
async def long_running_tool(signal, on_update):
    for i in range(total_steps):
        result = await do_step(i)
        on_update({"progress": i / total_steps, "partial": result})
```

### 2. Turn 保持隐式

**决策**：Turn 不作为第一类对象，但在序列化时用 `turn_start`/`turn_end` 事件边界标记。

**理由**：
- Pi 的 Turn 是隐式的（事件区间），不是显式对象
- 当前 `ChatEngine` 的 `round_index` 已经隐式标记了 Turn
- 序列化到 DB 时，用 `turn_start`/`turn_end` 事件的时间戳标记 Turn 边界

**Turn 边界定义**：
```
[turn_start] → [message_start → message_update* → message_end] → 
  [tool_execution_start → tool_execution_update* → tool_execution_end]* → 
  [turn_end]
```

### 3. 可中断/恢复语义

**决策**：保留 `TaskTracker` 模式，但将 `_cancelled` 检查点移到 Loop 的事件边界。

**理由**：
- Pi 的做法：`AbortSignal` 传给 StreamFn 和 beforeToolCall/afterToolCall
- 当前 `TaskTracker._cancelled` 标志在每轮之间检查
- **改进**：在关键事件边界检查取消信号（turn_end 时、tool_execution_end 时）

```python
# 在 AgentLoop 的关键边界检查取消
async def runLoop(...):
    while True:
        # ... stream assistant response ...
        
        if signal?.aborted:
            emit({"type": "agent_end", "messages": newMessages, "aborted": True})
            return
        
        # ... execute tool calls ...
        
        for tool_result in toolResults:
            if signal?.aborted:
                break
```

---

## 类型定义（Python）

### AgentEvent

```python
@dataclass
class AgentStartEvent:
    type: Literal["agent_start"] = "agent_start"

@dataclass
class AgentEndEvent:
    type: Literal["agent_end"] = "agent_end"
    messages: list[AgentMessage]
    aborted: bool = False

@dataclass
class TurnStartEvent:
    type: Literal["turn_start"] = "turn_start"

@dataclass
class TurnEndEvent:
    type: Literal["turn_end"] = "turn_end"
    message: AgentMessage
    toolResults: list[ToolResultMessage]

@dataclass
class MessageStartEvent:
    type: Literal["message_start"] = "message_start"
    message: AgentMessage

@dataclass
class MessageUpdateEvent:
    type: Literal["message_update"] = "message_update"
    message: AgentMessage
    assistantMessageEvent: dict  # Pi 的 AssistantMessageEvent

@dataclass
class MessageEndEvent:
    type: Literal["message_end"] = "message_end"
    message: AgentMessage

@dataclass
class ToolExecutionStartEvent:
    type: Literal["tool_execution_start"] = "tool_execution_start"
    toolCallId: str
    toolName: str
    args: dict

@dataclass
class ToolExecutionUpdateEvent:
    type: Literal["tool_execution_update"] = "tool_execution_update"
    toolCallId: str
    toolName: str
    args: dict
    partialResult: dict  # 长时工具进度推送

@dataclass
class ToolExecutionEndEvent:
    type: Literal["tool_execution_end"] = "tool_execution_end"
    toolCallId: str
    toolName: str
    result: dict
    isError: bool

AgentEvent = Union[
    AgentStartEvent, AgentEndEvent,
    TurnStartEvent, TurnEndEvent,
    MessageStartEvent, MessageUpdateEvent, MessageEndEvent,
    ToolExecutionStartEvent, ToolExecutionUpdateEvent, ToolExecutionEndEvent,
]
```

### AgentContext

```python
@dataclass
class AgentContext:
    """只读快照，传给 AgentLoop。"""
    systemPrompt: str
    messages: list[AgentMessage]
    tools: list[AgentTool]
    sessionId: str  # 用于 provider cache 和 SessionDataManager
```

### AgentLoopConfig

```python
@dataclass
class AgentLoopConfig:
    model: ModelInfo
    sessionId: str
    
    # LLM 转换
    convertToLlm: Callable[[list[AgentMessage]], list[dict]]
    
    # 可选上下文转换（在 convertToLlm 之前）
    transformContext: Optional[Callable[[list[AgentMessage], Optional[asyncio.Event]], 
                                        Awaitable[list[AgentMessage]]]] = None
    
    # API key 解析
    getApiKey: Optional[Callable[[str], Awaitable[Optional[str]]]] = None
    
    # 停止条件
    shouldStopAfterTurn: Optional[Callable[[ShouldStopAfterTurnContext], 
                                           Awaitable[bool]]] = None
    
    # 下一轮准备
    prepareNextTurn: Optional[Callable[[PrepareNextTurnContext, Optional[asyncio.Event]],
                                       Awaitable[Optional[AgentLoopTurnUpdate]]]] = None
    
    # 队列
    getSteeringMessages: Optional[Callable[[], Awaitable[list[AgentMessage]]]] = None
    getFollowUpMessages: Optional[Callable[[], Awaitable[list[AgentMessage]]]] = None
    
    # Tool 执行模式
    toolExecution: ToolExecutionMode = ToolExecutionMode.PARALLEL
    
    # 钩子
    beforeToolCall: Optional[BeforeToolCallFn] = None
    afterToolCall: Optional[AfterToolCallFn] = None
    
    # Stream options
    streamOptions: Optional[StreamOptions] = None
```

### 辅助类型

```python
class ToolExecutionMode(str, Enum):
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"

@dataclass
class ShouldStopAfterTurnContext:
    message: AgentMessage
    toolResults: list[ToolResultMessage]
    context: AgentContext
    newMessages: list[AgentMessage]

@dataclass
class PrepareNextTurnContext(ShouldStopAfterTurnContext):
    pass

@dataclass
class AgentLoopTurnUpdate:
    context: Optional[AgentContext] = None
    model: Optional[ModelInfo] = None
    thinkingLevel: Optional[str] = None  # "off" | "minimal" | ...

@dataclass
class BeforeToolCallContext:
    assistantMessage: AgentMessage
    toolCall: AgentToolCall
    args: dict
    context: AgentContext

@dataclass
class AfterToolCallContext:
    assistantMessage: AgentMessage
    toolCall: AgentToolCall
    args: dict
    result: ToolResult
    isError: bool
    context: AgentContext

@dataclass
class ToolResult:
    content: list[dict]  # [{"type": "text", "text": "..."}]
    details: Any = None
    terminate: bool = False
```

---

## 与现有系统的映射

| 当前 `ChatEngine` | 新 `Agent` 体系 | 说明 |
|-------------------|-----------------|------|
| `round_index` | `Turn`（隐式） | 每轮 LLM call = 一个 Turn |
| `max_rounds=60` | `AgentLoop` 轮次限制 | 可改为 token/time budget |
| `TaskTracker._cancelled` | `AbortSignal` | 移到 Loop 事件边界检查 |
| `step_start` / `step_result` | `tool_execution_start` / `tool_execution_end` | 对齐 Pi 事件类型 |
| `step_error` | `tool_execution_end` (isError=True) | 合并 |
| `task_start` / `task_complete` | `agent_start` / `agent_end` | 对齐 Pi 事件类型 |
| `plan_ready` / `plan_finalized` | 自定义事件或 `message_start` | 保留 GIS 专属事件 |

---

## 关键约束满足检查

- ✅ **兼容现有 SSE 协议**：`tool_execution_start/end/update` 映射到现有的 `step_start/result/error`
- ✅ **cooperative cancellation**：`AbortSignal` 在事件边界检查
- ✅ **tool_execution_update**：支持长时 GIS 工具进度推送
- ✅ **可序列化**：所有事件都是 dataclass，可 JSON 序列化
- ✅ **无显式 Step**：沿用 Pi 的事件流模型，不引入 Step

## Reference

- Pi `agent-loop.ts`: `/tmp/pi/packages/agent/src/agent-loop.ts` (完整已读)
- Pi `AgentEvent` type: `/tmp/pi/packages/agent/src/types.ts` (422-437 行)
- Pi `AgentLoopConfig` type: `/tmp/pi/packages/agent/src/types.ts` (144-287 行)
- Pi `BeforeToolCallContext` / `AfterToolCallContext`: `/tmp/pi/packages/agent/src/types.ts` (93-118 行)
- Pi `AgentLoopTurnUpdate`: `/tmp/pi/packages/agent/src/types.ts` (133-140 行)
- 当前 ChatEngine.chat_stream: `app/services/chat_engine.py` (540-803 行)
- 当前 SSE helpers: `app/services/chat/sse_helpers.py`
- 当前 TaskTracker: `app/services/task_tracker.py`
