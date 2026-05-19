# Plan-First 智能体循环 — 设计文档

**日期**: 2026-05-19
**状态**: 已批准，待实现
**主题**: 提升 LLM 意图理解准确度与工具编排合理性

## 背景与动机

WebGIS AI Agent 的对话引擎当前的「意图 → 工具」路径是：关键词匹配工具子集
(`ToolCatalog`) → 单次 LLM 调用 → 工具调用 → FC 循环。没有显式的规划或意图分类
步骤，工具路由是脆弱的关键词匹配。

确认的两个核心痛点：

1. **选错工具 / 误读意图** — 既有「对的工具没被推给 LLM」（检索问题），
   也有「工具都在但 LLM 选了相邻同义的错工具」（区分度问题）。当前无法判断
   哪个主导。
2. **多步任务规划差** — 复杂制图/分析任务步骤顺序乱、中途放弃、循环里反复
   刷同一类工具。

约束：

- 生产环境需同时支持**前沿模型（Claude/GPT）与国内模型（MiniMax/DeepSeek）**，
  方案不能只依赖前沿模型的能力。
- 规划阶段按**启发式门控**触发（短追问跳过，复杂请求才规划）。
- 循环对计划采用 **Checkpoint 式**约束（注入 checklist + 末尾校验，不硬拦截）。

## 设计原则

**规划是增强，永不是硬依赖。** 规划调用、解析、计划注入的任何环节失败，都必须
降级回当前的无计划 FC 循环行为。现有 `chat_engine` 测试在无计划路径下必须全绿。

借鉴 Claude Code 真正起作用的设计——**把计划显式化、持久化**（TodoWrite 思路）、
**精确的工具描述**——而非表面设计（不过滤工具 / 独立路由器模型）。本项目有 80+
工具，无法照搬「全推不过滤」。

## 架构总览

新增文件：

- `app/services/chat/planner.py` — 规划阶段：启发式门控 + 结构化规划 LLM 调用 +
  `Plan` dataclass + 按 session 的计划存储。
- `app/services/chat/decision_log.py` — 工具决策的结构化 JSONL 可观测性日志。

改动文件：

- `app/services/chat_engine.py` — FC 循环接入规划阶段与 checkpoint。
- `app/services/chat/context_builder.py` — 注入 `[执行计划]` 上下文块。
- `app/services/tool_catalog.py` — `select_schemas` 支持计划声明的 domain。
- `app/services/chat/dispatcher.py` — 接入决策日志。
- `app/services/chat/prompt.py` 及相关工具文件 — 易混工具 schema 加固。

## 模块 1 — 可观测性 `decision_log.py`

**目的**：用真实数据回答「检索 vs 区分度，哪个主导选错」，模型无关，**第一个上线**。

每次工具 dispatch 落一条结构化 JSONL 记录，字段：

- `session_id`、`round`（FC 循环轮次）
- `user_message`（截断至 ~200 字）
- `active_domains` 及来源标记（`keyword` / `plan` / `sticky`）
- `subset_size`（推送给 LLM 的工具 schema 数）/ `total_tools`
- `tool_chosen`、`tool_args`（截断）
- `result_quality`：`ok` / `empty` / `error`（复用 `dispatcher.is_suspicious_result`
  与 `is_error`）
- `plan_step_matched`：本次调用是否匹配某个计划步骤（无计划时为 `null`）

**接口**：

```python
def log_tool_decision(record: ToolDecisionRecord) -> None
```

**落地方式**：JSONL 追加写入日志目录（如 `logs/tool_decisions.jsonl`），通过
标准 `logging` 配置或独立文件句柄。写入失败只记 warning，绝不影响主流程。

**调用点**：`dispatcher.dispatch_tool` 在产出 outcome 后调用一次。`active_domains`
与 `plan_step_matched` 由 `chat_engine` 透传（dispatcher 当前不感知计划，
需新增可选参数）。

## 模块 2 — 规划阶段 `planner.py`

### 启发式门控

```python
def should_plan(message: str, messages: list[dict], has_active_plan: bool) -> bool
```

返回 `False`（跳过规划）的条件：

- 消息短（< ~20 字）**且**命中追问词模式（换/再/放大/缩小/颜色/隐藏/显示…）。
  复用 `context_builder.build_last_analysis_context` 的追问判断精神。
- 已存在进行中的计划（`has_active_plan`）且本轮判定为追问。

其余情况返回 `True`。纯函数，表驱动单测。

### 规划调用

```python
async def make_plan(cfg: LLMConfig, session_id: str, message: str,
                    env_summary: str) -> Plan | None
```

- 一次**非流式** LLM 调用，使用专用规划 prompt。
- 模型：默认复用 `settings.LLM_MODEL`；新增可选 `settings.LLM_PLANNER_MODEL`，
  为空时回退 `LLM_MODEL`。便于后续单独配更便宜的规划模型。
- 输出受约束 JSON：

  ```json
  {
    "intent": "一句话意图摘要",
    "domains": ["statistics", "chinese"],
    "steps": [
      {"n": 1, "goal": "锁定成都市行政边界", "tool_family": "chinese"},
      {"n": 2, "goal": "边界内搜索医院 POI", "tool_family": "chinese"},
      {"n": 3, "goal": "H3 网格聚合分析分布", "tool_family": "statistics"}
    ]
  }
  ```

