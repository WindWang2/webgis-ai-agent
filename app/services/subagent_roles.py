"""子代理角色库 + 层级预算（ADR-0101 Wave 7, §30/§32）。

此前 subagent 只有「任意 unrestricted 子代理」一个形态：调用方随手给
domains/extra_tools，max_rounds 是唯一预算。V2 引入**显式角色档**：

- 每个角色声明：模型角色（model_runtime role profile 键）、允许域、
  轮次/墙钟/工具调用/重工具预算、数据访问范围、是否允许状态突变工具；
- 角色 is 策略，不是新执行体 —— 执行仍由 SubagentDispatcher + 独立
  ChatEngine 完成（不变式：Pi/主引擎是宿主，子代理不是第二 agent 框架）；
- **突变工具过滤用描述符 side_effect 类**（Wave 1 的语义分类第一次落地到
  权限面）：allow_mutation=False 时 state_mutation/artifact_creation/
  external_side_effect/destructive 全部从子代理 schema 面剔除；
- **层级预算**（§32）：turn → agent → subagent → tools。SubagentBudget
  有界计数 + 超限显式失败（BudgetExceeded），绝不静默放宽。

隔离不变式（§31，全部既有 + 本模块强化）：
- 父 SessionPlan 仍是父真相（子代理 plan advancement 已被
  is_subagent_engine 抑制）；
- 子内部消息不进父对话（独立消息 LRU，既有）；
- 子产出的 ref 经差集**显式**回传（既有 refs diff）；
- 取消父→子传播（ADR-0100 令牌链接，既有）；
- 子工具执行与主路径同一 ToolRegistry 策略（既有，dispatch seam 不变）。
"""
from __future__ import annotations

import dataclasses
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class BudgetExceeded(BaseException):
    """子代理预算耗尽（工具调用/重工具/墙钟）。父循环可据此决策。

    继承 BaseException（review R1 CRITICAL）：镜像 OperationCancelled 的既有
    模式 —— 引擎/管道各层宽泛的 ``except Exception`` 兜底不得吞掉预算信号，
    否则显式预算失败退化为 no-progress 兜底。
    """


@dataclass(frozen=True)
class SubagentRole:
    """子代理角色档（全部字段显式 —— 无未约束子代理）。"""

    name: str
    title: str
    model_role: str                       # model_runtime.roles 键（routing 用）
    allowed_domains: Tuple[str, ...] = () # 空 = 不限域（调用方 domains 仍生效）
    max_rounds: int = 10
    max_wall_time_s: float = 300.0
    max_tool_calls: int = 40
    max_heavy_tool_calls: int = 8
    allow_mutation: bool = True           # False → 突变/工件/外部副作用工具全部剔除
    tool_blacklist: Tuple[str, ...] = ()
    # ADR-0104 决策 7（additive）：结构化输出契约 + 失败行为声明。
    # 声明式的：注入子代理任务头（[期望输出]），不改变 SubagentResult 形状。
    expected_outputs: Tuple[str, ...] = ()
    failure_behavior: str = "honest_failure_disclosed"

    #: expected_outputs 有界（V4「bounded everything」）
    _MAX_EXPECTED_OUTPUTS = 8
    _MAX_EXPECTED_OUTPUT_LEN = 120

    def __post_init__(self) -> None:
        if not isinstance(self.expected_outputs, tuple):
            object.__setattr__(self, "expected_outputs", tuple(self.expected_outputs))
        if len(self.expected_outputs) > self._MAX_EXPECTED_OUTPUTS:
            raise ValueError(
                f"角色 {self.name!r} expected_outputs 超过上限 "
                f"{self._MAX_EXPECTED_OUTPUTS} 条"
            )
        for item in self.expected_outputs:
            if not isinstance(item, str) or not item.strip():
                raise ValueError(
                    f"角色 {self.name!r} expected_outputs 必须是非空字符串"
                )
            if len(item) > self._MAX_EXPECTED_OUTPUT_LEN:
                raise ValueError(
                    f"角色 {self.name!r} expected_outputs 单条超过 "
                    f"{self._MAX_EXPECTED_OUTPUT_LEN} 字符上限"
                )
        if self.failure_behavior not in ROLE_FAILURE_BEHAVIORS:
            raise ValueError(
                f"角色 {self.name!r} failure_behavior {self.failure_behavior!r} "
                f"不在词汇表内；可用: {', '.join(ROLE_FAILURE_BEHAVIORS)}"
            )


