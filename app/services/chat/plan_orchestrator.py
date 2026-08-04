"""AgentPlanOrchestrator - 核心 Agent 计划编排与 Domain 状态同步引擎。

深入封装启发式门控、结构化 LLM 规划、LRU 计划内存缓存、
工具步骤匹配与 ToolCatalog 领域衰减。
"""
import dataclasses
import json
import logging
import re
from typing import Optional, List, Set

from app.services.chat.llm_client import LLMConfig, LRUCache
from app.services.tool_catalog import DOMAIN_KEYWORDS

logger = logging.getLogger(__name__)

# 追问词：短消息命中其一则视为承接上一轮的追问
_FOLLOWUP_PATTERN = re.compile(
    r"(换|再|又|放大|缩小|颜色|配色|隐藏|显示|去掉|删掉|清除|加粗|样式|"
    r"大一点|小一点|这个|那个|上面|刚才)"
)
_SHORT_THRESHOLD = 20  # 字符数

# 合法 domain 取值 = ToolCatalog 的主题键集合 + "core"（基础工具）
VALID_DOMAINS: Set[str] = set(DOMAIN_KEYWORDS) | {"core"}

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


def _planning_messages(user_message: str, env_summary: str) -> List[dict]:
    return [
        {"role": "system", "content": PLANNER_PROMPT},
        {"role": "user", "content": f"{env_summary}\n\n用户请求：{user_message}"},
    ]


@dataclasses.dataclass
class PlanStep:
    n: int
    goal: str
    tool_family: str
    done: bool = False


@dataclasses.dataclass
class Plan:
    intent: str
    domains: List[str]
    steps: List[PlanStep]


def parse_plan(raw: str) -> Optional[Plan]:
    """防御式解析规划 LLM 的 JSON 输出"""
    if not raw or not raw.strip():
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        logger.warning(f"[plan_orchestrator] 计划 JSON 解析失败: {text[:200]}")
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
    domains = [d for d in domains if d in VALID_DOMAINS]
    steps: List[PlanStep] = []
    for i, s in enumerate(steps_raw, start=1):
        if not isinstance(s, dict):
            continue
        steps.append(PlanStep(
            n=int(s.get("n", i)),
            goal=str(s.get("goal", "")),
            tool_family=str(s.get("tool_family", "core")),
        ))
    return Plan(intent=intent.strip(), domains=domains, steps=steps)


def should_plan(message: str, messages: List[dict], has_active_plan: bool) -> bool:
    """启发式门控判断"""
    text = (message or "").strip()
    is_short = len(text) <= _SHORT_THRESHOLD
    is_followup = bool(_FOLLOWUP_PATTERN.search(text))
    if is_short and is_followup and has_active_plan:
        return False
    return True


class AgentPlanOrchestrator:
    """Agent 计划编排与状态引擎"""

    def __init__(self, capacity: int = 200):
        self._plans: LRUCache = LRUCache(capacity=capacity)

    def get_plan(self, session_id: str) -> Optional[Plan]:
        return self._plans.get(session_id)

    def set_plan(self, session_id: str, plan: Plan) -> None:
        self._plans[session_id] = plan

    def clear_plan(self, session_id: str) -> None:
        if session_id in self._plans:
            del self._plans[session_id]

    async def make_plan(
        self,
        cfg: LLMConfig,
        session_id: str,
        user_message: str,
        env_summary: str,
    ) -> Optional[Plan]:
        """跑一次规划 LLM 调用，解析并存储计划"""
        try:
            from app.services.chat import planner
            resp = await planner.call_llm(cfg, _planning_messages(user_message, env_summary))
            choice = resp.get("choices", [{}])[0]
            msg = choice.get("message", {})
            raw = msg.get("content") or msg.get("reasoning_content") or ""
        except Exception as e:
            logger.warning(f"[plan_orchestrator] make_plan LLM 调用失败: {e}")
            return None
        plan = parse_plan(raw)
        if plan is None:
            logger.info(f"[plan_orchestrator] session={session_id} 计划解析失败，降级无计划")
            return None
        self.set_plan(session_id, plan)
        logger.info(
            f"[plan_orchestrator] session={session_id} 计划已生成: "
            f"intent={plan.intent!r} domains={plan.domains} steps={len(plan.steps)}"
        )
        return plan

    async def orchestrate_plan(
        self,
        cfg: LLMConfig,
        session_id: str,
        user_message: str,
        messages: List[dict],
        env_summary: str,
    ) -> Optional[Plan]:
        """门控 + 规划编排入口"""
        has_plan = self.get_plan(session_id) is not None
        if not should_plan(user_message, messages, has_plan):
            return None
        from app.services.chat import planner
        return await planner.make_plan(cfg, session_id, user_message, env_summary)

    def advance_step(self, session_id: str, tool_name: str, registry, tool_catalog=None) -> Optional[int]:
        """把工具调用匹配到第一个未完成的计划步骤并打勾，并同步触发 ToolCatalog 领域衰减"""
        plan = self.get_plan(session_id)
        if plan is None:
            return None
        tool_domains = set(registry.metadata(tool_name).get("domains", []))
        for step in plan.steps:
            if step.done:
                continue
            if step.tool_family in tool_domains or step.tool_family == "core":
                step.done = True
                if tool_catalog is not None and hasattr(tool_catalog, "decay_sticky_domain"):
                    try:
                        tool_catalog.decay_sticky_domain(session_id)
                    except Exception as e:
                        logger.warning(f"[plan_orchestrator] domain decay failed: {e}")
                return step.n
        return None


plan_orchestrator = AgentPlanOrchestrator()
