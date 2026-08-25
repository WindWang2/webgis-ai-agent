"""AgentPlanOrchestrator - 核心 Agent 计划编排与 Domain 状态同步引擎。

深入封装启发式门控、结构化 LLM 规划、LRU 计划内存缓存、
工具步骤匹配与 ToolCatalog 领域衰减。

design-v3 收敛（本切片）：
- CanonicalPlan（app/services/planning/）成为计划的持久化 source of truth；
  本模块的 ``Plan``/``PlanStep`` dataclass 退化为它的**投影**（compatibility
  projection），``get_plan``/``set_plan``/``clear_plan``/``make_plan``/
  ``advance_step``/``should_plan`` 保持原签名与返回类型。
- ``advance_step`` 的 ``"core"`` 通配改为**限定通配**（P1-A）：core 步骤只在
  调用工具已注册且非展示类（PRESENTATION_TOOLS：样式/视图/交互工具）时打勾，
  或工具的声明 domains 与 plan.domains 有交集；使用与 tool_dispatch_service
  一致的规范化工具名做 domain 查找（R6/R7）。幻觉/未注册工具与样式工具绝不打勾。
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

# H-1（#856）确定性短路之一 —— 最简门：极短、无任何域关键词、followup 分类
# 为 unclear 的消息（寒暄/打招呼）不值得一次规划 LLM 调用。跳过的只是"规划"，
# 回合本身照常执行（与 followup 跳过规划同语义）。
_MINIMAL_GATE_MAX_CHARS = 12

# H-1（#856）确定性短路之二 —— harness 合成置信度阈值：intent resolver 命中
# 显式任务规则（非 fallback）且置信度达标、recipe 候选非空时，直接由
# MapProductPlanner 合成 Plan，规划阶段 0 次 LLM 调用。
_HARNESS_SYNTH_MIN_CONFIDENCE = 0.65

# H-1：intent.task → 规划 domains 提示（与 RecipeRegistry 任务族对齐；
# detect_domains 的关键词命中会再取并集，这里只保证任务族自身的主域不缺）。
_TASK_DOMAIN_HINTS: dict = {
    "distribution_overview": ["statistics"],
    "simple_view": [],
    "administrative_statistic": ["statistics", "chinese"],
    "analytical_density": ["statistics"],
    "concentration_analysis": ["statistics"],
    "categorical_distribution": ["statistics"],
    "proximity_analysis": ["network", "statistics"],
    "accessibility_analysis": ["network"],
    "raster_distribution": ["raster"],
    "change_detection": ["temporal"],
}

# P1-A（R1-qualified）：core 通配的**展示类工具排除集**。
# 生产注册表里 0/149 个工具声明 domain "core"（149 个里有 86 个 tier-1 分析
# 工具干脆不声明任何 domain），而规划 prompt 让 LLM 对最常见的步骤发
# tool_family:"core"——所以 core 步骤必须用「已注册 ∧ 非展示类」的限定通配
# 打勾，否则样式/视图/交互类工具（webgis_layer_upsert、缩放/飞行/视角工具、
# 样式设置器…）会把用户"换个颜色"这类纯展示操作误打成分析步骤。
# 本集合从实际展示工具清单手工整理（app/tools/map_view.py、layer_manager.py、
# cartography.py、cartography_tools.py），保持显式、小、可审计。
PRESENTATION_TOOLS: frozenset[str] = frozenset({
    # 视图 / 相机控制（map_view.py + webgis_view_set）
    "fly_to_location", "zoom_to_bbox", "zoom_to_layer", "reset_map_view", "set_map_view",
    "webgis_view_set",
    # 图层展示 / 样式 / 交互（layer_manager.py）
    "alias_layer", "inventory_layers", "switch_base_layer", "set_layer_status",
    "update_layer_appearance", "reorder_layer", "remove_layer",
    "apply_layer_filter", "display_layer",
    # MapSpec 制图生命周期 / 版面 / 编译（cartography_tools.py + templates.py）
    "webgis_layer_upsert", "webgis_layer_remove", "webgis_layout_set",
    "webgis_project_init", "webgis_state_get", "webgis_source_profile",
    "webgis_validate", "webgis_compile_maplibre", "webgis_checkpoint",
    "webgis_rollback", "webgis_runtime_validate", "webgis_map_combine",
    # 成果导出（cartography.py）
    "export_thematic_map", "export_batch_maps",
})

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

__DOMAIN_LIST__

规划原则：
- 意图先行。分布类请求先经 webgis_map_intent 拿结构化意图与 CartographyRecipe
  （『每平方公里密度』=定量分析，『各区数量』=行政聚合+choropleth 而非热力图），
  数据回来后用 webgis_map_product 复检资格并组装完整产品。
- 由简入深。宽泛请求（如"分布情况"）优先安排原生热力图等轻量步骤。
  但点数 <10（HEATMAP_MIN_POINTS）或几何以线/面为主时禁止原生热力图，
  改为点图或先聚合（h3_binning）——执行侧有同阈值确定性拦截。
- 步骤控制在 5 步以内，每步聚焦一个明确产出。
- 简单请求可以只有 1 步。"""

