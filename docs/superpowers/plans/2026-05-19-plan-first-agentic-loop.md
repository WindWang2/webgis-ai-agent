# Plan-First 智能体循环 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为对话引擎加入显式规划阶段、计划驱动的工具选择、循环内 checkpoint 与工具决策可观测性日志，提升 LLM 意图理解准确度与多步任务编排合理性。

**Architecture:** 在 FC 循环前增加一个启发式门控的结构化规划 LLM 调用，产出 `Plan{intent, domains, steps}`，按 session 内存存储。计划的 domain 驱动 `ToolCatalog` 工具子集选择，计划本身作为 `[执行计划]` 上下文块注入每轮请求并逐步打勾。规划是纯增强——任何环节失败都降级回当前无计划循环。

**Tech Stack:** Python 3、FastAPI、httpx（OpenAI 兼容 Chat Completions）、pytest、dataclasses。

---

## 设计相对 spec 的两处细化

- **决策日志调用点**：spec 原写「dispatcher 调用」。实现改为在 `chat_engine` 的 FC 循环里调用——`chat_engine` 同时握有轮次、激活 domain、子集大小与 dispatch 结果，单一调用点，避免 `dispatcher.dispatch_tool` 的 4 个 return 分支各加一次。`decision_log.py` 模块本身不变。
- **末尾校验**：spec 原写「循环末一次校验」。实现改为：`build_plan_block` 在存在未完成步骤时追加一行提醒,该块每轮都注入,LLM 在决定最终回复那一轮自然看到。不加额外 LLM 调用,符合「Checkpoint 式、不硬拦截」。

## 文件结构

| 文件 | 职责 | 动作 |
|------|------|------|
| `app/core/config.py` | 新增 `LLM_PLANNER_MODEL` 设置 | 修改 |
| `app/services/chat/decision_log.py` | 工具决策结构化 JSONL 日志 | 新建 |
| `app/services/chat/planner.py` | 门控 + `Plan` + 解析 + `make_plan` + session 存储 + 步骤打勾 | 新建 |
| `app/services/tool_catalog.py` | `select_schemas` 支持 `declared_domains` | 修改 |
| `app/services/chat/context_builder.py` | `build_plan_block` + `compose_request_messages` 注入计划 | 修改 |
| `app/services/chat_engine.py` | 接入规划阶段、步骤打勾、决策日志 | 修改 |
| `app/tools/spatial.py` 等 | 易混工具 schema 加固 | 修改 |
| `tests/unit/test_decision_log.py` | 决策日志测试 | 新建 |
| `tests/unit/test_planner.py` | 规划阶段测试 | 新建 |
| `tests/unit/test_tool_catalog.py` | `declared_domains` 测试 | 修改 |
| `tests/test_chat_context_builder.py` | `build_plan_block` 注入测试 | 新建 |

---

### Task 1: 配置项 `LLM_PLANNER_MODEL`

**Files:**
- Modify: `app/core/config.py:43-44`

- [ ] **Step 1: 在 LLM 配置段加入 planner 模型设置**

在 `app/core/config.py` 中 `LLM_MODEL` 行之后插入一行：

```python
    LLM_BASE_URL: str = "https://api.deepseek.com"
    LLM_API_KEY: str = "your-api-key-here"
    LLM_MODEL: str = "deepseek-v4-flash"
    # 规划阶段专用模型；留空时回退 LLM_MODEL（便于以后单独配更便宜的模型）
    LLM_PLANNER_MODEL: str = ""
    LLM_PROMPT_CACHING_ENABLED: bool = True
```

- [ ] **Step 2: 验证配置可加载**

Run: `python -c "from app.core.config import settings; print(repr(settings.LLM_PLANNER_MODEL))"`
Expected: 打印 `''`（无报错）

- [ ] **Step 3: Commit**

```bash
git add app/core/config.py
git commit -m "feat(config): add LLM_PLANNER_MODEL setting"
```

---

### Task 2: 决策日志模块 `decision_log.py`

**Files:**
- Create: `app/services/chat/decision_log.py`
- Test: `tests/unit/test_decision_log.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/test_decision_log.py`：

```python
"""decision_log 单元测试 — 记录构造与 JSONL 序列化。"""
import json

from app.services.chat.decision_log import ToolDecisionRecord, log_tool_decision


def _record(**over):
    base = dict(
        session_id="s1",
        round=0,
        user_message="成都的医院分布",
        active_domains=["statistics", "chinese"],
        from_plan=True,
        subset_size=24,
        total_tools=82,
        tool_chosen="h3_binning",
        tool_args={"resolution": 8},
        result_quality="ok",
        plan_step_matched=3,
    )
    base.update(over)
    return ToolDecisionRecord(**base)


def test_record_to_dict_has_all_fields():
    d = _record().to_dict()
    assert d["session_id"] == "s1"
    assert d["tool_chosen"] == "h3_binning"
    assert d["result_quality"] == "ok"
    assert d["plan_step_matched"] == 3
    assert "ts" in d  # 时间戳自动注入


def test_log_writes_one_jsonl_line(tmp_path, monkeypatch):
    log_file = tmp_path / "tool_decisions.jsonl"
    monkeypatch.setattr("app.services.chat.decision_log._LOG_PATH", log_file)
    log_tool_decision(_record())
    log_tool_decision(_record(tool_chosen="buffer_analysis"))
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    parsed = json.loads(lines[1])
    assert parsed["tool_chosen"] == "buffer_analysis"


def test_log_failure_does_not_raise(monkeypatch):
    """写盘失败只记 warning，绝不影响主流程。"""
    def boom(*_a, **_k):
        raise OSError("disk full")
    monkeypatch.setattr("app.services.chat.decision_log._append_line", boom)
    log_tool_decision(_record())  # 不抛异常即通过
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/unit/test_decision_log.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.chat.decision_log'`

- [ ] **Step 3: 实现 decision_log 模块**

