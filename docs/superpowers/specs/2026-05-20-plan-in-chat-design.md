# 聊天里展示 AI 执行计划 — 设计文档

**日期**: 2026-05-20
**状态**: 已批准，待实现
**主题**: 让 plan-first 智能体循环对用户可见——AI 制定的计划与逐步打勾在聊天里实时呈现

## 背景与动机

Plan-First 智能体循环（`app/services/chat/planner.py`）已经能在工具调用前生成
结构化计划（`Plan(intent, domains, steps[])`），并在每个工具成功后调用
`mark_step_done` 把对应步骤的 `done` 字段翻成 `True`。但目前这套机制完全在
后端内部循环，**用户与前端都看不见**：

- 聊天里只能看到一串 `tool-call` 卡片，看不到 AI 把任务拆成了什么形状
- 调试时无法判断「为什么 AI 选了这个工具」——决策日志只落到
  `logs/tool_decisions.jsonl`，不在 UI 中
- 「AI 在做什么」的透明度低，多步任务里用户容易失去信心

本 spec 把 plan + 步骤完成情况通过 SSE 推到前端，渲染为助理消息顶部的
「执行计划」卡片。**纯展示，无用户干预**——一期不引入取消/重规划。

## 设计原则

**纯加法 + 静默降级。** 三个新 SSE 事件，前端两条新类型，一个新组件。规划
未发生 / SSE 失败 / 解析失败时，不渲染计划卡，主流程不感知。

**事件粒度细，前端累计。** 增量事件（plan_ready → plan_step_done* →
plan_finalized）而非全量快照。后端代码动 ~10 行，未来扩展错误/重试事件不用
重新设计契约。

**步骤三态聚合在前端。** 后端只传 `done: boolean`，前端把它扩展成
`pending | done | skipped` 三态机，把「未执行」语义留在前端处理。这样后端
`PlanStep` dataclass 不用动，三态只是 UI 概念。

## 架构总览

```
┌─────────────────────────────────────────────────────────┐
│ chat_engine.chat_stream()                               │
│                                                         │
│ 1. _maybe_plan() → Plan | None                          │
│    └─ yield sse_event("plan_ready", {...})              │
│                                                         │
│ 2. 工具循环: tool 执行成功 → mark_step_done(...) → step_n│
│    └─ yield sse_event("plan_step_done", {step_n})       │
│                                                         │
│ 3. 任务结束前:                                          │
│    skipped = [s.n for s in plan.steps if not s.done]    │
│    └─ yield sse_event("plan_finalized", {skipped})      │
└─────────────────────────────────────────────────────────┘
                          ↓ SSE
┌─────────────────────────────────────────────────────────┐
│ frontend/app/page.tsx SSE handler                       │
│                                                         │
│ plan_ready      → message.plan = { ..., finalized:false}│
│ plan_step_done  → message.plan.steps[n].status='done'   │
│ plan_finalized  → 把还在 pending 的转 'skipped'         │
└─────────────────────────────────────────────────────────┘
                          ↓
              <PlanCard plan={message.plan} />
              渲染在助理消息正文之前
```

## 数据契约

### SSE 事件（新增）

**`plan_ready`** — 在 `_maybe_plan()` 成功返回后立即发出。
```jsonc
{
  "session_id": "string",
  "task_id": "string",
  "intent": "成都医疗设施热力分析",
  "domains": ["chinese", "core"],
  "steps": [
    {"n": 1, "goal": "获取成都市行政边界", "tool_family": "chinese", "done": false},
    {"n": 2, "goal": "查询医院 POI",       "tool_family": "chinese", "done": false},
    {"n": 3, "goal": "生成热力图",         "tool_family": "core",    "done": false}
  ]
}
```

**`plan_step_done`** — 每次 `mark_step_done` 返回非空时发出。
```jsonc
{ "session_id": "string", "task_id": "string", "step_n": 1 }
```

**`plan_finalized`** — 在 `task_complete` 前发出，把还没打勾的步骤标记为
未执行。
```jsonc
{ "session_id": "string", "task_id": "string", "skipped": [3] }
```