# #720: 域清单单一来源 —— 从 ToolCatalog.DOMAIN_KEYWORDS 派生，杜绝
# planner prompt / catalog / capability 三处词汇漂移（temporal、dataset
# 曾在 prompt 中缺失，LLM 永远无法声明）。
from app.services.tool_catalog import DOMAIN_KEYWORDS as _DOMAIN_KEYWORDS

_DOMAIN_DESCRIPTIONS: dict[str, str] = {
    "chinese": "中国行政区划 / 中文地址 / 国内 POI（高德、天地图、本地矢量库）",
    "osm": "OpenStreetMap / Overpass 全球数据",
    "raster": "遥感 / 栅格 / 地形 / 植被指数",
    "network": "路径 / 可达性 / 服务区 / 等时圈",
    "temporal": "时间维度 / 时空分析 / 动态演变",
    "statistics": "热点 / 聚类 / 密度 / 插值 / 空间统计 / 行政计数",
    "mapspec": "图层增删改 / 版面布局（desired MapSpec 变更）",
    "report": "报告 / 导出 / 制图成果",
    "dataset": "数据集 / 数据源 / 上传 / 编目",
    "what_if": "情景模拟推演",
    "meta": "创建技能 / 自定义工具",
}


def _compose_domain_list() -> str:
    lines = [
        "合法的领域取值（domains 与 tool_family 都只能用这些；本清单由",
        "ToolCatalog.DOMAIN_KEYWORDS 单一来源派生——新增域请在那里注册）：",
        "- core      基础空间分析与图层管理（缓冲、裁剪、过滤、制图等）",
    ]
    for _k in _DOMAIN_KEYWORDS:
        lines.append(f"- {_k:<11}{_DOMAIN_DESCRIPTIONS.get(_k, '')}")
    return "\n".join(lines) + "\n"


PLANNER_PROMPT = PLANNER_PROMPT.replace("__DOMAIN_LIST__", _compose_domain_list())



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
    # #994: 精确打勾绑定 —— harness 合成计划时把该步骤 capability 裁决出的
    # 工具名集合（AnalysisStep.resolved_tool 或 capability 候选工具集）写入；
    # advance_step 对带绑定的步骤只认 binding 内的工具，无绑定才回退通配。
    tool_binding: Optional[List[str]] = None


