# Ticket: 新文件结构与模块划分

## Question

基于 Pi 和 OpenCode 的实际模块划分，设计新 Agent 系统的目录结构和模块依赖。

### Pi 的模块划分（已读源码）

```
packages/agent/src/
├── agent.ts              # Agent 类（状态管理、生命周期、队列）
├── agent-loop.ts         # 纯函数 Loop（事件流产生、tool 执行）
├── types.ts              # 类型定义（AgentEvent, AgentTool, AgentLoopConfig...）
├── stream-fn.ts          # LLM 流式调用抽象
├── harness/              # 高级工具
│   ├── compaction/       # 上下文压缩（token-aware LLM summarization）
│   ├── session/          # 会话持久化（JSONL repo, memory repo）
│   ├── skills/           # 技能系统
│   └── types.ts          # Harness 类型
├── proxy.ts              # 代理工具
└── index.ts              # 统一导出
```

**关键特点**：
- `agent.ts` 和 `agent-loop.ts` 严格分离（有状态 vs 纯函数）
- `types.ts` 是所有类型的单一真相源
- `harness/` 是可选的增强层，核心是 `agent.ts` + `agent-loop.ts`

### OpenCode 的模块划分（已读源码）

```
packages/core/src/
├── agent.ts              # Agent Service（CRUD，Effect-TS context pattern）
├── state.ts              # 状态管理（State.create, transform, draft）
├── tool/
│   ├── tool.ts           # Tool Definition（Schema-first, make/settle）
│   ├── registry.ts       # Tool 注册
│   ├── builtins.ts       # 内置工具
│   └── *.ts              # 具体工具（bash, read, write, grep...）
├── event/                # 事件系统
├── session/              # 会话管理
├── skill/                # 技能系统
├── session/              # 会话持久化
└── model.ts              # 模型管理
```

**关键特点**：
- 函数式架构（Effect-TS），所有副作用通过 Effect 管理
- Tool 是 Schema-first 的 `Definition<Input, Output>`
- Agent 是 Service pattern（通过 Effect Context 获取）

### 当前 webgis-ai-agent 的结构

```
app/
├── services/
│   ├── chat_engine.py      # 大杂烩（状态+LLM+dispatch+SSE+title+skill）
│   ├── chat/
│   │   ├── dispatcher.py   # 工具调度
│   │   ├── llm_client.py   # LLM 调用
│   │   ├── prompt.py       # 系统提示
│   │   ├── planner.py      # 规划阶段
│   │   ├── context_builder.py  # 上下文组装
│   │   ├── sse_helpers.py  # SSE 辅助
│   │   └── decision_log.py # 决策日志
│   ├── subagent.py         # Subagent 调度
│   ├── plan_mode.py        # Plan 模式
│   ├── tool_catalog.py     # 工具目录
│   ├── session_data.py     # 会话数据（内存）
│   ├── session_data_redis.py # 会话数据（Redis）
│   └── task_tracker.py     # 任务跟踪
├── tools/
│   ├── registry.py         # 工具注册
│   ├── subagent.py         # spawn_subagent 工具
│   ├── spatial.py          # 空间工具
│   ├── advanced_spatial.py # 高级空间工具
│   └── ...                 # 40+ 工具模块
└── lib/
    └── geo_analysis/       # GIS 算法库
```

### 需要决策的具体问题

1. **顶层包设计**：
   - Pi 的做法：`packages/agent/` 独立包
   - OpenCode 的做法：`packages/core/src/agent.ts` 在核心包内
   - 我们的考虑：现有 `app/services/chat_engine.py` 是大杂烩，需要拆分
   - **建议**：引入 `app/agent/` 顶层包，包含：
     ```
     app/agent/
     ├── __init__.py
     ├── _agent.py           # Agent 基类（状态管理、生命周期）
     ├── _loop.py            # AgentLoop 纯函数
     ├── _types.py           # 类型定义
     ├── _stream.py          # StreamFn 适配器
     ├── _event.py           # 事件系统
     ├── _context.py         # 上下文管理
     └── harness/            # 可选增强层
         ├── compaction.py   # 上下文压缩
         ├── skills.py       # 技能系统
         └── session.py      # 会话持久化
     ```

2. **模块依赖方向**：
   - Pi 的做法：`agent` → `types`（单向依赖）
   - OpenCode 的做法：`agent` → `state` → `tool`（分层依赖）
   - 我们的考虑：`app/agent/` 依赖 `app/tools/`（通过 ToolRegistry 接口），`app/tools/` 依赖 `app/lib/`
   - **建议**：`app/agent/` → `app/tools/` → `app/lib/`（单向依赖链）

3. **Harness 层是否需要**？
   - Pi 的做法：有 harness（compaction/session/skills），是可选的增强
   - 我们的考虑：compaction（上下文压缩）和 skills（技能系统）是必需的
   - **建议**：保留 harness 层，但 compaction 和 skills 是可选的（通过 feature flag 控制）

4. **旧代码的过渡**：
   - `app/services/chat_engine.py` 不删除，改为 `ChatEngineV1`（兼容层）
   - 新 `Agent` 类在 `app/agent/` 中，通过 feature flag 切换
   - 旧代码可以逐步迁移到新接口

5. **测试结构**：
   - Pi 的做法：`test/` 目录与 `src/` 同级
   - OpenCode 的做法：`tests/` 目录在仓库根
   - 我们的考虑：已有 `tests/unit/` 和 `tests/integration/`
   - **建议**：
     ```
     tests/
     ├── unit/
     │   ├── agent/           # 新 Agent 单元测试
     │   ├── tools/           # 工具测试（已有）
     │   └── ...
     ├── integration/
     │   ├── test_agent_loop.py   # AgentLoop 集成测试
     │   └── ...
     └── fixtures/
         └── agent/           # Agent 测试 fixture
     ```

请给出建议的目录结构图（ASCII art）和模块依赖图。

## Reference

- Pi `packages/agent/src/`: `/tmp/pi/packages/agent/src/`（完整已读）
- Pi `index.ts`: `/tmp/pi/packages/agent/src/index.ts`（导出结构）
- OpenCode `packages/core/src/`: `/tmp/opencode/packages/core/src/`（部分已读）
- 当前 `app/services/`: `app/services/`
- 当前 `app/tools/`: `app/tools/`
- 当前 `app/lib/`: `app/lib/`

## Constraints

- 必须保持与现有 FastAPI 路由的兼容性
- 渐进替换要求：新旧代码可以共存
- 测试结构必须支持现有的 pytest 框架
- `app/agent/` 不能破坏现有的 import 结构
