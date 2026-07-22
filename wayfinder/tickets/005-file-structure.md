# Ticket: 新文件结构与模块划分

## Resolution

基于对 Pi (`packages/agent/src/`) 和 OpenCode (`packages/core/src/`) 实际模块划分的分析，以及对当前 `app/services/chat_engine.py` (856 LOC 大杂烩) / `app/services/chat/` 子包 / `app/tools/` / `app/lib/geo_analysis/` 的完整阅读，设计如下。

---

## 设计决策

### 1. 顶层包：`app/agent/`

**决策**：引入 `app/agent/` 顶层包，包含核心 Agent 类体系。

**理由**：
- Pi 的做法：`packages/agent/` 独立包，与 `packages/ai/`（LLM）、`packages/coding-agent/`（CLI）分离
- 当前 `app/services/chat_engine.py` 是大杂烩（856 LOC），需要拆分为独立的 Agent 类
- `app/agent/` 与 `app/tools/`、`app/lib/` 平级，形成清晰的三层架构

**不采用 OpenCode 的函数式架构**（Effect-TS），因为我们使用 Python + asyncio， imperative 风格更自然。

### 2. 模块依赖方向：单向依赖链

**决策**：`app/agent/` → `app/tools/` → `app/lib/`

```
app/agent/       (Agent 类体系，不依赖具体 GIS 算法)
    ↓ 依赖
app/tools/       (工具注册、工具实现，依赖 GIS 算法)
    ↓ 依赖
app/lib/geo_analysis/  (纯 GIS 算法，无 Agent 依赖)
```

**具体规则**：
- `app/agent/` 可以导入 `app/tools/`（通过 `ToolRegistry` 接口）
- `app/tools/` 可以导入 `app/lib/`（GIS 算法）
- `app/lib/` **不能**导入 `app/tools/` 或 `app/agent/`
- `app/services/chat/` 是共享的 LLM/上下文工具，`app/agent/` 和旧代码都可以导入

### 3. Harness 层：保留，但最小化

**决策**：保留 `app/agent/harness/`，但只放当前必需的功能。

**Pi 的 harness 包含**：compaction、session、skills、system-prompt、messages
**我们的 harness 包含**：

```python
app/agent/harness/
├── __init__.py
├── compaction.py     # 上下文压缩（token-aware LLM summarization）
├── skills.py         # 技能系统（从 app/tools/skills.py 迁移）
├── session.py        # 会话持久化（与现有 DB/Redis 集成）
└── system_prompt.py  # 系统提示构建（从 app/services/chat/prompt.py 迁移）
```

**不放在 harness 中的**：
- `context_builder.py` → 留在 `app/services/chat/`（被 Agent 和旧代码共享）
- `sse_helpers.py` → 留在 `app/services/chat/`（被 FastAPI route 和 Agent 共享）
- `decision_log.py` → 留在 `app/services/chat/`（被 Agent 和旧代码共享）

### 4. 旧代码过渡：兼容层 + 渐进迁移

**决策**：保留旧代码，通过 feature flag 切换。

**过渡策略**：
1. **Phase 1**：创建 `app/agent/` 新包，旧 `ChatEngine` 保持不变
2. **Phase 2**：FastAPI route 通过 `USE_NEW_AGENT` env var 选择使用新 Agent 或旧 ChatEngine
3. **Phase 3**：逐步将旧代码迁移到新接口，最终移除 `ChatEngine`

```python
# app/api/routes/chat.py
from app.agent.runtime import AgentRuntime
from app.services.chat_engine import ChatEngine

agent_runtime = AgentRuntime(...)
legacy_engine = ChatEngine(...)

@app.post("/completions")
async def completions(request: Request):
    if os.getenv("USE_NEW_AGENT", "").lower() == "true":
        return await agent_runtime.handle_request(request)
    return await legacy_engine.chat(...)
```

### 5. 测试结构：扩展现有 `tests/`

**决策**：在现有 `tests/` 基础上扩展，不创建新的测试目录。

