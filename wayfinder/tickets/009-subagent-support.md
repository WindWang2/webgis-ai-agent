# Ticket: Subagent 支持

## 目标

实现 `app/agent/subagent/_subagent.py`，为 Agent 系统提供子代理能力，支持：
1. 主代理（ChatAgent）将复杂任务委派给专门化的子代理
2. 子代理继承父代理的上下文，但拥有独立的工具集和系统提示
3. 子代理结果自动回传给父代理的消息历史
4. 与现有 `app/tools/subagent.py` 工具集成

## 设计决策

### 1. Subagent 继承 Agent

**决策**：`Subagent(Agent)` 继承 Agent 基类，通过覆盖 `_build_system_prompt` 和 `_select_tools` 实现专门化。

**理由**：
- Agent 基类已经提供了完整的生命周期管理
- Subagent 只需要定制系统提示和工具选择
- 保持与 Pi 的 Agent subclass 模式一致

### 2. 父代理委派机制

**决策**：父代理通过 `_create_subagent` 工厂方法创建 Subagent，委派任务后通过 `AgentEvent` 接收结果。

**流程**：
1. ChatAgent 决定需要委派给 Subagent
2. 调用 `Subagent.create(parent_agent, task, tools_subset)` 创建子代理
3. 子代理运行自己的 AgentLoop
4. 结果以 `ToolResultMessage` 形式添加回父代理的消息历史

### 3. 工具集隔离

**决策**：Subagent 可以指定工具子集，不继承父代理的全部工具。

**实现**：
```python
subagent = Subagent.create(
    parent=self,
    task="分析这个GeoJSON数据",
    tools=["geojson_analyze", "spatial_stats"],
)
```

### 4. 与现有 subagent 工具集成

**决策**：`app/tools/subagent.py` 作为工具调用入口，内部使用 Subagent 类。

## 实现计划

1. 创建 `app/agent/subagent/_subagent.py`
2. 实现 Subagent 类（继承 Agent）
3. 实现 Subagent.create() 工厂方法
4. 在 ChatAgent 中添加 `_create_subagent` 方法
5. 添加单元测试

## 验收标准

1. Subagent 可以独立运行 AgentLoop
2. Subagent 结果正确回传给父代理
3. Subagent 工具集可以独立配置
4. 与现有 app/tools/subagent.py 工具集成
5. 所有现有测试继续通过
