"""G38 JoinPlan 契约 —— 版本化关联计划（pydantic，extra=forbid）。

计划 = 用户可编辑的作者态（草稿保存不执行、不改源、不改 MapSpec）。
digest 覆盖**所有影响执行语义**的自由度：源身份/版本/范围/模式/键与
变换/保留侧/基数/空值政策/空间谓词与多命中策略/字段映射/聚合/预算。
预览与执行基于同一 ``normalized_digest`` —— 旧预览在计划或源变化后
按 digest 失效（J17）。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.data_join_authoring import contracts
from app.services.data_join_authoring.errors import DataJoinError
from app.services.data_join_authoring.keys import KeyFieldSpec


class JoinSourceRef(BaseModel):
    """源锚（G18 DeliverySourceRef 的域内投影；解析/授权仍走 G18）。

    ``selection_handle``：可选 G24 权威选择句柄 —— 由用户在 UI/工具中先
    materialize 为 session ref 后作为 ``session_ref`` 输入是首选路径；
    直接给 handle 时本域在准备阶段解析为完整范围并冻结（>10k 由 G24
    自身闸拒绝，本域不静默截断）。
    """

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(pattern="^(session_ref|fabric_parquet|project_artifact|table_rows)$")
    session_id: Optional[str] = Field(default=None, max_length=128)
    ref: Optional[str] = Field(default=None, max_length=256)
    project_id: Optional[str] = Field(default=None, max_length=128)
    artifact_id: Optional[str] = Field(default=None, max_length=128)
    revision_no: Optional[int] = Field(default=None, ge=1)
    table_ref: Optional[str] = Field(default=None, max_length=256)

    def anchor_key(self) -> str:
        """源身份串（不含内容版本 —— 版本由快照指纹补足进 digest）。"""
        return json.dumps(self.model_dump(exclude_none=True, mode="json"), sort_keys=True)


class KeyField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    left_field: str = Field(min_length=1, max_length=128)
    right_field: str = Field(min_length=1, max_length=128)
    transform: Optional[str] = None
    zfill_width: int = Field(default=0, ge=0, le=32)

    def to_spec(self) -> KeyFieldSpec:
        return KeyFieldSpec(
            left_field=self.left_field,
            right_field=self.right_field,
            transform=self.transform,
            zfill_width=self.zfill_width,
        )


class FieldMappingEntry(BaseModel):
    """输出字段选择：来自左/右源的字段、可选别名。"""

    model_config = ConfigDict(extra="forbid")

    side: str = Field(pattern="^(left|right)$")
    field: str = Field(min_length=1, max_length=128)
    alias: Optional[str] = Field(default=None, min_length=1, max_length=128)

    def output_name(self) -> str:
        return self.alias or self.field


class AggregateSpec(BaseModel):
    """闭合聚合（多对一 / aggregate 多命中）。null 不当 0。"""

    model_config = ConfigDict(extra="forbid")

    function: str  # count/sum/mean/min/max
    right_field: Optional[str] = Field(default=None, max_length=128)  # count 可省
    alias: Optional[str] = Field(default=None, min_length=1, max_length=128)

    def output_name(self) -> str:
        if self.alias:
            return self.alias
        if self.function == "count":
            return "match_count"
        suffix = f"_{self.function}"
        return (self.right_field or "") + suffix


class SpatialOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    predicate: str = "within"  # within | within_or_boundary
    multi_match: str = "require_unique"
    pick_field: Optional[str] = Field(default=None, max_length=128)
    pick_direction: Optional[str] = None  # min | max（pick_by_field 必填）
    invalid_geometry_policy: str = "exclude_count"


class JoinPlan(BaseModel):
    """关联计划本体（schema_version=1）。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=contracts.JOIN_PLAN_SCHEMA_VERSION)
    title: str = Field(default="", max_length=200)
    mode: str  # attribute | spatial_point_in_polygon
    left: JoinSourceRef
    right: JoinSourceRef
    keys: List[KeyField] = Field(default_factory=list, max_length=8)
    keep_side: str = "left"  # left | inner
    output_cardinality: str = "many_to_one"
    null_policy: str = "never_match"
    spatial: Optional[SpatialOptions] = None
    aggregates: List[AggregateSpec] = Field(default_factory=list, max_length=16)
    field_map: List[FieldMappingEntry] = Field(default_factory=list, max_length=256)
    budgets: Dict[str, int] = Field(default_factory=dict)
    output_name: str = Field(default="关联结果", max_length=200)

    # ── 词表校验（fail-closed；不做静默纠正） ──────────────────────────

    @field_validator("mode")
    @classmethod
    def _v_mode(cls, v: str) -> str:
        if v not in contracts.JOIN_MODES:
            raise ValueError(f"mode 必须是 {sorted(contracts.JOIN_MODES)}")
        return v

    @field_validator("keep_side")
    @classmethod
    def _v_keep(cls, v: str) -> str:
        if v not in contracts.KEEP_SIDES:
            raise ValueError(f"keep_side 必须是 {sorted(contracts.KEEP_SIDES)}")
        return v

    @field_validator("output_cardinality")
    @classmethod
    def _v_card(cls, v: str) -> str:
        if v not in contracts.OUTPUT_CARDINALITIES:
            raise ValueError(
                f"output_cardinality 必须是 {sorted(contracts.OUTPUT_CARDINALITIES)}；"
                "many_to_many 显式不支持（诊断阶段先估乘积）")
        return v

    @field_validator("null_policy")
    @classmethod
    def _v_null(cls, v: str) -> str:
        if v not in contracts.NULL_POLICIES:
            raise ValueError(f"null_policy 必须是 {sorted(contracts.NULL_POLICIES)}")
        return v

    @field_validator("budgets")
    @classmethod
    def _v_budgets(cls, v: Dict[str, int]) -> Dict[str, int]:
        allowed = {
            "max_source_rows", "max_join_pairs", "max_output_rows",
            "temp_bytes_budget",
        }
        for k, n in v.items():
            if k not in allowed:
                raise ValueError(f"未知预算项 {k!r}")
            if not (1 <= int(n)):
                raise ValueError(f"预算 {k} 必须为正整数")
        return v

    @model_validator(mode="after")
    def _v_cross(self) -> "JoinPlan":
        if self.mode == "attribute":
            if not self.keys:
                raise ValueError("attribute 模式必须声明至少一个键字段")
            if self.spatial is not None:
                raise ValueError("attribute 模式不接受 spatial 选项")
        else:
            if self.keys:
                raise ValueError("spatial_point_in_polygon 模式不接受属性键")
            if self.spatial is None:
                raise ValueError("spatial_point_in_polygon 模式必须给出 spatial 选项")
            sp = self.spatial
            if sp.predicate not in contracts.SPATIAL_PREDICATES:
                raise ValueError(
                    f"spatial.predicate 必须是 {sorted(contracts.SPATIAL_PREDICATES)}")
            if sp.multi_match not in contracts.MULTI_MATCH_POLICIES:
                raise ValueError(
                    f"spatial.multi_match 必须是 {sorted(contracts.MULTI_MATCH_POLICIES)}")
            if sp.invalid_geometry_policy not in contracts.INVALID_GEOMETRY_POLICIES:
                raise ValueError(
                    "spatial.invalid_geometry_policy 必须是 "
                    f"{sorted(contracts.INVALID_GEOMETRY_POLICIES)}")
            if sp.multi_match == "pick_by_field":
                if not sp.pick_field:
                    raise ValueError("pick_by_field 必须给出 pick_field")
                if sp.pick_direction not in contracts.PICK_DIRECTIONS:
                    raise ValueError(
                        f"pick_direction 必须是 {sorted(contracts.PICK_DIRECTIONS)}")
        for agg in self.aggregates:
            if agg.function not in contracts.AGGREGATE_FUNCTIONS:
                raise ValueError(
                    f"聚合函数必须是 {sorted(contracts.AGGREGATE_FUNCTIONS)}")
            if agg.function != "count" and not agg.right_field:
                raise ValueError(f"{agg.function} 聚合必须给出 right_field")
        # 基数与聚合/多命中策略的一致性（spatial 模式下基数由 multi_match 推导）
        if self.mode == "spatial_point_in_polygon":
            derived = ("one_to_many"
                       if self.spatial.multi_match == "expand_all"
                       else "many_to_one")
            if self.output_cardinality != derived:
                raise ValueError(
                    f"spatial.{self.spatial.multi_match} 对应基数 {derived}，"
                    f"与声明的 {self.output_cardinality} 不一致")
        else:
            if self.aggregates and self.output_cardinality != "many_to_one":
                raise ValueError("属性聚合仅支持 many_to_one（右值按键聚合后单行输出）")
        return self

    # ── 规范化 digest ─────────────────────────────────────────────

    def normalized_payload(self) -> Dict[str, Any]:
        """参与 digest 的规范化载荷（全语义自由度 + 源锚）。"""
        return {
            "schema_version": self.schema_version,
            "method_version": contracts.METHOD_VERSION,
            "mode": self.mode,
            "left": self.left.anchor_key(),
            "right": self.right.anchor_key(),
            "keys": [k.model_dump(exclude_none=True) for k in self.keys],
            "keep_side": self.keep_side,
            "output_cardinality": self.output_cardinality,
            "null_policy": self.null_policy,
            "spatial": self.spatial.model_dump(exclude_none=True) if self.spatial else None,
            "aggregates": [a.model_dump(exclude_none=True) for a in self.aggregates],
            "field_map": [f.model_dump(exclude_none=True) for f in self.field_map],
            "budgets": dict(sorted(self.budgets.items())),
        }

    def normalized_digest(self) -> str:
        blob = json.dumps(self.normalized_payload(), sort_keys=True,
                          ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]

    def effective_budgets(self) -> Dict[str, int]:
        """计划内预算覆盖默认值，并 clamp 到域硬顶（执行前估算、执行中强制）。"""
        out = {
            "max_source_rows": contracts.DEFAULT_MAX_SOURCE_ROWS,
            "max_join_pairs": contracts.DEFAULT_MAX_JOIN_PAIRS,
            "max_output_rows": contracts.DEFAULT_MAX_OUTPUT_ROWS,
            "temp_bytes_budget": contracts.DEFAULT_TEMP_BYTES_BUDGET,
        }
        hard = {
            "max_source_rows": contracts.HARD_MAX_SOURCE_ROWS,
            "max_join_pairs": contracts.HARD_MAX_JOIN_PAIRS,
            "max_output_rows": contracts.HARD_MAX_OUTPUT_ROWS,
            "temp_bytes_budget": contracts.HARD_TEMP_BYTES,
        }
        for k, v in self.budgets.items():
            out[k] = max(1, min(int(v), hard[k]))
        return out

    def key_specs(self) -> List[KeyFieldSpec]:
        return [k.to_spec() for k in self.keys]


def parse_plan(data: Dict[str, Any]) -> JoinPlan:
    """dict → JoinPlan（结构/词表违规 → typed invalid_plan）。"""
    try:
        return JoinPlan.model_validate(data)
    except DataJoinError:
        raise
    except Exception as exc:  # pydantic ValidationError 等
        raise DataJoinError("invalid_plan", f"关联计划无效：{exc}") from exc
