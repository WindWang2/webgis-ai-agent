# 任务规划与 ReAct 执行监控设计

## 1. 概述

将现有 `chat_engine.py` 的单轮 FC 循环扩展为带状态跟踪和实时进度反馈的 ReAct 执行架构。核心思路：**不做预规划，而是增强现有 FC 循环**——每步执行后由 LLM 决定下一步，同时跟踪执行状态并通过 SSE 推送进度。

## 2. 背景

当前系统架构：
- `chat_engine.py`: FC 循环（最多 10 轮），无状态管理
- `registry.py`: 静态工具注册，同步/异步 dispatch
- `chat.py` (routes): `/chat/stream` SSE 流式 + `/chat/completions` 非流式
- 前端: `analysis-context.tsx` 管理分析结果，通过 SSE 接收 `tool_call` / `tool_result` / `content` 事件

问题：
- 复杂指令（如"成都市大学分布热力图"）的多步执行无进度可视化
- 前端无法区分"正在执行第几步"和"总共几步"
- 无任务取消机制
- 中间结果（如第一步查到的 POI）无法实时渲染到地图

## 3. 设计决策

### 为什么选 ReAct 模式而非预规划（Planner-Executor）

| 维度 | 预规划 | ReAct（选定） |
|------|--------|---------------|
| 参数依赖 | 需要复杂的 `$step-1.result` 引用机制 | 自然解决——LLM 看到上一步结果后决定下一步参数 |
| 简单/复杂判断 | 需要额外的分类逻辑 | 不需要——所有请求走同一循环，单步请求只执行一轮 |
| 实现复杂度 | 高（DAG 调度、参数替换、计划验证） | 低（增强现有 FC 循环即可） |
| 错误恢复 | 计划失效需要重新规划 | LLM 每步都能根据错误调整策略 |
| 风险 | LLM 生成无效计划的验证成本高 | 逐步执行，每步都有工具 schema 约束 |

## 4. 架构设计

### 4.1 整体架构

```
┌──────────┐     ┌──────────────────────┐     ┌────────────┐
│ 用户输入  │────▶│  ChatEngine          │────▶│  工具执行   │
│          │     │  (增强 FC 循环)        │     │ (registry) │
└──────────┘     │                      │     └────────────┘
                 │  TaskTracker (新增)    │           │
                 │  - 跟踪步骤状态       │◀──────────┘
                 │  - 发送 SSE 进度事件  │
                 └──────────────────────┘
                           │
                           ▼
                 ┌──────────────────────┐
                 │  前端                 │
                 │  - 任务进度条         │
                 │  - 中间 GeoJSON 渲染  │
                 └──────────────────────┘
```

### 4.2 核心组件

#### 4.2.1 TaskTracker（新增）

**职责**：跟踪当前任务的执行步骤和状态，不负责调度。

```python
class StepStatus(str, Enum):
    running = "running"
    completed = "completed"
    failed = "failed"

class TaskStep:
    id: str                  # 自增，如 "step-1"
    tool: str                # 工具名称
    params: dict             # 工具参数（执行时确定，非预规划）
    status: StepStatus
    result: Any              # 工具执行结果
    error: str | None
    started_at: datetime
    finished_at: datetime | None

class TaskStatus(str, Enum):
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"

class TaskInfo:
    id: str                  # UUID
    session_id: str          # 所属会话
    original_request: str    # 用户原始消息
    steps: list[TaskStep]    # 已执行和正在执行的步骤
    status: TaskStatus
    created_at: datetime
    finished_at: datetime | None
```

**关键设计**：`TaskInfo.steps` 是**追加式**的——每当 FC 循环发起一次工具调用，就 append 一个新 `TaskStep`。不预定义步骤数量。

#### 4.2.2 增强的 ChatEngine

在现有 `chat_stream` 的 FC 循环中嵌入 TaskTracker：

