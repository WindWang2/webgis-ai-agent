"""AgentPlanOrchestrator - 核心 Agent 计划编排与 Domain 状态同步引擎。

深入封装启发式门控、结构化 LLM 规划、LRU 计划内存缓存、
工具步骤匹配与 ToolCatalog 领域衰减。

design-v3 收敛（本切片）：
- CanonicalPlan（app/services/planning/）成为计划的持久化 source of truth；
  本模块的 ``Plan``/``PlanStep`` dataclass 退化为它的**投影**（compatibility
  projection），``get_plan``/``set_plan``/``clear_plan``/``make_plan``/
  ``advance_step``/``should_plan`` 保持原签名与返回类型。
- ``advance_step`` 移除 ``"core"`` 通配（R1）：只有真实 domain 重叠才打勾；
  使用与 tool_dispatch_service 一致的规范化工具名做 domain 查找（R6/R7）。
- ``parse_plan`` 逐字段防御（R4）：``n`` 强转、``tool_family`` 校验（非法 →
  None，步骤保留）、步骤数上限 MAX_PLAN_STEPS。
- ``should_plan`` 接受可选的 ``followup_kind``（followup.py 分类结果）：
  new_goal → 规划；style_change/ref_reuse/continuation + 活跃计划 → 跳过。
  不传时行为与旧版一致。
- 双衰减路径移除（R8）：``advance_step`` 不再调用
  ``tool_catalog.decay_sticky_domain``（该分支在生产路径从不触发——引擎
  从未传过 tool_catalog；TTL 衰减由 select_schemas 每轮自然进行）。
"""
import dataclasses
import json
import logging
import re
import uuid
from typing import Optional, List, Set

from app.services.chat.llm_client import LLMConfig, LRUCache
from app.services.tool_catalog import DOMAIN_KEYWORDS
from app.services.tool_dispatch_service import normalize_tool_name

from app.services.planning.models import (
    TERMINAL_STATUSES,
    CanonicalPlan,
    CanonicalStep,
    StepStatus,
)
from app.services.planning.store import plan_store
from app.services.planning.followup import FollowUpKind

logger = logging.getLogger(__name__)

# 追问词：短消息命中其一则视为承接上一轮的追问
_FOLLOWUP_PATTERN = re.compile(
    r"(换|再|又|放大|缩小|颜色|配色|隐藏|显示|去掉|删掉|清除|加粗|样式|"
    r"大一点|小一点|这个|那个|上面|刚才)"
)
_SHORT_THRESHOLD = 20  # 字符数

# 合法 domain 取值 = ToolCatalog 的主题键集合 + "core"（基础工具）
VALID_DOMAINS: Set[str] = set(DOMAIN_KEYWORDS) | {"core"}

# R4: 单计划步骤数硬上限（prompt 里的 5 步只是软约束，代码层强制 8）
MAX_PLAN_STEPS = 8

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
    tool_family: Optional[str]
    done: bool = False


@dataclasses.dataclass
class Plan:
    intent: str
    domains: List[str]
    steps: List[PlanStep]


def _coerce_step_n(raw: object, fallback: int) -> int:
    """R4: 把 LLM 输出的 ``n`` 稳健转成 int。None / "1.0" / "2" → int；
    无法解析 → fallback（该步骤在 steps 里的序号）。"""
    if isinstance(raw, bool):
        return fallback
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw) if raw.is_integer() else fallback
    if isinstance(raw, str):
        try:
            return int(float(raw.strip()))
        except (TypeError, ValueError):
            return fallback
    return fallback


def _validate_tool_family(raw: object, valid_families: Set[str]) -> Optional[str]:
    """R4: tool_family 必须在 (registry 声明的 domains ∪ VALID_DOMAINS) 内；
    非法 → None（步骤保留，仅失去打勾能力）。"""
    if not isinstance(raw, str) or not raw.strip():
        return None
    fam = raw.strip()
    return fam if fam in valid_families else None


