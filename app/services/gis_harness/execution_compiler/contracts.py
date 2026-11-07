"""Execution Compiler typed contracts —— 编译产物的封闭词表与模型。

全部模型 pydantic v2、``extra="forbid"``、bounded；确定性序列化
（``to_bounded_dict`` 同输入同输出——指纹与对账的基元）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

#: 编译裁决词表（封闭；三态——伪成功不允许）。
CompileVerdict = Literal["compilable", "needs_clarification", "blocked"]

#: 单位义务裁决词表（封闭；review-B P2-2：静态层真实可达三态——
#: 「声明与事实矛盾」的 violated 形态发生在运行期证据面，由
#: run_final_gate/science_final_gate 承载，不在静态层虚构第四态）。
UnitObligationVerdict = Literal[
    "satisfied",           # 语义/单位链路闭合（几何自足或分母在证）
    "missing_denominator", # 率/人均语义但分母数据事实缺席 → 只给计数+披露
    "not_applicable",      # 无量纲语义（纯定位/查看类）或事实未注入
]

#: 单位义务的语义类别（统计词表 → 量纲语义的规范投影）。
ObligationSemantics = Literal[
    "count", "density", "per_area", "per_capita", "rate", "share",
    "duration", "distance", "index", "none",
]

TASK_COMPILATION_SCHEMA = "task_compilation.v1"

# ── bounded 常量 ─────────────────────────────────────────────────────────
MAX_SIGNALS = 12
MAX_CANDIDATES = 4
MAX_EXPRESSIONS = 6
MAX_OBLIGATIONS = 8
MAX_CLARIFICATIONS = 4
MAX_DISCLOSURES = 6
MAX_BLOCK_REASONS = 4
MAX_TEXT = 200


class SignalEvidence(BaseModel):
    """一条编译信号：IR 字段路径 + 值 + 来源（可审计，无暗箱）。"""

    model_config = ConfigDict(extra="forbid")

    path: str = Field("", max_length=64)     # 如 statistics.normalization
    value: str = Field("", max_length=64)
    origin: str = Field("", max_length=16)   # Provenance.origin 词表子集
    weight: float = Field(1.0, ge=0.0, le=6.0)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path[:64],
            "value": self.value[:64],
            "origin": self.origin[:16],
            "weight": round(self.weight, 2),
        }


class RoleNeed(BaseModel):
    """族候选的数据角色需求投影（复用 families.role_requirements 词表）。"""

    model_config = ConfigDict(extra="forbid")

    role: str = Field("", max_length=32)
    required: bool = True
    geometry_kinds: Tuple[str, ...] = ()
    note: str = Field("", max_length=MAX_TEXT)


class FamilyCandidate(BaseModel):
    """一个族候选：匹配信号 + 得分 + 角色需求 + 排除理由。"""

    model_config = ConfigDict(extra="forbid")

    family: str = Field("", max_length=64)
    score: float = 0.0
    signals: List[SignalEvidence] = Field(default_factory=list,
                                          max_length=MAX_SIGNALS)
    role_needs: List[RoleNeed] = Field(default_factory=list,
                                       max_length=8)
    #: 未选该族时的确定性排除理由（封闭码，可进披露面）。
    exclusion_reasons: Tuple[str, ...] = ()

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "family": self.family[:64],
            "score": round(self.score, 3),
            "signals": [s.to_bounded_dict() for s in self.signals[:MAX_SIGNALS]],
            "role_needs": [
                {"role": r.role[:32], "required": r.required,
                 "geometry_kinds": list(r.geometry_kinds[:4]),
                 "note": r.note[:120]}
                for r in self.role_needs[:8]],
            "exclusion_reasons": [c[:64] for c in self.exclusion_reasons[:4]],
        }


class ExpressionCandidate(BaseModel):
    """一个表达候选（GC-02）：用户显式要求与 Agent 推荐增量分离。"""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field("", max_length=48)     # point_map/admin_choropleth/…
    label_zh: str = Field("", max_length=64)
    user_requested: bool = False             # 用户显式点名（representation/话语）
    suitable: Optional[bool] = None          # None=数据事实缺席（不虚构）
    reasons: Tuple[str, ...] = ()            # 适配/不适配理由（封闭码+参数）
    requires_roles: Tuple[str, ...] = ()     # 依赖的数据角色
    semantics: str = Field("", max_length=32)  # 量纲语义（与 UnitObligation 对齐）

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind[:48],
            "label_zh": self.label_zh[:64],
            "user_requested": self.user_requested,
            "suitable": self.suitable,
            "reasons": [r[:96] for r in self.reasons[:4]],
            "requires_roles": list(self.requires_roles[:6]),
            "semantics": self.semantics[:32],
        }


class UnitObligation(BaseModel):
    """一条量纲义务（GC-05）：统计语义 → 单位/分母要求 + 裁决。"""

    model_config = ConfigDict(extra="forbid")

    measure_id: str = Field("", max_length=16)
    statistic: str = Field("", max_length=16)        # StatisticVocab
    expected_semantics: ObligationSemantics = "none"
    expected_unit: str = Field("", max_length=16)    # UNIT_VOCABULARY 成员或 unitless
    denominator_kind: str = Field("", max_length=16)  # area/field/count/""=无
    verdict: UnitObligationVerdict = "not_applicable"
    detail: str = Field("", max_length=MAX_TEXT)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "measure_id": self.measure_id[:16],
            "statistic": self.statistic[:16],
            "expected_semantics": self.expected_semantics,
            "expected_unit": self.expected_unit[:16],
            "denominator_kind": self.denominator_kind[:16],
            "verdict": self.verdict,
            "detail": self.detail[:MAX_TEXT],
        }


class TaskCompilation(BaseModel):
    """RequirementDocument → 执行面编译产物（单一可序列化产物）。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = TASK_COMPILATION_SCHEMA
    document_id: str = Field("", max_length=64)
    requirement_digest: str = Field("", max_length=80)
    verdict: CompileVerdict = "blocked"
    family_candidates: List[FamilyCandidate] = Field(
        default_factory=list, max_length=MAX_CANDIDATES)
    expression_candidates: List[ExpressionCandidate] = Field(
        default_factory=list, max_length=MAX_EXPRESSIONS)
    unit_obligations: List[UnitObligation] = Field(
        default_factory=list, max_length=MAX_OBLIGATIONS)
    clarification_requests: Tuple[str, ...] = ()
    disclosures: Tuple[str, ...] = ()
    block_reasons: Tuple[str, ...] = ()

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema_version,
            "document_id": self.document_id[:64],
            "requirement_digest": self.requirement_digest[:80],
            "verdict": self.verdict,
            "family_candidates": [
                c.to_bounded_dict() for c in self.family_candidates[:MAX_CANDIDATES]],
            "expression_candidates": [
                e.to_bounded_dict() for e in self.expression_candidates[:MAX_EXPRESSIONS]],
            "unit_obligations": [
                u.to_bounded_dict() for u in self.unit_obligations[:MAX_OBLIGATIONS]],
            "clarification_requests": [
                c[:96] for c in self.clarification_requests[:MAX_CLARIFICATIONS]],
            "disclosures": [d[:120] for d in self.disclosures[:MAX_DISCLOSURES]],
            "block_reasons": [r[:96] for r in self.block_reasons[:MAX_BLOCK_REASONS]],
        }