```python
async def chat_stream(self, message, session_id):
    # 创建 TaskInfo
    task = self._tracker.create(session_id, message)
    yield sse_event("task_start", {"task_id": task.id})

    messages = self._get_or_create_session(session_id)
    messages.append({"role": "user", "content": message})

    for round_num in range(self.max_rounds):
        response = await self._call_llm(messages, tools)
        assistant_msg = response["choices"][0]["message"]

        if assistant_msg.get("tool_calls"):
            messages.append(assistant_msg)

            for tc in assistant_msg["tool_calls"]:
                tool_name = tc["function"]["name"]
                tool_args = tc["function"]["arguments"]

                # 1. 记录步骤开始
                step = self._tracker.start_step(task.id, tool_name, tool_args)
                yield sse_event("step_start", {
                    "task_id": task.id,
                    "step_id": step.id,
                    "step_index": len(task.steps),
                    "tool": tool_name,
                })

                # 2. 执行工具
                try:
                    result = await self.registry.dispatch(tool_name, tool_args)
                    self._tracker.complete_step(task.id, step.id, result)

                    # 3. 推送步骤结果（含 GeoJSON 检测）
                    has_geojson = _detect_geojson(result)
                    yield sse_event("step_result", {
                        "task_id": task.id,
                        "step_id": step.id,
                        "tool": tool_name,
                        "result": result,
                        "has_geojson": has_geojson,
                    })
                except Exception as e:
                    self._tracker.fail_step(task.id, step.id, str(e))
                    yield sse_event("step_error", {
                        "task_id": task.id,
                        "step_id": step.id,
                        "error": str(e),
                    })

                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result_str})
            continue
        else:
            # 最终回复
            self._tracker.complete_task(task.id)
            yield sse_event("content", {"content": content, "session_id": session_id})
            yield sse_event("task_complete", {
                "task_id": task.id,
                "step_count": len(task.steps),
                "summary": content[:100],
            })
            return

    self._tracker.fail_task(task.id, "达到最大工具调用轮数")
    yield sse_event("task_error", {"task_id": task.id, "error": "达到最大轮数"})
```

**与现有代码的关系**：这不是新写一个引擎，而是在 `chat_engine.py:chat_stream` 的 FC 循环中插入 tracker 调用和新 SSE 事件。现有的 `tool_call` / `tool_result` / `content` 事件保持不变，新事件是**叠加**的。

#### 4.2.3 GeoJSON 检测

```python
def _detect_geojson(result: Any) -> bool:
    """检测工具返回结果是否包含 GeoJSON"""
    if isinstance(result, dict):
        if result.get("type") == "FeatureCollection":
            return True
        # 嵌套检测：如 {"data": {"type": "FeatureCollection", ...}}
        for v in result.values():
            if isinstance(v, dict) and v.get("type") == "FeatureCollection":
                return True
    return False
```

### 4.3 SSE 事件设计

**新增事件**（叠加在现有 `tool_call` / `tool_result` / `content` 之上）：

| 事件名 | payload | 触发时机 |
|--------|---------|----------|
| `task_start` | `{task_id}` | 用户消息进入 FC 循环前 |
| `step_start` | `{task_id, step_id, step_index, tool}` | 每次工具调用前 |
| `step_result` | `{task_id, step_id, tool, result, has_geojson}` | 工具调用成功后 |
| `step_error` | `{task_id, step_id, error}` | 工具调用失败后 |
| `task_complete` | `{task_id, step_count, summary}` | FC 循环结束（LLM 给出最终回复） |
| `task_error` | `{task_id, error}` | 任务级失败（超出最大轮数） |

**事件顺序示例**（"成都市大学分布热力图"）：

```
task_start       → {task_id: "t-1"}
step_start       → {task_id: "t-1", step_id: "step-1", step_index: 1, tool: "query_osm_poi"}
tool_call        → {name: "query_osm_poi", arguments: {...}}    # 现有事件，保留
step_result      → {task_id: "t-1", step_id: "step-1", tool: "query_osm_poi", result: {...}, has_geojson: true}
tool_result      → {name: "query_osm_poi", result: {...}}       # 现有事件，保留
step_start       → {task_id: "t-1", step_id: "step-2", step_index: 2, tool: "spatial_stats"}
tool_call        → {name: "spatial_stats", arguments: {...}}
step_result      → {task_id: "t-1", step_id: "step-2", ...}
tool_result      → {name: "spatial_stats", result: {...}}
content          → {content: "已完成分析...", session_id: "..."}  # 现有事件，保留
task_complete    → {task_id: "t-1", step_count: 2, summary: "已完成分析..."}
```

前端可以选择性处理新事件——不处理也不影响现有功能（向后兼容）。

### 4.4 任务查询 API

**新增端点**（只读 + 取消，不暴露创建接口）：

```python
# app/api/routes/task.py

@router.get("/tasks/{task_id}")
async def get_task(task_id: str) -> TaskStatusResponse:
    """查询任务状态和步骤详情"""

@router.get("/tasks")
async def list_tasks(session_id: Optional[str] = None) -> TaskListResponse:
    """列出任务，可按 session 过滤"""

@router.delete("/tasks/{task_id}")
async def cancel_task(task_id: str) -> TaskCancelResponse:
    """取消正在执行的任务"""
```

任务创建由 `ChatEngine` 内部完成，不需要公共创建接口。

**取消机制**：`TaskTracker` 设置 `cancelled` 标志，`ChatEngine` 在每轮 FC 循环开头检查该标志，如为 `True` 则退出循环并发送 `task_cancelled` 事件。

### 4.5 状态存储

TaskTracker 内嵌于 ChatEngine，共享同一生命周期：

