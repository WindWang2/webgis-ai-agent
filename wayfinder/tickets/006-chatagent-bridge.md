# Ticket: ChatAgent 子类 + ChatEngine 桥接

## 目标

实现 `ChatAgent` 子类，作为新 Agent 系统与现有 `ChatEngine` 之间的桥梁，使得新系统能够：
1. 复用现有的上下文构建、SSE 映射、决策日志
2. 通过 `ToolRegistry` 选择和管理工具
3. 通过 `ToolCatalog` 做关键词匹配的工具预筛选
4. 与现有 FastAPI route 无缝集成（通过 feature flag）

## 设计决策

### 1. ChatAgent 继承 Agent 基类

**决策**：`ChatAgent(Agent)` 覆盖 `_select_tools`、`_build_system_prompt`、`dispatch_tool`。

**理由**：
- Agent 基类已经定义了完整生命周期
- ChatAgent 只需要填充 GIS 特定的行为
- 保持与 Pi 的 Agent subclass 模式一致

### 2. 工具选择：ToolCatalog + ToolRegistry 双层

**决策**：
1. `ToolCatalog` 做关键词匹配，返回候选工具名列表
2. `ToolRegistry` 根据候选名获取完整 tool schema
3. 将 tool schema 传入 AgentLoop

**理由**：
- ToolCatalog 是纯关键词匹配，不需要 LLM
- ToolRegistry 管理 tool 实现和 schema
- 两层分离关注点：匹配 vs 执行

### 3. 上下文构建：复用现有 ChatEngine 逻辑

**决策**：从 `app/services/chat/context_builder.py` 导入 `build_context` 函数，在 `_build_system_prompt` 和 `_compose_request_messages` 中复用。

**理由**：
- 现有上下文构建已经考虑了 GIS 特定格式
- 避免重复实现

### 4. SSE 事件映射：Event → SSE 格式

**决策**：在 `ChatAgent` 中实现 `_map_event_to_sse` 方法，将 AgentEvent 转换为前端 SSE 格式。

**理由**：
- 前端已经期望特定的 SSE 格式
- EventBus 是通用的，需要映射层

### 5. Feature Flag：USE_NEW_AGENT

**决策**：通过环境变量 `USE_NEW_AGENT=true` 切换新旧系统。

**实现**：
- Phase 1：创建 `app/agent/` 包（已完成）
- Phase 2：FastAPI route 检查 env var，选择新/旧系统
- Phase 3：逐步迁移旧代码

## 实现计划

### 文件结构

```
app/agent/
├── __init__.py              # 已存在
├── _types.py                # 已存在
├── _event.py                # 已存在
├── _stream.py               # 已存在
├── _agent.py                # 已存在
├── _loop.py                 # 已存在
├── _runtime.py              # 已存在
├── chat/
│   ├── __init__.py          # 已存在
│   └── _chat_agent.py       # [NEW] ChatAgent 子类
└── subagent/
    ├── __init__.py          # 已存在
    └── _subagent.py         # [NEW] Subagent 子类
```

### ChatAgent 核心方法

```python
class ChatAgent(Agent):
    def __init__(self, ...):
        super().__init__(...)
        self._tool_registry = ToolRegistry()
        self._tool_catalog = ToolCatalog()
    
    async def _build_system_prompt(self) -> str:
        """构建系统提示，复用现有 prompt 构建逻辑"""
    
    async def _select_tools(self, context: AgentContext) -> list[dict]:
        """通过 ToolCatalog + ToolRegistry 选择工具"""
    
    async def dispatch_tool(self, name: str, args: dict) -> ToolResult:
        """分发工具调用到 ToolRegistry"""
    
    def _map_event_to_sse(self, event: AgentEvent) -> dict:
        """将 AgentEvent 映射为 SSE 格式"""
    
    async def handle_request(self, request) -> StreamingResponse:
        """处理 HTTP 请求，返回 SSE 流"""
```

### 与 ChatEngine 的桥接

```python
# app/services/chat_engine.py
class ChatEngine:
    def __init__(self):
        self._agent = None  # 延迟初始化
    
    async def chat_stream(self, ...):
        if os.getenv("USE_NEW_AGENT", "").lower() == "true":
            return await self._chat_with_new_agent(...)
        return await self._chat_with_legacy(...)
```

## 验收标准

1. `ChatAgent` 可以通过 `prompt()` 方法启动对话
2. `ToolCatalog` 关键词匹配正确返回候选工具
3. `ToolRegistry` 能正确执行工具并返回结果
4. AgentEvent 正确映射为 SSE 格式
5. 通过 `USE_NEW_AGENT=true` 可以在新旧系统间切换
6. 所有现有测试继续通过
