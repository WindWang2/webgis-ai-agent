"""GISDatasetDescriptor —— 数据集语义的权威契约记录（ADR-0215，方向 F01）。

#1488（ADR-0207）回答"怎么推"语义（measurement/field_resolver/scale_semantics
是推导的单一事实源，本模块不改其内部）；本模块回答"推出来的语义是**哪个版本**"：

- **versioned**：``descriptor_version``；``from_dict`` 对未知版本 fail-closed 拒收
  （与 measurement.DatasetMeasurementProfile 同纪律）——绝不把旧语义猜成新语义；
- **bounded**：fields ≤64（与 DatasetProfile 同上限）、source refs ≤8、quality
  signals ≤16、provenance ≤8、逐字段 evidence ≤6 / checks ≤4 / roles ≤6；载荷字节
  上限 MAX_DESCRIPTOR_BYTES 由 store 层强制（契约层拒绝超限构造输出）；
- **deterministic**：``descriptor_fingerprint`` 对全部语义内容哈希（canonical JSON、
  排序键）；``derived_at`` 等易变字段不入指纹 —— 同一数据集版本在 ingest / query /
  map / replay 四条路径恒产出同一指纹（ADR-0215 DoD #1）；
- **单向映射**：descriptor → 既有四形状的投影（to_dataset_profile /
  to_measurement_profile / semantic_view / d1_view）全部**委托既有实现**，词表零
  复制；禁止反向（任何模块不得从原始数据另猜一份 descriptor 已有的语义）；
- **诚实缺省**：未知就是未知（crs="" / has_time_field=None / unit=""），绝不虚构
  （与 DatasetProfile 同一红线）。

变更对账：``compare_descriptors`` 复用 fingerprints.FingerprintSet.classify_change
与 staleness_verdict（不新增变更语义），并给出字段级稳定 reason codes ——
schema / 字段类型 / 单位 / 角色 / CRS / 时间语义任一变化都产生可解释结果。
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field, field_validator

from app.lib.data.fingerprints import (
    ChangeClass,
    FingerprintSet,
    canonical_fingerprint,
    classify_change,
    staleness_verdict,
)
from app.lib.data.fingerprints import StalenessVerdict

#: 契约版本（from_dict 对未知版本 fail-closed 拒收）。
DESCRIPTOR_VERSION = 1

#: 有界上限（硬上限；测试锁定）。
MAX_FIELDS = 64                       # 与 DatasetProfile.MAX_PROFILE_FIELDS 同值同源
MAX_GEOMETRY_TYPES = 8                # 与 DatasetProfile 同值同源
MAX_SOURCE_REFS = 8
MAX_QUALITY_SIGNALS = 16
MAX_PROVENANCE = 8
MAX_FIELD_ROLES = 6
MAX_FIELD_EVIDENCE = 6
MAX_FIELD_CHECKS = 4
MAX_SAMPLING_NOTES = 4
MAX_FIELD_NAME = 128

#: 指纹前缀（稳定格式；消费方按前缀识别 descriptor 语义证据）。
FINGERPRINT_PREFIX = "dsd-v1:"
SCHEMA_FINGERPRINT_PREFIX = "dsd-schema-v1:"

# ── 稳定 reason codes（分类级）─────────────────────────────────────────────
CODE_UNCHANGED = "DESCRIPTOR_UNCHANGED"
CODE_CRS_CHANGED = "DESCRIPTOR_CRS_CHANGED"
CODE_SCHEMA_CHANGED = "DESCRIPTOR_SCHEMA_CHANGED"
CODE_CONTENT_EVIDENCE_CHANGED = "DESCRIPTOR_CONTENT_EVIDENCE_CHANGED"
CODE_METADATA_ONLY_CHANGED = "DESCRIPTOR_METADATA_ONLY_CHANGED"
CODE_UNCOMPARABLE = "DESCRIPTOR_UNCOMPARABLE"

# ── 字段级 / 结构级 reason codes ───────────────────────────────────────────
CODE_FIELD_ADDED = "DESCRIPTOR_FIELD_ADDED"
CODE_FIELD_REMOVED = "DESCRIPTOR_FIELD_REMOVED"
CODE_FIELD_RETYPE = "DESCRIPTOR_FIELD_RETYPE"
CODE_FIELD_UNIT_CHANGED = "DESCRIPTOR_FIELD_UNIT_CHANGED"
CODE_FIELD_KIND_CHANGED = "DESCRIPTOR_FIELD_KIND_CHANGED"
CODE_FIELD_ROLE_CHANGED = "DESCRIPTOR_FIELD_ROLE_CHANGED"
CODE_FIELD_NULLABILITY_CHANGED = "DESCRIPTOR_FIELD_NULLABILITY_CHANGED"
CODE_GEOMETRY_CHANGED = "DESCRIPTOR_GEOMETRY_CHANGED"
CODE_RASTER_SHAPE_CHANGED = "DESCRIPTOR_RASTER_SHAPE_CHANGED"
CODE_TEMPORAL_CHANGED = "DESCRIPTOR_TEMPORAL_CHANGED"
CODE_VERSION_BUMPED = "DESCRIPTOR_VERSION_BUMPED"

# ── 基础设施级 reason codes（store / 消费面共用）───────────────────────────
CODE_VERSION_UNSUPPORTED = "DESCRIPTOR_VERSION_UNSUPPORTED"
CODE_STORE_CORRUPT = "DESCRIPTOR_STORE_CORRUPT"
CODE_TOO_LARGE = "DESCRIPTOR_TOO_LARGE"
CODE_MISSING = "DESCRIPTOR_MISSING"
CODE_FINGERPRINT_MISMATCH = "DESCRIPTOR_FINGERPRINT_MISMATCH"

_STALE_REASON_BY_CLASS: Dict[str, str] = {
    ChangeClass.CRS.value: CODE_CRS_CHANGED,
    ChangeClass.SCHEMA.value: CODE_SCHEMA_CHANGED,
    ChangeClass.CONTENT.value: CODE_CONTENT_EVIDENCE_CHANGED,
    ChangeClass.METADATA_ONLY.value: CODE_METADATA_ONLY_CHANGED,
    ChangeClass.UNKNOWN.value: CODE_UNCOMPARABLE,
}


class SourceRef(BaseModel):
    """来源引用（引用 + 可选指纹；**绝不搬运数据或全量 schema**）。"""

    type: str = ""          # ref / artifact / url / catalog_item …
    ref: str = ""           # 引用标识（≤200）
    fingerprint: str = ""   # 来源内容指纹（如 ref content_hash），可缺省

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "type": str(self.type)[:32],
            "ref": str(self.ref)[:200],
            "fingerprint": str(self.fingerprint)[:128],
        }


class FieldEntry(BaseModel):
    """单字段语义条目（schema + 角色 + 量纲 + 证据，全部有界）。

    roles / measurement_* 的值域由既有词表定义（SemanticFieldRole /
    MeasurementKind / UnitDimension）——本契约只冻结结果字符串，不复制词表。
    """

    name: str
    dtype: str = ""
    nullable: Optional[bool] = None        # None = 无证据（诚实缺省）
    null_ratio: Optional[float] = None
    roles: List[str] = Field(default_factory=list)
    role_confidence: str = ""              # 角色判定的置信分级（语义闸消费）
    measurement_kind: str = ""             # MeasurementKind.value；"" = 证据不足
    unit_dimension: str = ""               # UnitDimension.value；"" = 未知
    unit: str = ""                         # canonical unit 名；"" = 未知
    kind_confidence: str = ""
    unit_confidence: str = ""
    domain_hint: Optional[List[float]] = None
    center_hint: Optional[float] = None
    evidence: List[str] = Field(default_factory=list)
    checks: List[Dict[str, str]] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return str(v)[:MAX_FIELD_NAME]

    @field_validator("roles")
    @classmethod
    def _roles(cls, v: List[str]) -> List[str]:
        return [str(r)[:64] for r in v[:MAX_FIELD_ROLES]]

    @field_validator("evidence")
    @classmethod
    def _evidence(cls, v: List[str]) -> List[str]:
        return [str(e)[:128] for e in v[:MAX_FIELD_EVIDENCE]]

    @field_validator("checks")
    @classmethod
    def _checks(cls, v: List[Dict[str, str]]) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []
        for c in v[:MAX_FIELD_CHECKS]:
            if isinstance(c, dict) and c.get("code"):
                out.append({"code": str(c["code"])[:64],
                            "detail": str(c.get("detail") or "")[:200]})
        return out

    @field_validator("null_ratio")
    @classmethod
    def _ratio(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return None
        try:
            return round(float(v), 6)
        except (TypeError, ValueError):
            return None

    @field_validator("domain_hint")
    @classmethod
    def _domain(cls, v: Optional[List[float]]) -> Optional[List[float]]:
        if not v or len(v) != 2:
            return None
        try:
            return [round(float(v[0]), 6), round(float(v[1]), 6)]
        except (TypeError, ValueError):
            return None

    def to_bounded_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "name": self.name,
            "dtype": self.dtype,
            "nullable": self.nullable,
            "null_ratio": self.null_ratio,
            "roles": list(self.roles),
            "role_confidence": self.role_confidence,
            "measurement_kind": self.measurement_kind,
            "unit_dimension": self.unit_dimension,
            "unit": self.unit,
            "kind_confidence": self.kind_confidence,
            "unit_confidence": self.unit_confidence,
            "domain_hint": list(self.domain_hint) if self.domain_hint else None,
            "center_hint": self.center_hint,
            "evidence": list(self.evidence),
            "checks": [dict(c) for c in self.checks],
        }
        return {k: v for k, v in out.items() if v not in (None, "", [])}

    @classmethod
    def from_bounded_dict(cls, data: Any) -> Optional["FieldEntry"]:
        if not isinstance(data, dict) or not data.get("name"):
            return None
        domain = data.get("domain_hint")
        checks = [
            c for c in (data.get("checks") or [])
            if isinstance(c, dict) and c.get("code")
        ]
        nullable = data.get("nullable")
        return cls(
            name=str(data["name"])[:MAX_FIELD_NAME],
            dtype=str(data.get("dtype") or ""),
            nullable=bool(nullable) if isinstance(nullable, bool) else None,
            null_ratio=(
                float(data["null_ratio"])
                if isinstance(data.get("null_ratio"), (int, float))
                and not isinstance(data.get("null_ratio"), bool) else None
            ),
            roles=[str(r) for r in (data.get("roles") or [])][:MAX_FIELD_ROLES],
            role_confidence=str(data.get("role_confidence") or ""),
            measurement_kind=str(data.get("measurement_kind") or ""),
            unit_dimension=str(data.get("unit_dimension") or ""),
            unit=str(data.get("unit") or ""),
            kind_confidence=str(data.get("kind_confidence") or ""),
            unit_confidence=str(data.get("unit_confidence") or ""),
            domain_hint=(
                [float(v) for v in domain[:2]]
                if isinstance(domain, list) and len(domain) == 2
                and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                        for v in domain[:2]) else None
            ),
            center_hint=(
                float(data["center_hint"])
                if isinstance(data.get("center_hint"), (int, float))
                and not isinstance(data.get("center_hint"), bool) else None
            ),
            evidence=[str(e) for e in (data.get("evidence") or [])][:MAX_FIELD_EVIDENCE],
            checks=checks[:MAX_FIELD_CHECKS],
        )


class RasterShape(BaseModel):
    """栅格形状事实（RasterProfile 的契约冻结投影；矢量恒缺席）。"""

    width: Optional[int] = None
    height: Optional[int] = None
    band_count: Optional[int] = None
    nodata: Optional[float] = None
    pixel_size: Optional[float] = None
    dtype: str = ""


class TemporalSemantics(BaseModel):
    """时间语义（诚实缺省：None = 无证据，绝不虚构）。"""

    has_time_field: Optional[bool] = None
    time_field: str = ""
    coverage_start: str = ""
    coverage_end: str = ""
    granularity: str = ""
    observation_count: Optional[int] = None


class SamplingEvidence(BaseModel):
    """采样证据（descriptor 不携带值样本，只携带采样事实 —— 有界可审计）。"""

    strategy: str = ""                # first_n_features / descriptor_projection / …
    feature_cap: int = 0              # 采样窗口上限（0 = 未采样）
    fields_capped: bool = False       # 字段清单是否被截断
    fields_explicit: bool = False     # 字段清单是否权威（DatasetProfile.fields_status）
    samples_per_field: int = 0
    notes: List[str] = Field(default_factory=list)


def _schema_facts(descriptor: "GISDatasetDescriptor") -> Dict[str, Any]:
    """schema 指纹的哈希输入（结构事实子集；确定性排序由 canonical_fingerprint 保证）。"""
    return {
        "kind": descriptor.kind,
        "geometry_types": sorted(descriptor.geometry_types),
        "feature_count": descriptor.feature_count,
        "bbox": [round(float(b), 9) for b in descriptor.bbox] if descriptor.bbox else None,
        "crs": descriptor.crs,
        "raster": (
            descriptor.raster.model_dump() if descriptor.raster is not None else None
        ),
        "fields": [f.to_bounded_dict() for f in descriptor.fields],
        "numeric_fields": sorted(descriptor.numeric_fields),
        "categorical_fields": sorted(descriptor.categorical_fields),
        "binary_fields": sorted(descriptor.binary_fields),
        "temporal": descriptor.temporal.model_dump(),
    }


class GISDatasetDescriptor(BaseModel):
    """数据集语义契约 v1（权威记录；ingest/query/map/replay 同一指纹）。"""

    descriptor_version: int = DESCRIPTOR_VERSION
    dataset_key: str = ""
    kind: Literal["vector", "raster", "table", "unknown"] = "unknown"

    geometry_types: List[str] = Field(default_factory=list)
    feature_count: Optional[int] = None
    bbox: Optional[List[float]] = None
    crs: str = ""                                   # 未知留空（不虚构）
    raster: Optional[RasterShape] = None

    fields: List[FieldEntry] = Field(default_factory=list)
    # 字段清单成员（构建期 DatasetProfile 显式清单的冻结投影 —— 投影面
    # 直接还原，不从 dtype 重推；dtype 与清单是两路证据，不混淆）。
    numeric_fields: List[str] = Field(default_factory=list)
    categorical_fields: List[str] = Field(default_factory=list)
    binary_fields: List[str] = Field(default_factory=list)

    temporal: TemporalSemantics = Field(default_factory=TemporalSemantics)

    source_refs: List[SourceRef] = Field(default_factory=list)

    # 质量证据（值级事实 + 稳定信号码 ≤16）
    artifact_type: str = ""                         # 分类证据（resolver artifactType）
    null_ratio_max: Optional[float] = None
    value_variance: Optional[float] = None
    duplicate_coordinate_count: Optional[int] = None
    unique_coordinate_count: Optional[int] = None
    longitude_convention: str = ""
    crosses_antimeridian: Optional[bool] = None
    quality_signals: List[str] = Field(default_factory=list)

    sampling: SamplingEvidence = Field(default_factory=SamplingEvidence)
    provenance: List[Dict[str, str]] = Field(default_factory=list)

    derived_at: str = ""                            # 易变字段：不入指纹
    schema_fingerprint: str = ""
    descriptor_fingerprint: str = ""

    @field_validator("geometry_types")
    @classmethod
    def _geoms(cls, v: List[str]) -> List[str]:
        return [str(t)[:32] for t in v[:MAX_GEOMETRY_TYPES]]

    @field_validator("fields")
    @classmethod
    def _fields(cls, v: List[FieldEntry]) -> List[FieldEntry]:
        return list(v[:MAX_FIELDS])

    @field_validator("numeric_fields", "categorical_fields", "binary_fields")
    @classmethod
    def _field_lists(cls, v: List[str]) -> List[str]:
        return [str(x)[:MAX_FIELD_NAME] for x in v[:MAX_FIELDS]]

    @field_validator("source_refs")
    @classmethod
    def _refs(cls, v: List[SourceRef]) -> List[SourceRef]:
        return list(v[:MAX_SOURCE_REFS])

    @field_validator("quality_signals")
    @classmethod
    def _signals(cls, v: List[str]) -> List[str]:
        return [str(s)[:64] for s in v[:MAX_QUALITY_SIGNALS]]

    @field_validator("provenance")
    @classmethod
    def _provenance(cls, v: List[Dict[str, str]]) -> List[Dict[str, str]]:
        out = []
        for p in v[:MAX_PROVENANCE]:
            if isinstance(p, dict):
                out.append({
                    "producer": str(p.get("producer") or "")[:64],
                    "method": str(p.get("method") or "")[:64],
                })
        return out

    @field_validator("bbox")
    @classmethod
    def _bbox(cls, v: Optional[List[float]]) -> Optional[List[float]]:
        if v is None or len(v) != 4:
            return None
        try:
            return [float(b) for b in v]
        except (TypeError, ValueError):
            return None

    # ── 指纹（确定性；derived_at 不入哈希）────────────────────────────
    def fingerprint_payload(self) -> Dict[str, Any]:
        """指纹哈希输入（语义内容全集；显式排除易变/指针类字段）。

        排除项与理由：``derived_at``（时间戳）、``dataset_key``（会话内指针，
        同一数据在不同 session 的 ref id 不同）、``source_refs``（指针集合，
        同一语义可经不同 ref/artifact 命名）、``provenance``（生产者标签）
        —— 语义身份 ≠ 指针身份：同一数据语义无论从哪条路径、哪个指针
        到达，指纹必须相同（ADR-0215 DoD #1）。
        """
        return {
            "descriptor_version": self.descriptor_version,
            "kind": self.kind,
            "artifact_type": self.artifact_type,
            "geometry_types": sorted(self.geometry_types),
            "feature_count": self.feature_count,
            "bbox": [round(float(b), 9) for b in self.bbox] if self.bbox else None,
            "crs": self.crs,
            "raster": self.raster.model_dump() if self.raster is not None else None,
            "fields": [f.to_bounded_dict() for f in self.fields],
            "numeric_fields": sorted(self.numeric_fields),
            "categorical_fields": sorted(self.categorical_fields),
            "binary_fields": sorted(self.binary_fields),
            "temporal": self.temporal.model_dump(),
            "numeric_fields": sorted(self.numeric_fields),
            "categorical_fields": sorted(self.categorical_fields),
            "binary_fields": sorted(self.binary_fields),
            "null_ratio_max": self.null_ratio_max,
            "value_variance": self.value_variance,
            "duplicate_coordinate_count": self.duplicate_coordinate_count,
            "unique_coordinate_count": self.unique_coordinate_count,
            "longitude_convention": self.longitude_convention,
            "crosses_antimeridian": self.crosses_antimeridian,
            "quality_signals": sorted(self.quality_signals),
            "sampling": self.sampling.model_dump(),
        }

    def compute_fingerprints(self) -> Tuple[str, str]:
        """(schema_fingerprint, descriptor_fingerprint)（纯函数；同输入恒同输出）。"""
        schema_fp = SCHEMA_FINGERPRINT_PREFIX + canonical_fingerprint(
            _schema_facts(self))
        desc_fp = FINGERPRINT_PREFIX + canonical_fingerprint(self.fingerprint_payload())
        return schema_fp, desc_fp

    def with_fingerprints(self) -> "GISDatasetDescriptor":
        """填入指纹后的新实例（不可变更新；derived_at 不参与哈希）。"""
        schema_fp, desc_fp = self.compute_fingerprints()
        return self.model_copy(
            update={"schema_fingerprint": schema_fp, "descriptor_fingerprint": desc_fp}
        )

    # ── 序列化（canonical dict；载荷有界）────────────────────────────
    def to_dict(self) -> Dict[str, Any]:
        return {
            "descriptor_version": self.descriptor_version,
            "dataset_key": str(self.dataset_key)[:200],
            "kind": self.kind,
            "artifact_type": self.artifact_type,
            "geometry_types": list(self.geometry_types),
            "feature_count": self.feature_count,
            "bbox": list(self.bbox) if self.bbox else None,
            "crs": self.crs,
            "raster": self.raster.model_dump() if self.raster is not None else None,
            "fields": [f.to_bounded_dict() for f in self.fields],
            "numeric_fields": list(self.numeric_fields),
            "categorical_fields": list(self.categorical_fields),
            "binary_fields": list(self.binary_fields),
            "temporal": self.temporal.model_dump(),
            "source_refs": [r.to_bounded_dict() for r in self.source_refs],
            "null_ratio_max": self.null_ratio_max,
            "value_variance": self.value_variance,
            "duplicate_coordinate_count": self.duplicate_coordinate_count,
            "unique_coordinate_count": self.unique_coordinate_count,
            "longitude_convention": self.longitude_convention,
            "quality_signals": list(self.quality_signals),
            "sampling": self.sampling.model_dump(),
            "provenance": self.provenance,
            "derived_at": self.derived_at,
            "schema_fingerprint": self.schema_fingerprint,
            "descriptor_fingerprint": self.descriptor_fingerprint,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "GISDatasetDescriptor":
        """反序列化（fail-closed：非 dict / 未知版本 / 载荷坏字段 → ValueError）。

        与 measurement.from_dict 同纪律：宁可拒收，不静默降级成"猜的语义"。
        """
        if not isinstance(data, dict):
            raise ValueError("dataset descriptor 必须是 dict")
        version = data.get("descriptor_version")
        if version != DESCRIPTOR_VERSION:
            raise ValueError(
                f"{CODE_VERSION_UNSUPPORTED}: 不支持的 descriptor_version {version!r}"
                f"（本契约只认 {DESCRIPTOR_VERSION}）"
            )
        raster = data.get("raster")
        fields: List[FieldEntry] = []
        raw_fields = data.get("fields")
        if isinstance(raw_fields, list):
            for item in raw_fields[:MAX_FIELDS]:
                entry = FieldEntry.from_bounded_dict(item)
                if entry is not None:
                    fields.append(entry)
        refs = [
            SourceRef(
                type=str(r.get("type") or "")[:32],
                ref=str(r.get("ref") or "")[:200],
                fingerprint=str(r.get("fingerprint") or "")[:128],
            )
            for r in (data.get("source_refs") or [])[:MAX_SOURCE_REFS]
            if isinstance(r, dict)
        ]
        sampling_raw = data.get("sampling") or {}
        bbox = data.get("bbox")
        temporal_raw = data.get("temporal") or {}
        return cls(
            dataset_key=str(data.get("dataset_key") or "")[:200],
            kind=data.get("kind") if data.get("kind") in ("vector", "raster", "table", "unknown") else "unknown",
            artifact_type=str(data.get("artifact_type") or "")[:64],
            geometry_types=[str(t) for t in (data.get("geometry_types") or [])][:MAX_GEOMETRY_TYPES],
            feature_count=(
                int(data["feature_count"])
                if isinstance(data.get("feature_count"), int)
                and not isinstance(data.get("feature_count"), bool) else None
            ),
            bbox=[float(b) for b in bbox] if isinstance(bbox, list) and len(bbox) == 4 else None,
            crs=str(data.get("crs") or ""),
            raster=RasterShape(**raster) if isinstance(raster, dict) else None,
            fields=fields,
            numeric_fields=[str(x) for x in (data.get("numeric_fields") or [])][:MAX_FIELDS],
            categorical_fields=[str(x) for x in (data.get("categorical_fields") or [])][:MAX_FIELDS],
            binary_fields=[str(x) for x in (data.get("binary_fields") or [])][:MAX_FIELDS],
            temporal=TemporalSemantics(
                has_time_field=(
                    bool(temporal_raw["has_time_field"])
                    if isinstance(temporal_raw.get("has_time_field"), bool) else None
                ),
                time_field=str(temporal_raw.get("time_field") or ""),
                coverage_start=str(temporal_raw.get("coverage_start") or "")[:64],
                coverage_end=str(temporal_raw.get("coverage_end") or "")[:64],
                granularity=str(temporal_raw.get("granularity") or "")[:32],
                observation_count=(
                    int(temporal_raw["observation_count"])
                    if isinstance(temporal_raw.get("observation_count"), int)
                    and not isinstance(temporal_raw.get("observation_count"), bool) else None
                ),
            ),
            source_refs=refs,
            null_ratio_max=(
                float(data["null_ratio_max"])
                if isinstance(data.get("null_ratio_max"), (int, float))
                and not isinstance(data.get("null_ratio_max"), bool) else None
            ),
            value_variance=(
                float(data["value_variance"])
                if isinstance(data.get("value_variance"), (int, float))
                and not isinstance(data.get("value_variance"), bool) else None
            ),
            duplicate_coordinate_count=(
                int(data["duplicate_coordinate_count"])
                if isinstance(data.get("duplicate_coordinate_count"), int)
                and not isinstance(data.get("duplicate_coordinate_count"), bool) else None
            ),
            unique_coordinate_count=(
                int(data["unique_coordinate_count"])
                if isinstance(data.get("unique_coordinate_count"), int)
                and not isinstance(data.get("unique_coordinate_count"), bool) else None
            ),
            longitude_convention=str(data.get("longitude_convention") or "")[:16],
            quality_signals=[str(s) for s in (data.get("quality_signals") or [])][:MAX_QUALITY_SIGNALS],
            sampling=SamplingEvidence(
                strategy=str(sampling_raw.get("strategy") or "")[:64],
                feature_cap=(
                    int(sampling_raw["feature_cap"])
                    if isinstance(sampling_raw.get("feature_cap"), int)
                    and not isinstance(sampling_raw.get("feature_cap"), bool) else 0
                ),
                fields_capped=bool(sampling_raw.get("fields_capped")),
                fields_explicit=bool(sampling_raw.get("fields_explicit")),
                samples_per_field=(
                    int(sampling_raw["samples_per_field"])
                    if isinstance(sampling_raw.get("samples_per_field"), int)
                    and not isinstance(sampling_raw.get("samples_per_field"), bool) else 0
                ),
                notes=[str(n)[:256] for n in (sampling_raw.get("notes") or [])[:MAX_SAMPLING_NOTES]],
            ),
            provenance=[
                {"producer": str(p.get("producer") or "")[:64],
                 "method": str(p.get("method") or "")[:64]}
                for p in (data.get("provenance") or [])[:MAX_PROVENANCE]
                if isinstance(p, dict)
            ],
            derived_at=str(data.get("derived_at") or "")[:64],
            schema_fingerprint=str(data.get("schema_fingerprint") or "")[:96],
            descriptor_fingerprint=str(data.get("descriptor_fingerprint") or "")[:96],
        )

    # ── 变更对账 ─────────────────────────────────────────────────────
    def fingerprint_set(self) -> FingerprintSet:
        """四维指纹集（复用 V3 原语；crs 维用 CRS 字符串，诚实缺省 None）。"""
        return FingerprintSet(
            content=self.descriptor_fingerprint or None,
            schema=self.schema_fingerprint or None,
            metadata=None,       # 描述性 metadata 不入 descriptor 身份（未采集即缺席）
            crs=self.crs or None,
        )


def compare_descriptors(
    old: Optional[GISDatasetDescriptor],
    new: Optional[GISDatasetDescriptor],
) -> "DescriptorDelta":
    """新旧 descriptor → 可解释变更（change_class + 字段级 reason codes）。

    - 任一缺席 → UNCOMPARABLE（「没记录」≠「没变」）；
    - 版本不一致 → VERSION_BUMPED（contract 级变化压过一切字段级判定）；
    - 分类复用 classify_change（crs > schema > content > metadata 优先级）；
    - 字段级 codes 逐维比对（bounded ≤MAX_FIELDS 条），供消费面精确披露
      「什么变了」—— 版本/类型/单位/角色任一变化都可解释。
    """
    if old is None or new is None:
        return DescriptorDelta(
            change_class=ChangeClass.UNKNOWN,
            verdict=StalenessVerdict.RECOMPUTE,
            reason_codes=[CODE_UNCOMPARABLE],
        )
    codes: List[str] = []
    if old.descriptor_version != new.descriptor_version:
        codes.append(CODE_VERSION_BUMPED)
        return DescriptorDelta(
            change_class=ChangeClass.SCHEMA,
            verdict=StalenessVerdict.RECOMPUTE,
            reason_codes=codes,
        )

    field_diffs = _diff_fields(old.fields, new.fields)
    structural: List[str] = []
    if sorted(old.geometry_types) != sorted(new.geometry_types):
        structural.append(CODE_GEOMETRY_CHANGED)
    if (old.raster is None) != (new.raster is None) or (
        old.raster is not None
        and new.raster is not None
        and old.raster.model_dump() != new.raster.model_dump()
    ):
        structural.append(CODE_RASTER_SHAPE_CHANGED)
    if old.temporal.model_dump() != new.temporal.model_dump():
        structural.append(CODE_TEMPORAL_CHANGED)

    change_class = classify_change(old.fingerprint_set(), new.fingerprint_set())
    if change_class is ChangeClass.CRS:
        codes.append(CODE_CRS_CHANGED)
    elif change_class is ChangeClass.SCHEMA:
        codes.append(CODE_SCHEMA_CHANGED)
    elif change_class is ChangeClass.CONTENT:
        codes.append(CODE_CONTENT_EVIDENCE_CHANGED)
    elif change_class is ChangeClass.METADATA_ONLY:
        codes.append(CODE_METADATA_ONLY_CHANGED)
    elif change_class is ChangeClass.NONE:
        codes.append(CODE_UNCHANGED)
    else:
        codes.append(CODE_UNCOMPARABLE)
    codes.extend(structural)
    codes.extend(field_diffs.codes)
    verdict = staleness_verdict(change_class, upstream_alive=True)
    return DescriptorDelta(
        change_class=change_class,
        verdict=verdict,
        reason_codes=codes[:MAX_QUALITY_SIGNALS],
        field_diffs=field_diffs.diffs,
    )


class FieldDiff(BaseModel):
    field: str
    code: str
    old: str = ""
    new: str = ""


class _FieldDiffResult(BaseModel):
    codes: List[str] = Field(default_factory=list)
    diffs: List[FieldDiff] = Field(default_factory=list)


def _diff_fields(old: List[FieldEntry], new: List[FieldEntry]) -> _FieldDiffResult:
    """字段级 diff（有界：≤MAX_FIELDS 条 diff；稳定排序保证确定性）。"""
    old_by = {f.name: f for f in old}
    new_by = {f.name: f for f in new}
    diffs: List[FieldDiff] = []
    codes: List[str] = []
    for name in sorted(set(old_by) - set(new_by))[:MAX_FIELDS]:
        diffs.append(FieldDiff(field=name, code=CODE_FIELD_REMOVED))
    for name in sorted(set(new_by) - set(old_by))[:MAX_FIELDS]:
        diffs.append(FieldDiff(field=name, code=CODE_FIELD_ADDED))
    for name in sorted(set(old_by) & set(new_by))[:MAX_FIELDS]:
        o, n = old_by[name], new_by[name]
        if o.dtype != n.dtype:
            diffs.append(FieldDiff(field=name, code=CODE_FIELD_RETYPE,
                                   old=o.dtype, new=n.dtype))
        if o.nullable != n.nullable:
            diffs.append(FieldDiff(field=name, code=CODE_FIELD_NULLABILITY_CHANGED,
                                   old=str(o.nullable), new=str(n.nullable)))
        if o.measurement_kind != n.measurement_kind:
            diffs.append(FieldDiff(field=name, code=CODE_FIELD_KIND_CHANGED,
                                   old=o.measurement_kind, new=n.measurement_kind))
        if o.unit != n.unit or o.unit_dimension != n.unit_dimension:
            diffs.append(FieldDiff(field=name, code=CODE_FIELD_UNIT_CHANGED,
                                   old=o.unit or o.unit_dimension,
                                   new=n.unit or n.unit_dimension))
        if sorted(o.roles) != sorted(n.roles):
            diffs.append(FieldDiff(field=name, code=CODE_FIELD_ROLE_CHANGED,
                                   old=",".join(sorted(o.roles)),
                                   new=",".join(sorted(n.roles))))
    for d in diffs:
        codes.append(d.code)
    return _FieldDiffResult(codes=codes, diffs=diffs)


class DescriptorDelta(BaseModel):
    """变更判定结果（reason codes 稳定可断言；field_diffs ≤MAX_FIELDS 条）。"""

    change_class: str
    verdict: str
    reason_codes: List[str] = Field(default_factory=list)
    field_diffs: List[FieldDiff] = Field(default_factory=list)

    @property
    def is_unchanged(self) -> bool:
        return self.change_class == ChangeClass.NONE.value

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "change_class": self.change_class,
            "verdict": self.verdict,
            "reason_codes": list(self.reason_codes[:MAX_QUALITY_SIGNALS]),
            "field_diffs": [d.model_dump() for d in self.field_diffs[:MAX_FIELDS]],
        }


# 补充：ChangeClass/StalenessVerdict 是 str Enum —— pydantic 字段直接收 str 值。
DescriptorDelta.model_rebuild()


# ── 下游 stale 词表（qualification / reuse 消费面的结果码）─────────────────
CODE_STALE_CRS = "DESCRIPTOR_STALE_CRS"
CODE_STALE_SCHEMA = "DESCRIPTOR_STALE_SCHEMA"
CODE_STALE_CONTENT = "DESCRIPTOR_STALE_CONTENT"
CODE_STALE_METADATA = "DESCRIPTOR_STALE_METADATA"
CODE_STALE_UNCOMPARABLE = "DESCRIPTOR_STALE_UNCOMPARABLE"

_STALE_CODE_BY_CLASS: Dict[str, str] = {
    ChangeClass.CRS.value: CODE_STALE_CRS,
    ChangeClass.SCHEMA.value: CODE_STALE_SCHEMA,
    ChangeClass.CONTENT.value: CODE_STALE_CONTENT,
    ChangeClass.METADATA_ONLY.value: CODE_STALE_METADATA,
    ChangeClass.UNKNOWN.value: CODE_STALE_UNCOMPARABLE,
}


def qualification_stale_reason(change_class: str) -> str:
    """ChangeClass → 下游 stale 结果码（与「什么变了」的 *_CHANGED 码分离）。"""
    return _STALE_CODE_BY_CLASS.get(str(change_class), CODE_STALE_UNCOMPARABLE)


def stale_reason_for(change_class: str) -> str:
    """ChangeClass → 稳定 stale reason code（qualification 消费面用）。"""
    return _STALE_REASON_BY_CLASS.get(str(change_class), CODE_UNCOMPARABLE)


__all__ = [
    "DESCRIPTOR_VERSION",
    "MAX_FIELDS", "MAX_SOURCE_REFS", "MAX_QUALITY_SIGNALS", "MAX_PROVENANCE",
    "FINGERPRINT_PREFIX", "SCHEMA_FINGERPRINT_PREFIX",
    "SourceRef", "FieldEntry", "RasterShape", "TemporalSemantics",
    "SamplingEvidence", "GISDatasetDescriptor",
    "compare_descriptors", "DescriptorDelta", "FieldDiff",
    "stale_reason_for",
    "CODE_UNCHANGED", "CODE_CRS_CHANGED", "CODE_SCHEMA_CHANGED",
    "CODE_CONTENT_EVIDENCE_CHANGED", "CODE_METADATA_ONLY_CHANGED",
    "CODE_UNCOMPARABLE", "CODE_FIELD_ADDED", "CODE_FIELD_REMOVED",
    "CODE_FIELD_RETYPE", "CODE_FIELD_UNIT_CHANGED", "CODE_FIELD_KIND_CHANGED",
    "CODE_FIELD_ROLE_CHANGED", "CODE_FIELD_NULLABILITY_CHANGED",
    "CODE_GEOMETRY_CHANGED", "CODE_RASTER_SHAPE_CHANGED", "CODE_TEMPORAL_CHANGED",
    "CODE_VERSION_BUMPED", "CODE_VERSION_UNSUPPORTED", "CODE_STORE_CORRUPT",
    "CODE_TOO_LARGE", "CODE_MISSING", "CODE_FINGERPRINT_MISMATCH",
    "CODE_STALE_CRS", "CODE_STALE_SCHEMA", "CODE_STALE_CONTENT",
    "CODE_STALE_METADATA", "CODE_STALE_UNCOMPARABLE",
    "qualification_stale_reason",
]
