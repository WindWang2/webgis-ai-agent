# Ticket: Full Migration + Cleanup

## 目标

完成从 ChatEngine 到新 Agent 系统的全面迁移，包括文档更新、API 导出、集成测试和遗留代码标记。

## 设计决策

### 1. 迁移策略

**决策**：保留旧 ChatEngine 作为后备，通过 feature flag 控制。新代码优先使用 Agent 系统。

### 2. 公共 API 导出

**决策**：在 `app/agent/__init__.py` 中导出所有公共类。

### 3. 集成测试

**决策**：添加端到端测试验证新 Agent 系统与现有 FastAPI 路由的集成。

### 4. 文档更新

**决策**：更新 WAYFINDER_MAP.md 记录迁移进度。

## 实现计划

1. 更新 `app/agent/__init__.py` 导出所有公共 API
2. 添加集成测试 `tests/integration/test_agent_integration.py`
3. 更新 WAYFINDER_MAP.md
4. 最终验证所有测试通过