事件不发出（=该轮未规划）时，前端绝不渲染计划卡。任何一个事件解析失败
都被前端 silent-drop，不影响其他事件。

### 前端类型（新增）

`frontend/lib/types.ts`（沿用现有 messages 类型所在文件）：
```typescript
export interface PlanStepState {
  n: number;
  goal: string;
  tool_family: string;
  status: 'pending' | 'done' | 'skipped';
}

export interface PlanState {
  intent: string;
  domains: string[];
  steps: PlanStepState[];
  finalized: boolean;
}
```

Message 接口追加可选 `plan?: PlanState` 字段。

## 后端改动

### `app/services/chat_engine.py`

**`_maybe_plan` 返回值**：当前签名 `async def _maybe_plan(...) -> None`，
改为 `-> Plan | None`，把 `make_plan` 的返回值透出去。

**`chat_stream()` 修改三处**（每处都用 `try/except Exception: pass` 包裹，
SSE 发送失败永远不能拖垮工具循环）：

1. `_maybe_plan` 调用处（约第 489 行）：
```python
plan = await self._maybe_plan(session_id, message, messages)
try:
    if plan is not None:
        yield sse_event("plan_ready", {
            "session_id": session_id,
            "task_id": task.id,
            "intent": plan.intent,
            "domains": plan.domains,
            "steps": [
                {"n": s.n, "goal": s.goal, "tool_family": s.tool_family, "done": False}
                for s in plan.steps
            ],
        })
except Exception:
    pass
```

2. 工具执行成功后（`yield sse_event("step_result", ...)` 同一处）：
   `_log_tool_decision` 的实现要把内部对 `mark_step_done` 的调用上提到外面，
   返回 `step_n` 给调用方使用——这样 SSE 事件能拿到 step_n。
```python
step_n = planner.mark_step_done(session_id, tool_name, self.registry)
self._log_tool_decision(session_id, round_index, message, tool_name,
                        tool_args_dict, outcome, len(tools or []), step_n)
try:
    if step_n is not None:
        yield sse_event("plan_step_done", {
            "session_id": session_id, "task_id": task.id, "step_n": step_n,
        })
except Exception:
    pass
```

3. 任务终止前 —— 包括 `task_complete`、`task_cancelled`、`task_error`
   三个终态事件之前都要发 `plan_finalized`，避免 PlanCard 卡在 in-progress
   状态。提炼为内联辅助 closure：

```python
def _emit_plan_finalized_if_needed():
    try:
        plan = planner.get_plan(session_id)
        if plan is None:
            return
        skipped = [s.n for s in plan.steps if not s.done]
        return sse_event("plan_finalized", {
            "session_id": session_id, "task_id": task.id, "skipped": skipped,
        })
    except Exception:
        return None

# 每个终态分支调用一次:
ev = _emit_plan_finalized_if_needed()
if ev: yield ev
yield sse_event("task_complete", {...})
```

`task_cancelled` 与 `task_error` 分支同样在 yield 之前插入相同代码段。

### `app/services/chat/planner.py`

无改动。`Plan` / `PlanStep` / `mark_step_done` 已经够用。

### `app/utils/sse.py`

文件首部注释里追加一行：「新增事件 `plan_ready` / `plan_step_done` /
`plan_finalized` 由 `chat_engine.chat_stream` 发出，前端类型见
`frontend/lib/types.ts::PlanState`」——非正式契约文档。

## 前端改动

### `frontend/app/page.tsx`

在现有 SSE handler 的 `else if` 链里新增三个分支（贴近 `step_result` 分支
所在位置）：