@dataclasses.dataclass
class Plan:
    intent: str
    domains: List[str]
    steps: List[PlanStep]
    # GIS Harness 结构化意图（MapRequestIntent.model_dump，additive）：
    # 确定性 resolver 产物，供渲染/审计/后续产品组装消费；LLM 意图不变。
    gis_intent: Optional[dict] = None
    recipe_id: str = ""


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
    if not steps:
        # P2-4（adversarial P2-7 zombie plan）：解析出的步骤列表为空
        # （steps:[] 或全部步骤非法被跳过）→ 视为无计划，绝不返回
        # 一个"活着的空计划"卡住 should_plan/get_plan 状态机。
        logger.info(f"[plan_orchestrator] 计划 JSON 步骤列表为空，降级无计划: {text[:200]}")
        return None
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
                    # #994: binding must survive projection round-trips,
                    # otherwise a reloaded plan silently degrades to wildcard.
                    tool_binding=list(s.tool_binding) if s.tool_binding else None,
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
                tool_binding=list(s.tool_binding) if s.tool_binding else None,
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

        P2-5：**热缓存路径同样做终态过滤**——本轮 flush 把 canonical 算成
        completed 后，下轮 get_plan 不得再把它当活跃计划返回（进程内保持
        "已完成即失效"，与 restore 的冷路径一致）。
        """
        plan = self._plans.get(session_id)
        if plan is not None:
            canon = self._canonical.get(session_id)
            if canon is not None and canon.status in TERMINAL_STATUSES:
                self.clear_plan(session_id)
                return None
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
        终态计划视为无活跃计划（热缓存路径与 get_plan 一样做终态过滤，
        见 P2-5）。
        """
        cached = self._plans.get(session_id)
        if cached is not None:
            canon = self._canonical.get(session_id)
            if canon is not None and canon.status in TERMINAL_STATUSES:
                self.clear_plan(session_id)
                return None
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
        """把本进程 canonical 计划（含步骤 done 状态）持久化到 plan_store。

        P1-B(4)：flush 前 bump revision —— canonical 被 advance_step 打勾 /
        recompute_status 改状态，revision 必须真实递增，否则 tool_metrics 里
        plan_revision 恒为 1，跨 worker revision guard 也失去意义。

        P3 #5（partially_completed 语义）：save 前先 ``recompute_status()``——
        步骤打勾后 canonical 状态永远由步骤状态推导，不靠调用方手动设置。
        部分成功（部分步骤完成、其余 failed/skipped 且无 pending/running）推导
        为 ``partially_completed``：它是**非终态**（TERMINAL_STATUSES 不含它），
        ``get_plan`` / ``restore_plan`` 不过滤它，重启后仍作为活跃计划恢复
        （可继续推进剩余步骤）——与 plan_mode 的可恢复部分完成语义一致。
        """
        canon = self._canonical.get(session_id)
        if canon is None:
            return
        canon.status = canon.recompute_status()
        canon.bump_revision()
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

    async def _persist_new_plan(self, session_id: str, plan: Plan) -> None:
        """新计划入库（supersede 旧非终态计划，绝不静默覆盖）。

        make_plan（LLM 规划）与 _synth_plan_from_harness（确定性合成）共用。
        """
        prev = self._canonical.get(session_id)
        if prev is None:
            # P1-B(3)：进程 _canonical 缓存未命中（evicted / 重启后的新 worker）
            # 时，supersede 判定必须回落到 store 的当前计划——否则一个刚
            # 恢复出旧计划/无缓存的 worker 会静默 overwrite 掉更新计划，
            # 而不是把它 supersede。
            try:
                prev = await plan_store.load_current(session_id)
            except Exception as e:  # noqa: BLE001 store 读失败不阻断规划
                logger.warning(f"[plan_orchestrator] 读取 store 当前计划失败: {e}")
                prev = None
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

    def _minimal_chat_gate(
        self, message: str, followup_kind: Optional[FollowUpKind]
    ) -> bool:
        """H-1 最简门：寒暄类消息不值得一次规划 LLM 调用。

        条件（全部满足才拦）：followup 分类为 unclear（或未分类）、消息
        极短、无任何域关键词。命中任一域关键词（如"成都小学分布"命中
        chinese/osm/statistics）即放行——真正的 GIS 请求不会走到这里。
        """
        text = (message or "").strip()
        if not text or len(text) > _MINIMAL_GATE_MAX_CHARS:
            return False
        if followup_kind is not None and followup_kind != FollowUpKind.unclear:
            return False
        try:
            from app.services.tool_catalog import ToolCatalog
            return not ToolCatalog.detect_domains(text)
        except Exception:  # noqa: BLE001 关键词检测失败 → 不拦，走原路径
            return False

    async def _synth_plan_from_harness(
        self, session_id: str, user_message: str
    ) -> Optional[Plan]:
        """H-1 harness 确定性合成：高置信 intent + 唯一候选 recipe 时零 LLM 规划。

        合成条件（保守，三者缺一即回落 LLM 规划）：
        - intent 命中显式任务规则（matched_rules[0] 非 fallback）；
        - confidence ≥ _HARNESS_SYNTH_MIN_CONFIDENCE（识别出主体/范围）；
        - RecipeRegistry.select_candidates 非空。
        合成的步骤 goal 取 MapProductPlanner 的能力 purpose 文案，tool_family
        统一为 "core"（P1-A 限定通配：已注册非展示类工具即可打勾）。
        #994: 步骤同时携带 tool_binding（步骤 capability 裁决出的工具名集合，
        优先 AnalysisStep.resolved_tool，回落 capability 候选工具集，且只留
        当前注册表里真实存在的工具）——advance_step 对绑定步骤精确打勾，
        未解析到任何候选的步骤保持 core 通配。
        """
        try:
            from app.services.gis_harness import (
                MapProductPlanner,
                resolve_map_request_intent,
            )
            from app.services.gis_harness.planner import capability_tool_map
        except Exception:  # noqa: BLE001 harness 不可用 → 回落 LLM 规划
            return None
        try:
            intent = resolve_map_request_intent(user_message)
            matched = list(getattr(intent, "matched_rules", []) or [])
            if not matched or matched[0] == "fallback_distribution_default":
                return None
            if float(getattr(intent, "confidence", 0.0) or 0.0) < _HARNESS_SYNTH_MIN_CONFIDENCE:
                return None
            gplanner = MapProductPlanner()
            candidates = gplanner.recipes.select_candidates(intent)
            if not candidates:
                return None
            recipe = candidates[0]
            registry = _get_registry()
            try:
                available = set(registry.list_tools()) if registry is not None else None
            except Exception:  # noqa: BLE001
                available = None
            product = gplanner.plan_from_intent(
                intent, recipe_id=recipe.id, available_tools=available,
            )
        except Exception as e:  # noqa: BLE001 合成失败 → 回落 LLM 规划
            logger.info(f"[plan_orchestrator] harness 确定性合成失败，回落 LLM 规划: {e}")
            return None

        domains: set = set(_TASK_DOMAIN_HINTS.get(intent.task, []))
        try:
            from app.services.tool_catalog import ToolCatalog
            domains |= ToolCatalog.detect_domains(user_message)
        except Exception:  # noqa: BLE001
            pass
        valid_domains = sorted(d for d in domains if d in VALID_DOMAINS)

        def _binding_for(s) -> Optional[List[str]]:
            # #994: 优先取 planner 裁决出的单工具；未裁决时回落 capability
            # 的完整候选工具集。过滤到注册表现存工具，保证 advance_step 的
            # 精确匹配真实可达；无任何候选 → None（该步骤保持通配）。
            candidates: List[str] = (
                [s.resolved_tool] if s.resolved_tool
                else list(capability_tool_map().get(s.capability, []))
            )
            if available is not None:
                candidates = [t for t in candidates if t in available]
            return candidates or None

        steps = [
            PlanStep(
                n=i,
                goal=(s.purpose or s.capability),
                tool_family="core",
                tool_binding=_binding_for(s),
            )
            for i, s in enumerate(
                (s for s in product.analysis_steps if s.status != "unavailable"),
                start=1,
            )
        ]
        if not steps:
            return None
        plan = Plan(
            intent=(user_message or "").strip()[:120],
            domains=valid_domains or ["statistics"],
            steps=steps,
        )
        plan.gis_intent = intent.model_dump()
        plan.recipe_id = recipe.id
        self._apply_capability_validation(plan, registry)
        await self._persist_new_plan(session_id, plan)
        logger.info(
            f"[plan_orchestrator] session={session_id} harness 确定性合成计划"
            f"（规划 0 次 LLM 调用）: recipe={recipe.id} steps={len(steps)}"
        )
        return plan

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

        # GIS Harness（additive）：确定性 intent + 推荐 recipe 附着到计划。
        # LLM 规划不变；结构化意图供 plan_ready 事件/审计/产品组装消费。
        try:
            from app.services.gis_harness import (
                MapProductPlanner,
                resolve_map_request_intent,
            )
            gis_intent = resolve_map_request_intent(user_message)
            candidates = MapProductPlanner().recipes.select_candidates(gis_intent)
            plan.gis_intent = gis_intent.model_dump()
            plan.recipe_id = candidates[0].id if candidates else ""
        except Exception as e:  # noqa: BLE001 - harness 附着失败不阻断规划
            logger.warning(f"[plan_orchestrator] gis intent attach failed: {e}")

        await self._persist_new_plan(session_id, plan)
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
        """门控 + 规划编排入口（followup_kind 透传给 should_plan，design-v3 §followup）。

        H-1（#856）两级确定性短路，LLM 只兜底：
        1. 最简门 —— 寒暄类消息（短 + 无域关键词 + unclear）直接不规划；
        2. harness 合成 —— 高置信 intent + 候选 recipe 时由 MapProductPlanner
           确定性合成计划（0 次 LLM 调用），gis_intent/recipe_id 照常附着。
        """
        has_plan = self.get_plan(session_id) is not None
        if not should_plan(user_message, messages, has_plan, followup_kind=followup_kind):
            return None
        if self._minimal_chat_gate(user_message, followup_kind):
            logger.info(
                f"[plan_orchestrator] session={session_id} 最简门命中"
                f"（寒暄/短消息无域关键词），跳过规划 LLM 调用"
            )
            return None
        synth = await self._synth_plan_from_harness(session_id, user_message)
        if synth is not None:
            return synth
        from app.services.chat import planner
        return await planner.make_plan(cfg, session_id, user_message, env_summary)

    def advance_step(self, session_id: str, tool_name: str, registry, tool_catalog=None) -> Optional[int]:
        """把工具调用匹配到第一个未完成的计划步骤并打勾（R1/R6/R7 + P1-A + #994）。

        - **绑定步骤（#994 精确匹配）**：harness 合成计划携带 tool_binding
          （capability 裁决出的工具名集合）——只有完成的工具名 ∈ binding 才
          打勾；不匹配绝不落入通配/domain 重叠（此前 3 步计划可被 3 个毫不
          相关的分析调用依次"完成"）。
        - 非 core 无绑定步骤：只有调用工具声明 domain 与步骤 tool_family 真实重叠
          才打勾（R1，G-report 实测未知工具/样式工具误打勾）。
        - **core 步骤（限定通配，P1-A）**：生产注册表没有任何工具声明
          domain "core"，而 planner prompt 对最常见步骤发 tool_family:"core"，
          纯 domain 重叠会让 core 步骤永远无法打勾。因此 core 步骤按
          「工具已注册（先规范化名称）∧ 不在 PRESENTATION_TOOLS 展示类排除集」
          打勾——成功调用的分析工具（buffer_analysis / clip_layer…）打勾，
          样式/视图/交互工具与幻觉工具名不打勾；另加一层安全网：工具的
          声明 domains 与 plan.domains 有交集时也打勾。
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
        try:
            registered = norm in set(registry.list_tools())
        except Exception:  # noqa: BLE001 registry 异常 → 视为未注册
            registered = False
        plan_domains = set(plan.domains)
        for idx, step in enumerate(plan.steps):
            if step.done:
                continue
            if step.tool_binding:
                # #994: 绑定步骤只认精确匹配——完成的工具名 ∈ binding 才打勾，
                # 不匹配时继续看下一个步骤（本步骤绝不落入下方通配分支）。
                if norm in step.tool_binding:
                    step.done = True
                    if canon is not None and idx < len(canon.steps):
                        canon.steps[idx].status = StepStatus.completed
                    return step.n
                continue
            if step.tool_family == "core":
                # P1-A 限定通配：core 步骤只按「已注册 ∧ 非展示类」打勾，或
                # 工具的声明 domains 与 plan.domains 有交集（额外安全网）。
                # core 步骤**永远不走**裸 domain 重叠分支——否则一个谎称
                # core domain 的展示工具仍能误打勾。
                if (registered and norm not in PRESENTATION_TOOLS) or (
                    tool_domains & plan_domains
                ):
                    step.done = True
                    if canon is not None and idx < len(canon.steps):
                        canon.steps[idx].status = StepStatus.completed
                    return step.n
            elif step.tool_family in tool_domains:
                step.done = True
                if canon is not None and idx < len(canon.steps):
                    canon.steps[idx].status = StepStatus.completed
                return step.n
        return None


plan_orchestrator = AgentPlanOrchestrator()