```python
tests/
├── unit/
│   ├── agent/                    # 新 Agent 单元测试
│   │   ├── __init__.py
│   │   ├── test_agent.py         # Agent 基类测试
│   │   ├── test_loop.py          # AgentLoop 测试
│   │   ├── test_event.py         # EventBus 测试
│   │   ├── test_stream.py        # StreamFn 适配器测试
│   │   └── test_runtime.py       # AgentRuntime 测试
│   ├── tools/                    # 已有工具测试
│   ├── chat/                     # 已有 chat 子模块测试
│   └── ...
├── integration/
│   ├── test_agent_loop_integration.py  # AgentLoop 集成测试
│   ├── test_agent_legacy_compat.py     # 新旧兼容测试
│   └── ...
└── fixtures/
    └── agent/                    # Agent 测试 fixture
        ├── conftest.py           # Agent fixture 工厂
        └── mock_llm.py           # Mock LLM 响应
```

---

## 完整目录结构

### 新增：`app/agent/`

```
app/agent/
├── __init__.py              # 统一导出：Agent, AgentLoop, AgentRuntime, EventBus, 等
├── _agent.py                # Agent 基类（状态管理、生命周期、队列、钩子）
├── _loop.py                 # AgentLoop 纯函数（run_agent_loop, execute_parallel/sequential）
├── _types.py                # 类型定义（AgentEvent, AgentContext, AgentLoopConfig, ...）
├── _stream.py               # StreamFn 适配器（将 LLMClient 包装为 StreamFn）
├── _event.py                # 事件系统（EventBus, AgentEventSink, EventListenerFn）
├── _context_manager.py      # 上下文管理（AgentContext 构建、PerceptionInjector）
├── _runtime.py              # AgentRuntime（管理 Agent 实例生命周期）
│
├── chat/                    # ChatAgent 子类（对话 Agent）
│   ├── __init__.py
│   ├── _chat_agent.py       # ChatAgent(Agent)：对话 Agent 实现
│   ├── _tool_runner.py      # 工具执行（prepare/execute/finalize）
│   └── _self_healing.py     # Self-healing 提示生成
│
├── subagent/                # Subagent 子类（子任务 Agent）
│   ├── __init__.py
│   ├── _subagent.py         # Subagent(Agent)：子任务 Agent 实现
│   └── _tool_selector.py    # 工具子集选择器
│
└── harness/                 # 可选增强层
    ├── __init__.py
    ├── compaction.py         # 上下文压缩（token-aware LLM summarization）
    ├── skills.py             # 技能系统（动态加载 .py/.md 技能）
    ├── session.py            # 会话持久化（DB/Redis 集成）
    └── system_prompt.py      # 系统提示构建（GIS 专属提示词）
```

### 保持不变：`app/services/chat/`

```
app/services/chat/
├── __init__.py
├── dispatcher.py            # 工具调度（保留，被 Agent 和旧代码共享）
├── llm_client.py            # LLM 客户端（保留，被 StreamFn 适配器使用）
├── prompt.py                # 系统提示模板（保留，被 harness/system_prompt.py 使用）
├── planner.py               # 规划阶段（保留，被 ChatAgent.prepareNextTurn 使用）
├── context_builder.py       # 上下文组装（保留，被 Agent 和旧代码共享）
├── sse_helpers.py           # SSE 辅助（保留，被 FastAPI route 使用）
├── decision_log.py          # 决策日志（保留，被 EventBus listener 使用）
└── context/                 # 上下文子模块
    ├── layer_schema.py
    ├── geometry.py
    └── viewport_naming.py
```

### 保持不变：`app/tools/`

```
app/tools/
├── __init__.py              # init_tools() - 注册所有工具
├── registry.py              # ToolRegistry（保持不变）
├── skills.py                # 技能加载（保留，harness/skills.py 调用）
├── subagent.py              # spawn_subagent 工具（保留，Subagent 子类调用）
├── spatial.py               # 空间工具
├── advanced_spatial.py      # 高级空间工具
├── ...                      # 40+ 工具模块
```

