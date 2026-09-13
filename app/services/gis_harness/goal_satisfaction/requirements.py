"""GoalRequirement 派生（ADR-0183 G1）—— 结构化事实 → 确定性需求清单。

来源红线（决策 D-004）：需求只从 **intent / SessionPlan 章节 / 计划产物**
等结构化事实确定性派生；不从 raw text regex 抽需求（那是 intent 层的
既有职责，本层绝不重复）。

派生规则（同输入同 contract，fingerprint 稳定）：

- ``map``：``intent.output_intents`` 含 ``map`` **或** 章节 ``map_layers``
  非空（计划了结果层 = 要图）；
- ``analysis:<capability>``：章节 ``analysis_steps`` 每行一个子目标
  （行 purpose 为 NL 摘要）——multi-goal 的天然载体；
- ``comparison``：``intent.comparison`` 非空，或存在 compare 族分析行；
- ``statistics`` / ``chart``：``intent.output_intents`` 对应项；
- ``export:<fmt>``：``intent.export_intents`` 每格式一条（交付契约）；
- ``must_not`` / ``pinned`` / ``user_constraints``：schema 支持，结构化
  事实缺席时保持为空（诚实缺席，不虚构用户约束）。

零需求（纯聊天 / 无 GIS 章节）→ 返回 None：调用方保持既有行为零漂移。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.gis_harness.workflow_instance import canonical_fingerprint

from .contracts import (
    MAX_CONSTRAINTS,
    MAX_REQUIREMENTS,
    RequirementKind,
    GoalContract,
    GoalRequirement,
)

#: 能力分类（真实 registry 词表：analysis/data_access/density/network/
#: platform/raster/statistics —— 无 comparison/aggregate 类别；比较族按
#: 能力名确定性识别，与 goal_graph 的理想化映射解耦）。
_COMPARISON_NAME_MARKERS = (
    "compare", "comparison", "change_detection", "change_point",
)
_COMPARISON_ZH_MARKERS = ("对比", "比较", "变化检测")
_STATISTICS_NAME_MARKERS = ("aggregat", "statistic")
_STATISTICS_ZH_MARKERS = ("聚合", "统计")


def _capability_category(capability: str) -> str:
    """capability → registry category（缺席 = ""，不虚构）。"""
    if not capability:
        return ""
    try:
        from app.lib.gis.capability_registry import get_capability_registry

        return str(getattr(
            get_capability_registry().get(capability), "category", "") or "")
    except Exception:  # noqa: BLE001 — registry 缺席 = 分类不可用（诚实空）
        return ""


def classify_capability(capability: str, purpose: str = "") -> str:
    """能力 → 语义分类（comparison | statistics | analysis；纯函数）。

    registry category 优先（statistics 类 23 能力全量命中）；比较族与
    聚合族按能力名/中文目的词确定性识别 —— registry 缺席时同样可判。
    """
    name = (capability or "").lower()
    text = f"{name} {(purpose or '').lower()}"
    if _capability_category(capability) == "statistics":
        return "statistics"
    if any(m in text for m in _COMPARISON_NAME_MARKERS) or \
            any(m in (purpose or "") for m in _COMPARISON_ZH_MARKERS):
        return "comparison"
    if any(m in name for m in _STATISTICS_NAME_MARKERS) or \
            any(m in (purpose or "") for m in _STATISTICS_ZH_MARKERS):
        return "statistics"
    return "analysis"


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _rows(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [r for r in value if isinstance(r, dict)]


def derive_goal_contract(
    chapter: Optional[Dict[str, Any]],
    user_goal: str = "",
) -> Optional[GoalContract]:
    """章节结构化事实 → GoalContract（纯函数；零需求 → None）。"""
    if not isinstance(chapter, dict) or not chapter:
        return None
    intent = _dict(chapter.get("intent"))
    output_intents = [str(o) for o in (intent.get("output_intents") or [])
                      if isinstance(o, str)]
    export_intents = [str(e) for e in (intent.get("export_intents") or [])
                      if isinstance(e, str)]
    map_layers = _rows(chapter.get("map_layers"))
    analysis_rows = _rows(chapter.get("analysis_steps"))
    comparison_text = str(intent.get("comparison") or "").strip()
    scope_name = str((_dict(intent.get("scope"))).get("name") or "")
    group_by = str(intent.get("group_by") or "")

    requirements: List[GoalRequirement] = []

    # 1) map：要图语义（output intent 或已计划结果层）。
    if "map" in output_intents or map_layers:
        requirements.append(GoalRequirement(
            id="map",
            kind=RequirementKind.MAP,
            summary=str(intent.get("query") or chapter.get("query")
                        or user_goal or "")[:200],
            required=True,
            scope_name=scope_name[:64],
            source="intent:output_intents" if "map" in output_intents
            else "chapter:map_layers",
        ))

    # 2) analysis：每个分析步骤行一个子目标（去重 capability）。
    compare_rows: List[Dict[str, Any]] = []
    seen_caps: set = set()
    for row in analysis_rows[:MAX_REQUIREMENTS]:
        cap = str(row.get("capability") or "").strip()
        if not cap or cap in seen_caps:
            continue
        seen_caps.add(cap)
        if classify_capability(cap, str(row.get("purpose") or "")) == "comparison":
            compare_rows.append(row)
        requirements.append(GoalRequirement(
            id=f"analysis:{cap}"[:64],
            kind=RequirementKind.ANALYSIS,
            summary=str(row.get("purpose") or cap)[:200],
            required=True,
            capability=cap[:64],
            scope_name=scope_name[:64],
            group_by=group_by[:48],
            source="chapter:analysis_steps",
        ))

    # 3) comparison：显式对比意图或 compare 族行（二者只造一条，避免同义重复）。
    if comparison_text or compare_rows:
        requirements.append(GoalRequirement(
            id="comparison",
            kind=RequirementKind.COMPARISON,
            summary=comparison_text[:200]
            or str(_dict(compare_rows[0]).get("purpose")
                   or "comparison")[:200],
            required=True,
            scope_name=scope_name[:64],
            group_by=group_by[:48],
            source="intent:comparison" if comparison_text
            else "chapter:analysis_steps:compare",
        ))

    # 4) statistics / chart：output intents（确定性可验 → required）。
    if "statistics" in output_intents:
        requirements.append(GoalRequirement(
            id="statistics", kind=RequirementKind.STATISTICS,
            summary="statistics output", required=True,
            scope_name=scope_name[:64], group_by=group_by[:48],
            source="intent:output_intents",
        ))
    if "chart" in output_intents:
        requirements.append(GoalRequirement(
            id="chart", kind=RequirementKind.CHART,
            summary="chart output", required=True,
            scope_name=scope_name[:64], group_by=group_by[:48],
            source="intent:output_intents",
        ))

    # 5) export：每个显式导出格式一条交付契约。
    for fmt in export_intents[:8]:
        requirements.append(GoalRequirement(
            id=f"export:{fmt}"[:64], kind=RequirementKind.EXPORT,
            summary=f"export delivery: {fmt}", required=True,
            export_format=fmt[:16],
            scope_name=scope_name[:64],
            source="intent:export_intents",
        ))

    if not requirements:
        return None

    # 无法确定性验证的 output intents（summary/table 文案）不造需求 ——
    # 诚实缺席优于假 ledger 行（评估层不为它们虚构证据绑定）。

    constraints = [str(a)[:200] for a in (intent.get("assumptions") or [])
                   if isinstance(a, str) and a.strip()][:MAX_CONSTRAINTS]
    contract = GoalContract(
        goal_id=_goal_id(chapter, user_goal),
        summary=str(chapter.get("query") or intent.get("query")
                    or user_goal or "")[:200],
        requirements=requirements[:MAX_REQUIREMENTS],
        user_constraints=constraints,
    )
    return contract


def _goal_id(chapter: Dict[str, Any], user_goal: str) -> str:
    """与 session_plan.goal_key 同源的目标键（不一致即 supersede）。"""
    try:
        from app.services.session_plan import goal_key

        return goal_key(chapter, user_goal)[:128]
    except Exception:  # noqa: BLE001 — 投影缺席退化为 query 串
        return str(chapter.get("query") or user_goal or "")[:128]


def contract_fingerprint(contract: GoalContract) -> str:
    """contract 结构指纹（同输入同值；报告面 stale 判定输入）。"""
    return canonical_fingerprint(contract.to_bounded_dict())


__all__ = [
    "derive_goal_contract",
    "contract_fingerprint",
    "classify_capability",
]
