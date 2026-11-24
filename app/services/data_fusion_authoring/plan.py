"""Data Fusion Plan 契约 —— 版本化融合计划（pydantic，extra=forbid）。

计划 = 用户可编辑的作者态（草稿保存不执行、不改源、不改 MapSpec）。
digest 覆盖**所有影响执行语义**的自由度：源身份/版本/许可/字段声明与
映射/裁决规则/类别词表/匹配策略与阈值/几何采纳/提交政策/预算。
预览、执行与增量刷新基于同一 ``normalized_digest`` —— 计划或源变化后
旧预览按 digest 失效。

红线（与 contracts.py 同源）：
- 每源必须显式声明：名称字段、可选 id/地址/类别/时间字段、类别词表
  映射、输出字段映射与逐字段裁决规则 —— 缺声明的字段不进输出；
- 来源信任 = ``priority``（1 高优先）+ 理由字符串；两源 priority 必须
  不同（完全并列时 trust_priority 无法唯一裁决 → 计划期即拒绝）；
- 权重必须恰好覆盖 EVIDENCE_KINDS、非负、和为 1（0 需显式声明）；
- 阈值关系：review_min ≤ auto_match_min；ambiguous_margin 有界；
- point_poi 必须给 max_match_distance_m/close_distance_m 且 close ≤ max。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.data_fusion_authoring import contracts
from app.services.data_fusion_authoring.errors import DataFusionError


class FusionSourceSpec(BaseModel):
    """单个来源的完整声明（锚 + 授权仍走 G18 resolve_source）。

    ``priority``：信任序（1 = 最高）。两源不同 —— trust_priority 需要
    唯一序；同优先级在计划期即 typed 拒绝（不静默按字母序）。
    ``priority_reason``：信任理由（必填、≥4 字符）—— 来源信任是带理由
    的声明，不是绝对正确性。
    """

    model_config = ConfigDict(extra="forbid")

    # 源锚（G18 DeliverySourceRef 域内投影）
    kind: str = Field(pattern="^(session_ref|fabric_parquet|project_artifact|table_rows)$")
    session_id: Optional[str] = Field(default=None, max_length=128)
    ref: Optional[str] = Field(default=None, max_length=256)
    project_id: Optional[str] = Field(default=None, max_length=128)
    artifact_id: Optional[str] = Field(default=None, max_length=128)
    revision_no: Optional[int] = Field(default=None, ge=1)
    table_ref: Optional[str] = Field(default=None, max_length=256)

    # 来源声明
    label: str = Field(min_length=1, max_length=200)
    priority: int = Field(ge=1, le=2)
    priority_reason: str = Field(min_length=4, max_length=500)
    license: Optional[str] = Field(default=None, max_length=200)
    attribution: Optional[str] = Field(default=None, max_length=200)

    # 字段声明（融合语义的输入接缝）
    external_id_field: Optional[str] = Field(default=None, max_length=128)
    #: 共享标识空间声明（如 "unified_social_credit_code"）。external_id
    #: 证据只在两侧声明**同一** id_space 时才可比 —— 不同源各自的内部
    #: 编码（教育局码 vs OSM id）没有可比性，绝不静默比较。
    id_space: Optional[str] = Field(default=None, max_length=128)
    name_field: str = Field(min_length=1, max_length=128)
    address_field: Optional[str] = Field(default=None, max_length=128)
    category_field: Optional[str] = Field(default=None, max_length=128)
    #: 源类别原始值 → 规范词表（仅类别字段存在时允许非空）
    category_map: Dict[str, str] = Field(default_factory=dict)
    #: 时间字段（ISO-8601 可解析文本/数字时间戳）；newest_temporal 语义需要
    temporal_field: Optional[str] = Field(default=None, max_length=128)

    # 输出字段映射 + 逐字段裁决规则（未映射字段不进输出）
    field_mappings: List["FieldMapping"] = Field(default_factory=list, max_length=64)

    def anchor_key(self) -> str:
        """源身份串（不含内容版本 —— 版本由快照指纹补足进 digest）。"""
        return json.dumps(
            self.model_dump(exclude_none=True, mode="json"),
            sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class FieldMapping(BaseModel):
    """单字段映射：源字段 → 输出字段 + 冲突裁决规则（封闭词表）。"""

    model_config = ConfigDict(extra="forbid")

    source_field: str = Field(min_length=1, max_length=128)
    output_field: str = Field(min_length=1, max_length=128)
    #: ADJUDICATION_RULES 之一（字段冲突时的确定性裁决）
    rule: str = Field(default="trust_priority")

    @field_validator("rule")
    @classmethod
    def _v_rule(cls, v: str) -> str:
        if v not in contracts.ADJUDICATION_RULES:
            raise ValueError(f"rule 必须是 {sorted(contracts.ADJUDICATION_RULES)}")
        return v


class MatchStrategy(BaseModel):
    """匹配策略：权重/阈值/归一化/空间范围（全部显式、入 digest）。"""

    model_config = ConfigDict(extra="forbid")

    #: 证据权重：必须恰好覆盖 EVIDENCE_KINDS（含显式 0.0）、非负、Σ=1。
    weights: Dict[str, float]
    #: 自动合并下限（≥ review_min）
    auto_match_min: float = Field(default=0.85, ge=0.0, le=1.0)
    #: 进入审阅带下限（< review_min 即 unmatched）
    review_min: float = Field(default=0.60, ge=0.0, le=1.0)
    #: 歧义边际：best - second < margin → ambiguous（不自动合并）
    ambiguous_margin: float = Field(default=0.05, ge=0.0, le=0.5)
    #: point_poi：距离得分满分区（≤ close 全分）、零分区（> max 不成候选）
    max_match_distance_m: float = Field(default=150.0, gt=0.0)
    close_distance_m: float = Field(default=30.0, gt=0.0)
    #: area/line：重叠率下限（候选过滤）与满分阈值
    overlap_min: float = Field(default=0.30, gt=0.0, le=1.0)
    overlap_full: float = Field(default=0.60, gt=0.0, le=1.0)
    #: 名称归一化管线（有序、封闭词表、入 digest）
    name_normalizers: List[str] = Field(
        default_factory=lambda: ["trim", "casefold", "collapse_spaces"],
        max_length=8)
    #: 类别 unknown 的中性得分系数（诚实记录 unknown，不折算 equal）
    category_unknown_factor: float = Field(default=0.5, ge=0.0, le=1.0)
    #: external_id 双侧均有且不同 → 硬冲突（进审阅，不自动合并）
    id_mismatch_blocks_match: bool = True

    @field_validator("weights")
    @classmethod
    def _v_weights(cls, v: Dict[str, float]) -> Dict[str, float]:
        missing = contracts.EVIDENCE_KINDS - set(v)
        extra = set(v) - contracts.EVIDENCE_KINDS
        if missing or extra:
            raise ValueError(
                f"weights 必须恰好覆盖 {sorted(contracts.EVIDENCE_KINDS)}"
                f"（缺 {sorted(missing)}，多 {sorted(extra)}）")
        for k, w in v.items():
            if w < 0.0:
                raise ValueError(f"权重 {k} 不能为负")
        total = sum(v.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"权重和必须为 1.0（当前 {total}）")
        return v

    @field_validator("name_normalizers")
    @classmethod
    def _v_norm(cls, v: List[str]) -> List[str]:
        for n in v:
            if n not in contracts.NAME_NORMALIZERS:
                raise ValueError(
                    f"name_normalizers 必须是 {sorted(contracts.NAME_NORMALIZERS)} 子集")
        return v

    @model_validator(mode="after")
    def _v_cross(self) -> "MatchStrategy":
        if self.review_min > self.auto_match_min:
            raise ValueError("review_min 不能大于 auto_match_min")
        if self.close_distance_m > self.max_match_distance_m:
            raise ValueError("close_distance_m 不能大于 max_match_distance_m")
        if self.overlap_full < self.overlap_min:
            raise ValueError("overlap_full 不能小于 overlap_min")
        return self

    def distance_weights_positive(self, entity_kind: str) -> bool:
        return self.weights.get("distance", 0.0) > 0.0


class GeometryPolicy(BaseModel):
    """配对融合后的输出几何来源（绝不自动 dissolve）。"""

    model_config = ConfigDict(extra="forbid")

    adoption: str = Field(default="primary_source")
    #: newest_temporal 需要至少一侧声明 temporal_field（交叉校验在 Plan）

    @field_validator("adoption")
    @classmethod
    def _v_adoption(cls, v: str) -> str:
        if v not in contracts.GEOMETRY_ADOPTIONS:
            raise ValueError(
                f"geometry.adoption 必须是 {sorted(contracts.GEOMETRY_ADOPTIONS)}")
        return v


class FusionPlan(BaseModel):
    """融合计划本体（schema_version=1；pairwise 两源）。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=contracts.FUSION_PLAN_SCHEMA_VERSION)
    title: str = Field(default="", max_length=200)
    entity_kind: str
    source_a: FusionSourceSpec
    source_b: FusionSourceSpec
    strategy: MatchStrategy
    geometry: GeometryPolicy = Field(default_factory=GeometryPolicy)
    #: 提交政策（未决审阅项处理）
    commit_policy: str = Field(default="require_resolved")
    invalid_geometry_policy: str = Field(default="exclude_count")
    budgets: Dict[str, int] = Field(default_factory=dict)
    output_name: str = Field(default="融合成果", max_length=200)
    #: 目标描述（可选 —— 写进质量报告语境，帮助用户理解适用性）
    purpose: Optional[str] = Field(default=None, max_length=500)

    # ── 词表校验（fail-closed；不做静默纠正） ──────────────────────────

    @field_validator("entity_kind")
    @classmethod
    def _v_kind(cls, v: str) -> str:
        if v not in contracts.ENTITY_KINDS:
            raise ValueError(f"entity_kind 必须是 {sorted(contracts.ENTITY_KINDS)}")
        return v

    @field_validator("commit_policy")
    @classmethod
    def _v_commit(cls, v: str) -> str:
        if v not in contracts.COMMIT_POLICIES:
            raise ValueError(f"commit_policy 必须是 {sorted(contracts.COMMIT_POLICIES)}")
        return v

    @field_validator("invalid_geometry_policy")
    @classmethod
    def _v_invalig_geo(cls, v: str) -> str:
        if v not in contracts.INVALID_GEOMETRY_POLICIES:
            raise ValueError(
                "invalid_geometry_policy 必须是 "
                f"{sorted(contracts.INVALID_GEOMETRY_POLICIES)}")
        return v

    @field_validator("budgets")
    @classmethod
    def _v_budgets(cls, v: Dict[str, int]) -> Dict[str, int]:
        allowed = {
            "max_source_rows", "max_candidate_pairs", "max_output_features",
            "temp_bytes_budget", "deadline_s",
        }
        for k, n in v.items():
            if k not in allowed:
                raise ValueError(f"未知预算项 {k!r}")
            if int(n) < 1:
                raise ValueError(f"预算 {k} 必须为正")
        return v

    @model_validator(mode="after")
    def _v_cross(self) -> "FusionPlan":
        # 来源优先序唯一（trust_priority 裁决唯一性）
        if self.source_a.priority == self.source_b.priority:
            raise ValueError(
                "两来源 priority 必须不同（1=最高）；完全并列时 "
                "trust_priority 无法唯一裁决")
        # point_poi 必须有正 distance 权重（空间邻近是其核心证据之一；
        # 但不强制 name>0 —— 纯空间+类别场景允许显式声明）
        if self.entity_kind == "point_poi":
            if self.strategy.weights.get("distance", 0.0) <= 0.0:
                raise ValueError(
                    "point_poi 实体必须给 distance 正权重（空间邻近≠同一，"
                    "但完全无视空间会引入跨城同名误配）")
            if not self.strategy.id_mismatch_blocks_match and \
                    self.strategy.weights.get("external_id", 0.0) > 0.0:
                raise ValueError(
                    "external_id 正权重与 id_mismatch_blocks_match=False 矛盾："
                    "id 冲突是硬证据，必须阻断自动合并")
        # external_id 证据要求可比的共享标识空间
        if self.strategy.weights.get("external_id", 0.0) > 0.0:
            ida, idb = self.source_a.id_space, self.source_b.id_space
            if not (ida and idb and ida == idb):
                raise ValueError(
                    "external_id 正权重要求两源声明同一 id_space"
                    "（各自内部编码不可比；无可比 id 空间请把该权重显式置 0）")
            if not (self.source_a.external_id_field
                    and self.source_b.external_id_field):
                raise ValueError(
                    "external_id 正权重要求两源都声明 external_id_field")
        # area/line 重叠证据
        if self.entity_kind in ("area_link", "line_link"):
            if self.strategy.weights.get("distance", 0.0) <= 0.0:
                raise ValueError(
                    f"{self.entity_kind} 实体必须给 distance 正权重（重叠率证据）")
        # newest_temporal 语义需要时间字段
        if self.geometry.adoption == "newest_temporal" and not (
                self.source_a.temporal_field and self.source_b.temporal_field):
            raise ValueError(
                "geometry.adoption=newest_temporal 要求两源都声明 temporal_field")
        # category_map 只在类别字段存在时允许
        for side, src in (("a", self.source_a), ("b", self.source_b)):
            if src.category_map and not src.category_field:
                raise ValueError(
                    f"source_{side}.category_map 需要先声明 category_field")
            # 映射字段不得使用保留前缀
            for m in src.field_mappings:
                if m.output_field.startswith(contracts.PROVENANCE_KEY):
                    raise ValueError(
                        f"source_{side} 输出字段不得使用保留前缀 "
                        f"{contracts.PROVENANCE_KEY!r}：{m.output_field}")
            # newest_temporal / temporal 裁决需要字段
            for m in src.field_mappings:
                if m.rule == "newest_temporal" and not src.temporal_field:
                    raise ValueError(
                        f"source_{side} 字段 {m.source_field} 声明 "
                        "newest_temporal 规则但源未声明 temporal_field")
        # 输出字段名跨源冲突检测（同输出名两侧都要 → 走裁决；这里允许，
        # 由执行期冲突判定处理；仅拒绝同名不同 rule 的歧义声明）
        rules: Dict[str, str] = {}
        for side, src in (("a", self.source_a), ("b", self.source_b)):
            seen_local: Dict[str, str] = {}
            for m in src.field_mappings:
                if m.output_field in seen_local:
                    raise ValueError(
                        f"source_{side} 内输出字段重名：{m.output_field}")
                seen_local[m.output_field] = m.rule
                prev = rules.get(m.output_field)
                if prev is not None and prev != m.rule:
                    raise ValueError(
                        f"输出字段 {m.output_field} 两侧声明了不同裁决规则 "
                        f"({prev} vs {m.rule})——语义歧义，请统一")
                rules[m.output_field] = m.rule
        total_outputs = len(rules) + 1  # provenance
        if total_outputs > contracts.MAX_OUTPUT_FIELDS:
            raise ValueError(
                f"输出字段总数 {total_outputs} 超过上限 "
                f"{contracts.MAX_OUTPUT_FIELDS}")
        return self

    # ── 规范化 digest ─────────────────────────────────────────────

    def normalized_payload(self) -> Dict[str, Any]:
        """参与 digest 的规范化载荷（全语义自由度 + 源锚）。"""
        return {
            "schema_version": self.schema_version,
            "method_version": contracts.METHOD_VERSION,
            "entity_kind": self.entity_kind,
            "source_a": self.source_a.anchor_key(),
            "source_b": self.source_b.anchor_key(),
            "strategy": self.strategy.model_dump(exclude_none=True),
            "geometry": self.geometry.model_dump(exclude_none=True),
            "commit_policy": self.commit_policy,
            "invalid_geometry_policy": self.invalid_geometry_policy,
            "budgets": dict(sorted(self.budgets.items())),
        }

    def normalized_digest(self) -> str:
        blob = json.dumps(self.normalized_payload(), sort_keys=True,
                          ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]

    def source_by_priority(self) -> "FusionSourceSpec":
        """按优先序返回高优先源（trust_priority/primary_source 锚）。"""
        if self.source_a.priority < self.source_b.priority:
            return self.source_a
        return self.source_b

    def effective_budgets(self) -> Dict[str, int]:
        """计划内预算覆盖默认值，并 clamp 到域硬顶（执行前估算、执行中强制）。"""
        out: Dict[str, float] = {
            "max_source_rows": contracts.DEFAULT_MAX_SOURCE_ROWS,
            "max_candidate_pairs": contracts.DEFAULT_MAX_CANDIDATE_PAIRS,
            "max_output_features": contracts.DEFAULT_MAX_OUTPUT_FEATURES,
            "temp_bytes_budget": contracts.DEFAULT_TEMP_BYTES_BUDGET,
            "deadline_s": contracts.DEFAULT_DEADLINE_S,
        }
        hard: Dict[str, float] = {
            "max_source_rows": contracts.HARD_MAX_SOURCE_ROWS,
            "max_candidate_pairs": contracts.HARD_MAX_CANDIDATE_PAIRS,
            "max_output_features": contracts.HARD_MAX_OUTPUT_FEATURES,
            "temp_bytes_budget": contracts.HARD_MAX_TEMP_BYTES,
            "deadline_s": contracts.HARD_MAX_DEADLINE_S,
        }
        for k, v in self.budgets.items():
            out[k] = max(1, min(int(v), int(hard[k])))
        return {k: int(v) for k, v in out.items()}

    def outputs_of(self, side: str) -> List[FieldMapping]:
        src = self.source_a if side == "a" else self.source_b
        return list(src.field_mappings)


def parse_plan(data: Dict[str, Any]) -> FusionPlan:
    """dict → FusionPlan（结构/词表违规 → typed invalid_plan）。"""
    try:
        return FusionPlan.model_validate(data)
    except DataFusionError:
        raise
    except Exception as exc:  # pydantic ValidationError 等
        raise DataFusionError("invalid_plan", f"融合计划无效：{exc}") from exc


FusionSourceSpec.model_rebuild()
