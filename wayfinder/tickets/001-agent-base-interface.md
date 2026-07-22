# Ticket: Agent 基类接口契约设计

## Resolution

基于对 Pi (`earendil-works/pi`) 实际源码的逐行分析，以及对当前 `ChatEngine` / `SubagentDispatcher` / `ToolRegistry` / `SessionDataManager` / `TaskTracker` 的完整阅读，设计如下。

---

## 设计决策

### 1. 状态管理：Agent 持有 `_state`（Pi 模式）

**决策**：基类持有 `_state`，不通过外部 Memory 注入。

**理由**：
- Pi 的 Agent 是有状态的，`_state` 包含 `tools`、`messages`、`model`、`systemPrompt`
- 当前 `ChatEngine` 的 `_sessions` (LRUCache) 和 `_session_locks` 是 per-session 状态，应该移到 Agent 外部（由 `AgentRuntime` 管理）
- Agent 持有当前对话的 messages/tools/model，但 session 级别的 refs/map_state 仍在 `SessionDataManager` 中
- `tools` 和 `messages` 的赋值必须 copy-on-write（Pi 的 getter/setter 模式），防止外部突变

```python
class AgentState:
    systemPrompt: str
    model: ModelInfo
    tools: list[AgentTool]          # copy-on-write
    messages: list[AgentMessage]    # copy-on-write
    # runtime state
    isStreaming: bool
    streamingMessage: Optional[AgentMessage]
    pendingToolCalls: set[str]
    errorMessage: Optional[str]
```

### 2. 生命周期钩子：全部放在基类，通过函数属性配置（Pi 模式）

**决策**：4 个钩子全部放在基类 `Agent`，子类通过构造函数或属性覆盖。

**GIS 专属钩子的位置**：
- `beforeToolCall`：注入 `[环境感知]`（所有子类都需要）
- `afterToolCall`：self-healing 错误提示（所有子类都需要）
- `prepareNextTurn`：plan-first 的 planning 阶段（`ChatAgent` 使用）
- `shouldStopAfterTurn`：subagent 的轮次预算检查（`Subagent` 使用）

```python
class Agent:
    beforeToolCall: Optional[BeforeToolCallFn]
    afterToolCall: Optional[AfterToolCallFn]
    prepareNextTurn: Optional[PrepareNextTurnFn]
    shouldStopAfterTurn: Optional[ShouldStopAfterTurnFn]
    
    # GIS-specific hooks (called inside the above)
    def _inject_perception(self, context: AgentContext) -> AgentContext: ...
    def _build_self_healing_hint(self, result: ToolResult) -> Optional[str]: ...
```

### 3. 队列机制：保留 steering/follow-up 队列（Pi 模式）

**决策**：保留 `PendingMessageQueue`，支持 "all" / "one-at-a-time" 模式。

**理由**：
- 长时 GIS 分析任务（热力图 30s，遥感分析 2min）期间，用户可能中途发送新消息
- 当前 `ChatEngine` 没有队列机制，用户必须等当前任务完成才能发新消息
- Pi 的 `steer()` 在 agent 运行中注入，`followUp()` 在 agent 停止后运行

```python
class PendingMessageQueue:
    mode: QueueMode  # "all" | "one-at-a-time"
    
    def enqueue(self, message: AgentMessage) -> None: ...
    def drain(self) -> list[AgentMessage]: ...
    def clear(self) -> None: ...

# In Agent:
steeringQueue: PendingMessageQueue  # 运行中注入
followUpQueue: PendingMessageQueue  # 停止后运行
```

### 4. StreamFn 抽象：创建适配器将 `call_llm_stream` 包装为 `StreamFn`

**决策**：创建 `StreamFnAdapter`，将 `LLMClient.call_llm_stream` 的 `(event_type, data)` tuple 流转换为 Pi 风格的事件流。

**理由**：
- Pi 的 `StreamFn` 返回 `AssistantMessageEventStream`（增量事件流）
- 我们的 `call_llm_stream` 返回 `(event_type, data)` tuples
- 适配器负责转换格式，Agent 不感知具体 LLM SDK

```python
StreamFn = Callable[[ModelInfo, AgentContext, StreamOptions], 
                    AsyncIterator[AssistantMessageEvent]]

# Adapter:
def create_stream_fn(llm_client: LLMClient) -> StreamFn:
    async def stream_fn(model, context, options=None):
        async for event_type, event_data in llm_client.call_llm_stream(...):
            yield convert_to_pi_event(event_type, event_data)
    return stream_fn
```

---

## 基类接口定义（伪代码）

```python
class Agent:
    # ── State (copy-on-write) ──────────────────────────────
    _state: AgentState
    
    @property
    def tools(self) -> list[AgentTool]: ...
    @tools.setter
    def tools(self, value: list[AgentTool]) -> None: ...
    
    @property
    def messages(self) -> list[AgentMessage]: ...
    @messages.setter
    def messages(self, value: list[AgentMessage]) -> None: ...
    
    # ── Lifecycle hooks (all optional, override in subclass) ──
    beforeToolCall: Optional[BeforeToolCallFn]
    afterToolCall: Optional[AfterToolCallFn]
    prepareNextTurn: Optional[PrepareNextTurnFn]
    shouldStopAfterTurn: Optional[ShouldStopAfterTurnFn]
    
    # ── Queues ─────────────────────────────────────────────
    steeringQueue: PendingMessageQueue
    followUpQueue: PendingMessageQueue
    
    # ── LLM abstraction ────────────────────────────────────
    streamFunction: StreamFn
    getApiKey: Optional[GetApiKeyFn]
    
    # ── Core API ───────────────────────────────────────────
    async def prompt(self, message: AgentMessage | str) -> None: ...
    def `continue`(self) -> None: ...     # keyword in backticks to avoid syntax error
    def abort(self) -> None: ...
    def reset(self) -> None: ...
    
    # ── Subscription ───────────────────────────────────────
    def subscribe(self, listener: ListenerFn) -> Callable[[], None]: ...
    
    # ── Internal (called by AgentLoop) ─────────────────────
    def createContextSnapshot(self) -> AgentContext: ...
    def createLoopConfig(self) -> AgentLoopConfig: ...
    async def processEvents(self, event: AgentEvent) -> None: ...
```