创建 `app/services/chat/decision_log.py`：

```python
"""工具决策的结构化可观测性日志（JSONL）。

每次工具调用落一条记录，用真实数据回答「选错工具时，是检索问题
（对的工具没被推给 LLM）还是区分度问题（工具都在但选了相邻错工具）」。

写盘失败只记 warning，绝不影响对话主流程。
"""
from __future__ import annotations

import dataclasses
import datetime
import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_LOG_PATH = Path("logs/tool_decisions.jsonl")


@dataclasses.dataclass
class ToolDecisionRecord:
    session_id: str
    round: int
    user_message: str
    active_domains: list[str]
    from_plan: bool
    subset_size: int
    total_tools: int
    tool_chosen: str
    tool_args: dict[str, Any]
    result_quality: str            # "ok" | "empty" | "error"
    plan_step_matched: Optional[int]  # 命中的计划步骤号；无计划时为 None

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["ts"] = datetime.datetime.now().isoformat(timespec="seconds")
        d["user_message"] = (self.user_message or "")[:200]
        return d


def _append_line(line: str) -> None:
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def log_tool_decision(record: ToolDecisionRecord) -> None:
    """追加一条决策记录。任何 IO 失败都被吞掉，只记 warning。"""
    try:
        _append_line(json.dumps(record.to_dict(), ensure_ascii=False))
    except Exception as e:  # noqa: BLE001 — 可观测性绝不能拖垮主流程
        logger.warning(f"[decision_log] 写入失败: {e}")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/unit/test_decision_log.py -v`
Expected: PASS（3 个测试）

- [ ] **Step 5: Commit**

```bash
git add app/services/chat/decision_log.py tests/unit/test_decision_log.py
git commit -m "feat(chat): add tool decision JSONL observability log"
```

---

### Task 3: 规划门控 `should_plan`

**Files:**
- Create: `app/services/chat/planner.py`
- Test: `tests/unit/test_planner.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/test_planner.py`：

```python
"""planner 单元测试 — 门控、计划解析、make_plan。"""
import pytest

from app.services.chat.planner import should_plan


@pytest.mark.parametrize("message,has_plan,expected", [
    ("分析成都市三甲医院的空间分布并做热点检测", False, True),   # 长复杂请求 → 规划
    ("换个颜色", True, False),                                   # 短追问 + 有计划 → 跳过
    ("再放大点", True, False),                                   # 短追问 + 有计划 → 跳过
    ("画个热力图", False, True),                                  # 短但无计划 → 规划
    ("成都和重庆两个城市的人口对比", False, True),                  # 长请求 → 规划
    ("隐藏这个图层", True, False),                                # 短追问词 + 有计划 → 跳过
])
def test_should_plan_gate(message, has_plan, expected):
    assert should_plan(message, [], has_plan) is expected


def test_short_followup_word_without_plan_still_plans():
    """无活跃计划时，即使是追问词也要规划（没有上文可承接）。"""
    assert should_plan("换个颜色", [], has_active_plan=False) is True
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/unit/test_planner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.chat.planner'`

- [ ] **Step 3: 实现 should_plan**

创建 `app/services/chat/planner.py`（本任务只放门控部分，后续任务追加）：

```python
"""规划阶段：启发式门控 + 结构化规划 LLM 调用 + Plan 存储 + 步骤打勾。

设计原则：规划是增强，永不是硬依赖。任何环节失败都降级回无计划循环。
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# 追问词：短消息命中其一则视为承接上一轮的追问
_FOLLOWUP_PATTERN = re.compile(
    r"(换|再|又|放大|缩小|颜色|配色|隐藏|显示|去掉|删掉|清除|加粗|样式|"
    r"大一点|小一点|这个|那个|上面|刚才)"
)
_SHORT_THRESHOLD = 20  # 字符数


def should_plan(message: str, messages: list[dict], has_active_plan: bool) -> bool:
    """启发式门控：判断本轮是否需要跑规划阶段。

    跳过规划（返回 False）的条件：消息短 且 命中追问词 且 已有活跃计划。
    其余情况返回 True。无活跃计划时即使是追问也规划（没有上文可承接）。
    """
    text = (message or "").strip()
    is_short = len(text) <= _SHORT_THRESHOLD
    is_followup = bool(_FOLLOWUP_PATTERN.search(text))
    if is_short and is_followup and has_active_plan:
        return False
    return True
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/unit/test_planner.py -v`
Expected: PASS（7 个参数化用例 + 1 个）

- [ ] **Step 5: Commit**

```bash
git add app/services/chat/planner.py tests/unit/test_planner.py
git commit -m "feat(chat): add should_plan heuristic gate"
```

---

### Task 4: `Plan` 数据结构与 `parse_plan`

**Files:**
- Modify: `app/services/chat/planner.py`
- Test: `tests/unit/test_planner.py`

- [ ] **Step 1: 追加失败测试**

在 `tests/unit/test_planner.py` 末尾追加：

```python
from app.services.chat.planner import Plan, PlanStep, parse_plan


def test_parse_plan_valid():
    raw = '''{
      "intent": "分析成都医院分布",
      "domains": ["chinese", "statistics"],
      "steps": [
        {"n": 1, "goal": "锁定成都边界", "tool_family": "chinese"},
        {"n": 2, "goal": "H3 聚合", "tool_family": "statistics"}
      ]
    }'''
    plan = parse_plan(raw)
    assert isinstance(plan, Plan)
    assert plan.intent == "分析成都医院分布"
    assert plan.domains == ["chinese", "statistics"]
    assert len(plan.steps) == 2
    assert plan.steps[0] == PlanStep(n=1, goal="锁定成都边界", tool_family="chinese")
    assert plan.steps[0].done is False


def test_parse_plan_strips_code_fence():
    raw = '```json\n{"intent":"x","domains":["core"],"steps":[]}\n```'
    plan = parse_plan(raw)
    assert plan is not None
    assert plan.intent == "x"


def test_parse_plan_filters_invalid_domains():
    raw = '{"intent":"x","domains":["chinese","nonsense"],"steps":[]}'
    plan = parse_plan(raw)
    assert plan is not None
    assert plan.domains == ["chinese"]  # 越界 domain 被丢弃


def test_parse_plan_malformed_json_returns_none():
    assert parse_plan("not json at all") is None
    assert parse_plan("") is None


def test_parse_plan_missing_fields_returns_none():
    assert parse_plan('{"intent":"x"}') is None          # 缺 domains/steps
    assert parse_plan('{"domains":[],"steps":[]}') is None  # 缺 intent
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/unit/test_planner.py -k parse_plan -v`
Expected: FAIL — `ImportError: cannot import name 'Plan'`