### 保持不变：`app/services/`

```
app/services/
├── chat_engine.py           # 旧 ChatEngine（兼容层，最终移除）
├── subagent.py              # 旧 SubagentDispatcher（兼容层，最终移除）
├── plan_mode.py             # Plan 模式（保留，被 ChatAgent 使用）
├── tool_catalog.py          # 工具目录（保留，被 ChatAgent._select_tools 使用）
├── session_data.py          # 会话数据（保留，被 AgentRuntime 使用）
├── session_data_redis.py    # Redis 会话数据（保留）
├── task_tracker.py          # 任务跟踪（保留，被 AgentRuntime 使用）
└── ...
```

### 最终目标结构（Phase 3 完成后）

```
app/
├── agent/                   # 新 Agent 类体系
│   ├── _agent.py
│   ├── _loop.py
│   ├── _types.py
│   ├── _stream.py
│   ├── _event.py
│   ├── _context.py
│   ├── _runtime.py
│   ├── chat/
│   ├── subagent/
│   └── harness/
├── tools/                   # 工具注册和实现
├── services/
│   ├── chat/                # 共享的 LLM/上下文/SSE 工具
│   ├── plan_mode.py         # Plan 模式
│   ├── tool_catalog.py      # 工具目录
│   ├── session_data.py      # 会话数据
│   └── task_tracker.py      # 任务跟踪
├── lib/
│   └── geo_analysis/        # GIS 算法
└── main.py                  # FastAPI 入口
```

---

## 与 Pi/OpenCode 的对比

| 方面 | Pi | OpenCode | 我们的设计 |
|------|----|----------|-----------|
| Agent 位置 | `packages/agent/` 独立包 | `packages/core/src/agent.ts` | `app/agent/` 顶层包 |
| Loop 位置 | `packages/agent/src/agent-loop.ts` | 无显式 Loop | `app/agent/_loop.py` |
| 类型定义 | `packages/agent/src/types.ts` | `packages/core/src/agent.ts` | `app/agent/_types.py` |
| Harness | `packages/agent/src/harness/` | 无显式 harness | `app/agent/harness/` |
| Tool 定义 | `AgentTool` 接口 | `Definition<Input, Output>` | 保留现有 `@tool` 装饰器 |
| Tool 注册 | 无显式注册（通过 AgentTool 接口） | `make()` + `settle()` | `ToolRegistry`（现有） |
| 状态管理 | Agent 持有 mutable state | Service pattern（Effect Context） | Agent 持有 mutable state |
| 架构风格 | OOP + 纯函数 | 函数式（Effect-TS） | OOP + 纯函数（对齐 Pi） |

---

## 关键约束满足检查

- ✅ **保持与现有 FastAPI 路由的兼容性**：旧 `ChatEngine` 保持不变，新 Agent 通过 feature flag 切换
- ✅ **渐进替换**：新旧代码共存，Phase 1-3 渐进迁移
- ✅ **测试结构支持 pytest**：扩展现有 `tests/unit/agent/` 和 `tests/integration/`
- ✅ **不破坏现有 import 结构**：`app/agent/` 是新增包，不修改现有 import
- ✅ **单向依赖链**：`app/agent/` → `app/tools/` → `app/lib/`
- ✅ **共享代码留在原地**：`app/services/chat/` 被新旧代码共享，不迁移

## Reference

- Pi `packages/agent/src/`: `/tmp/pi/packages/agent/src/`（完整已读）
- Pi `index.ts`: `/tmp/pi/packages/agent/src/index.ts`（导出结构）
- OpenCode `packages/core/src/`: `/tmp/opencode/packages/core/src/`（部分已读）
- 当前 `app/services/chat_engine.py` (完整已读)
- 当前 `app/services/chat/` 子包 (完整已读)
- 当前 `app/tools/registry.py` (完整已读)
- 当前 `app/services/subagent.py` (完整已读)
- 当前 `app/services/task_tracker.py` (完整已读)
- 当前 `app/services/session_data.py` (完整已读)