```typescript
} else if (event.event === 'plan_ready') {
  const data = JSON.parse(event.data);
  setMessages(prev => prev.map(m => m.id === thinkingId ? { ...m,
    plan: {
      intent: data.intent,
      domains: data.domains ?? [],
      steps: (data.steps ?? []).map((s: any) => ({
        n: s.n, goal: s.goal, tool_family: s.tool_family, status: 'pending',
      })),
      finalized: false,
    },
  } : m));
} else if (event.event === 'plan_step_done') {
  const data = JSON.parse(event.data);
  setMessages(prev => prev.map(m => {
    if (m.id !== thinkingId || !m.plan) return m;
    return { ...m, plan: { ...m.plan,
      steps: m.plan.steps.map(s => s.n === data.step_n ? { ...s, status: 'done' } : s),
    }};
  }));
} else if (event.event === 'plan_finalized') {
  const data = JSON.parse(event.data);
  const skipped = new Set<number>(data.skipped ?? []);
  setMessages(prev => prev.map(m => {
    if (m.id !== thinkingId || !m.plan) return m;
    return { ...m, plan: { ...m.plan,
      finalized: true,
      steps: m.plan.steps.map(s => skipped.has(s.n) ? { ...s, status: 'skipped' } : s),
    }};
  }));
}
```

### 新建 `frontend/components/chat/plan-card.tsx`

```typescript
'use client';

import { ClipboardList, Check, Circle, MinusCircle } from 'lucide-react';
import type { PlanState } from '@/lib/types';

interface Props {
  plan: PlanState;
}

const STATUS_ICON = {
  done: <Check className="h-3 w-3 text-emerald-500" />,
  pending: <Circle className="h-3 w-3 text-muted-foreground/50 animate-pulse" />,
  skipped: <MinusCircle className="h-3 w-3 text-muted-foreground/40" />,
};

export function PlanCard({ plan }: Props) {
  const doneCount = plan.steps.filter(s => s.status === 'done').length;
  const total = plan.steps.length;
  if (total === 0) return null;
  return (
    <div className="my-2 p-3 rounded-lg border border-border bg-card/60">
      <div className="flex items-center gap-2 mb-2">
        <ClipboardList className="h-4 w-4 text-primary" />
        <span className="text-sm font-semibold text-foreground truncate">{plan.intent}</span>
        <span className="text-[10px] ml-auto text-muted-foreground tabular-nums">
          {doneCount} / {total}
        </span>
      </div>
      <ul className="space-y-1">
        {plan.steps.map(s => (
          <li
            key={s.n}
            className={`flex items-center gap-2 text-[11px] ${
              s.status === 'skipped' ? 'opacity-50' : ''
            }`}
          >
            <span className="shrink-0">{STATUS_ICON[s.status]}</span>
            <span className={`flex-1 ${s.status === 'done' ? 'text-foreground' : 'text-muted-foreground'}`}>
              {s.goal}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
```

### 接入助理消息渲染

在 chat 渲染 assistant message 内容的地方（具体定位实现期间通过 grep 找到，
通常在 `components/chat/message-list.tsx` 或 `components/chat/chat-message.tsx`），
在消息正文 / token 流之前插入：

```typescript
{message.plan && <PlanCard plan={message.plan} />}
```

## 数据流

```
1. 用户："做一个成都医院分布热力图"
2. chat_engine.chat_stream():
   - _maybe_plan() 成功 → Plan(intent, domains, steps=[3])
   - yield sse_event("plan_ready", {steps: [3 项 pending]})
3. 前端 page.tsx: setMessages 把 plan 挂到 thinkingId 消息
   <PlanCard> 立即渲染，3 步全 pending
4. 工具循环:
   - get_local_admin_boundary → mark_step_done → step_n=1
   - yield plan_step_done {step_n:1}     [前端: step 1 → done]
   - search_poi → step_n=2
   - yield plan_step_done {step_n:2}     [前端: step 2 → done]
   - heatmap_data → step_n=3
   - yield plan_step_done {step_n:3}     [前端: step 3 → done]
5. 任务结束:
   - yield plan_finalized {skipped:[]}   [前端: finalized=true]
   - yield task_complete {...}
```

如果 step 3 没匹配（mark_step_done 返回 None），plan_finalized 的 skipped
里会带 `[3]`，前端把它转灰色 + MinusCircle 图标。

## 错误处理