- `domains` / `tool_family` 取值限定在 `DOMAIN_KEYWORDS` 的键集合 + `core`。
- **防御式解析**：JSON 解析失败、字段缺失、domain 越界 → 记日志并返回 `None`
  （降级为无计划循环）。

### `Plan` dataclass 与存储

```python
@dataclass
class PlanStep:
    n: int
    goal: str
    tool_family: str
    done: bool = False

@dataclass
class Plan:
    intent: str
    domains: list[str]
    steps: list[PlanStep]
```

按 session 存于 `planner.py` 模块级内存字典（参照 `ToolCatalog._sticky` 的做法），
提供 `get_plan(session_id)` / `set_plan(session_id, plan)` / `clear_plan(session_id)`。
`ChatEngine.clear_session` 时一并清理。

## 模块 3 — 计划驱动工具选择

`ToolCatalog.select_schemas` 增加可选参数：

```python
def select_schemas(self, user_message: str, session_id: Optional[str] = None,
                   declared_domains: Optional[set[str]] = None) -> list[dict]
```

存在计划时，`declared_domains = set(plan.domains)`。激活 domain 取
**计划声明 ∪ 关键词检测 ∪ sticky**——关键词检测保留作安全网，不替换。
这修复「对的工具没被推给 LLM」的检索分支。

**自救通道**：核对 `list_available_tools(domain=...)` 元工具（`tool_catalog.py`
docstring 提到的 Tier 3 显式查询入口）确实已注册并可被 LLM 调用。LLM 发现需要
的工具不在子集时可主动查询。若未接好，本设计补齐其注册。

## 模块 4 — 循环内 Checkpoint

### 计划注入

`context_builder` 新增：

```python
def build_plan_block(plan: Plan) -> str
```

产出 `[执行计划]` system 块，挨着 `[环境感知]` 注入到 `compose_request_messages`
组装的请求里。步骤以 `✅ / ⬜` 前缀显示完成状态，附 `intent`。

### 步骤打勾

每轮工具执行后，启发式匹配标记完成步骤：用工具所属 domain / 工具名与
`step.tool_family` 及 `step.goal` 做匹配，命中则置 `done=True`。匹配为尽力而为，
不要求精确。

### 末尾校验

FC 循环结束前（产出最终回复那一轮之前）若存在计划且有未完成步骤：把未完成步骤
列表折进最终回复的上下文提示——`以下计划步骤尚未完成：…，请确认是否需要补充
执行，或在回复中向用户说明原因`。**Checkpoint 式：不硬拦截**，LLM 可合理偏离。

## 模块 5 — Schema 加固（区分度修复）

重写易混工具簇的 description，加显式 `✅ 用于 …` / `❌ 不要用于 …`。模型无关，
高杠杆。目标工具簇：

- `heatmap_data` / `h3_binning` / `kde_surface` / `kde_contours`
- `buffer_analysis` / `multi_ring_buffer` / `service_area`
- `get_local_admin_boundary` / `get_admin_division` / `get_district`
- `apply_layer_filter` / `attribute_filter`
- `spatial_aggregate` / `zonal_stats`

改在对应工具定义文件 / registry 元数据处，保持与现有工具描述风格一致。

## 数据流

```
user message
  └─ should_plan(message, messages, has_active_plan) 门控
       ├─ 否 → 直接进 FC 循环（当前行为，零变化）
       └─ 是 → make_plan() → Plan{intent, domains, steps}
                 │              └─ 失败 → None → 降级为无计划循环
                 └─ set_plan(session_id, plan)
                 └─ FC 循环每轮：
                      compose_request_messages 注入 [环境感知] + [执行计划]
                      select_tools 用 plan.domains ∪ 关键词 ∪ sticky
                      工具轮结束：启发式标记完成步骤
                      dispatcher 落 decision_log 记录
                 └─ 循环末：未完成步骤折进最终回复上下文
```

## 错误处理

| 失败点 | 降级行为 |
|--------|----------|
| 规划 LLM 调用失败 / 超时 | 记日志，`make_plan` 返回 `None`，走无计划循环 |
| 规划输出无法解析 / 字段缺失 / domain 越界 | 同上 |
| 计划 `domains` 为空 | `select_schemas` 回退纯关键词检测 |
| 计划后的追问 | `should_plan` 跳过，复用旧计划不重规划 |
| 决策日志写入失败 | 记 warning，不影响主流程 |

## 测试策略

- `planner.should_plan` — 表驱动用例（消息 → 期望 bool），覆盖追问词、长消息、
  有/无活跃计划。
- `planner` 计划 JSON 解析 — 合法 / 畸形 JSON / 残缺字段 / domain 越界。
- `ToolCatalog.select_schemas` — 带 `declared_domains` 的子集结果。
- `decision_log` — 记录字段形状与 JSONL 序列化。
- 集成 — 多步请求生成计划并被循环消费；短追问跳过规划并复用旧计划。
- **回归** — 现有 `tests/test_chat_engine*.py` 全部通过（无计划路径 = 当前行为）。

## 非目标（YAGNI）

- 不引入 embedding / 语义检索替代关键词目录（区分度问题用改 schema 更划算）。
- 不引入独立的意图分类模型 / 路由器模型。
- 不做计划的硬约束（不拒绝计划外的工具调用）。
- 不做计划的持久化落库（内存存储即可，会话级生命周期）。