class InvalidationScope(BaseModel):
    """IR patch → 失效面投影（GC-09）。

    style_changed=True 且科学集空 = 纯风格变更（零科学重算——
    与 analysis_workflows.style_only_invalidation 同一钉扎语义）。
    """

    model_config = ConfigDict(extra="forbid")

    style_changed: bool = False
    aoi_changed: bool = False
    subject_changed: bool = False
    measure_changed: bool = False
    time_changed: bool = False
    spatial_relation_changed: bool = False
    #: 失效数据角色（换城市 → 全量角色：旧城市 ref 不得假匹配）。
    invalidated_roles: Tuple[str, ...] = ()
    #: 需重算步骤（角色失效传播 + 参数语义变化的直接步骤 + 下游闭包）。
    science_invalidated_steps: Tuple[str, ...] = ()
    #: 输入未变、可复用证据的步骤（最小重算证明面）。
    preserved_steps: Tuple[str, ...] = ()
    reasons: Tuple[str, ...] = ()

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "style_changed": self.style_changed,
            "aoi_changed": self.aoi_changed,
            "subject_changed": self.subject_changed,
            "measure_changed": self.measure_changed,
            "time_changed": self.time_changed,
            "spatial_relation_changed": self.spatial_relation_changed,
            "invalidated_roles": list(self.invalidated_roles[:16]),
            "science_invalidated_steps": list(
                self.science_invalidated_steps[:24]),
            "preserved_steps": list(self.preserved_steps[:24]),
            "reasons": [r[:96] for r in self.reasons[:6]],
        }


__all__ = [
    "CompileVerdict",
    "ObligationSemantics",
    "UnitObligationVerdict",
    "TASK_COMPILATION_SCHEMA",
    "SignalEvidence",
    "RoleNeed",
    "FamilyCandidate",
    "ExpressionCandidate",
    "UnitObligation",
    "TaskCompilation",
    "InvalidationScope",
]