- [ ] **Step 3: 实现 Plan / PlanStep / parse_plan**

在 `app/services/chat/planner.py` 顶部 import 段补充：

```python
import dataclasses
import json

from app.services.tool_catalog import DOMAIN_KEYWORDS
```

在 `_SHORT_THRESHOLD` 常量之后追加：

```python
# 合法 domain 取值 = ToolCatalog 的主题键集合 + "core"（基础工具）
VALID_DOMAINS: set[str] = set(DOMAIN_KEYWORDS) | {"core"}


@dataclasses.dataclass
class PlanStep:
    n: int
    goal: str
    tool_family: str
    done: bool = False


@dataclasses.dataclass
class Plan:
    intent: str
    domains: list[str]
    steps: list[PlanStep]


def parse_plan(raw: str) -> Plan | None:
    """防御式解析规划 LLM 的 JSON 输出。任何异常 / 字段缺失 → 返回 None。"""
    if not raw or not raw.strip():
        return None
    text = raw.strip()
    # 剥离 ```json ... ``` 代码围栏
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        logger.warning(f"[planner] 计划 JSON 解析失败: {text[:200]}")
        return None
    if not isinstance(obj, dict):
        return None
    intent = obj.get("intent")
    domains = obj.get("domains")
    steps_raw = obj.get("steps")
    if not isinstance(intent, str) or not intent.strip():
        return None
    if not isinstance(domains, list) or not isinstance(steps_raw, list):
        return None
    # 过滤越界 domain
    domains = [d for d in domains if d in VALID_DOMAINS]
    steps: list[PlanStep] = []
    for i, s in enumerate(steps_raw, start=1):
        if not isinstance(s, dict):
            continue
        steps.append(PlanStep(
            n=int(s.get("n", i)),
            goal=str(s.get("goal", "")),
            tool_family=str(s.get("tool_family", "core")),
        ))
    return Plan(intent=intent.strip(), domains=domains, steps=steps)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/unit/test_planner.py -v`
Expected: PASS（全部用例）

- [ ] **Step 5: Commit**

```bash
git add app/services/chat/planner.py tests/unit/test_planner.py
git commit -m "feat(chat): add Plan dataclass and defensive parse_plan"
```

---

### Task 5: session 计划存储 + 步骤打勾

**Files:**
- Modify: `app/services/chat/planner.py`
- Test: `tests/unit/test_planner.py`

- [ ] **Step 1: 追加失败测试**

在 `tests/unit/test_planner.py` 末尾追加：

```python
from app.services.chat.planner import (
    get_plan, set_plan, clear_plan, mark_step_done,
)
from app.tools.registry import ToolRegistry


def test_plan_store_set_get_clear():
    plan = Plan(intent="x", domains=["core"], steps=[])
    set_plan("sess-A", plan)
    assert get_plan("sess-A") is plan
    clear_plan("sess-A")
    assert get_plan("sess-A") is None


def test_get_plan_unknown_session_returns_none():
    assert get_plan("never-seen") is None


def test_mark_step_done_matches_by_tool_domain():
    reg = ToolRegistry()
    reg.register("h3_binning", "h3", func=lambda **_: {}, tier=2, domains=["statistics"])
    plan = Plan(intent="x", domains=["statistics"], steps=[
        PlanStep(n=1, goal="聚合", tool_family="statistics"),
        PlanStep(n=2, goal="再聚合", tool_family="statistics"),
    ])
    set_plan("sess-B", plan)
    mark_step_done("sess-B", "h3_binning", reg)
    assert plan.steps[0].done is True
    assert plan.steps[1].done is False   # 只打勾第一个未完成的匹配步骤
    clear_plan("sess-B")


def test_mark_step_done_no_plan_is_noop():
    reg = ToolRegistry()
    mark_step_done("no-plan-sess", "anything", reg)  # 不抛异常即通过
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/unit/test_planner.py -k "plan_store or mark_step or unknown_session" -v`
Expected: FAIL — `ImportError: cannot import name 'get_plan'`

- [ ] **Step 3: 实现 session 存储与步骤打勾**

在 `app/services/chat/planner.py` 末尾追加：

```python
# session_id -> Plan（会话级内存存储，参照 ToolCatalog._sticky）
_plans: dict[str, Plan] = {}


def get_plan(session_id: str) -> Plan | None:
    return _plans.get(session_id)


def set_plan(session_id: str, plan: Plan) -> None:
    _plans[session_id] = plan


def clear_plan(session_id: str) -> None:
    _plans.pop(session_id, None)


def mark_step_done(session_id: str, tool_name: str, registry) -> int | None:
    """把工具调用匹配到第一个未完成的计划步骤并打勾。

    匹配启发式：取工具在 registry 中标注的 domains，找第一个未完成、
    tool_family 落在该 domains 内（或 tool_family=="core"）的步骤。
    返回被打勾的步骤号；无匹配 / 无计划时返回 None。
    """
    plan = _plans.get(session_id)
    if plan is None:
        return None
    tool_domains = set(registry.metadata(tool_name).get("domains", []))
    for step in plan.steps:
        if step.done:
            continue
        if step.tool_family in tool_domains or step.tool_family == "core":
            step.done = True
            return step.n
    return None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/unit/test_planner.py -v`
