"""SkillResolver —— 确定性优先的技能选择（ADR-0182 §2.4；goal S4/S9）。

输入 Goal/Intent/Situation 事实，输出 ranked skills + confidence + reasons +
rejected（带原因码与 fallback 建议）+ clarification 请求。

红线：

- **deterministic-first，零 LLM**：打分是纯函数，同输入同输出；LLM 可以在
  上游改写 goal 文本/意图槽位，但不能替代本层的资格与排序裁决；
- 事实缺席 = unknown（放行，不参与打分也不触发硬门槛）——与
  recipes.EligibilityContext 同红线：未知 ≠ 不满足，绝不虚构证据；
- 资格硬门槛失败必须显式 ineligible + 原因码 + fallback 建议
  （S9：点数据不能偷偷画 choropleth；S14：禁止 silent fallback）；
- 不做工具选择：输出的是技能与能力需求，工具由 Tool Resolver /
  CapabilityRegistry 解析。
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.services.gis_harness.skills.contract import SkillContract
from app.services.gis_harness.skills.situation import SelectionFacts

#: 置信度分档阈值（确定性标定）。
CONFIDENCE_HIGH = 0.66
CONFIDENCE_MEDIUM = 0.33

#: 分数归一化基准（达到该分即满置信；信号满配约 8-10 分）。
_SCORE_NORM = 8.0


class SkillRejection(BaseModel):
    """一个技能被拒绝（或不可行）的记录（可审计）。"""
    skill_id: str
    reason_codes: List[str] = Field(default_factory=list)
    detail: str = ""
    fallback_skill_id: str = ""       # S14：拒绝必须伴随 fallback 建议（若有声明）


class SkillCandidate(BaseModel):
    """一个可行候选（排序后返回）。"""
    skill_id: str
    score: float
    confidence: float
    confidence_band: str              # high/medium/low
    matched_signals: List[str] = Field(default_factory=list)
    skill_version: str = ""

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill_id[:64],
            "score": round(self.score, 2),
            "confidence": round(self.confidence, 2),
            "confidence_band": self.confidence_band,
            "matched_signals": [s[:48] for s in self.matched_signals[:8]],
            "skill_version": self.skill_version[:16],
        }


class SkillClarification(BaseModel):
    """歧义/信息不足时的澄清请求（带提示问题；不猜测）。"""
    reason_code: str                  # AMBIGUOUS_TOP_CANDIDATES / NO_MATCH / EMPTY_GOAL
    detail: str = ""
    question_hints: List[str] = Field(default_factory=list)


class SkillSelectionResult(BaseModel):
    """技能选择结果（全部确定性产物）。"""
    ranked: List[SkillCandidate] = Field(default_factory=list)
    rejected: List[SkillRejection] = Field(default_factory=list)
    selected: Optional[str] = None
    clarification: Optional[SkillClarification] = None

    @property
    def top(self) -> Optional[SkillCandidate]:
        return self.ranked[0] if self.ranked else None

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "selected": self.selected[:64] if self.selected else None,
            "ranked": [c.to_bounded_dict() for c in self.ranked[:5]],
            "rejected": [
                {"skill_id": r.skill_id[:64],
                 "reason_codes": [c[:48] for c in r.reason_codes[:4]],
                 "fallback_skill_id": r.fallback_skill_id[:64]}
                for r in self.rejected[:8]
            ],
            "clarification": (
                self.clarification.reason_code if self.clarification else None),
        }


def _band(confidence: float) -> str:
    if confidence >= CONFIDENCE_HIGH:
        return "high"
    if confidence >= CONFIDENCE_MEDIUM:
        return "medium"
    return "low"


def _keyword_hit(pattern: str, text: str) -> bool:
    """zh 子串 / en 整词命中（与 RecipeRegistry / ontology 同红线）。"""
    if not pattern:
        return False
    low = pattern.lower()
    if low.isascii() and low.isalnum():
        return bool(re.search(rf"(?<![a-z]){ re.escape(low) }(?![a-z])",
                              text.lower()))
    return low in text.lower()


class SkillResolver:
    """技能解析器：持有技能契约集合，提供确定性选择与资格裁决。"""

    def __init__(
        self,
        skills: List[SkillContract],
        *,
        capability_exists: Optional[Callable[[str], bool]] = None,
    ) -> None:
        self._by_id: Dict[str, SkillContract] = {}
        seen: set = set()
        dupes: List[str] = []
        for s in skills:
            if s.id in seen:
                dupes.append(s.id)
            seen.add(s.id)
            self._by_id[s.id] = s
        if dupes:
            raise ValueError(f"SkillResolver: duplicate skill ids {sorted(dupes)[:4]}")
        self._capability_exists = capability_exists

    # ── 基础面 ────────────────────────────────────────────────────────
    def get(self, skill_id: str) -> Optional[SkillContract]:
        return self._by_id.get(skill_id)

    @property
    def skill_ids(self) -> List[str]:
        return sorted(self._by_id)

    def __len__(self) -> int:
        return len(self._by_id)

    # ── 选择（S4）─────────────────────────────────────────────────────
    def resolve(
        self,
        facts: SelectionFacts,
        *,
        limit: int = 5,
        packs: Optional[Tuple[str, ...]] = None,
        include_deprecated: bool = False,
    ) -> SkillSelectionResult:
        """goal/intent/situation 事实 → 有序技能候选。

        排序：score 降序 → 关键词直接证据优先 → id 字典序（确定性）。
        """
        result = SkillSelectionResult()
        if not facts.goal_text.strip() and not facts.task_type \
                and not facts.ontology_matches:
            result.clarification = SkillClarification(
                reason_code="EMPTY_GOAL",
                detail="目标为空：无法进行技能选择",
                question_hints=["想分析什么对象或现象？", "期望什么形式的成果（图/统计/报告）？"],
            )
            return result

        scored: List[SkillCandidate] = []
        for skill in self._by_id.values():
            if packs and skill.pack not in packs:
                continue
            if skill.deprecated and not include_deprecated:
                result.rejected.append(SkillRejection(
                    skill_id=skill.id,
                    reason_codes=["SKILL_DEPRECATED"],
                    detail=f"已被 {skill.deprecated_by} 取代",
                    fallback_skill_id=skill.deprecated_by,
                ))
                continue
            ineligible = self.check_eligibility(skill, facts)
            if ineligible is not None:
                result.rejected.append(ineligible)
                continue
            score, signals = self._score(skill, facts)
            if score <= 0.0:
                continue
            confidence = min(1.0, score / _SCORE_NORM)
            scored.append(SkillCandidate(
                skill_id=skill.id,
                score=score,
                confidence=confidence,
                confidence_band=_band(confidence),
                matched_signals=signals,
                skill_version=skill.version,
            ))

        scored.sort(key=lambda c: (
            -c.score,
            0 if any(s.startswith("kw:") for s in c.matched_signals) else 1,
            c.skill_id,
        ))
        result.ranked = scored[:limit]
        if result.ranked:
            result.selected = result.ranked[0].skill_id
        elif result.clarification is None:
            # 零候选：与其静默无果，不如显式请求澄清（S4 clarification 语义）
            result.clarification = SkillClarification(
                reason_code="NO_MATCH",
                detail="没有命中任何技能：目标可能超出当前技能库或表述不足",
                question_hints=[
                    "想分析什么对象或现象？",
                    "数据是点、面还是栅格？",
                    "期望什么形式的成果（图/统计/报告）？",
                ],
            )

        # 歧义澄清：并列高分（分差 ≤0.5）且事实面薄弱（无 task/几何/度量语义）
        if len(result.ranked) >= 2 and not facts.task_type \
                and not facts.geometry_kinds and not facts.measure_semantics:
            a, b = result.ranked[0], result.ranked[1]
            if abs(a.score - b.score) <= 0.5:
                result.clarification = SkillClarification(
                    reason_code="AMBIGUOUS_TOP_CANDIDATES",
                    detail=f"『{a.skill_id}』与『{b.skill_id}』并列可行，事实不足以消歧",
                    question_hints=[
                        "数据是点、面还是栅格？",
                        "想要空间分布图、统计对比还是变化分析？",
                    ],
                )
        return result

    # ── 资格裁决（S9）─────────────────────────────────────────────────
    def check_eligibility(
        self, skill: SkillContract, facts: SelectionFacts,
    ) -> Optional[SkillRejection]:
        """资格硬门槛；返回 None = 可行（含 unknown 放行）。

        硬门槛只在**双方事实都在场且矛盾**时触发（unknown ≠ 不满足）。
        """
        reason_codes: List[str] = []
        req = skill.required_situation

        # 几何兼容：point 数据集不能进 polygon-only 技能（S9 红线）。
        # 事实里的 "unknown" 不是矛盾证据，先过滤（unknown ≠ 不满足）。
        fact_geoms = [g for g in facts.geometry_kinds if g != "unknown"]
        if req.geometry_kinds and fact_geoms:
            if not (set(req.geometry_kinds) & set(fact_geoms)):
                reason_codes.append("GEOMETRY_NOT_SUPPORTED")
        # 边界/网络/DEM 在场性（显式 False 才拒；None=unknown 放行）
        if req.requires_boundary and facts.has_boundary is False:
            reason_codes.append("BOUNDARY_MISSING")
        if req.requires_network and facts.has_network is False:
            reason_codes.append("NETWORK_MISSING")
        if req.requires_dem and facts.has_dem is False:
            reason_codes.append("DEM_MISSING")
        # 样本量（事实在场才判）
        if req.min_features is not None and facts.feature_count is not None \
                and facts.feature_count < req.min_features:
            reason_codes.append("INSUFFICIENT_DATA")
        # 数据角色：facts.data_roles 是会话数据角色的**穷举**（来自
        # situation 投影），因此必需角色必须是已知集合的子集；部分在场的
        # 穷举集合缺角色 = 缺失（与"ANY 交集放行"相比更诚实）。
        if req.data_roles and facts.data_roles:
            available = set(facts.data_roles) | set(facts.bound_roles)
            if not set(req.data_roles) <= available:
                reason_codes.append("DATA_ROLE_MISSING")
        # 期数门槛（S12）：期数事实在场且低于技能 min_periods → 不足
        if facts.period_count is not None and skill.temporal_semantics is not None \
                and skill.temporal_semantics.min_periods > 1 \
                and facts.period_count < skill.temporal_semantics.min_periods:
            reason_codes.append("TEMPORAL_INSUFFICIENT")

        # 能力硬门槛（注册表谓词注入；默认走 CapabilityRegistry）——
        # **独立于其他门槛**：即使事实面全绿，required+hard_gate 能力缺席
        # 也必须拒绝（触发 fallback 而不是静默降级）。
        if self._capability_exists is not None:
            missing = sorted(
                r.capability_id for r in skill.capability_requirements
                if r.criticality == "required" and r.hard_gate
                and not self._capability_exists(r.capability_id))
            if missing:
                reason_codes.append("CAPABILITY_MISSING:" + ",".join(missing[:4]))

        if not reason_codes:
            return None

        rejection = SkillRejection(
            skill_id=skill.id,
            reason_codes=reason_codes,
            detail="；".join(reason_codes),
        )
        # S14：从过程 IR 找同因 fallback 建议（alternative_skill 优先）
        trigger_map = {
            "GEOMETRY_NOT_SUPPORTED": "unsupported_geometry",
            "BOUNDARY_MISSING": "missing_input",
            "NETWORK_MISSING": "missing_input",
            "DEM_MISSING": "missing_input",
            "TEMPORAL_INSUFFICIENT": "insufficient_data",
            "INSUFFICIENT_DATA": "insufficient_data",
            "DATA_ROLE_MISSING": "missing_input",
            # 能力缺席 ≈ 提供方不可用（能力由 provider 承载）
            "CAPABILITY_MISSING": "provider_unavailable",
        }
        for code in reason_codes:
            trigger = trigger_map.get(code.split(":", 1)[0])
            if not trigger:
                continue
            for fb in skill.procedure.fallbacks:
                if fb.trigger == trigger and fb.action == "alternative_skill":
                    rejection.fallback_skill_id = fb.fallback_skill_id
                    break
            if rejection.fallback_skill_id:
                break
        return rejection

    # ── 打分（确定性信号）────────────────────────────────────────────
    def _score(
        self, skill: SkillContract, facts: SelectionFacts,
    ) -> Tuple[float, List[str]]:
        score = 0.0
        signals: List[str] = []

        # 1) 本体任务命中（最强语义证据）
        onto_hits = set(skill.ontology_tasks) & set(facts.ontology_matches)
        if onto_hits:
            score += 3.0
            signals.append(f"ontology:{sorted(onto_hits)[0]}")

        # 2) intent task family 对齐
        if facts.task_type and facts.task_type in skill.task_types:
            score += 2.5
            signals.append(f"task:{facts.task_type}")

        # 3) 关键词命中（zh 子串 / en 整词；封顶防关键词堆砌）
        kw_hits = 0
        for pattern in skill.intent_patterns:
            if _keyword_hit(pattern, facts.goal_text):
                kw_hits += 1
                if kw_hits <= 4:
                    signals.append(f"kw:{pattern[:24]}")
        if kw_hits:
            score += min(kw_hits, 4.0) * 1.0

        # 4) 几何互补（需求与事实相交 → 加分；不相交已在资格层拒绝）
        req = skill.required_situation
        fact_geoms = [g for g in facts.geometry_kinds if g != "unknown"]
        if req.geometry_kinds and fact_geoms:
            if set(req.geometry_kinds) & set(fact_geoms):
                score += 1.0
                signals.append(f"geometry:{sorted(set(req.geometry_kinds) & set(fact_geoms))[0]}")

        # 5) 数据角色覆盖
        available_roles = set(facts.data_roles) | set(facts.bound_roles)
        role_hits = set(req.data_roles) & available_roles
        if role_hits:
            score += 0.5 * len(role_hits)
            signals.append(f"roles:{sorted(role_hits)[0]}")

        # 6) scope 层级对齐
        if req.scope_unit and facts.scope_unit and req.scope_unit == facts.scope_unit:
            score += 0.5
            signals.append(f"scope:{facts.scope_unit}")

        # 7) 度量语义对齐（count 技能 ≠ rate 技能）
        ss = skill.statistical_semantics
        if ss is not None and ss.measure_semantics and facts.measure_semantics:
            if facts.measure_semantics in ss.measure_semantics:
                score += 0.75
                signals.append(f"measure:{facts.measure_semantics}")
            elif facts.measure_semantics in ("count", "rate", "density", "percentage"):
                # 已知度量语义与技能产出语义冲突 → 减分（不拒，goal 可能要求变换）
                score -= 0.5

        # 8) 时间模式对齐
        ts = skill.temporal_semantics
        if ts is not None and facts.temporal_mode:
            if facts.temporal_mode == ts.temporal_mode:
                score += 1.0
                signals.append(f"temporal:{facts.temporal_mode}")
            elif ts.temporal_mode == "snapshot" and facts.temporal_mode != "snapshot":
                score -= 0.5

        return score, signals


__all__ = [
    "SkillRejection",
    "SkillCandidate",
    "SkillClarification",
    "SkillSelectionResult",
    "SkillResolver",
]
