# Ticket: 事件系统设计

## Resolution

基于对 Pi (`agent-loop.ts` + `types.ts`) 实际事件系统的逐行分析，以及当前 `sse_helpers.py` / `frontend/lib/types/agent-events.ts` / `decision_log.py` / `session_data.py` 的完整阅读，设计如下。

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

```python
class EventBus:
    """事件总线：支持多消费者，按订阅顺序执行。"""
    
    def __init__(self):
        self._listeners: list[EventListenerFn] = []
    
    def subscribe(self, listener: EventListenerFn) -> Callable[[], None]:
        """订阅事件，返回 unsubscribe 函数。"""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)
    
    async def emit(self, event: AgentEvent, signal: Optional[asyncio.Event]) -> None:
        """向所有 listener 发出事件，按订阅顺序 await。"""
        for listener in self._listeners:
            await listener(event, signal)

# In Agent:
class Agent:
    _eventBus: EventBus
    
    def subscribe(self, listener: EventListenerFn) -> Callable[[], None]:
        return self._eventBus.subscribe(listener)
    
    async def processEvents(self, event: AgentEvent) -> None:
        # 1. 更新 _state
        # 2. 通过 EventBus 发出事件
        await self._eventBus.emit(event, self._activeRun?.abortController.signal)
```

### 3. 事件与 SSE 的映射：内部事件 → 外部 SSE

**决策**：事件系统是内部抽象，SSE 是外部序列化。映射层在 FastAPI route 中处理。

**理由**：
- Pi 的事件是内部抽象，不直接对应 SSE
- 我们的前端已经依赖 SSE 协议
- 映射层负责：事件类型转换、数据格式化、SSE 格式封装

```python
# FastAPI route 中的映射
class SSEMapper:
    """将 AgentEvent 映射为 SSE 格式字符串。"""
    
    EVENT_TYPE_MAP = {
        "agent_start": "task_start",          # GIS 兼容
        "agent_end": "task_complete",         # GIS 兼容
        "turn_start": "turn_start",           # 新增
        "turn_end": "turn_end",               # 新增
        "message_start": "content",           # GIS 兼容（token 流用 message_update）
        "message_update": "token",            # GIS 兼容
        "message_end": "content",             # GIS 兼容
        "tool_execution_start": "step_start", # GIS 兼容
        "tool_execution_update": "step_result", # GIS 兼容（partial）
        "tool_execution_end": "step_result",  # GIS 兼容
    }
    
    def to_sse(self, event: AgentEvent) -> str:
        """将 AgentEvent 转换为 SSE 格式字符串。"""
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
    "tool_execution_start",  # 记录工具调用开始
    "tool_execution_end",    # 记录工具调用结果
    "turn_start",            # 记录 turn 开始
    "turn_end",              # 记录 turn 结束
    "agent_start",           # 记录 agent run 开始
    "agent_end",             # 记录 agent run 结束
}

async def on_event(event: AgentEvent):
    """EventBus listener：持久化关键事件。"""
    if event["type"] in PERSIST_EVENT_TYPES:
        await log_to_decision_log(event)
    
    # 事件摘要存入 session_data_manager（最近 20 条）
    await session_data_manager.append_event(
        session_id=context.sessionId,
        event=event["type"],
        data=summarize_event(event),
    )
```

---

## 完整类型定义

