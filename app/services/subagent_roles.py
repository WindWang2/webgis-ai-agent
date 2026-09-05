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

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

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
}


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