Expected: PASS（全部用例）

- [ ] **Step 5: Commit**

```bash
git add app/services/chat/planner.py tests/unit/test_planner.py
git commit -m "feat(chat): add session plan store and step checkpoint"
```

---

### Task 6: 规划 LLM 调用 `make_plan`

**Files:**
- Modify: `app/services/chat/planner.py`
- Test: `tests/unit/test_planner.py`

- [ ] **Step 1: 追加失败测试**

在 `tests/unit/test_planner.py` 末尾追加：

```python
from app.services.chat.llm_client import LLMConfig
from app.services.chat import planner as planner_mod


@pytest.fixture
def cfg():
    return LLMConfig(base_url="http://x", model="m", api_key="k")


@pytest.mark.asyncio
async def test_make_plan_success_stores_plan(cfg, monkeypatch):
    async def fake_call_llm(_cfg, _messages, _tools=None):
        return {"choices": [{"message": {"content":
            '{"intent":"分析医院","domains":["statistics"],'
            '"steps":[{"n":1,"goal":"聚合","tool_family":"statistics"}]}'}}]}
    monkeypatch.setattr(planner_mod, "call_llm", fake_call_llm)
    plan = await planner_mod.make_plan(cfg, "sess-C", "分析成都医院分布", "[环境感知]")
    assert plan is not None
    assert plan.intent == "分析医院"
    assert planner_mod.get_plan("sess-C") is plan   # 成功即存储
    planner_mod.clear_plan("sess-C")


@pytest.mark.asyncio
async def test_make_plan_llm_failure_returns_none(cfg, monkeypatch):
    async def boom(_cfg, _messages, _tools=None):
        raise RuntimeError("LLM down")
    monkeypatch.setattr(planner_mod, "call_llm", boom)
    plan = await planner_mod.make_plan(cfg, "sess-D", "复杂请求", "[环境感知]")
    assert plan is None
    assert planner_mod.get_plan("sess-D") is None


@pytest.mark.asyncio
async def test_make_plan_unparseable_returns_none(cfg, monkeypatch):
    async def fake(_cfg, _messages, _tools=None):
        return {"choices": [{"message": {"content": "对不起我不会"}}]}
    monkeypatch.setattr(planner_mod, "call_llm", fake)
    plan = await planner_mod.make_plan(cfg, "sess-E", "复杂请求", "[环境感知]")
    assert plan is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/unit/test_planner.py -k make_plan -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'make_plan'`

- [ ] **Step 3: 实现 make_plan 与规划 prompt**

在 `app/services/chat/planner.py` 的 import 段补充：

```python
from app.services.chat.llm_client import LLMConfig, call_llm
```

在 `VALID_DOMAINS` 定义之后追加规划 prompt 常量：

```python
PLANNER_PROMPT = """你是 WebGIS 空间分析任务的规划器。给定用户请求与当前地图状态，
输出一个简洁的执行计划。只输出 JSON，不要任何解释文字、不要 Markdown 代码围栏。

JSON 结构：
{
  "intent": "一句话概括用户真正想要的结果",
  "domains": ["涉及的领域，取值见下"],
  "steps": [
    {"n": 1, "goal": "这一步要达成什么", "tool_family": "该步所属领域"}
  ]
}

合法的领域取值（domains 与 tool_family 都只能用这些）：
- core      基础空间分析与图层管理（缓冲、裁剪、过滤、制图等）
- chinese   中国行政区划 / 中文地址 / 国内 POI（高德、天地图、本地矢量库）
- osm       OpenStreetMap / Overpass 全球数据
- raster    遥感 / 栅格 / 地形 / 植被指数
- network   路径 / 可达性 / 服务区 / 等时圈
- statistics 热点 / 聚类 / 密度 / 插值 / 空间统计
- report    报告 / 导出 / 制图成果
- what_if   情景模拟推演
- meta      创建技能 / 自定义工具

规划原则：
- 由简入深。宽泛请求（如"分布情况"）优先安排原生热力图等轻量步骤。
- 步骤控制在 5 步以内，每步聚焦一个明确产出。
- 简单请求可以只有 1 步。"""


def _planning_messages(user_message: str, env_summary: str) -> list[dict]:
    return [
        {"role": "system", "content": PLANNER_PROMPT},
        {"role": "user", "content": f"{env_summary}\n\n用户请求：{user_message}"},
    ]
```

在文件末尾（`mark_step_done` 之后）追加：

```python
async def make_plan(
    cfg: LLMConfig,
    session_id: str,
    user_message: str,
    env_summary: str,
) -> Plan | None:
    """跑一次规划 LLM 调用，解析并存储计划。任何失败都返回 None（降级无计划）。"""
    try:
        resp = await call_llm(cfg, _planning_messages(user_message, env_summary))
        choice = resp.get("choices", [{}])[0]
        msg = choice.get("message", {})
        raw = msg.get("content") or msg.get("reasoning_content") or ""
    except Exception as e:  # noqa: BLE001 — 规划失败必须降级，不能拖垮对话
        logger.warning(f"[planner] make_plan LLM 调用失败: {e}")
        return None
    plan = parse_plan(raw)
    if plan is None:
        logger.info(f"[planner] session={session_id} 计划解析失败，降级无计划")
        return None
    set_plan(session_id, plan)
    logger.info(
        f"[planner] session={session_id} 计划已生成: "
        f"intent={plan.intent!r} domains={plan.domains} steps={len(plan.steps)}"
    )
    return plan
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/unit/test_planner.py -v`
Expected: PASS（全部用例，含 make_plan 3 个）

- [ ] **Step 5: Commit**

```bash
git add app/services/chat/planner.py tests/unit/test_planner.py
git commit -m "feat(chat): add make_plan structured planning LLM call"
```

---