#: 失败行为词汇表（bounded；ADR-0104 决策 7）。角色失败/预算耗尽/取消时，
#: 父循环可期待的披露语义：
#: - honest_failure_disclosed：失败原因如实写入 error/summary（默认）；
#: - fail_closed：契约外输出一律按失败处理，不降级、不部分披露；
#: - degrade_with_disclosure：可降级为部分结果，但必须披露未完成部分。
ROLE_FAILURE_BEHAVIORS: Tuple[str, ...] = (
    "honest_failure_disclosed",
    "fail_closed",
    "degrade_with_disclosure",
)


#: 内置角色库（§30 六类）。调用方可传自定义 SubagentRole（同等显式约束）。
SUBAGENT_ROLES: Dict[str, SubagentRole] = {
    # 数据/语料研究员：只查数，不改状态
    "data_researcher": SubagentRole(
        name="data_researcher",
        title="数据研究员：检索 POI/政区/OSM/数据集并汇报",
        model_role="subagent_worker",
        allowed_domains=("chinese", "osm", "dataset"),
        max_rounds=8,
        max_wall_time_s=240.0,
        max_tool_calls=30,
        max_heavy_tool_calls=2,
        allow_mutation=False,
    ),
    # GIS 数据巡检：读图读层做质检
    "gis_inspector": SubagentRole(
        name="gis_inspector",
        title="GIS 巡检员：检查图层/数据质量并汇报",
        model_role="subagent_worker",
        allowed_domains=("statistics", "core", "dataset"),
        max_rounds=8,
        max_tool_calls=30,
        allow_mutation=False,
    ),
    # 科学评审：纯读 + 无工具倾向（ reviewer 不该替主代理跑分析）
    "scientific_reviewer": SubagentRole(
        name="scientific_reviewer",
        title="科学评审：审查方法论与结果合理性",
        model_role="subagent_reviewer",
        max_rounds=4,
        max_tool_calls=8,
        max_heavy_tool_calls=0,
        allow_mutation=False,
    ),
    # 制图评审
    "cartography_reviewer": SubagentRole(
        name="cartography_reviewer",
        title="制图评审：审查地图产品与视觉表达",
        model_role="subagent_reviewer",
        max_rounds=4,
        max_tool_calls=8,
        max_heavy_tool_calls=0,
        allow_mutation=False,
    ),
    # 工具结果核验：可跑少量廉价工具验证主张
    "tool_result_verifier": SubagentRole(
        name="tool_result_verifier",
        title="结果核验员：复算关键数字/验证 ref 有效",
        model_role="subagent_reviewer",
        max_rounds=6,
        max_tool_calls=12,
        max_heavy_tool_calls=2,
        allow_mutation=False,
    ),
    # 廉价长上下文摘要：零工具
    "cheap_summarizer": SubagentRole(
        name="cheap_summarizer",
        title="摘要员：压缩长文本/历史为短摘要",
        model_role="structured_extraction",
        max_rounds=2,
        max_tool_calls=0,
        max_heavy_tool_calls=0,
        allow_mutation=False,
    ),
    # ------------------------------------------------------------------
    # ADR-0104 决策 7：专家团队角色（Wave 6）。全部只读（allow_mutation=False
    # → 复用描述符 side_effect fail-closed 过滤），约束相对调用方只收紧不放宽。
    # 既有六角色保持原义不变（兼容：test_role_library_complete_and_bounded 等）。
    # ------------------------------------------------------------------
    # 规划员：只读盘点能力/数据/算法面，产出分步计划文本（绝不触碰
    # SessionPlan —— propose_plan/execute_plan/get_plan_status 恒黑名单）
    "planner": SubagentRole(
        name="planner",
        title="规划员：只读盘点数据/能力/算法，产出分步执行计划文本",
        model_role="subagent_worker",
        allowed_domains=("dataset", "statistics", "raster", "network", "temporal", "osm", "chinese"),
        max_rounds=6,
        max_wall_time_s=180.0,
        max_tool_calls=16,
        max_heavy_tool_calls=2,
        allow_mutation=False,
        expected_outputs=(
            "step_plan",
            "capability_gaps",
            "data_requirements",
        ),
        failure_behavior="honest_failure_disclosed",
    ),
    # 廉价语料工人：批量只读检索/摘要（cheap_summarizer 的带工具变体 ——
    # 审计口径：语料工人需要廉价读工具，非零工具摘要员的复制）
    "corpus_worker": SubagentRole(
        name="corpus_worker",
        title="语料工人：廉价批量只读检索与分条摘要",
        model_role="corpus_worker",
        allowed_domains=("chinese", "osm", "dataset"),
        max_rounds=4,
        max_wall_time_s=120.0,
        max_tool_calls=10,
        max_heavy_tool_calls=0,
        allow_mutation=False,
        expected_outputs=(
            "corpus_digest",
            "source_refs",
        ),
        failure_behavior="degrade_with_disclosure",
    ),
    # 空间科学家：分析工具面（statistics/raster/temporal）+ 只读数据巡检；
    # 无 style/map 突变（mapspec 域不入 allowed_domains + 突变过滤双保险）
    "spatial_scientist": SubagentRole(
        name="spatial_scientist",
        title="空间科学家：只读数据巡检 + 统计/时空分析，产出证据化结论",
        model_role="subagent_worker",
        allowed_domains=("statistics", "raster", "temporal", "dataset", "network"),
        max_rounds=10,
        max_wall_time_s=300.0,
        max_tool_calls=24,
        max_heavy_tool_calls=4,
        allow_mutation=False,
        expected_outputs=(
            "analysis_summary",
            "evidence_refs",
            "assumptions_and_limits",
        ),
        failure_behavior="honest_failure_disclosed",
    ),
    # 算法评审：只读复核算法解析依据与证据链（AlgorithmDescriptor VNext
    # 证据面）；区别于 scientific_reviewer（无工具方法论评审）
    "algorithm_reviewer": SubagentRole(
        name="algorithm_reviewer",
        title="算法评审：复核算法解析依据与证据链，不改任何状态",
        model_role="scientific_review",
        allowed_domains=("statistics", "raster", "temporal", "dataset"),
        max_rounds=5,
        max_wall_time_s=180.0,
        max_tool_calls=10,
        max_heavy_tool_calls=1,
        allow_mutation=False,
        expected_outputs=(
            "algorithm_conformance_verdict",
            "resolver_evidence_gaps",
            "recommended_corrections",
        ),
        failure_behavior="fail_closed",
    ),
    # 地图观察员：只读观察面（webgis_cartography_status 等纯读工具），
    # 无破坏性动作；黑名单是描述符误分类时的第二道保险
    "map_observer": SubagentRole(
        name="map_observer",
        title="地图观察员：只读观察地图/渲染状态并出具观察报告",
        model_role="subagent_worker",
        allowed_domains=("report", "mapspec", "dataset"),
        tool_blacklist=(
            "webgis_map_intent",
            "webgis_layer_remove",
            "webgis_layout_set",
            "webgis_checkpoint",
            "webgis_rollback",
        ),
        max_rounds=4,
        max_wall_time_s=120.0,
        max_tool_calls=8,
        max_heavy_tool_calls=1,
        allow_mutation=False,
        expected_outputs=(
            "observation_report",
            "anomalies",
        ),
        failure_behavior="honest_failure_disclosed",
    ),
    # 完成核验员：对照完成门/证据链核验产出（区别于 tool_result_verifier
    # 的「复算关键数字」—— 本角色核验完成度与证据齐备性）
    "result_verifier": SubagentRole(
        name="result_verifier",
        title="完成核验员：对照完成门与证据链核验产出，不重跑分析",
        model_role="subagent_reviewer",
        allowed_domains=("report", "dataset", "statistics"),
        max_rounds=5,
        max_wall_time_s=120.0,
        max_tool_calls=8,
        max_heavy_tool_calls=1,
        allow_mutation=False,
        expected_outputs=(
            "completion_verdict",
            "missing_evidence",
        ),
        failure_behavior="fail_closed",
    ),
    # 文档交叉校验员：对照文档/规范核对主张，只读 + 零重工具
    # （model_role=doc_crosscheck → 既有的 cheap fallback 组 + tool_surface_v3
    # ROLE_SIDE_EFFECT_POLICY 既有条目）
    "doc_crosschecker": SubagentRole(
        name="doc_crosschecker",
        title="文档交叉校验员：对照文档/规范核对主张，只读",
        model_role="doc_crosscheck",
        allowed_domains=("dataset",),
        max_rounds=3,
        max_wall_time_s=90.0,
        max_tool_calls=6,
        max_heavy_tool_calls=0,
        allow_mutation=False,
        expected_outputs=(
            "crosscheck_report",
            "contradictions",
        ),
        failure_behavior="honest_failure_disclosed",
    ),
}