def _registry_domains(registry: object) -> Set[str]:
    """收集 registry 里所有工具声明的 domains（用于 parse_plan 校验）。"""
    if registry is None:
        return set()
    domains: Set[str] = set()
    try:
        for meta in registry.all_metadata().values():
            domains.update(meta.get("domains", []))
    except Exception as e:  # noqa: BLE001 防御式：registry 异常不阻断规划
        logger.warning(f"[plan_orchestrator] 读取 registry domains 失败: {e}")
    return domains


def _get_registry() -> object:
    """按仓库既有模式拿全局 ToolRegistry（agent_pi_bridge 注入）；未初始化 → None。"""
    try:
        from app.agent_pi_bridge import get_tool_registry
        return get_tool_registry()
    except Exception:  # noqa: BLE001 — 测试/独立环境未注入 registry 时降级
        return None


def parse_plan(raw: str, registry: object = None) -> Optional[Plan]:
    """防御式解析规划 LLM 的 JSON 输出（R4：逐字段容错，绝不 raise）。"""
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
    valid_domains = set(VALID_DOMAINS) | _registry_domains(registry)
    domains = [d for d in domains if isinstance(d, str) and d in valid_domains]
    valid_families = valid_domains
    steps: List[PlanStep] = []
    for i, s in enumerate(steps_raw[:MAX_PLAN_STEPS], start=1):
        if not isinstance(s, dict):
            continue  # 坏步骤跳过，不报废整个计划
        steps.append(PlanStep(
            n=_coerce_step_n(s.get("n"), i),
            goal=str(s.get("goal", "")),
            tool_family=_validate_tool_family(s.get("tool_family", "core"), valid_families),
        ))
    return Plan(intent=intent.strip(), domains=domains, steps=steps)


def should_plan(
    message: str,
    messages: List[dict],
    has_active_plan: bool,
    followup_kind: Optional[FollowUpKind] = None,
) -> bool:
    """启发式门控判断。

    ``followup_kind``（design-v3 §followup）显式传入时优先：
    - new_goal               → 必须规划（含"换成查路线"这类短消息换目标）
    - style_change / ref_reuse / continuation + 有活跃计划 → 跳过
    - 其余（unclear / 无活跃计划）→ 回落到旧的长度 + 关键词启发式。
    ``followup_kind`` 为 None 时行为与旧版完全一致（向后兼容）。
    """
    text = (message or "").strip()
    if followup_kind is not None:
        if followup_kind == FollowUpKind.new_goal:
            return True
        if has_active_plan and followup_kind in (
            FollowUpKind.style_change,
            FollowUpKind.ref_reuse,
            FollowUpKind.continuation,
        ):
            return False
        # unclear，或 kind 有值但无活跃计划 → 走旧启发式
    is_short = len(text) <= _SHORT_THRESHOLD
    is_followup = bool(_FOLLOWUP_PATTERN.search(text))
    if is_short and is_followup and has_active_plan:
        return False
    return True


def render_plan_block(plan) -> str:
    """把 Plan 投影渲染成 [执行计划] 系统块（单一渲染来源，design-v3 §4）。

    context_assembler / context_builder 都走这里，避免重复的 plan 状态格式化。
    """
    lines = [
        "[执行计划] — 你为本任务制定的步骤，按此推进，完成一步即视为打勾",
        f"- 意图: {plan.intent}",
    ]
    if getattr(plan, "steps", None):
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