### Task 7: `ToolCatalog` 支持计划声明的 domain

**Files:**
- Modify: `app/services/tool_catalog.py:102-122`
- Test: `tests/unit/test_tool_catalog.py`

- [ ] **Step 1: 追加失败测试**

在 `tests/unit/test_tool_catalog.py` 末尾追加：

```python
def test_declared_domains_activates_tier2_without_keyword(catalog):
    """计划声明的 domain 即使关键词没命中也激活对应 tier 2 工具。"""
    schemas = catalog.select_schemas("随便一句话", session_id="d1",
                                     declared_domains={"raster"})
    names = _names(schemas)
    assert "compute_ndvi" in names   # raster 工具被纳入
    assert "fetch_dem" in names


def test_declared_domains_union_with_keywords(catalog):
    """计划 domain 与关键词检测取并集，关键词仍生效。"""
    schemas = catalog.select_schemas("规划一条驾车路线", session_id="d2",
                                     declared_domains={"raster"})
    names = _names(schemas)
    assert "compute_ndvi" in names   # 来自 declared_domains
    assert "plan_route" in names     # 来自关键词"路线/驾车"


def test_declared_domains_none_preserves_old_behavior(catalog):
    """不传 declared_domains 时行为与旧版一致（纯关键词）。"""
    schemas = catalog.select_schemas("计算 NDVI", session_id="d3")
    assert "compute_ndvi" in _names(schemas)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/unit/test_tool_catalog.py -k declared_domains -v`
Expected: FAIL — `TypeError: select_schemas() got an unexpected keyword argument 'declared_domains'`

- [ ] **Step 2b: 核对 `list_available_tools` 自救元工具是否已注册**

Run: `grep -rn 'list_available_tools' app/tools/`
Expected: 找到该工具的 `@tool(...)` 定义（应为 tier 3 / 显式查询入口）。

若 grep **无结果**（元工具不存在），需补齐：在 `app/tools/registry.py` 或一个
合适的 meta 工具文件中注册一个 `list_available_tools(domain: str)` 工具，
其实现返回 `registry.all_metadata()` 中该 domain 下的工具名与描述，`tier=1`
（让 LLM 任何时候可调用以自救）。若 grep **有结果**，确认其 `tier` 不为 3 或
确实可被 LLM 在缺工具时发现——记录现状即可，本步无需改动。

- [ ] **Step 3: 修改 select_schemas**

把 `app/services/tool_catalog.py` 的 `select_schemas` 方法（102-122 行）整体替换为：

```python
    def select_schemas(
        self,
        user_message: str,
        session_id: Optional[str] = None,
        declared_domains: Optional[set[str]] = None,
    ) -> list[dict]:
        """根据用户消息 + 会话粘性 + 计划声明的 domain，返回本轮 schema 子集。

        declared_domains 来自规划阶段产出的 Plan.domains；与关键词检测、
        sticky 取并集——关键词检测保留作安全网，不被替换。
        """
        active_domains = self._activate_domains(user_message, session_id)
        if declared_domains:
            active_domains = active_domains | set(declared_domains)
        names: set[str] = set()
        for name, meta in self.registry.all_metadata().items():
            tier = int(meta.get("tier", 1))
            if tier == 1:
                names.add(name)
                continue
            if tier == 2:
                tool_domains = set(meta.get("domains", []))
                if tool_domains & active_domains:
                    names.add(name)
                continue
            # tier 3 永远不自动纳入；由 list_available_tools 显式查询
        schemas = self.registry.get_schemas_subset(names)
        logger.debug(
            "[ToolCatalog] session=%s domains=%s selected=%d/%d",
            session_id, sorted(active_domains), len(schemas), len(self.registry.get_schemas()),
        )
        return schemas
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/unit/test_tool_catalog.py -v`
Expected: PASS（原有用例 + 3 个新用例）

- [ ] **Step 5: Commit**

```bash
git add app/services/tool_catalog.py tests/unit/test_tool_catalog.py
git commit -m "feat(catalog): select_schemas accepts plan-declared domains"
```

---

### Task 8: `[执行计划]` 上下文块注入

**Files:**
- Modify: `app/services/chat/context_builder.py`
- Test: `tests/test_chat_context_builder.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_chat_context_builder.py`：

```python
"""context_builder 计划注入测试。"""
from app.services.chat.context_builder import build_plan_block, compose_request_messages
from app.services.chat.planner import Plan, PlanStep, set_plan, clear_plan


def test_build_plan_block_shows_checkboxes():
    plan = Plan(intent="分析医院分布", domains=["chinese"], steps=[
        PlanStep(n=1, goal="锁定边界", tool_family="chinese", done=True),
        PlanStep(n=2, goal="搜索 POI", tool_family="chinese", done=False),
    ])
    block = build_plan_block(plan)
    assert "[执行计划]" in block
    assert "分析医院分布" in block
    assert "✅" in block and "⬜" in block
    assert "锁定边界" in block and "搜索 POI" in block


def test_build_plan_block_warns_on_incomplete():
    plan = Plan(intent="x", domains=["core"], steps=[
        PlanStep(n=1, goal="a", tool_family="core", done=False),
    ])
    assert "未完成" in build_plan_block(plan)


def test_build_plan_block_all_done_no_warning():
    plan = Plan(intent="x", domains=["core"], steps=[
        PlanStep(n=1, goal="a", tool_family="core", done=True),
    ])
    assert "未完成" not in build_plan_block(plan)


def test_compose_request_messages_injects_plan_when_present():
    msgs = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "你好"},
    ]
    set_plan("ctx-sess", Plan(intent="测试意图", domains=["core"], steps=[]))
    try:
        out = compose_request_messages("ctx-sess", msgs)
        joined = " ".join(m["content"] for m in out if m.get("role") == "system")
        assert "测试意图" in joined
    finally:
        clear_plan("ctx-sess")


def test_compose_request_messages_no_plan_no_block():
    msgs = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "你好"},
    ]
    clear_plan("ctx-sess-2")
    out = compose_request_messages("ctx-sess-2", msgs)
    joined = " ".join(m["content"] for m in out if m.get("role") == "system")
    assert "[执行计划]" not in joined
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_chat_context_builder.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_plan_block'`

