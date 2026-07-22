# Ticket: Harness 模块（compaction, skills, session）

## 目标

实现 `app/agent/harness/` 子包，将 Agent 系统的支撑功能从 `app/services/chat/` 迁移过来，保持新 Agent 系统的自包含性。

## 设计决策

### 1. Harness 模块范围

**决策**：只迁移新 Agent 系统实际需要的内容，不搬动仍在被旧代码大量使用的模块。

**包含**：
- `compaction.py` — 上下文压缩（token-aware LLM summarization）
- `skills.py` — 技能系统（从 app/tools/skills.py 导入并重新导出）
- `session.py` — 会话持久化（与现有 DB/Redis 集成）
- `system_prompt.py` — 系统提示构建（从 app/services/chat/prompt.py 导入并重新导出）

**不包含**（留在 app/services/chat/）：
- `context_builder.py` — 被 Agent 和旧代码共享
- `sse_helpers.py` — 被 FastAPI route 和 Agent 共享
- `decision_log.py` — 被 Agent 和旧代码共享

### 2. 依赖方向

**决策**：`app/agent/harness/` → `app/services/chat/` → `app/tools/`

Harness 模块可以导入 `app/services/chat/` 和 `app/tools/`，但不能被 `app/lib/` 导入。

### 3. 实现策略

**决策**：Harness 模块作为薄封装层，重新导出已有功能，避免重复实现。

```python
# app/agent/harness/skills.py
from app.tools.skills import list_md_skills, get_md_skill

__all__ = ["list_md_skills", "get_md_skill"]
```

## 实现计划

1. 创建 `app/agent/harness/__init__.py`
2. 创建 `app/agent/harness/skills.py` — 重新导出 skills
3. 创建 `app/agent/harness/system_prompt.py` — 重新导出 SYSTEM_PROMPT
4. 创建 `app/agent/harness/session.py` — 会话持久化辅助
5. 创建 `app/agent/harness/compaction.py` — 上下文压缩（新实现）
6. 添加单元测试

## 验收标准

1. Harness 模块可以正确导入所有重新导出的功能
2. ChatAgent 可以通过 harness 模块访问 skills 和 system prompt
3. 上下文压缩功能可以正确压缩长对话历史
4. 所有现有测试继续通过