```python
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Union
import asyncio

# ── Event types ────────────────────────────────────────────

@dataclass
class AgentStartEvent:
    type: Literal["agent_start"] = "agent_start"

@dataclass
class AgentEndEvent:
    type: Literal["agent_end"] = "agent_end"
    messages: list[AgentMessage]
    aborted: bool = False
    truncated: bool = False  # max_rounds 达到

@dataclass
class TurnStartEvent:
    type: Literal["turn_start"] = "turn_start"

@dataclass
class TurnEndEvent:
    type: Literal["turn_end"] = "turn_end"
    message: AgentMessage
    toolResults: list[ToolResultMessage]
    details: dict = field(default_factory=dict)  # GIS: plan_step, perception, etc.

@dataclass
class MessageStartEvent:
    type: Literal["message_start"] = "message_start"
    message: AgentMessage
    details: dict = field(default_factory=dict)  # GIS: perception snapshot

@dataclass
class MessageUpdateEvent:
    type: Literal["message_update"] = "message_update"
    message: AgentMessage
    assistantMessageEvent: dict  # Pi 的 AssistantMessageEvent（增量 token）
    details: dict = field(default_factory=dict)

@dataclass
class MessageEndEvent:
    type: Literal["message_end"] = "message_end"
    message: AgentMessage
    details: dict = field(default_factory=dict)

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
    partialResult: dict  # 长时工具进度推送（GIS: {"progress": 0.5, "partial": ...}）
    details: dict = field(default_factory=dict)

@dataclass
class ToolExecutionEndEvent:
    type: Literal["tool_execution_end"] = "tool_execution_end"
    toolCallId: str
    toolName: str
    result: dict
    isError: bool
    details: dict = field(default_factory=dict)

AgentEvent = Union[
    AgentStartEvent, AgentEndEvent,
    TurnStartEvent, TurnEndEvent,
    MessageStartEvent, MessageUpdateEvent, MessageEndEvent,
    ToolExecutionStartEvent, ToolExecutionUpdateEvent, ToolExecutionEndEvent,
]

# ── Event bus ─────────────────────────────────────────────

EventListenerFn = Callable[[AgentEvent, Optional[asyncio.Event]], Awaitable[None]]
AgentEventSink = Callable[[AgentEvent], Awaitable[None]]  # Loop 使用的简化接口

class EventBus:
    """事件总线：支持多消费者，按订阅顺序执行。"""
    
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

---

## 与当前系统的映射

| 当前系统 | 新事件系统 | 说明 |
|----------|-----------|------|
| `sse_event("task_start", ...)` | `AgentStartEvent` → SSE mapper → `task_start` | 类型对齐 |
| `sse_event("task_complete", ...)` | `AgentEndEvent` → SSE mapper → `task_complete` | 类型对齐 |
| `sse_event("turn_start", ...)` | `TurnStartEvent` → SSE mapper → `turn_start` | 新增 |
| `sse_event("turn_end", ...)` | `TurnEndEvent` → SSE mapper → `turn_end` | 新增 |
| `sse_event("content", ...)` | `MessageStartEvent` / `MessageUpdateEvent` → SSE mapper → `content` / `token` | 拆分 |
| `sse_event("step_start", ...)` | `ToolExecutionStartEvent` → SSE mapper → `step_start` | 类型对齐 |
| `sse_event("step_result", ...)` | `ToolExecutionEndEvent` → SSE mapper → `step_result` | 类型对齐 |
| `sse_event("step_error", ...)` | `ToolExecutionEndEvent` (isError=True) → SSE mapper → `step_error` | 合并 |
| `sse_event("plan_ready", ...)` | `TurnStartEvent.details` → SSE mapper → `plan_ready` | details 携带 |
| `sse_event("plan_finalized", ...)` | `TurnEndEvent.details` → SSE mapper → `plan_finalized` | details 携带 |
| `decision_log.py` | EventBus listener → `log_tool_decision()` | 解耦 |
| `session_data._event_log` | EventBus listener → `append_event()` | 解耦 |
| `broadcast_ws_event()` | EventBus listener → WS broadcast | 解耦 |

---

## 关键约束满足检查

- ✅ **向后兼容前端 SSE 协议**：`SSEMapper` 将新事件类型映射到现有 SSE 事件名
- ✅ **多消费者支持**：`EventBus` 支持 SSE 输出、决策日志、审计、WebSocket 广播
- ✅ **事件可序列化**：所有事件都是 dataclass，可 JSON 序列化
- ✅ **agent_end listener settle**：`EventBus.emit` 按订阅顺序 await，保证 Pi 的 settle 语义
- ✅ **GIS 专属事件**：通过 `details` 字段携带，不新增事件类型
- ✅ **持久化策略**：关键事件持久化到 `decision_log`，事件摘要存入 `session_data_manager`

## Reference

- Pi `AgentEvent` type: `/tmp/pi/packages/agent/src/types.ts` (422-437 行，完整已读)
- Pi `Agent.subscribe`: `/tmp/pi/packages/agent/src/agent.ts` (243-246 行)
- Pi `emit` in agent-loop: `/tmp/pi/packages/agent/src/agent-loop.ts` (多处)
- 当前 `sse_event`: `app/services/chat/sse_helpers.py`
- 当前 frontend events: `frontend/lib/types/agent-events.ts`
- 当前 session event_log: `app/services/session_data.py`
- 当前 decision_log: `app/services/chat/decision_log.py`
- 当前 WS broadcast: `app/services/ws_service.py`