| 场景 | 行为 |
|------|------|
| 该轮 `should_plan` 返回 false | `_maybe_plan` 不调用 make_plan → 不发 plan_ready → 前端无 plan 字段 → 不渲染 |
| 规划 LLM 调用失败 | `make_plan` 已经 catch 并 return None → 同上 |
| `plan_ready` JSON 解析失败 | 前端 `JSON.parse` 抛错被 try/catch 吃掉，message.plan 仍为 undefined |
| `plan_step_done` step_n 不在 plan.steps 内 | `map` 找不到匹配的 n，no-op |
| `plan_finalized` 在 `plan_ready` 之前到达 | `m.plan` 为 undefined，分支早 return |
| SSE 发送本身抛错 | 每段 yield 包在 `try/except Exception: pass` 内，工具循环继续 |
| 多个会话并行 | `_plans` dict 已经是 session_id 隔离，事件携带 session_id 前端可校验 |

## 测试策略

**后端**（pytest）：
- 扩展 `tests/test_chat_engine_planning.py`：
  - `test_chat_stream_emits_plan_ready_when_plan_created`：mock `_maybe_plan`
    返回 Plan，捕获 chat_stream 的 yield 序列，断言里面有 `event: plan_ready`
    且 data 包含 intent + 3 个 pending step
  - `test_chat_stream_emits_plan_step_done_after_tool`：mock 工具执行 + 已存在
    Plan，断言 step_done 事件携带正确 step_n
  - `test_chat_stream_emits_plan_finalized_with_skipped`：plan 有 3 步只完成
    2 步，task_complete 前收到 finalized 事件且 `skipped == [3]`
  - `test_chat_stream_no_plan_events_when_plan_skipped`：`_maybe_plan` 返回
    None 时，SSE 流里**不**出现任何 plan_* 事件

**前端**（vitest + RTL）：
- `frontend/components/chat/plan-card.test.tsx`（新建）：
  - 渲染 3 步 (2 done / 1 pending) → 计数显示 `2 / 3`、图标分布正确
  - 1 个 skipped 步骤显示 opacity-50 类
  - 空 steps 数组 → 返回 null，不崩
- `frontend/app/page.tsx` SSE handler 的状态过渡走全量回归。

**契约同步**：后端事件 schema 与前端 `PlanState` 任一端改动需要同步另一端
测试。`app/utils/sse.py` 注释里指明两端的关联文件。

## 验收标准

- 触发任意 plan-first 路径请求后：
  - 助理消息顶部立即出现 `<PlanCard>`，意图为 AI 制定的 intent，步骤全 pending
  - 每个工具成功执行后，对应步骤从 pending → done
  - 任务结束时，未完成的步骤转 skipped（灰色 + MinusCircle）
- 短消息追问（`should_plan` 返回 false）：助理消息**不**出现 PlanCard
- 规划 LLM 失败 / SSE 解析失败：助理消息**不**出现 PlanCard，工具循环正常
- 多轮对话中：每个新的"开始规划"轮次产生独立的 PlanCard（挂在新的助理
  消息上），旧消息的 PlanCard 保持其完成时的状态；如果新一轮 `should_plan`
  返回 false（追问短消息），新助理消息不显示 PlanCard，但旧消息上的 PlanCard
  保留可见
- 既有测试在改造后全部通过

## 范围外（明确不做）

- **不**做用户干预（取消、编辑、重规划）—— 二期再考虑
- **不**在 PlanCard 里展示决策日志（domain 选择原因等）—— 决策日志仍只写
  `logs/tool_decisions.jsonl`，调试用
- **不**为 PlanCard 加折叠/展开 —— 计划本来就只有 ≤5 步，一直展开即可
- **不**改 `Plan` / `PlanStep` 后端数据结构 —— 三态机在前端聚合
- **不**实现「插入额外步骤」UI —— 工具实际调用数 ≠ 步骤数时，多出的工具
  仍由现有 tool-call 卡片展示，PlanCard 只反映原始计划

## 与既有工作的关系

直接构建在 plan-first 智能体循环之上（`app/services/chat/planner.py`、
`chat_engine._maybe_plan` / `_log_tool_decision`）。本 spec 不改这些模块的
内部数据结构，只在它们的钩子点上追加 SSE 发送。

LLM 端无需感知 plan_* 事件——它们是后端→前端的契约，对话提示不变。
