"""SpatialSemanticContract.v1 —— 实例级空间语义契约（GOAL 01 核心 schema）。

定位（BASELINE G1 的空位；决策 D1）：MethodUseContract 是**算法静态目录**
（schema v1，无数据侧）、measurement.py 是**字段级画像**、spatial_receipt
是 **inspect 期收据** —— 本契约是三者之外的第四层：**一次数据 × 范围 ×
操作的运行时绑定证据**，回答"这次计算在哪个 CRS 里做、参数单位是什么、
分母口径是什么、依据是什么"。

纪律（不可妥协）：

- **未知 ≠ 猜测**：crs/单位/分母证据不足时字段为 ""/"unknown"，绝不
  填默认值；消费方（validate/preflight）按词表判定 blocked/requires_
  clarification，绝不静默放行；
- **bounded + versioned + serializable**：列表全带上限（有界证据纪律），
  ``to_dict/from_dict`` 版本 fail-closed（复刻 measurement.py 先例）；
- **descriptor 级**：契约只携带 crs/bbox/采样坐标结构等描述符证据，
  绝不携带几何全量（不进 prompt、不进 LLM）；
- 纯数据模型：本模块无推导逻辑（inspect/resolve/validate 各司其职）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from app.services.gis_harness.spatial_semantics.vocabulary import (
    AXIS_ORDER_VOCABULARY,
    CALCULATION_MODE_VOCABULARY,
    CRS_SOURCE_VOCABULARY,
    DENOMINATOR_VOCABULARY,
    DISTANCE_UNIT_VOCABULARY,
    SCALE_LEVEL_VOCABULARY,
    SCOPE_OVERLAP_VOCABULARY,
    SPATIAL_PRECISION_VOCABULARY,
)

#: 契约 schema 版本（结构变更时 +1；from_dict 对未知版本 fail-closed）。
SPATIAL_SEMANTIC_CONTRACT_VERSION = 1

# 有界证据上限（与 method_use_contract._MAX_FINDINGS=12 同量级纪律）。
_MAX_REASON_CODES = 12
_MAX_ASSUMPTIONS = 8
_MAX_NORMALIZATION = 4
_MAX_FIELD_LEN = 64


class ScopeEvidence(BaseModel):
    """请求范围（AOI）证据 —— 范围依据可解释（BASELINE G13 的补位）。

    AOI 在既有链路只有 name/level（intent.py ScopeIntent）；本结构补上
    几何级证据（bbox + bbox_crs）与来源，供 scope×data 对账。
    """

    name: str = Field(default="", max_length=_MAX_FIELD_LEN)
    level: str = Field(default="", max_length=16)     # country/…/district/unknown
    bbox: Optional[List[float]] = None                # [minx, miny, maxx, maxy]
    bbox_crs: str = Field(default="", max_length=_MAX_FIELD_LEN)
    source: str = Field(                              # user/admin/memory/viewport/none
        default="none", max_length=16)
    overlap_with_data: str = "unknown"                # SCOPE_OVERLAP_VOCABULARY

    @field_validator("overlap_with_data")
    @classmethod
    def _v_overlap(cls, v: str) -> str:
        if v not in SCOPE_OVERLAP_VOCABULARY:
            raise ValueError(f"overlap_with_data 越词表: {v!r}")
        return v


class ContractReasonCode(BaseModel):
    """契约内理由码行（code + 有界明细 + 涉及字段名）。"""

    code: str = Field(default="", max_length=48)
    detail: str = Field(default="", max_length=200)
    field: str = Field(default="", max_length=48)     # 涉及契约字段名（治理披露）


class SpatialSemanticContract(BaseModel):
    """一次空间数据 × 范围的语义绑定（typed、bounded、versioned）。

    字段三组：数据侧（source_crs…geometry_type）、范围侧（requested_scope）、
    计算语义侧（calculation_mode…density_unit）+ 治理（assumptions/reason_codes）。
    """

    schema_version: int = SPATIAL_SEMANTIC_CONTRACT_VERSION

    # — 数据侧 —
    dataset_ref: str = Field(default="", max_length=128)   # ref:xxx / inline
    dataset_fingerprint: str = Field(default="", max_length=128)
    source_crs: str = Field(default="", max_length=_MAX_FIELD_LEN)
    crs_source: str = "unknown"                            # CRS_SOURCE_VOCABULARY
    crs_class: str = "unknown"                             # crs_safety 四态
    axis_order: str = "unknown"                            # AXIS_ORDER_VOCABULARY
    axis_order_evidence: str = Field(default="", max_length=120)
    geometry_type: str = "unknown"                         # point/line/polygon/mixed/unknown
    feature_count: int = 0
    data_bbox: Optional[List[float]] = None                # 原始坐标 bbox（未判 CRS）
    longitude_convention: str = "unknown"                  # pm180/e360/ambiguous/unknown

    # — 范围侧 —
    requested_scope: ScopeEvidence = Field(default_factory=ScopeEvidence)

    # — 计算语义侧（由 resolve 填充；inspect 阶段保持 unknown）—
    calculation_mode: str = "unknown"                      # CALCULATION_MODE_VOCABULARY
    working_crs: str = Field(default="", max_length=_MAX_FIELD_LEN)
    working_crs_basis: str = Field(default="", max_length=200)
    distance_unit: str = "unknown"                         # DISTANCE_UNIT_VOCABULARY/unknown
    area_unit: str = "unknown"                             # AREA_UNIT_VOCABULARY/unknown
    stat_denominator: str = "none"                         # DENOMINATOR_VOCABULARY
    denominator_unit: str = Field(default="", max_length=32)
    density_unit: str = Field(default="", max_length=48)   # 合成口径（如 个/km²）
    spatial_precision: str = "unknown"                     # SPATIAL_PRECISION_VOCABULARY
    scale_level: str = "unknown"                           # SCALE_LEVEL_VOCABULARY
    datum_normalization: List[str] = Field(                # 归一链（bd09→gcj02→wgs84）
        default_factory=list, max_length=_MAX_NORMALIZATION)

    # — 治理 —
    assumptions: List[str] = Field(default_factory=list, max_length=_MAX_ASSUMPTIONS)
    reason_codes: List[ContractReasonCode] = Field(
        default_factory=list, max_length=_MAX_REASON_CODES)

    # — 校验（词表 fail-closed：越词表即构造失败，防伪造）—
    @field_validator("crs_source")
    @classmethod
    def _v_crs_source(cls, v: str) -> str:
        if v not in CRS_SOURCE_VOCABULARY:
            raise ValueError(f"crs_source 越词表: {v!r}")
        return v

    @field_validator("axis_order")
    @classmethod
    def _v_axis_order(cls, v: str) -> str:
        if v not in AXIS_ORDER_VOCABULARY:
            raise ValueError(f"axis_order 越词表: {v!r}")
        return v

    @field_validator("calculation_mode")
    @classmethod
    def _v_calculation_mode(cls, v: str) -> str:
        if v not in CALCULATION_MODE_VOCABULARY:
            raise ValueError(f"calculation_mode 越词表: {v!r}")
        return v

    @field_validator("stat_denominator")
    @classmethod
    def _v_denominator(cls, v: str) -> str:
        if v not in DENOMINATOR_VOCABULARY:
            raise ValueError(f"stat_denominator 越词表: {v!r}")
        return v

    @field_validator("distance_unit")
    @classmethod
    def _v_distance_unit(cls, v: str) -> str:
        if v != "unknown" and v not in DISTANCE_UNIT_VOCABULARY:
            raise ValueError(f"distance_unit 越词表: {v!r}")
        return v

    @field_validator("scale_level")
    @classmethod
    def _v_scale_level(cls, v: str) -> str:
        if v not in SCALE_LEVEL_VOCABULARY:
            raise ValueError(f"scale_level 越词表: {v!r}")
        return v

    @field_validator("spatial_precision")
    @classmethod
    def _v_precision(cls, v: str) -> str:
        if v not in SPATIAL_PRECISION_VOCABULARY:
            raise ValueError(f"spatial_precision 越词表: {v!r}")
        return v

    # overlap 顶层投影（scope.overlap_with_data 的只读别名，方便对账）
    @property
    def scope_overlap(self) -> str:
        return self.requested_scope.overlap_with_data

    # ── 序列化（版本 fail-closed；bounded 投影）─────────────────────────────
    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "dataset_ref": self.dataset_ref,
            "dataset_fingerprint": self.dataset_fingerprint,
            "source_crs": self.source_crs,
            "crs_source": self.crs_source,
            "crs_class": self.crs_class,
            "axis_order": self.axis_order,
            "axis_order_evidence": self.axis_order_evidence,
            "geometry_type": self.geometry_type,
            "feature_count": self.feature_count,
            "data_bbox": (
                [round(float(v), 6) for v in self.data_bbox[:4]]
                if self.data_bbox and len(self.data_bbox) == 4 else None
            ),
            "longitude_convention": self.longitude_convention,
            "requested_scope": self.requested_scope.model_dump(),
            "calculation_mode": self.calculation_mode,
            "working_crs": self.working_crs,
            "working_crs_basis": self.working_crs_basis,
            "distance_unit": self.distance_unit,
            "area_unit": self.area_unit,
            "stat_denominator": self.stat_denominator,
            "denominator_unit": self.denominator_unit,
            "density_unit": self.density_unit,
            "spatial_precision": self.spatial_precision,
            "scale_level": self.scale_level,
            "datum_normalization": list(self.datum_normalization[:_MAX_NORMALIZATION]),
            "assumptions": list(self.assumptions[:_MAX_ASSUMPTIONS]),
            "reason_codes": [
                {
                    "code": rc.code,
                    "detail": rc.detail[:200],
                    "field": rc.field[:48],
                }
                for rc in self.reason_codes[:_MAX_REASON_CODES]
            ],
        }

    @classmethod
    def from_dict(cls, data: Any) -> "SpatialSemanticContract":
        """反序列化（fail-closed：未知版本/非 dict 拒收，不静默降级）。"""
        if not isinstance(data, dict):
            raise ValueError("spatial semantic contract 必须是 dict")
        version = data.get("schema_version")
        if version != SPATIAL_SEMANTIC_CONTRACT_VERSION:
            raise ValueError(
                f"不支持的 schema_version: {version!r}"
                f"（本契约只认 {SPATIAL_SEMANTIC_CONTRACT_VERSION}）"
            )
        scope_raw = data.get("requested_scope")
        scope = (
            ScopeEvidence(**scope_raw) if isinstance(scope_raw, dict)
            else ScopeEvidence()
        )
        rc_raw = data.get("reason_codes")
        reason_codes = [
            ContractReasonCode(
                code=str(r.get("code") or ""),
                detail=str(r.get("detail") or ""),
                field=str(r.get("field") or ""),
            )
            for r in (rc_raw if isinstance(rc_raw, list) else [])[:_MAX_REASON_CODES]
            if isinstance(r, dict)
        ]
        return cls(
            dataset_ref=str(data.get("dataset_ref") or "")[:128],
            dataset_fingerprint=str(data.get("dataset_fingerprint") or "")[:128],
            source_crs=str(data.get("source_crs") or "")[:_MAX_FIELD_LEN],
            crs_source=str(data.get("crs_source") or "unknown"),
            crs_class=str(data.get("crs_class") or "unknown"),
            axis_order=str(data.get("axis_order") or "unknown"),
            axis_order_evidence=str(data.get("axis_order_evidence") or "")[:120],
            geometry_type=str(data.get("geometry_type") or "unknown"),
            feature_count=int(data.get("feature_count") or 0),
            data_bbox=data.get("data_bbox") if isinstance(
                data.get("data_bbox"), list) else None,
            longitude_convention=str(data.get("longitude_convention") or "unknown"),
            requested_scope=scope,
            calculation_mode=str(data.get("calculation_mode") or "unknown"),
            working_crs=str(data.get("working_crs") or "")[:_MAX_FIELD_LEN],
            working_crs_basis=str(data.get("working_crs_basis") or "")[:200],
            distance_unit=str(data.get("distance_unit") or "unknown"),
            area_unit=str(data.get("area_unit") or "unknown"),
            stat_denominator=str(data.get("stat_denominator") or "none"),
            denominator_unit=str(data.get("denominator_unit") or "")[:32],
            density_unit=str(data.get("density_unit") or "")[:48],
            spatial_precision=str(data.get("spatial_precision") or "unknown"),
            scale_level=str(data.get("scale_level") or "unknown"),
            datum_normalization=[
                str(s)[:64] for s in (data.get("datum_normalization")
                                      if isinstance(data.get("datum_normalization"), list)
                                      else [])[:_MAX_NORMALIZATION]
            ],
            assumptions=[
                str(s)[:200] for s in (data.get("assumptions")
                                       if isinstance(data.get("assumptions"), list)
                                       else [])[:_MAX_ASSUMPTIONS]
            ],
            reason_codes=reason_codes,
        )


__all__ = [
    "SPATIAL_SEMANTIC_CONTRACT_VERSION",
    "ScopeEvidence",
    "ContractReasonCode",
    "SpatialSemanticContract",
]
