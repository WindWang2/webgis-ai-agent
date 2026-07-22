# Ticket: 事件系统设计

## Resolution

基于对 Pi (`agent-loop.ts` + `types.ts`) 实际事件系统的逐行分析，以及当前 `sse_helpers.py` / `frontend/lib/types/agent-events.ts` / `decision_log.py` / `session_data.py` 的完整阅读，设计如下。

> **Review 修复 (2026-07-21)**：
> - 补充 `Agent._create_emit_fn()` 包装器，解决 `AgentEventSink` 单参数与 `EventBus.emit` 双参数的接口差异
> - 补充 `message_update` 的 `assistantMessageEvent` 结构说明（Pi 的增量 token 格式）
> - 明确 SSE mapper 属于 FastAPI route 层，不在核心事件系统内
> - 补充 EventBus 的 `agent_end` settle 语义说明

---

## 设计决策

### 1. 事件类型集合：10 种核心 + GIS 扩展

**决策**：保留 Pi 的 10 种核心事件类型，增加 GIS 专属扩展。

**理由**：
- Pi 的 10 种事件已经覆盖了 agent 生命周期的所有关键节点
- GIS 专属事件（如 `perception_update`、`plan_ready`）可以通过现有事件的 `details` 字段携带，不需要新增事件类型
- 保持事件类型最小化，降低前端消费复杂度

```python
# 核心事件（对齐 Pi）
AgentEvent = Union[
    AgentStartEvent,           # agent_start
    AgentEndEvent,             # agent_end
    TurnStartEvent,            # turn_start
    TurnEndEvent,              # turn_end
    MessageStartEvent,         # message_start
    MessageUpdateEvent,        # message_update
    MessageEndEvent,           # message_end
    ToolExecutionStartEvent,   # tool_execution_start
    ToolExecutionUpdateEvent,  # tool_execution_update
    ToolExecutionEndEvent,     # tool_execution_end
]

# GIS 扩展（通过 details 字段携带，不新增事件类型）
# - perception_update: 在 message_start/message_end 的 details 中携带
# - plan_ready/plan_finalized: 在 turn_start/turn_end 的 details 中携带
# - layer_toggled: 在 tool_execution_update 的 partialResult 中携带
```

### 2. 事件总线：subscribe 回调模式 + EventBus 中间层

**决策**：保留 Pi 的 `subscribe(listener)` 回调模式，但增加 `EventBus` 中间层支持多消费者。

**理由**：
- Pi 的做法：`Agent.subscribe(listener)` — 简单直接，listener 按订阅顺序执行
- 我们的需求：多个消费者（SSE 输出 + 决策日志 + 审计）
- 方案：`EventBus` 内部维护 listener 列表，`subscribe()` 返回 unsubscribe 函数
- `Agent` 持有 `EventBus`，Loop 的 `emit` 委托给 `EventBus`

**关键接口差异**（Pi vs 我们的设计）：
- Pi 的 `AgentEventSink` = `(event: AgentEvent) => Promise<void>` — 单参数
- Pi 的 `Agent.subscribe` listener = `(event: AgentEvent, signal: AbortSignal) => Promise<void>` — 双参数
- Loop 调用 `emit(event)`（单参数），Agent 内部调用 `listener(event, signal)`（双参数）

我们的设计保持这个模式：
```python
AgentEventSink = Callable[[AgentEvent], Awaitable[None]]  # Loop 使用的单参数接口
EventListenerFn = Callable[[AgentEvent, Optional[asyncio.Event]], Awaitable[None]]  # listener 双参数

# Agent 创建包装器，将单参数 emit 转换为带 signal 的 EventBus.emit
async def _create_emit_fn(self) -> AgentEventSink:
    async def emit(event: AgentEvent) -> None:
        signal = self._activeRun?.abortController.signal if self._activeRun else None
        await self._eventBus.emit(event, signal)
    return emit
```

```python
class EventBus:
    """事件总线：支持多消费者，按订阅顺序执行。
    
    settle 语义：agent_end 事件的 listener Promise 必须全部 settle 后，
    Agent 才算进入 idle 状态（对齐 Pi 的 agent_end listener 语义）。
    """
    
    def __init__(self):
        self._listeners: list[EventListenerFn] = []
    
    def subscribe(self, listener: EventListenerFn) -> Callable[[], None]:
        """订阅事件，返回 unsubscribe 函数。"""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)
    
    async def emit(self, event: AgentEvent, signal: Optional[asyncio.Event] = None) -> None:
        """向所有 listener 发出事件，按订阅顺序 await。"""
        for listener in self._listeners:
            try:
                await listener(event, signal)
            except Exception as e:
                logger.warning(f"[EventBus] listener failed: {e}")
    
    def clear(self) -> None:
        """清除所有 listener（用于 session 清理）。"""
        self._listeners.clear()
```

### 3. 事件与 SSE 的映射：内部事件 → 外部 SSE

**决策**：事件系统是内部抽象，SSE 是外部序列化。映射层在 FastAPI route 中处理，不在核心事件系统内。

**理由**：
- Pi 的事件是内部抽象，不直接对应 SSE
- 我们的前端已经依赖 SSE 协议
- 映射层负责：事件类型转换、数据格式化、SSE 格式封装
- SSE mapper 属于 FastAPI route 层的适配器，不是核心事件系统的职责

```python
# FastAPI route 中的映射（不属于 app/agent/ 核心包）
class SSEMapper:
    """将 AgentEvent 映射为 SSE 格式字符串（FastAPI route 层适配器）。"""
    
    EVENT_TYPE_MAP = {
        "agent_start": "task_start",
        "agent_end": "task_complete",
        "turn_start": "turn_start",       # 前端需新增处理
        "turn_end": "turn_end",           # 前端需新增处理
        "message_start": "content",
        "message_update": "token",
        "message_end": "content",
        "tool_execution_start": "step_start",
        "tool_execution_update": "step_result",
        "tool_execution_end": "step_result",
    }
    
    def to_sse(self, event: AgentEvent) -> str:
        event_type = self.EVENT_TYPE_MAP.get(event["type"], event["type"])
        data = self._serialize(event)
        return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
```

### 4. 事件持久化：决策日志 + 事件摘要

**决策**：不持久化完整事件流，只持久化关键事件到 `decision_log`。

**理由**：
- Pi 的做法：不持久化事件流（通过 session storage 间接保存）
- 当前的做法：`event_log` 只保留最近 20 条
- 我们的做法：持久化 `tool_execution_start/end` 和 `turn_start/end` 到 `decision_log`（JSONL）
- 事件摘要（而非完整事件）存入 `session_data_manager._event_log`

```python
# 持久化策略
PERSIST_EVENT_TYPES = {
    "tool_execution_start",
    "tool_execution_end",
    "turn_start",
    "turn_end",
    "agent_start",
    "agent_end",
}

async def on_event(event: AgentEvent, signal: Optional[asyncio.Event], session_id: str):
    """EventBus listener：持久化关键事件。"""
    if event["type"] in PERSIST_EVENT_TYPES:
        await log_to_decision_log(event)
    
    # 事件摘要存入 session_data_manager（最近 20 条）
    await session_data_manager.append_event(
        session_id=session_id,
        event=event["type"],
        data=summarize_event(event),
    )
```