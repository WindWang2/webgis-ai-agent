"""Capability-driven Tool Surface compiler（Runtime V4 / §27-30，ADR-0091）。

纯派生投影：**不是**第二个 planner / agent / 持久状态 —— 输入是本就驻留
内存的 SessionPlan（ADR-0076 单一计划真相）+ registry 元数据，输出本轮
工具面的偏好约束。同输入必同输出，零 IO、零持久化。

    compile_tool_surface(plan=..., product_status=...)
        → ToolSurface { phase, preferred_tools, allowed_domains,
                        fallback_tools, hidden_tools, evidence }

与 ToolCatalog 的分工：
- ToolCatalog 仍拥有选择权（tier + 关键词域 + sticky + 字节预算）；
- ToolSurface 只做**阶段感知的偏好修正**：
  1. preferred_tools —— 产品阶段的前门工具在预算截断时幸存
     （assembly 阶段的 webgis_map_product 此前可被同域字母序挤出）；
  2. allowed_domains —— 阶段把当前 plan 步骤的域并入激活面（与关键词
     检测取并集，绝不替换 —— 关键词是安全网）；
  3. hidden_tools —— v1 恒空：关键词命中的 tier-2 一律可见（把用户显式
     提到的工具藏起来只会制造弃用路径），tier-3 维持 list_available_tools
     显式自救语义。词面保留以支撑未来的策略收紧。

阶段模型（plan 步骤 tool_family → 产品阶段）：

    planning  无步骤族（计划刚成/意图阶段） → intent/inspection 前门
    data      dataset/chinese/osm 等取数域   → +data_access
    analysis  raster/network/statistics/…    → +analysis
    assembly  mapspec/report（产品组装）     → +rendering/mutation 前门
    final     全部步骤完成                   → +export/inspection
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Optional, Tuple

# 阶段常量（封闭词表）。
PHASE_PLANNING = "planning"
PHASE_DATA = "data"
PHASE_ANALYSIS = "analysis"
PHASE_ASSEMBLY = "assembly"
PHASE_FINAL = "final"

# tool_family / plan domain → 阶段（多义词按产品语义归段；core 归 analysis）。
_DOMAIN_PHASE: Dict[str, str] = {
    "dataset": PHASE_DATA,
    "chinese": PHASE_DATA,
    "osm": PHASE_DATA,
    "raster": PHASE_ANALYSIS,
    "network": PHASE_ANALYSIS,
    "statistics": PHASE_ANALYSIS,
    "temporal": PHASE_ANALYSIS,
    "what_if": PHASE_ANALYSIS,
    "core": PHASE_ANALYSIS,
    "mapspec": PHASE_ASSEMBLY,
    "report": PHASE_ASSEMBLY,
    "meta": PHASE_PLANNING,
}

# 各阶段的前门工具（预算豁免 —— tier-1 已必发，这里只列 tier-2 前门）。
_PHASE_PREFERRED: Dict[str, Tuple[str, ...]] = {
    PHASE_PLANNING: ("webgis_map_intent",),
    PHASE_DATA: ("webgis_map_intent",),
    PHASE_ANALYSIS: ("webgis_map_intent",),
    PHASE_ASSEMBLY: (
        "webgis_map_product",
        "webgis_component_catalog",
        "webgis_component_update",
        "generate_chart",
    ),
    PHASE_FINAL: (
        "webgis_component_catalog",
        "webgis_map_product",
    ),
}

# 各阶段并入的激活域（与关键词命中取并集）。
_PHASE_DOMAINS: Dict[str, Tuple[str, ...]] = {
    PHASE_PLANNING: (),
    PHASE_DATA: ("dataset",),
    PHASE_ANALYSIS: ("core", "statistics"),
    PHASE_ASSEMBLY: ("mapspec", "report"),
    PHASE_FINAL: ("report", "mapspec"),
}


# product_action 建议动作 → 阶段（Pi 路径的权威相位信号：adviser 是
# SessionPlan 派生的确定性投影，不引入第二计划真相）。
_ACTION_PHASE: Dict[str, str] = {
    "retry_analysis": PHASE_ANALYSIS,
    "run_analysis": PHASE_ANALYSIS,
    "produce_layer": PHASE_ASSEMBLY,
    "repair_layer_render": PHASE_ASSEMBLY,
    "produce_chart": PHASE_ASSEMBLY,
    "produce_statistics": PHASE_ASSEMBLY,
    "finalize_product": PHASE_FINAL,
}


@dataclass(frozen=True)
class ToolSurface:
    """一轮工具面的派生偏好（只读；由 ToolCatalog 消费，不持久化）。"""

    phase: str
    preferred_tools: FrozenSet[str] = frozenset()
    allowed_domains: FrozenSet[str] = frozenset()
    fallback_tools: FrozenSet[str] = frozenset()
    hidden_tools: FrozenSet[str] = frozenset()
    evidence: Tuple[str, ...] = field(default_factory=tuple)

    def projection_line(self) -> str:
        """有界单行披露（诊断/观测；不进 LLM context）。"""
        bits = [f"phase={self.phase}"]
        if self.preferred_tools:
            bits.append(f"preferred={len(self.preferred_tools)}")
        if self.allowed_domains:
            bits.append(f"domains={','.join(sorted(self.allowed_domains))}")
        return f"[ToolSurface] {' '.join(bits)}"


def _plan_phase(plan: Any) -> Tuple[str, Tuple[str, ...]]:
    """SessionPlan → (phase, evidence_fragments)。纯函数、防御式。"""
    steps = getattr(plan, "steps", None) or []
    if not steps:
        return PHASE_PLANNING, ("no plan steps → planning")
    pending = [s for s in steps if not getattr(s, "done", False)]
    if not pending:
        return PHASE_FINAL, ("all steps done → final")
    current = pending[0]
    family = (getattr(current, "tool_family", None) or "").strip()
    step_n = getattr(current, "n", "?")
    if not family:
        return PHASE_PLANNING, (f"step {step_n} has no tool_family → planning",)
    phase = _DOMAIN_PHASE.get(family)
    if phase is None:
        # 未知族（registry 演进新域）：不猜 —— 保持通用面。
        return PHASE_PLANNING, (f"step {step_n} family={family} unmapped → planning",)
    return phase, (f"step {step_n} family={family} → {phase}",)


def _chapter_phase(chapter: Any) -> Tuple[str, Tuple[str, ...]]:
    """SessionPlan 信封（gis_chapter dict）→ (phase, evidence)。

    Pi 路径的计划真相是 SessionPlan 信封（legacy Plan 只在 legacy 路径）——
    阶段从信封行派生（读真相，不建第二份）：首个未完成 data_requirement
    → data；首个未完成 analysis_step → analysis；全部完成 → 落到
    product_status 覆盖层（无欠账时 planning）。防御式：非 dict → planning。
    """
    if not isinstance(chapter, dict):
        return PHASE_PLANNING, ("chapter absent → planning",)
    done_states = {"complete", "completed", "skipped", "done", "ok"}

    def _pending(rows: Any) -> list:
        out = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            if str(row.get("status") or "").lower() not in done_states:
                out.append(row)
        return out

    pending_data = _pending(chapter.get("data_requirements"))
    if pending_data:
        return PHASE_DATA, (f"{len(pending_data)} pending data rows → data",)
    pending_steps = _pending(chapter.get("analysis_steps"))
    if pending_steps:
        return PHASE_ANALYSIS, (f"{len(pending_steps)} pending analysis rows → analysis",)
    return PHASE_PLANNING, ("no pending chapter rows → planning",)


def compile_tool_surface(
    *,
    plan: Any = None,
    product_status: Optional[str] = None,
    registry_meta: Optional[Dict[str, Dict[str, Any]]] = None,
    chapter: Any = None,
    next_action: Optional[str] = None,
) -> ToolSurface:
    """编译本轮工具面偏好。

    参数：
    - plan：legacy Plan（legacy 路径进程内真相；None → 试试 chapter）；
    - chapter：SessionPlan 信封的 gis_chapter dict（Pi 路径计划真相）——
      plan 缺席时从信封行派生阶段；
    - next_action：product_action adviser 的确定性建议动作（在场时优先于
      plan/chapter 派生 —— adviser 是欠账 facets 的精确投影）；
    - product_status：可选的完成度状态（complete/needs_repair/…）——
      在场时覆盖阶段推导（产品欠账 → assembly；complete → final）；
    - registry_meta：可选的 registry.all_metadata() 快照 —— 在场时把
      preferred 过滤到真实存在的工具名（不虚构）。

    纯函数：同输入必同输出，无 IO 无状态。
    """
    evidence: list[str] = []
    if next_action and next_action in _ACTION_PHASE:
        phase = _ACTION_PHASE[next_action]
        evidence.append(f"next_action={next_action} → {phase}")
    elif plan is not None:
        phase, frags = _plan_phase(plan)
        evidence.extend(frags)
    elif chapter is not None:
        phase, frags = _chapter_phase(chapter)
        evidence.extend(frags)
    else:
        phase = PHASE_PLANNING
        evidence.append("no plan/chapter → planning (generic surface)")

    # 产品状态覆盖（显式事实优先于步骤推断）。
    if product_status == "complete":
        phase = PHASE_FINAL
        evidence.append(f"product_status={product_status} → final")
    elif product_status in ("needs_repair", "needs-repair"):
        phase = PHASE_ASSEMBLY
        evidence.append(f"product_status={product_status} → assembly")

    preferred = frozenset(_PHASE_PREFERRED.get(phase, ()))
    if registry_meta is not None:
        preferred = frozenset(p for p in preferred if p in registry_meta)
        dropped = len(_PHASE_PREFERRED.get(phase, ())) - len(preferred)
        if dropped:
            evidence.append(f"{dropped} preferred tools absent from registry → pruned")
    domains = frozenset(_PHASE_DOMAINS.get(phase, ()))

    return ToolSurface(
        phase=phase,
        preferred_tools=preferred,
        allowed_domains=domains,
        # tier-3 自救通道（list_available_tools 恒 tier-1 可见）。
        fallback_tools=frozenset({"list_available_tools"}),
        # v1 恒空（见模块 docstring：关键词命中的 tier-2 不隐藏 —— 安全网
        # 优先；词面保留支撑未来策略）。
        hidden_tools=frozenset(),
        evidence=tuple(evidence),
    )


__all__ = [
    "ToolSurface",
    "compile_tool_surface",
    "_ACTION_PHASE",
    "PHASE_PLANNING",
    "PHASE_DATA",
    "PHASE_ANALYSIS",
    "PHASE_ASSEMBLY",
    "PHASE_FINAL",
]