```python
class TaskTracker:
    """任务状态跟踪器 - 内存存储"""
    _tasks: dict[str, TaskInfo] = {}        # task_id -> TaskInfo
    _session_tasks: dict[str, list[str]] = {} # session_id -> [task_id, ...]

    def create(self, session_id: str, request: str) -> TaskInfo: ...
    def start_step(self, task_id: str, tool: str, params: dict) -> TaskStep: ...
    def complete_step(self, task_id: str, step_id: str, result: Any) -> None: ...
    def fail_step(self, task_id: str, step_id: str, error: str) -> None: ...
    def complete_task(self, task_id: str) -> None: ...
    def fail_task(self, task_id: str, error: str) -> None: ...
    def cancel(self, task_id: str) -> bool: ...
    def get(self, task_id: str) -> TaskInfo | None: ...
    def list_by_session(self, session_id: str) -> list[TaskInfo]: ...
```

不单独建 `TaskStore` 类——TaskTracker 本身就是存储。生产环境如需持久化，替换 `_tasks` dict 为 Redis 即可。

## 5. 前端设计

### 5.1 任务进度组件

**位置**：聊天消息流内（inline），不是固定面板。当 `task_start` 事件到达时，在聊天区域插入一个可折叠的进度卡片。

**进度卡片内容**：
- 标题：用户原始请求（截断至 30 字）
- 步骤列表（实时更新）：
  - 状态指示：spinner（进行中）/ 勾（完成）/ 叉（失败）
  - 工具名称 + 简短结果摘要（如"查询到 45 个 POI"）
- 底部：进度条（已完成步骤数 / 当前总步骤数，动态增长）
- 取消按钮

**交互**：
- 默认展开，任务完成后自动折叠为单行摘要
- 点击可重新展开查看步骤详情

### 5.2 中间结果渲染

收到 `step_result` 且 `has_geojson: true` 时：
1. 调用 `addResult()` 将 GeoJSON 添加到 `analysis-context`
2. 使用半透明样式渲染为"草稿层"（opacity: 0.5）
3. 收到 `task_complete` 后，将所有草稿层转为正式样式（opacity: 1.0）

复用现有的 `AnalysisProvider` 和 `generateDefaultLayerStyle`，仅需新增 `draft` 标记。

### 5.3 前端状态管理

新增 `TaskContext`（或扩展现有 `AnalysisContext`）：

```typescript
interface TaskStep {
  id: string;
  tool: string;
  status: 'running' | 'completed' | 'failed';
  result?: unknown;
  error?: string;
}

interface TaskState {
  id: string;
  steps: TaskStep[];
  status: 'running' | 'completed' | 'failed' | 'cancelled';
}

// 在 SSE 事件处理中更新
```

## 6. 约束条件

1. **向后兼容**：现有前端不处理新 SSE 事件也能正常工作
2. **最大轮数**：保持现有 10 轮限制（可配置化为 `settings.MAX_FC_ROUNDS`）
3. **单步超时**：60 秒（现有 `httpx.AsyncClient(timeout=120.0)` 是 LLM 调用超时，工具超时需单独设置）
4. **存储**：内存存储，与 ChatEngine 同生命周期

## 7. 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `app/services/task_tracker.py` | 新增 | TaskTracker、TaskInfo、TaskStep 定义 |
| `app/services/chat_engine.py` | 修改 | 在 FC 循环中嵌入 TaskTracker，新增 SSE 事件 |
| `app/api/routes/task.py` | 新增 | 任务查询和取消 API |
| `app/api/routes/__init__.py` | 修改 | 注册 task router |
| `frontend/lib/contexts/task-context.tsx` | 新增 | 任务状态 Context |
| `frontend/components/chat/task-progress.tsx` | 新增 | 任务进度卡片组件 |
| `frontend/components/chat/chat-panel.tsx` | 修改 | 集成进度卡片到消息流 |
| `frontend/lib/hooks/use-sse.ts` (或等效) | 修改 | 处理新 SSE 事件类型 |

## 8. 实施顺序

| 阶段 | 子任务 | 说明 |
|------|--------|------|
| 1 | 后端核心 | `task_tracker.py` + 修改 `chat_engine.py` 嵌入跟踪 |
| 2 | SSE 事件 | 新事件发送 + 验证向后兼容 |
| 3 | 任务 API | `task.py` 路由 + 注册 |
| 4 | 前端状态 | `task-context.tsx` + SSE 事件处理 |
| 5 | 前端 UI | 进度卡片组件 + 集成到聊天面板 |
| 6 | 中间渲染 | 草稿层渲染 + 任务完成后转正式 |

## 9. 不在本期范围

- 任务持久化（Redis）——当前内存存储足够
- 重试机制——LLM 本身会根据错误调整策略
- 并行步骤执行——当前无真实并行场景
- 任务历史查询——等有持久化后再做
