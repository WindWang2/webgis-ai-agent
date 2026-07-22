# Ticket: AgentRuntime + Feature Flag 集成

## 目标

将 AgentRuntime 连接到 FastAPI route，通过 `USE_NEW_AGENT` 环境变量实现新旧系统的无缝切换。

## 设计决策

### 1. Feature Flag 位置

**决策**：在 FastAPI route 层做切换，不修改 ChatEngine。

**实现**：
```python
# app/api/routes/chat.py
USE_NEW_AGENT = os.getenv("USE_NEW_AGENT", "").lower() == "true"

@router.post("/stream")
async def chat_stream(req: ChatRequest, _user: dict = Depends(get_current_user_optional)):
    if USE_NEW_AGENT and agent_runtime:
        return await agent_runtime.handle_stream_request(req, _user)
    # Legacy path
    ...
```

### 2. AgentRuntime.handle_request / handle_stream_request

**决策**：在 AgentRuntime 上添加两个方法，封装完整的请求处理逻辑。

```python
class AgentRuntime:
    async def handle_request(self, req, user) -> ChatResponse:
        """Non-streaming request → ChatResponse"""
    
    async def handle_stream_request(self, req, user) -> StreamingResponse:
        """Streaming request → StreamingResponse"""
```

### 3. AgentRuntime 生命周期

**决策**：AgentRuntime 在 lifespan 中初始化，持有 ChatEngine 的共享组件。

```python
# app/main.py lifespan
agent_runtime = AgentRuntime(
    registry=registry,
    catalog=catalog,
    llm_client=llm_client,
    session_data_manager=session_data_manager,
    task_tracker=task_tracker,
)
```

### 4. 共享组件

**决策**：registry, catalog, session_data_manager, task_tracker 在 ChatEngine 和 AgentRuntime 之间共享（单例）。

**理由**：这些组件是无状态的或已经有线程安全保证，不需要复制。

## 实现计划

1. 扩展 AgentRuntime 添加 handle_request/handle_stream_request 方法
2. 在 FastAPI route 中添加 USE_NEW_AGENT 条件分支
3. 确保 ChatAgent 能通过 AgentRuntime 正确初始化
4. 添加集成测试验证 feature flag 切换

## 验收标准

1. `USE_NEW_AGENT=true` 时，/chat/stream 路由使用新 Agent 系统
2. `USE_NEW_AGENT` 未设置时，继续使用旧 ChatEngine
3. 两种路径返回相同的 SSE 事件格式
4. AgentRuntime 正确管理 Agent 实例生命周期
5. 所有现有测试继续通过