def validate_subagent_role_registry(
    registry: Optional[Dict[str, SubagentRole]] = None,
) -> None:
    """注册表自检（import 期 fail-fast，与 registry_validation 同方向）：

    - 每个条目的 key 与 role.name 一致（身份漂移 = 定义重复）；
    - 不存在「不同 key、除 name 外逐字段相同」的重复角色定义；
    - 新字段（expected_outputs/failure_behavior）已由 __post_init__ 校验。
    """
    reg = SUBAGENT_ROLES if registry is None else registry
    seen_payloads: Dict[Tuple[Any, ...], str] = {}
    for key, role in reg.items():
        if role.name != key:
            raise ValueError(
                f"子代理角色注册表身份不一致: key={key!r} vs role.name={role.name!r}"
            )
        payload = tuple(
            getattr(role, f.name)
            for f in dataclasses.fields(role)
            if f.name != "name"
        )
        duplicate_of = seen_payloads.get(payload)
        if duplicate_of is not None:
            raise ValueError(
                f"子代理角色注册表存在重复定义: {key!r} 与 {duplicate_of!r} 配置相同"
            )
        seen_payloads[payload] = key


validate_subagent_role_registry()


def get_subagent_role(name: str) -> SubagentRole:
    role = SUBAGENT_ROLES.get(name)
    if role is not None:
        return role
    raise ValueError(
        f"未知子代理角色 {name!r}；可用: {', '.join(sorted(SUBAGENT_ROLES))}（或传自定义 SubagentRole）"
    )


