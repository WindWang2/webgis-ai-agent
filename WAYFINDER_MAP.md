# Wayfinder Map: Agent System Rewrite

## Destination

将 webgis-ai-agent 的 agent 系统从隐式散落在 `ChatEngine` 中的逻辑，重构成具有明确第一类抽象（Agent 类、事件循环、内存管理、工具协议）的现代 code agent 架构，参考 Pi (earendil-works/pi) / OpenDevin / Claude Code 的设计模式，同时保留已验证的 GIS 专属模式（ref-based 数据流、环境感知、工具分层）。

完成标志：新 `Agent` 类体系可独立运行、可通过 feature flag 切换、全部 1154+ 测试通过。

## Notes

- **参考实现**: 
  - **Pi** (earendil-works/pi, TypeScript, 75k stars) — `Agent` 类 + `AgentLoop` 纯函数 + 事件流 + ToolExecution + Compaction
    - `Agent`: 有状态，持有 mutable state (tools/messages/model)，生命周期钩子 (beforeToolCall/afterToolCall/prepareNextTurn/shouldStopAfterTurn)，steering/follow-up 队列
    - `AgentLoop`: 纯函数，接收 context+config+emit+streamFn，产生事件流 (agent_start/turn_start/tool_execution_*/agent_end)
    - Tool 协议: `AgentTool` 接口，`execute(toolCallId, params, signal, onUpdate)` → `AgentToolResult{content, details, terminate}`
    - 上下文压缩: token-aware Compaction (LLM summarization + file ops tracking + retained tail)
    - Session: JSONL-based storage (memory-repo, jsonl-repo)
  - **OpenCode** (anomalyco/opencode, TypeScript, 188k stars) — Effect-TS 函数式架构
    - Tool 定义: `Definition<Input, Output>` Schema-first，`execute(input, context)` → Effect
    - Tool 注册: `make()` + `withPermission()` + `settle()` 函数式注册
    - Tool 输出: `Content` union (text/file)，`toModelOutput` 映射到 LLM 显示
    - Agent: Service pattern (Effect-TS context)，CRUD for agent configurations
    - 权限系统: `withPermission` 装饰器 (permission-based tool access control)
  - **OpenHands** (All-Hands-AI/OpenHands, Python) — 平台级 agent 框架，MCP 集成，agent profiles 配置
- **用户决策摘要**:
  - Q1 Agent 抽象: **B** — 多态 Agent 类型 (基类 `Agent` + 子类 `ChatAgent` / `PlanAgent` / `Subagent`)
  - Q2 执行模型: **B** — Agent 步骤模型 (每个 LLM 响应是一个 `Step`: think → act → observe)
  - Q3 上下文管理: **B** — 保持现状 (messages 列表 + session_data_manager refs)，但规范化接口
  - Q4 Subagent 隔离: **C** — 混合 (默认进程内独立 Agent 实例，可选进程外 worker)
  - Q5 工具协议: **A** — 保持 Python 装饰器 (`@tool(registry, ...)`)，只改 Agent 侧调用方式
  - Q6 回退兼容: **A** — 渐进替换 (新 Agent 与旧 ChatEngine 并存，feature flag 切换)
- **必须保留的模式**: ref-based 数据流、三层工具目录 (tier 1/2/3)、自愈错误循环、SSE 保活流、具身感知注入、plan-first 优雅降级
- **关键约束**: GIS 生产数据在跑，不能中断服务；Python 后端，FastAPI + asyncio

## Decisions so far

<!-- 每关闭一个 ticket，在这里追加一行 -->
- [001] Agent 基类接口契约：状态化 Agent 类 + 4 生命周期钩子 + PendingMessageQueue + StreamFn 抽象 ✅
- [002] Step/Turn 模型定义：无显式 Step 类型，Turn 隐式，10 个 AgentEvent dataclass ✅
- [003] AgentLoop 边界设计：纯函数 Loop + parallel/sequential 工具执行 + MiniMax XML fallback + max_rounds=60 ✅
- [004] 事件系统设计：EventBus 中间层 + 10 核心事件类型 + GIS 扩展 via details + SSEMapper ✅
- [005] 文件结构设计：app/agent/ 顶层包 + 单向依赖 + harness 层 + feature flag 渐进迁移 ✅
- [006] ChatAgent 桥接：ChatAgent(Agent) 子类 + ToolCatalog/ToolRegistry 双层工具选择 + SSE 事件映射 ✅
- [007] AgentRuntime + Feature Flag：AgentRuntime 管理会话生命周期 + USE_NEW_AGENT 环境变量切换 ✅
- [008] Harness 模块：skills, system_prompt, session, compaction 薄封装层 ✅
- [009] Subagent 支持：Subagent(Agent) 子类 + 工厂方法 + SubagentResult 回传 ✅
- [010] 完整迁移：公共 API 导出 + 集成测试 + 1186 测试全通过 ✅

## Open Tickets (Frontier)

所有 10 个 tickets 已完成。后续工作：
- [ ] 端到端测试：用真实 LLM 验证新 Agent 系统在测试环境中的完整对话流程
- [ ] 性能基准：对比新旧系统的 token 消耗和响应时间
- [ ] 文档：更新 ARCHITECTURE.md 描述新 Agent 系统架构
- Step 模型的持久化格式 (如何序列化到 DB/Redis)
- 进程外 Subagent Worker 的通信协议 (未来扩展)
- 上下文压缩策略的具体阈值和算法

## Out of scope

- 前端 UI 重写 (仅 backend agent 系统)
- 新的 LLM provider 接入 (保持现有 OpenAI-compatible 接口)
- GIS 算法库重写 (仅 agent 调度层)
- 容器化/沙箱执行 (Pi 的 Gondolin/Docker 模式，当前不需要)