### 子类划分

| 方法/属性 | 基类 `Agent` | `ChatAgent` | `PlanAgent` | `Subagent` |
|-----------|:-----------:|:-----------:|:-----------:|:----------:|
| `_state` | ✅ 持有 | - | - | - |
| `beforeToolCall` | ✅ 默认注入感知 | 覆盖 | 继承 | 继承 |
| `afterToolCall` | ✅ 默认 self-healing | 覆盖 | 继承 | 继承 |
| `prepareNextTurn` | ✅ 空实现 | ✅ plan-first | ✅ plan-only | ✅ 轮次检查 |
| `shouldStopAfterTurn` | ✅ 空实现 | ✅ 检查 | 继承 | ✅ 轮次预算 |
| `prompt()` | ✅ 实现 | - | - | - |
| `continue()` | ✅ 实现 | - | - | - |
| `abort()` | ✅ 实现 | - | - | - |
| `subscribe()` | ✅ 实现 | - | - | - |
| `createContextSnapshot()` | ✅ 实现 | - | - | - |
| `createLoopConfig()` | ✅ 实现 | - | - | - |
| `processEvents()` | ✅ 实现 | - | - | - |

### 抽象方法（子类必须实现）

| 方法 | 子类 | 说明 |
|------|------|------|
| `_build_system_prompt()` | `ChatAgent` | 构建系统提示词 |
| `_select_tools()` | `ChatAgent` | 工具选择策略 |
| `_dispatch_tool()` | `ChatAgent` | 工具执行 |
| `_compose_request_messages()` | `ChatAgent` | 上下文组装 |

---

## 与当前系统的映射

| 当前 `ChatEngine` | 新 `Agent` 体系 | 说明 |
|-------------------|-----------------|------|
| `ChatEngine.__init__` | `Agent.__init__` + `AgentRuntime` | 配置拆分 |
| `self._sessions` (LRU) | `AgentRuntime._sessions` | 移出 Agent |
| `self._session_locks` | `AgentRuntime._locks` | 移出 Agent |
| `self.registry` | `AgentRuntime.registry` | 移出 Agent |
| `self.catalog` | `Agent._select_tools()` | 子类实现 |
| `self.tracker` | `AgentRuntime.tracker` | 移出 Agent |
| `chat_stream()` | `Agent.prompt()` → `AgentLoop` | 流程不变 |
| `chat()` | `Agent.prompt()` → `AgentLoop` (non-stream) | 流程不变 |
| `_maybe_plan()` | `ChatAgent.prepareNextTurn` | 集成进 hook |
| `_dispatch_tool()` | `ChatAgent._dispatch_tool()` | 子类实现 |
| `_apply_skill()` | `Agent.beforeToolCall` | 基类默认 |
| `_call_llm_stream()` | `StreamFnAdapter` | 适配器 |
| `SubagentDispatcher` | `Subagent(Agent)` | 子类化 |
| `TaskTracker` | 保留，由 `AgentRuntime` 管理 | 不变 |

---

## 关键约束满足检查

- ✅ **ref-based 数据流**：`SessionDataManager` 在 Agent 外部，refs 通过 `AgentContext` 传递
- ✅ **环境感知注入**：`Agent.beforeToolCall` 默认实现注入 `[环境感知]`
- ✅ **工具三层目录**：`ToolCatalog` 通过 `_select_tools()` 集成
- ✅ **渐进替换**：新 Agent 与旧 ChatEngine 并存，通过 feature flag 切换
- ✅ **FastAPI + asyncio**：`StreamFn` 是 async generator，兼容现有 SSE 输出
- ✅ **copy-on-write**：`tools` 和 `messages` 的 setter 做 `.copy()` / `.slice()`

## Reference

- Pi `Agent` class: `/tmp/pi/packages/agent/src/agent.ts` (171-577 行，完整已读)
- Pi `AgentTool` interface: `/tmp/pi/packages/agent/src/types.ts` (380-403 行)
- Pi `AgentEvent` type: `/tmp/pi/packages/agent/src/types.ts` (422-437 行)
- Pi `AgentLoopConfig` type: `/tmp/pi/packages/agent/src/types.ts` (144-287 行)
- 当前 `ChatEngine`: `app/services/chat_engine.py` (完整已读)
- 当前 `SubagentDispatcher`: `app/services/subagent.py` (完整已读)
- 当前 `ToolRegistry`: `app/tools/registry.py` (完整已读)
- 当前 `TaskTracker`: `app/services/task_tracker.py` (完整已读)
- 当前 `SessionDataManager`: `app/services/session_data.py` (完整已读)
