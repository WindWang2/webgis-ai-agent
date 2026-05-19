"""规划阶段：启发式门控 + 结构化规划 LLM 调用 + Plan 存储 + 步骤打勾。

设计原则：规划是增强，永不是硬依赖。任何环节失败都降级回无计划循环。
"""
from __future__ import annotations

import dataclasses
import json
import logging
import re

from app.services.tool_catalog import DOMAIN_KEYWORDS

logger = logging.getLogger(__name__)

# 追问词：短消息命中其一则视为承接上一轮的追问
_FOLLOWUP_PATTERN = re.compile(
    r"(换|再|又|放大|缩小|颜色|配色|隐藏|显示|去掉|删掉|清除|加粗|样式|"
    r"大一点|小一点|这个|那个|上面|刚才)"
)
_SHORT_THRESHOLD = 20  # 字符数

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