class AgentPlanOrchestrator:
    """Agent 计划编排与状态引擎（canonical 持久化 + 投影缓存）。"""

    def __init__(self, capacity: int = 200):
        # 投影缓存：get/set 返回的就是这里存的对象（身份语义不变）。
        self._plans: LRUCache = LRUCache(capacity=capacity)
        # canonical 副本：与 plan_store 的写穿缓存共享同一对象（原地修改即可见）。
        self._canonical: LRUCache = LRUCache(capacity=capacity)

    # ── 投影 <-> canonical 转换 ──────────────────────────────────────────

    def _to_projection(self, canon: CanonicalPlan) -> Plan:
        """CanonicalPlan → Plan 投影（done = canonical step 已 completed）。"""
        return Plan(
            intent=canon.intent,
            domains=list(canon.domains),
            steps=[
                PlanStep(
                    n=s.n,
                    goal=s.goal,
                    tool_family=s.tool_family,
                    done=s.status == StepStatus.completed,
                )
                for s in canon.steps
            ],
        )

    def _canonical_from_projection(self, plan: Plan, session_id: str) -> CanonicalPlan:
        """Plan 投影 → 新 CanonicalPlan（新计划：新 plan_id、status=proposed）。"""
        plan_id = f"plan-orch-{uuid.uuid4().hex[:12]}"
        steps = [
            CanonicalStep(
                id=f"s{i}",
                n=s.n,
                goal=s.goal,
                tool_family=s.tool_family,
                status=StepStatus.completed if s.done else StepStatus.pending,
            )
            for i, s in enumerate(plan.steps, start=1)
        ]
        return CanonicalPlan(
            plan_id=plan_id,
            session_id=session_id,
            intent=plan.intent,
            domains=list(plan.domains),
            steps=steps,
        )

    # ── 公共 API（签名不变） ─────────────────────────────────────────────

    def get_plan(self, session_id: str) -> Optional[Plan]:
        """返回投影；LRU 未命中时尝试从 plan_store 缓存同步恢复（R5/R10）。

        恢复出的计划若已是终态（completed/failed/cancelled/superseded），
        视为无活跃计划。真正的跨进程/重启恢复走 ``restore_plan``（异步读 store）。
        """
        plan = self._plans.get(session_id)
        if plan is not None:
            return plan
        canon = plan_store.peek(session_id)
        if canon is None:
            return None
        if canon.status in TERMINAL_STATUSES:
            return None
        proj = self._to_projection(canon)
        self._plans[session_id] = proj
        self._canonical[session_id] = canon
        return proj

    async def restore_plan(self, session_id: str) -> Optional[Plan]:
        """从 plan_store 恢复当前计划（进程重启续接，R5/R10）。

        已在本进程 LRU 中则直接返回（不重建投影，保持身份）。恢复出的
        终态计划视为无活跃计划。
        """
        cached = self._plans.get(session_id)
        if cached is not None:
            return cached
        canon = await plan_store.load_current(session_id)
        if canon is None:
            return None
        self._canonical[session_id] = canon
        if canon.status in TERMINAL_STATUSES:
            return None
        proj = self._to_projection(canon)
        self._plans[session_id] = proj
        return proj

    async def flush(self, session_id: str) -> None:
        """把本进程 canonical 计划（含步骤 done 状态）持久化到 plan_store。"""
        canon = self._canonical.get(session_id)
        if canon is None:
            return
        canon.status = canon.recompute_status()
        await plan_store.save(canon)

    def set_plan(self, session_id: str, plan: Plan) -> None:
        self._plans[session_id] = plan
        canon = self._canonical_from_projection(plan, session_id)
        self._canonical[session_id] = canon
        plan_store.cache_put(canon)

    def clear_plan(self, session_id: str) -> None:
        if session_id in self._plans:
            del self._plans[session_id]
        if session_id in self._canonical:
            del self._canonical[session_id]
        plan_store.forget(session_id)

    async def make_plan(
        self,
        cfg: LLMConfig,
        session_id: str,
        user_message: str,
        env_summary: str,
    ) -> Optional[Plan]:
        """跑一次规划 LLM 调用，解析、能力校验并存储计划。

        新计划替换旧的非终态计划时，旧 canonical 会被 ``plan_store.supersede``
        标记 superseded（design-v3：换目标绝不静默覆盖）。
        """
        registry = _get_registry()
        try:
            from app.services.chat import planner
            resp = await planner.call_llm(cfg, _planning_messages(user_message, env_summary))
            choice = resp.get("choices", [{}])[0]
            msg = choice.get("message", {})
            raw = msg.get("content") or msg.get("reasoning_content") or ""
        except Exception as e:
            logger.warning(f"[plan_orchestrator] make_plan LLM 调用失败: {e}")
            return None
        plan = parse_plan(raw, registry=registry)
        if plan is None:
            logger.info(f"[plan_orchestrator] session={session_id} 计划解析失败，降级无计划")
            return None
        self._apply_capability_validation(plan, registry)

        prev = self._canonical.get(session_id)
        canon = self._canonical_from_projection(plan, session_id)
        if (
            prev is not None
            and prev.status not in TERMINAL_STATUSES
            and prev.plan_id != canon.plan_id
        ):
            # design-v3：换目标 → 旧计划 superseded，绝不静默覆盖。
            await plan_store.supersede(session_id, canon)
        else:
            await plan_store.save(canon)
        self.set_plan(session_id, plan)
        logger.info(
            f"[plan_orchestrator] session={session_id} 计划已生成: "
            f"intent={plan.intent!r} domains={plan.domains} steps={len(plan.steps)}"
        )
        return plan

    def _apply_capability_validation(self, plan: Plan, registry: object) -> None:
        """design-v3 §capability：能力校验只记日志，不阻断规划（warning 不阻塞）。

        tool_family 无任何已注册工具支撑的步骤 → tool_family=None（步骤保留，
        失去打勾能力），避免该步骤永远无法完成的死步骤。
        """
        if registry is None:
            return
        try:
            from app.services.planning.capability import validate_plan_capabilities
            from app.services.planning.models import (
                CanonicalPlan as _CP,
                CanonicalStep as _CS,
            )
            temp = _CP(
                plan_id="tmp",
                session_id="",
                intent=plan.intent,
                domains=list(plan.domains),
                steps=[
                    _CS(id=f"s{i}", n=s.n, goal=s.goal, tool_family=s.tool_family)
                    for i, s in enumerate(plan.steps, start=1)
                ],
            )
            issues = validate_plan_capabilities(temp, registry)
            for issue in issues:
                logger.warning(f"[plan_orchestrator] 能力校验: {issue}")
            family_domains: Set[str] = set()
            for meta in registry.all_metadata().values():
                family_domains.update(meta.get("domains", []))
            for step in plan.steps:
                if (
                    step.tool_family
                    and step.tool_family != "core"
                    and step.tool_family not in family_domains
                ):
                    logger.warning(
                        f"[plan_orchestrator] step n={step.n} tool_family "
                        f"{step.tool_family!r} 无已注册工具，置 None"
                    )
                    step.tool_family = None
        except Exception as e:  # noqa: BLE001 能力校验失败不阻断规划
            logger.warning(f"[plan_orchestrator] 能力校验失败: {e}")

    async def orchestrate_plan(
        self,
        cfg: LLMConfig,
        session_id: str,
        user_message: str,
        messages: List[dict],
        env_summary: str,
        followup_kind: Optional[FollowUpKind] = None,
    ) -> Optional[Plan]:
        """门控 + 规划编排入口（followup_kind 透传给 should_plan，design-v3 §followup）。"""
        has_plan = self.get_plan(session_id) is not None
        if not should_plan(user_message, messages, has_plan, followup_kind=followup_kind):
            return None
        from app.services.chat import planner
        return await planner.make_plan(cfg, session_id, user_message, env_summary)

    def advance_step(self, session_id: str, tool_name: str, registry, tool_catalog=None) -> Optional[int]:
        """把工具调用匹配到第一个未完成的计划步骤并打勾（R1/R6/R7）。

        - 移除 ``"core"`` 通配：只有调用工具声明 domain 与步骤 tool_family
          真实重叠才打勾（R1，G-report 实测未知工具/样式工具误打勾）。
        - 使用规范化工具名做 domain 查找（R7：遗留名如 set_layer_style 也能匹配）。
        - 不再调用 ``tool_catalog.decay_sticky_domain``（R8 死代码分支；TTL 衰减
          由 select_schemas 每轮自然进行）。``tool_catalog`` 参数保留仅为签名兼容。
        """
        plan = self.get_plan(session_id)
        if plan is None:
            return None
        canon = self._canonical.get(session_id)
        norm = normalize_tool_name(tool_name)
        try:
            tool_domains = set(registry.metadata(norm).get("domains", []))
        except Exception:  # noqa: BLE001 未知工具/registry 异常 → 无 domain → 不打勾
            tool_domains = set()
        for idx, step in enumerate(plan.steps):
            if step.done:
                continue
            if step.tool_family in tool_domains:
                step.done = True
                if canon is not None and idx < len(canon.steps):
                    canon.steps[idx].status = StepStatus.completed
                return step.n
        return None


plan_orchestrator = AgentPlanOrchestrator()