- [ ] **Step 3: 实现 build_plan_block 并接入 compose_request_messages**

在 `app/services/chat/context_builder.py` 末尾（`compose_request_messages` 之前）追加：

```python
def build_plan_block(plan) -> str:
    """把 Plan 渲染成 [执行计划] 系统块，步骤带 ✅/⬜ 完成标记。

    存在未完成步骤时追加一行提醒——这就是 Checkpoint 式的「末尾校验」：
    每轮都注入，LLM 在决定最终回复那一轮自然看到，不硬拦截。
    """
    lines = [
        "[执行计划 — 你为本任务制定的步骤，按此推进，完成一步即视为打勾]",
        f"- 意图: {plan.intent}",
    ]
    if plan.steps:
        lines.append("- 步骤:")
        for step in plan.steps:
            mark = "✅" if step.done else "⬜"
            lines.append(f"  {mark} {step.n}. {step.goal}")
        if any(not s.done for s in plan.steps):
            lines.append(
                "⚠️ 仍有未完成步骤。若要给出最终回复，请先确认这些步骤是否"
                "已无必要，或在回复中向用户说明未完成的原因。"
            )
    return "\n".join(lines)
```

然后修改 `compose_request_messages`：在 `last_ctx = build_last_analysis_context(messages)` 这一行之前插入计划块注入逻辑。把函数中 `head = [sys_msg]` 之后的部分替换为：

```python
    head = [sys_msg]

    # 注入 [执行计划] 块（若本会话存在活跃计划）
    from app.services.chat.planner import get_plan
    plan = get_plan(session_id)
    if plan is not None:
        head.append({"role": "system", "content": build_plan_block(plan)})

    last_ctx = build_last_analysis_context(messages)
    if last_ctx:
        head.append({"role": "system", "content": last_ctx})
    head.extend(messages[1:])
    return head
```

> 注：`get_plan` 用函数内局部 import，避免 `context_builder` ↔ `planner` 的模块级循环 import。

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_chat_context_builder.py -v`
Expected: PASS（5 个测试）

- [ ] **Step 5: Commit**

```bash
git add app/services/chat/context_builder.py tests/test_chat_context_builder.py
git commit -m "feat(chat): inject [执行计划] context block into requests"
```

---

### Task 9: `chat_engine` 接入规划阶段、步骤打勾、决策日志

**Files:**
- Modify: `app/services/chat_engine.py`
- Test: `tests/test_chat_engine_planning.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_chat_engine_planning.py`：

```python
"""ChatEngine 规划阶段集成测试。"""
import pytest

from app.tools.registry import ToolRegistry
from app.services.tool_catalog import ToolCatalog
from app.services.chat_engine import ChatEngine
from app.services.chat import planner as planner_mod


@pytest.fixture
def engine():
    reg = ToolRegistry()
    reg.register("buffer_analysis", "buffer", func=lambda **_: {})
    eng = ChatEngine(reg, tool_catalog=ToolCatalog(reg))
    return eng


def test_planner_llm_config_uses_planner_model(engine, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "LLM_PLANNER_MODEL", "cheap-model")
    cfg = engine._planner_llm_config()
    assert cfg.model == "cheap-model"


def test_planner_llm_config_falls_back_to_main_model(engine, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "LLM_PLANNER_MODEL", "")
    cfg = engine._planner_llm_config()
    assert cfg.model == engine.model


@pytest.mark.asyncio
async def test_maybe_plan_runs_for_complex_request(engine, monkeypatch):
    captured = {}
    async def fake_make_plan(cfg, session_id, message, env):
        captured["called"] = True
        plan = planner_mod.Plan(intent="x", domains=["core"], steps=[])
        planner_mod.set_plan(session_id, plan)
        return plan
    monkeypatch.setattr(planner_mod, "make_plan", fake_make_plan)
    await engine._maybe_plan("sess-P1", "分析成都市三甲医院的空间分布并做热点检测", [])
    assert captured.get("called") is True
    planner_mod.clear_plan("sess-P1")