# ---------------------------------------------------------------------------
# 层级预算（§32）
# ---------------------------------------------------------------------------

@dataclass
class SubagentBudget:
    """单次子代理运行的预算计数器（线程内使用；asyncio 单线程模型）。"""

    max_tool_calls: int
    max_heavy_tool_calls: int
    max_wall_time_s: float
    _tool_calls: int = 0
    _heavy_calls: int = 0
    _started_at: float = field(default_factory=time.monotonic)

    def check_tool(self, *, is_heavy: bool) -> None:
        self._tool_calls += 1
        if self._tool_calls > self.max_tool_calls:
            raise BudgetExceeded(f"tool_calls {self._tool_calls} > {self.max_tool_calls}")
        if is_heavy:
            self._heavy_calls += 1
            if self._heavy_calls > self.max_heavy_tool_calls:
                raise BudgetExceeded(
                    f"heavy_tool_calls {self._heavy_calls} > {self.max_heavy_tool_calls}"
                )

    def check_wall_time(self) -> None:
        if time.monotonic() - self._started_at > self.max_wall_time_s:
            raise BudgetExceeded(
                f"wall_time > {self.max_wall_time_s}s"
            )

    def remaining_wall_time_s(self) -> float:
        """剩余墙钟预算（V4 §32 roll-up 用：子代理墙钟 = min(自身, 父剩余)）。"""
        return max(0.0, self.max_wall_time_s - (time.monotonic() - self._started_at))

    def usage(self) -> Dict[str, Any]:
        return {
            "tool_calls": self._tool_calls,
            "heavy_tool_calls": self._heavy_calls,
            "wall_time_s": round(time.monotonic() - self._started_at, 1),
        }


def wrap_dispatch_with_budget(dispatch_fn, budget: SubagentBudget, registry):
    """包装子引擎 dispatch（实例级），超预算抛 BudgetExceeded。

    返回 async 函数，签名与 ToolDispatchService.dispatch 一致
    (tc, session_id, executed_tools)。工具成本先验经 registry 元数据判定。
    """
    from app.tools.descriptor import SideEffectClass

    async def _budgeted(tc, session_id, executed_tools=None):
        budget.check_wall_time()
        func_info = tc.get("function", {}) if isinstance(tc, dict) else {}
        name = func_info.get("name") or ""
        try:
            desc = registry.descriptor(name)
            is_heavy = desc.cost == "heavy" or desc.side_effect in (
                SideEffectClass.DESTRUCTIVE, SideEffectClass.EXTERNAL_SIDE_EFFECT,
            )
        except KeyError:
            is_heavy = False
        budget.check_tool(is_heavy=is_heavy)
        return await dispatch_fn(tc, session_id, executed_tools)

    return _budgeted
