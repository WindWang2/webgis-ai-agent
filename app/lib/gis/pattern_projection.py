"""Intent → Pattern projection (ADR-0092 C4).

纯函数投影：query/intent + 语义画像 → 匹配的分析模式 + 诚实的缺口披露。
绝不执行任何分析 —— 输出是给 Pi 的方法论建议（recommended capabilities 仍
需经 SessionPlan → CapabilityRegistry → AlgorithmResolver 落地），以及
"缺什么数据、能下什么结论、不能下什么结论" 的如实声明。

C4 的红线：不过度执行。没有人口数据时，spatial_equity 的披露必须原样传达
「当前只能评价数量/密度；公平性判断需要人口分母」，而不是悄悄降级成计数
比较然后宣称均衡。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.lib.gis.analysis_patterns import PATTERNS, AnalysisPattern
from app.lib.gis.semantic_profile import (
    SemanticDatasetProfile,
    SemanticFieldRole,
)


class PatternMatch(BaseModel):
    """一个匹配模式及其在当前数据上的满足/缺口状态（bounded）。"""

    pattern_id: str
    name_zh: str = ""
    matched_via: List[str] = Field(default_factory=list)  # task | keyword:<w>
    recommended_capabilities: List[str] = Field(default_factory=list)
    required_output_facets: List[str] = Field(default_factory=list)
    normalization_guidance: str = ""
    common_pitfalls: List[str] = Field(default_factory=list)
    satisfied_roles: List[str] = Field(default_factory=list)
    missing_roles: List[str] = Field(default_factory=list)
    disclosures: List[str] = Field(default_factory=list)
    # Semantic V2（ADR-0098）：决策族规划期义务披露（task 命中即触发）。
    decision_disclosures: List[str] = Field(default_factory=list)
    # 机器可读方法论警告码（稳定契约：UI 渲染 / benchmark 断言 / 版本化
    # 证据消费方依赖它；文案可演进，code 不可复用为其它含义）。
    warning_codes: List[str] = Field(default_factory=list)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "name_zh": self.name_zh,
            "matched_via": self.matched_via[:3],
            "recommended_capabilities": self.recommended_capabilities[:8],
            "required_output_facets": list(self.required_output_facets)[:8],
            "normalization_guidance": self.normalization_guidance[:200],
            "common_pitfalls": list(self.common_pitfalls)[:4],
            "satisfied_roles": self.satisfied_roles[:8],
            "missing_roles": self.missing_roles[:8],
            "disclosures": self.disclosures[:6],
            "decision_disclosures": self.decision_disclosures[:4],
            "warning_codes": self.warning_codes[:4],
        }


class PatternProjection(BaseModel):
    matches: List[PatternMatch] = Field(default_factory=list)
    # 数据能力边界声明（与模式无关的全局披露，如「样本仅 60 条」）。
    data_disclosures: List[str] = Field(default_factory=list)

    def to_bounded_list(self) -> List[Dict[str, Any]]:
        return [m.to_bounded_dict() for m in self.matches[:6]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "matches": self.to_bounded_list(),
            "data_disclosures": list(self.data_disclosures[:4]),
        }


_ROLE_LABELS: Dict[str, str] = {
    SemanticFieldRole.NORMALIZATION_DENOMINATOR.value: "归一化分母（如人口/面积）",
    SemanticFieldRole.POPULATION_MEASURE.value: "人口字段",
    SemanticFieldRole.AREA_MEASURE.value: "面积字段",
    SemanticFieldRole.TEMPORAL_DIMENSION.value: "时间字段",
    SemanticFieldRole.COUNT_MEASURE.value: "计数字段",
    SemanticFieldRole.RATIO_MEASURE.value: "比率字段",
}

#: 缺角色时的诚实披露模板（角色 → 披露文案；C4 红线的落地处）。
_ROLE_MISSING_DISCLOSURES: Dict[str, str] = {
    SemanticFieldRole.NORMALIZATION_DENOMINATOR.value: (
        "规划期尚未确认人口/面积等分母字段：可以评价数量与密度，"
        "不能可靠评价人均/地均意义上的资源公平性；"
        "若数据画像确认无分母，需补充分母数据（如人口栅格或统计表）后才能下公平性结论。"
    ),
    SemanticFieldRole.POPULATION_MEASURE.value: (
        "缺少人口数据：无法计算人均指标，公平性/覆盖率的结论只能停留在数量层面。"
    ),
    SemanticFieldRole.TEMPORAL_DIMENSION.value: (
        "缺少时间字段：无法做时序对比，只能给出单期快照。"
    ),
}

#: (pattern_id, missing role) → 稳定机器可读警告码（Semantic V2 §5：
#: 方法论诚实不变量的可断言契约）。文案可演进；code 语义冻结。
_ROLE_WARNING_CODES: Dict[Tuple[str, str], str] = {
    ("spatial_equity", SemanticFieldRole.NORMALIZATION_DENOMINATOR.value):
        "EQUITY_MISSING_DENOMINATOR",
    ("temporal_change", SemanticFieldRole.TEMPORAL_DIMENSION.value):
        "CHANGE_MISSING_TEMPORAL_FIELD",
}

#: 决策族（一等 task）→ 规划期义务披露的稳定警告码。
_DECISION_WARNING_CODES: Dict[str, str] = {
    "site_selection": "SITE_SELECTION_CRITERIA_UNDECLARED",
    "suitability": "SUITABILITY_WEIGHT_PROVENANCE",
    "risk_exposure": "RISK_RECEPTORS_UNCONFIRMED",
}


def _role_satisfied(
    role: SemanticFieldRole, sem: Optional[SemanticDatasetProfile]
) -> bool:
    if sem is None:
        return False
    return sem.has_role(role)


def _match_score(pattern: AnalysisPattern, task: str, query: str) -> Tuple[int, List[str]]:
    score = 0
    via: List[str] = []
    if task and task in pattern.task_aliases:
        score += 10
        via.append("task")
    lowered = (query or "").lower()
    for kw in pattern.query_keywords:
        if kw.lower() in lowered:
            score += 2
            via.append(f"keyword:{kw}")
            if len(via) >= 4:
                break
    return score, via


def project_patterns(
    query: str,
    intent_task: str = "",
    semantic_profile: Optional[SemanticDatasetProfile] = None,
    *,
    patterns: Optional[List[AnalysisPattern]] = None,
) -> PatternProjection:
    """查询（+ 可选任务分类 + 可选语义画像）→ 模式匹配与披露（纯函数）。

    semantic_profile 缺席时：模式照常匹配，但 required_roles 一律记为
    missing 并给出对应披露 —— 数据未知 ≠ 数据满足。
    """
    patterns = patterns if patterns is not None else list(PATTERNS)
    matches: List[PatternMatch] = []
    disclosures: List[str] = []
    if semantic_profile is None:
        disclosures.append(
            "未提供数据语义画像：模式匹配未结合真实字段证据"
            "（可先调用 profile_dataset_semantics 提升判断精度）。"
        )
    scored: List[Tuple[int, AnalysisPattern, List[str]]] = []
    for p in patterns:
        score, via = _match_score(p, intent_task, query)
        if score > 0:
            scored.append((score, p, via))
    scored.sort(key=lambda t: (-t[0], t[1].id))

    for score, p, via in scored[:6]:
        satisfied: List[str] = []
        missing: List[str] = []
        role_disclosures: List[str] = []
        warning_codes: List[str] = []
        for role in p.required_roles:
            if _role_satisfied(role, semantic_profile):
                satisfied.append(role.value)
            else:
                missing.append(role.value)
                text = _ROLE_MISSING_DISCLOSURES.get(role.value)
                if text:
                    role_disclosures.append(text)
                code = _ROLE_WARNING_CODES.get((p.id, role.value))
                if code:
                    warning_codes.append(code)
        for role in p.optional_roles:
            if _role_satisfied(role, semantic_profile):
                satisfied.append(role.value)
        # 决策族义务披露：只在 intent.task 精确命中该模式的一等 task 时
        # 触发 —— 借位别名（proximity 等）不触发（否则每个邻近查询都背
        # 上风险披露噪声）。规划期决策要素（准则/权重/受体）必然未确认，
        # 披露是产品的一部分而非噪声。
        decision: List[str] = []
        if (
            p.decision_disclosures
            and intent_task
            and p.first_class_task
            and intent_task == p.first_class_task
        ):
            decision = list(p.decision_disclosures)[:4]
            code = _DECISION_WARNING_CODES.get(p.id)
            if code:
                warning_codes.append(code)
        matches.append(PatternMatch(
            pattern_id=p.id,
            name_zh=p.name_zh,
            matched_via=list(dict.fromkeys(via))[:4],
            recommended_capabilities=list(p.recommended_capabilities)[:8],
            required_output_facets=list(p.required_output_facets)[:8],
            normalization_guidance=p.normalization_guidance,
            common_pitfalls=list(p.common_pitfalls)[:4],
            satisfied_roles=satisfied[:8],
            missing_roles=missing[:8],
            disclosures=list(dict.fromkeys(role_disclosures))[:6],
            decision_disclosures=decision,
            warning_codes=warning_codes[:4],
        ))
    return PatternProjection(matches=matches, data_disclosures=disclosures[:4])


__all__ = [
    "PatternMatch",
    "PatternProjection",
    "project_patterns",
]