@pytest.mark.asyncio
async def test_maybe_plan_skips_short_followup(engine, monkeypatch):
    planner_mod.set_plan("sess-P2", planner_mod.Plan(intent="x", domains=["core"], steps=[]))
    captured = {}
    async def fake_make_plan(*a, **k):
        captured["called"] = True
    monkeypatch.setattr(planner_mod, "make_plan", fake_make_plan)
    await engine._maybe_plan("sess-P2", "换个颜色", [])
    assert "called" not in captured   # 短追问 + 有计划 → 跳过规划
    planner_mod.clear_plan("sess-P2")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_chat_engine_planning.py -v`
Expected: FAIL — `AttributeError: 'ChatEngine' object has no attribute '_planner_llm_config'`

- [ ] **Step 3: 在 ChatEngine 加入 `_planner_llm_config`、`_maybe_plan`、`_log_tool_decision`**

在 `app/services/chat_engine.py` 的 `_llm_config` 方法之后追加三个方法：

```python
    def _planner_llm_config(self) -> LLMConfig:
        """规划阶段的 LLM 配置：LLM_PLANNER_MODEL 非空时覆盖 model。"""
        cfg = self._llm_config()
        if settings.LLM_PLANNER_MODEL:
            return LLMConfig(
                base_url=cfg.base_url,
                model=settings.LLM_PLANNER_MODEL,
                api_key=cfg.api_key,
                use_prompt_caching=cfg.use_prompt_caching,
            )
        return cfg

    async def _maybe_plan(self, session_id: str, message: str, messages: list[dict]) -> None:
        """启发式门控通过则跑规划阶段。规划是增强，失败静默降级。"""
        from app.services.chat import planner
        has_plan = planner.get_plan(session_id) is not None
        if not planner.should_plan(message, messages, has_plan):
            return
        env = self._get_map_state_summary(session_id)
        try:
            await planner.make_plan(self._planner_llm_config(), session_id, message, env)
        except Exception as e:  # noqa: BLE001 — 规划绝不能拖垮对话
            logger.warning(f"[chat_engine] 规划阶段异常，降级无计划: {e}")

    def _log_tool_decision(
        self,
        session_id: str,
        round_index: int,
        message: str,
        tool_name: str,
        tool_args: dict,
        outcome: dict,
        subset_size: int,
    ) -> None:
        """落一条工具决策记录。可观测性，绝不影响主流程。

        subset_size 由调用方传入本轮已算好的工具子集大小——不在此处重算，
        因为 select_schemas 会衰减 ToolCatalog 的 sticky TTL，重复调用会
        让 sticky domain 过早失效。
        """
        from app.services.chat import planner
        from app.services.chat.decision_log import ToolDecisionRecord, log_tool_decision
        from app.services.chat.dispatcher import is_suspicious_result

        if outcome.get("is_error"):
            quality = "error"
        elif is_suspicious_result(outcome.get("result")):
            quality = "empty"
        else:
            quality = "ok"
        plan = planner.get_plan(session_id)
        # active_domains 是只读诊断接口，不触发激活/衰减，安全
        active = self.catalog.active_domains(session_id) if self.catalog else set()
        try:
            log_tool_decision(ToolDecisionRecord(
                session_id=session_id,
                round=round_index,
                user_message=message,
                active_domains=sorted(active),
                from_plan=plan is not None,
                subset_size=subset_size,
                total_tools=len(self.registry.get_schemas()),
                tool_chosen=tool_name,
                tool_args=tool_args if isinstance(tool_args, dict) else {},
                result_quality=quality,
                plan_step_matched=planner.mark_step_done(session_id, tool_name, self.registry),
            ))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[chat_engine] 决策日志记录失败: {e}")
```

- [ ] **Step 4: 在 `chat` 与 `chat_stream` 中调用规划阶段**

在 `chat` 方法中，`await self._save_msg_async(session_id, "user", message)` 之后、
`executed_tools` 初始化之前，插入一行：

```python
        await self._maybe_plan(session_id, message, messages)
```

在 `chat_stream` 方法中，同样在 `await self._save_msg_async(session_id, "user", message)`
之后、`task = self.tracker.create(...)` 之前，插入同一行：

```python
        await self._maybe_plan(session_id, message, messages)
```

- [ ] **Step 5: 在 `chat_stream` 工具循环中调用决策日志**

`chat_stream` 的 FC 循环用 `for _ in range(self.max_rounds)` 做轮次。先把这一行的
轮次变量改成具名的，便于日志记录：

```python
        for round_index in range(self.max_rounds):
```

（同步把该 `chat_stream` 循环体内原本引用 `_` 轮次变量的地方——若有——一并改名；
当前实现循环体未引用 `_`，通常只需改 `for` 行。）

然后在工具循环里，`outcome = await dispatch_task` 之后、
`msg_result_str = outcome["llm_payload"]` 之前，插入：

```python
                    self._log_tool_decision(
                        session_id, round_index, message, tool_name,
                        tool_args_dict, outcome, len(tools or []),
                    )
```

> 注：`tools` 是本轮 `self._select_tools(...)` 已算好的工具子集，`len(tools or [])`
> 即 `subset_size`，不重算以免衰减 sticky TTL。`mark_step_done` 已在
> `_log_tool_decision` 内部调用，步骤打勾与决策日志在同一处完成。
>
> 范围说明：决策日志只接入 `chat_stream`（生产流式路径）。非流式 `chat`
> 仅接入规划阶段（Step 4），不接决策日志——保持改动聚焦。

- [ ] **Step 6: `clear_session` 清理计划**

在 `app/services/chat_engine.py` 的 `clear_session` 方法中，`session_data_manager.clear_session(session_id)` 之后插入：

```python
            from app.services.chat import planner
            planner.clear_plan(session_id)
            if self.catalog is not None:
                self.catalog.reset_session(session_id)
```

- [ ] **Step 7: 运行测试确认通过**

Run: `pytest tests/test_chat_engine_planning.py -v`
Expected: PASS（5 个测试）

- [ ] **Step 8: 运行 chat_engine 回归测试**

Run: `pytest tests/test_chat_engine.py tests/test_chat_engine_history.py tests/test_chat_engine_tracking.py -v`
Expected: PASS（全部通过——无计划路径行为不变）

- [ ] **Step 9: Commit**

```bash
git add app/services/chat_engine.py tests/test_chat_engine_planning.py
git commit -m "feat(chat): wire planning stage, step checkpoint, decision log into ChatEngine"
```

---

### Task 10: 易混工具 schema 加固

**Files:**
- Modify: `app/tools/spatial.py`（`heatmap_data`）
- Modify: `app/tools/advanced_spatial.py`（`h3_binning`、`buffer` 相关）
- Modify: 其余含目标工具的文件（按 grep 结果定位）

- [ ] **Step 1: 定位所有目标工具的定义位置**

Run: `grep -rn 'name="\(heatmap_data\|h3_binning\|kde_surface\|kde_contours\|buffer_analysis\|multi_ring_buffer\|service_area\|get_local_admin_boundary\|get_admin_division\|get_district\|apply_layer_filter\|attribute_filter\|spatial_aggregate\|zonal_stats\)"' app/tools/`
Expected: 列出每个工具 `@tool(...)` 装饰器所在的文件与行号

- [ ] **Step 2: 重写 `heatmap_data` 描述**

把 `app/tools/spatial.py` 中 `heatmap_data` 的 `description` 参数替换为（保留 `args_model=HeatmapDataArgs` 不变）：

```python
           description=(
               "点要素热力图。✅ 用于：用户宽泛询问『分布』『热度』『密度趋势』时"
               "的首选——优先 render_type='native' 原生渲染，轻量、不增加数据负担。"
               "\n❌ 不要用于：(1) 需要网格统计值（每格计数/求和）— 用 h3_binning；"
               "(2) 需要矢量等值面用于导出/制图 — 用 kde_contours；"
               "(3) 需要连续概率面做后续叠加分析 — 用 kde_surface。"
           ),
```

- [ ] **Step 3: 重写 `h3_binning` 描述**

把 `app/tools/advanced_spatial.py` 中 `h3_binning` 的 `description` 参数替换为（`tier=2, domains=["statistics"]` 与 `param_descriptions` 保持不变）：

```python
           description=(
               "H3 六边形网格聚合：把点数据聚合到指定分辨率的 H3 网格（代替传统鱼网）。"
               "✅ 用于：需要每个网格的统计值（计数/求和/均值）做数据驱动渲染，"
               "或作为 h3_lisa 空间聚类检验的前置步骤。"
               "\n❌ 不要用于：(1) 只想快速看分布趋势 — 用 heatmap_data(render_type='native')；"
               "(2) 需要平滑的连续密度面 — 用 kde_surface。"
           ),
```

- [ ] **Step 4: 重写其余易混工具描述**

对 Step 1 grep 结果中的以下工具，按同样的 `✅ 用于 / ❌ 不要用于` 模式重写 `description`，
保持与 `dissolve_layer`（`advanced_spatial.py` 已有范例）一致的风格：

- `kde_surface`：✅ 生成覆盖全域的连续概率密度格网，用于后续叠加/选址建模。❌ 不要用于首选可视化（默认不直接展示）— 看趋势用 heatmap_data，要等值面用 kde_contours。
- `kde_contours`：✅ 生成矢量等值面（等值线/面）用于制图与导出。❌ 不要用于快速看趋势 — 用 heatmap_data。
- `buffer_analysis`：✅ 单一固定半径缓冲区。❌ 多个半径环带用 multi_ring_buffer；按行程时间/距离的可达范围用 service_area。
- `multi_ring_buffer`：✅ 同一要素多个半径的同心环带。❌ 单一半径用 buffer_analysis。
- `service_area`：✅ 沿路网的行程时间/距离可达范围（等时圈）。❌ 简单直线半径用 buffer_analysis。
- `get_local_admin_boundary`：✅ 中国境内行政区边界的首选——本地矢量库，最快最稳。❌ 非中国境内数据才回退在线工具。
- `get_admin_division`：✅ 在线行政区划（天地图），用于本地库未覆盖或非中国区域。❌ 中国境内优先 get_local_admin_boundary。
- `get_district`：✅ 高德行政区划，作为 get_admin_division 失败时的备选。❌ 首选本地库 get_local_admin_boundary。
- `apply_layer_filter`：✅ 实时筛选现有图层的可见要素，不产生新图层。❌ 需要导出新要素集或链式分析用 attribute_filter。
- `attribute_filter`：✅ 按属性筛出新的要素集用于后续分析/导出。❌ 只想临时改可见性用 apply_layer_filter。
- `spatial_aggregate`：✅ 统计每个多边形内的点数量/属性（POI 计数）。❌ 多边形内的栅格统计（人口/降雨/海拔）用 zonal_stats。
- `zonal_stats`：✅ 多边形/行政区内的栅格统计（总量/均值/占比）。❌ 点要素的计数聚合用 spatial_aggregate。

> 若某工具描述当前是单行字符串，改成括号包裹的多行拼接字符串（参照 `dissolve_layer`）。
> 不改任何 `tier` / `domains` / `param_descriptions` / `args_model` 参数。

- [ ] **Step 5: 验证工具注册无破坏**

Run: `pytest tests/test_tool_registry.py tests/test_spatial_tools.py -v`
Expected: PASS（schema 描述是纯文本，注册逻辑不受影响）

- [ ] **Step 6: 验证全部工具仍能正常加载**

Run: `python -c "from app.tools.registry import ToolRegistry; from app import tools; print('tools import OK')"`
Expected: 打印 `tools import OK`（若 tools 包有统一注册入口，按实际入口调整此命令）

- [ ] **Step 7: Commit**

```bash
git add app/tools/
git commit -m "feat(tools): harden confusable tool descriptions with ✅/❌ disambiguation"
```

---

### Task 11: 全量回归与收尾

**Files:** 无（仅验证）

- [ ] **Step 1: 运行完整测试套件**

Run: `pytest tests/ -q`
Expected: 全部通过；新增的 `test_decision_log.py`、`test_planner.py`、`test_chat_context_builder.py`、`test_chat_engine_planning.py` 均在内

- [ ] **Step 2: 确认决策日志目录被 gitignore（避免提交日志数据）**

Run: `grep -q '^logs/' .gitignore && echo "logs already ignored" || echo "NEED to add logs/"`
Expected: `logs already ignored`；若输出 `NEED to add logs/`，向 `.gitignore` 追加一行 `logs/` 并 commit

- [ ] **Step 3: 最终 commit（如有 .gitignore 改动）**

```bash
git add .gitignore
git commit -m "chore: ignore tool_decisions.jsonl log output"
```

---

## 验收标准

- 复杂多步请求触发规划阶段，`logs/tool_decisions.jsonl` 出现 `from_plan: true` 记录。
- 短追问（"换个颜色"）在已有计划时跳过规划，不产生额外 LLM 调用。
- 规划 LLM 调用失败 / 输出不可解析时，对话照常进行（无计划路径）。
- 计划的 domain 使对应 tier 2 工具进入 schema 子集，即使关键词未命中。
- `[执行计划]` 块出现在请求上下文中，步骤随工具执行打勾。
- 所有既有测试通过——无计划路径行为与改动前完全一致。
