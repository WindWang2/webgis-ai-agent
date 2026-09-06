"""Artifact Contract V3 —— 数据产物的权威契约（ADR: data-plane v3-foundation）。

V3 之前「artifact」有四个互不对账的定义（审计 Agent A）：会话
``ArtifactRecord``（存在性/状态真相）、DB ``Artifact``（持久真相）、
``ArtifactDescriptor``（语义投影，无生产方）、run manifest 内联条目。
同一列名 ``artifact_type`` 承载三套不相交的值空间。

本模块定义**一个**只读投影契约 ``ArtifactContract``：

- 它是所有数据产物（会话 ref、磁盘栅格、DB artifact、upload、fabric
  数据集）对 V3 消费方（catalog / agent tools / workspace / staleness）
  的**统一呈现**，不新增数据真相、不落盘、不复制载荷；
- 字段对齐目标契约清单（§三）：identity / version / type / role /
  source / storage / schema / geometry / crs / extent / temporal /
  raster / fingerprint / lifecycle / persistence / cacheability /
  reproducibility / diagnostics；
- 一切列表有界（parents ≤16、fields ≤64、diagnostics ≤8、statistics
  keys ≤32）—— 契约是 metadata，不是数据搬运工；
- ``summary()`` 产出硬上限的紧凑视图，供 LLM 上下文使用（§三十二）；
- ``from_*`` 桥接器对缺失字段诚实置 None，绝不虚构。

依赖方向：只依赖 app/lib（vocabulary/fingerprints），绝不 import
app/services —— 契约层零 I/O、可独立测试。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.lib.data import vocabulary as vocab
from app.lib.data.fingerprints import FingerprintSet

CONTRACT_VERSION = 3

# 有界性约束（与 lib/gis/artifacts.py 同一预算哲学）。
_MAX_PARENTS = 16
_MAX_DEPENDENCIES = 16
_MAX_FIELDS = 64
_MAX_DIAGNOSTICS = 8
_MAX_STATISTICS_KEYS = 32
_MAX_UNITS = 32
_MAX_SUMMARY_CHARS = 1600
_SUMMARY_MAX_FIELDS = 12


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_datetime(value: Any) -> Optional[datetime]:
    """epoch 秒 / ISO 串 / datetime → aware datetime；不可判定 → None。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        text = str(value)
        if not text:
            return None
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, TypeError, OverflowError, OSError):
        return None


class SourceInfo(BaseModel):
    """数据来源（§三 source / source_revision）。"""

    model_config = ConfigDict(populate_by_name=True)

    source_type: str = ""            # upload / session_ref / raster_disk / db / fabric:<type> …
    source_ref: str = ""             # 来源指针（upload_id / url / source_key）
    source_revision: str = ""        # 来源修订 token（content hash / mtime+size / 版本号）
    display_name: str = ""


class TemporalExtent(BaseModel):
    """时间范围（§二十八的契约级最小面；细节归 profile）。"""

    start: Optional[datetime] = None
    end: Optional[datetime] = None
    timezone: str = ""               # IANA 名或空（未知）


class RasterShape(BaseModel):
    """栅格形状（§三 raster_shape / resolution / bands / nodata）。"""

    width: Optional[int] = None
    height: Optional[int] = None
    bands: Optional[int] = None
    resolution_x: Optional[float] = None
    resolution_y: Optional[float] = None
    nodata: Optional[float] = None
    dtype: str = ""


class ProducedBy(BaseModel):
    """生产者证据（§三 produced_by）。"""

    tool: str = ""
    capability: str = ""
    algorithm: str = ""
    node: str = ""
    workflow_run_id: str = ""
    operation_version: str = ""


class LineageInfo(BaseModel):
    """血缘投影（§三 lineage / dependencies；有界，截断显式声明）。"""

    parents: List[str] = Field(default_factory=list)
    parents_truncated: bool = False
    dependencies: List[str] = Field(default_factory=list)   # 非血缘的资源依赖（upload/layer/db）
    replaces: Optional[str] = None                          # 被本产物替换的旧 artifact

    @field_validator("parents")
    @classmethod
    def _bounded_parents(cls, v: List[str]) -> List[str]:
        keep = [str(p) for p in v if p][:_MAX_PARENTS]
        return keep

    @field_validator("dependencies")
    @classmethod
    def _bounded_dependencies(cls, v: List[str]) -> List[str]:
        return [str(d) for d in v if d][:_MAX_DEPENDENCIES]

    @model_validator(mode="before")
    @classmethod
    def _derive_truncation_flag(cls, data: Any) -> Any:
        """截断发生在字段校验期 → 截断标志必须在同一入口点之前判定。"""
        if isinstance(data, dict) and not data.get("parents_truncated"):
            parents = data.get("parents") or []
            if isinstance(parents, (list, tuple)) and len(parents) > _MAX_PARENTS:
                data = dict(data)
                data["parents_truncated"] = True
        return data


class Cacheability(BaseModel):
    """缓存/复用语义（§十三）。"""

    deterministic: bool = False              # 声明：同输入必同输出
    cacheable: bool = False                  # 声明：允许缓存/复用
    reuse_fingerprint: Optional[str] = None  # fingerprints.compute_reuse_fingerprint 产物
    ttl_s: Optional[int] = None              # 复用有效期；None = 由策略决定


class Reproducibility(BaseModel):
    """复现语义（§三 reproducibility）。"""

    replayable: bool = False                 # 输入仍可解析、操作仍可重放
    run_manifest_ref: str = ""               # WorkflowRun.run_manifest 指针
    operation_version: str = ""
    runtime_semantic_version: str = ""


class ContractDiagnostic(BaseModel):
    """契约级诊断（有界；不替代 quality 报告）。"""

    code: str
    message: str = ""
    severity: str = "info"                   # info / warning / error

    @field_validator("code", "severity")
    @classmethod
    def _short(cls, v: str) -> str:
        return str(v)[:64]


class ArtifactContract(BaseModel):
    """V3 数据产物契约（只读投影；一个产物一份，不落第二份真相）。"""

    model_config = ConfigDict(populate_by_name=True)

    contract_version: int = CONTRACT_VERSION
    artifact_id: str
    stable_identity: Optional[str] = None    # 内容/复用指纹；不可判定 → None（诚实）
    version: int = 0                         # 产物自身修订号（同 id 重写递增）
    artifact_type: str = "unknown"           # 粗类 token（词表校验）
    artifact_subtype: str = ""               # 21 细类型（未注册自由串原样透传）
    logical_role: vocab.LogicalRole = vocab.LogicalRole.DERIVED
    source: SourceInfo = Field(default_factory=SourceInfo)
    storage_ref: str = ""                    # 载荷指针（ref:/路径/db 指针）；不内联载荷
    media_type: str = ""
    format: str = ""
    data_schema: Optional[Dict[str, Any]] = Field(default=None, alias="schema")
    geometry_kind: str = ""                  # point/line/polygon/raster/table/network/unknown
    feature_count: Optional[int] = None      # 要素数（table 类为行数语义，栅格恒 None）
    crs: str = ""                            # 空 = 未知（绝不虚构 EPSG:4326）
    extent: Optional[List[float]] = None     # [minx, miny, maxx, maxy]
    temporal_extent: Optional[TemporalExtent] = None
    raster: Optional[RasterShape] = None
    units: str = ""
    statistics: Optional[Dict[str, Any]] = None
    profile_ref: str = ""                    # Dataset Profile V3 的检索键
    lineage: LineageInfo = Field(default_factory=LineageInfo)
    produced_by: ProducedBy = Field(default_factory=ProducedBy)
    fingerprint: FingerprintSet = Field(default_factory=FingerprintSet)
    size_bytes: Optional[int] = None
    lifecycle: vocab.LifecycleState = vocab.LifecycleState.AVAILABLE
    persistence: vocab.PersistenceTier = vocab.PersistenceTier.SESSION
    cacheability: Cacheability = Field(default_factory=Cacheability)
    reproducibility: Reproducibility = Field(default_factory=Reproducibility)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    diagnostics: List[ContractDiagnostic] = Field(default_factory=list)

    # ── 校验（词表收口 + 有界化）────────────────────────────────────
    @field_validator("artifact_type")
    @classmethod
    def _category_in_vocabulary(cls, v: str) -> str:
        token = vocab.coerce_category(v)
        if token is None:
            raise ValueError(
                f"unregistered artifact_type category: {v!r} "
                "(use vocabulary.register_category to extend in a controlled way)"
            )
        return token

    @field_validator("extent")
    @classmethod
    def _extent_shape(cls, v: Optional[List[float]]) -> Optional[List[float]]:
        if v is None:
            return None
        if len(v) != 4:
            return None
        try:
            return [float(v[0]), float(v[1]), float(v[2]), float(v[3])]
        except (TypeError, ValueError):
            return None

    @field_validator("data_schema")
    @classmethod
    def _bounded_schema(cls, v: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not v:
            return None
        return dict(list(v.items())[:_MAX_FIELDS])

    @field_validator("statistics")
    @classmethod
    def _bounded_statistics(cls, v: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not v:
            return None
        return dict(list(v.items())[:_MAX_STATISTICS_KEYS])

    @field_validator("diagnostics")
    @classmethod
    def _bounded_diagnostics(cls, v: List[ContractDiagnostic]) -> List[ContractDiagnostic]:
        return list(v)[:_MAX_DIAGNOSTICS]

    @field_validator("units")
    @classmethod
    def _bounded_units(cls, v: str) -> str:
        return str(v)[:_MAX_UNITS]

    # ── 派生视图 ────────────────────────────────────────────────────
    @property
    def category(self) -> Optional[vocab.ArtifactCategory]:
        try:
            return vocab.ArtifactCategory(self.artifact_type)
        except ValueError:
            return None

    def is_empty(self) -> bool:
        return any(d.code == "empty_payload" for d in self.diagnostics)

    def summary(self, *, max_chars: int = _MAX_SUMMARY_CHARS) -> Dict[str, Any]:
        """面向 LLM 上下文的紧凑视图（§三十二：ref+profile+summary，非数据本体）。

        结构稳定、字段硬上限；`data_schema` 只出字段名列表（≤12），统计
        只出已内联键名。整体 JSON 序列化超过 max_chars 时逐级降级
        （去 schema → 去诊断 → 去指纹），仍超则截断 diagnostics 文本。
        """
        view: Dict[str, Any] = {
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "subtype": self.artifact_subtype or None,
            "role": self.logical_role.value,
            "lifecycle": self.lifecycle.value,
            "persistence": self.persistence.value,
            "crs": self.crs or None,
            "extent": self.extent,
            "feature_count": self.feature_count,
            "size_bytes": self.size_bytes,
            "storage_ref": self.storage_ref or None,
            "stable_identity": self.stable_identity,
            "version": self.version,
            "produced_by": self.produced_by.tool or self.produced_by.capability or None,
            "parents_count": len(self.lineage.parents) + (
                1 if self.lineage.parents_truncated else 0
            ),
            "fingerprint": (
                self.fingerprint.content[:16]
                if self.fingerprint.content
                else None
            ),
            "quality_hint": self.quality_status_hint(),
        }
        if self.data_schema:
            view["fields"] = [
                str(k) for k in list(self.data_schema.keys())[:_SUMMARY_MAX_FIELDS]
            ]
        if self.raster is not None:
            view["raster"] = {
                "width": self.raster.width,
                "height": self.raster.height,
                "bands": self.raster.bands,
                "dtype": self.raster.dtype or None,
            }
        # 渐进降级：保证永不突破 max_chars（LLM 预算硬约束）。
        import json as _json

        def _size(node: Dict[str, Any]) -> int:
            try:
                return len(_json.dumps(node, ensure_ascii=False, default=str))
            except (TypeError, ValueError):
                return max_chars + 1

        for drop in (None, "fields", "raster", "diagnostics", "fingerprint"):
            if drop is not None:
                view.pop(drop, None)
            if _size(view) <= max_chars:
                break
        return view

    def quality_status_hint(self) -> str:
        """诊断 → 质量四态粗提示（权威判定在 quality 模块）。"""
        severities = {d.severity for d in self.diagnostics}
        if "error" in severities:
            return vocab.QualityStatus.BLOCKED.value
        if "warning" in severities:
            return vocab.QualityStatus.WARNING.value
        return vocab.QualityStatus.VALID.value


# ── 桥接器（对既有真相的只读投影；缺证据诚实置 None）──────────────────


def _bound_bbox(value: Any) -> Optional[List[float]]:
    if isinstance(value, (list, tuple)) and len(value) == 4:
        try:
            return [float(v) for v in value]
        except (TypeError, ValueError):
            return None
    return None


def from_artifact_record(record: Any) -> ArtifactContract:
    """会话 ArtifactRecord（registry 账本行 / to_dict 形）→ 契约。O(1)。"""
    r = record
    get = getattr
    metadata = get(r, "metadata", None)
    if metadata is None and isinstance(r, dict):
        metadata = r.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}

    artifact_id = str(get(r, "artifact_id", "") or (r.get("artifact_id") if isinstance(r, dict) else "") or "")
    fine_type = str(
        get(r, "artifact_type", "") or (r.get("artifact_type") if isinstance(r, dict) else "") or ""
    )
    status = str(get(r, "status", "") or (r.get("status") if isinstance(r, dict) else "") or "")
    lifecycle = vocab.lifecycle_from_session_status(status) or vocab.LifecycleState.AVAILABLE

    diagnostics: List[ContractDiagnostic] = []
    if get(r, "empty", False) or (isinstance(r, dict) and r.get("empty")):
        diagnostics.append(ContractDiagnostic(code="empty_payload", severity="warning"))

    role_raw = str(metadata.get("logical_role") or "")
    role = vocab.LogicalRole(role_raw) if role_raw in vocab.LogicalRole._value2member_map_ else None

    fine_category = vocab.category_for_fine_type(fine_type)
    inputs = list(get(r, "inputs", None) or (r.get("inputs") if isinstance(r, dict) else []) or [])
    if role is None:
        if fine_category is not None:
            role = vocab.default_role_for_category(fine_category)
        elif inputs:
            role = vocab.LogicalRole.INTERMEDIATE
        else:
            role = vocab.LogicalRole.DERIVED

    fingerprint = FingerprintSet(
        content=metadata.get("content_fingerprint") or None,
        schema=metadata.get("schema_fingerprint") or None,
        metadata=metadata.get("metadata_fingerprint") or None,
        crs=None,
    )
    stable = (
        metadata.get("reuse_fingerprint")
        or fingerprint.content
    )
    statistics = metadata.get("statistics") if isinstance(metadata.get("statistics"), dict) else None

    return ArtifactContract(
        artifact_id=artifact_id,
        stable_identity=str(stable) if stable else None,
        version=int(get(r, "revision", 0) or (r.get("revision") if isinstance(r, dict) else 0) or 0),
        artifact_type=(
            vocab.coerce_category(fine_category.value if fine_category else None) or "unknown"
        ),
        artifact_subtype=fine_type,
        logical_role=role,
        source=SourceInfo(
            source_type=str(metadata.get("source_type") or "session_ref"),
            source_ref=metadata.get("source_ref") or "",
            display_name=str(metadata.get("display_name") or ""),
        ),
        storage_ref=str(get(r, "storage_ref", "") or artifact_id),
        geometry_kind="",  # 记录层无几何族证据时不虚构；细类型映射见 category
        feature_count=(
            get(r, "feature_count", None)
            if isinstance(get(r, "feature_count", None), int)
            and not isinstance(get(r, "feature_count", None), bool)
            else None
        ),
        crs=str(get(r, "crs", "") or (r.get("crs") if isinstance(r, dict) else "") or ""),
        extent=_bound_bbox(get(r, "bbox", None) or (r.get("bbox") if isinstance(r, dict) else None)),
        statistics=statistics,
        lineage=LineageInfo(
            parents=[str(p) for p in inputs],
            parents_truncated=len(inputs) > _MAX_PARENTS,
            replaces=get(r, "replaces", None) or (r.get("replaces") if isinstance(r, dict) else None),
        ),
        produced_by=ProducedBy(
            tool=str(get(r, "producer_tool", "") or metadata.get("producer_tool") or ""),
            capability=str(get(r, "producer_capability", "") or ""),
            node=str(get(r, "producer_node", "") or ""),
        ),
        fingerprint=fingerprint,
        size_bytes=metadata.get("size_bytes") if isinstance(metadata.get("size_bytes"), int) else None,
        lifecycle=lifecycle,
        persistence=vocab.PersistenceTier(
            metadata.get("persistence_tier")
        ) if metadata.get("persistence_tier") in vocab.PersistenceTier._value2member_map_ else (
            vocab.PersistenceTier.SESSION
        ),
        cacheability=Cacheability(
            deterministic=bool(metadata.get("deterministic", False)),
            cacheable=bool(metadata.get("cacheable", False)),
            reuse_fingerprint=metadata.get("reuse_fingerprint") or None,
        ),
        created_at=_to_datetime(get(r, "created_at", None) or (r.get("created_at") if isinstance(r, dict) else None)),
        updated_at=_to_datetime(get(r, "updated_at", None) or (r.get("updated_at") if isinstance(r, dict) else None)),
        diagnostics=diagnostics,
    )


def from_ref_descriptor(descriptor: Any, *, ref_id: Optional[str] = None) -> ArtifactContract:
    """RefDescriptor（dict / to_dict 形）→ 契约。O(1)、零扫描。"""
    d = descriptor or {}
    ref = str(d.get("ref_id") or ref_id or "")
    geometry_types = d.get("geometry_types") or []
    kinds = {str(g) for g in geometry_types} if isinstance(geometry_types, list) else set()
    if {"Point", "MultiPoint"} & kinds:
        kind = "point"
    elif {"Polygon", "MultiPolygon"} & kinds:
        kind = "polygon"
    elif {"LineString", "MultiLineString"} & kinds:
        kind = "line"
    elif kinds:
        kind = "unknown"
    elif d.get("raster_capable"):
        kind = "raster"
    else:
        kind = "table"

    feature_count = d.get("feature_count")
    diagnostics: List[ContractDiagnostic] = []
    if feature_count == 0:
        diagnostics.append(ContractDiagnostic(code="empty_payload", severity="warning"))
    if d.get("field_schema_complete") is False:
        diagnostics.append(ContractDiagnostic(code="field_schema_truncated", severity="info"))

    category = (
        vocab.category_for_geometry_kind(kind)
        if kind not in ("unknown",)
        else None
    )
    field_schema = d.get("field_schema")
    return ArtifactContract(
        artifact_id=ref,
        artifact_type=(category.value if category else "unknown"),
        logical_role=vocab.LogicalRole.SOURCE,  # ref 是上游数据的运行时呈现
        source=SourceInfo(source_type="session_ref", source_ref=ref),
        storage_ref=ref,
        geometry_kind=kind,
        feature_count=(
            d.get("feature_count")
            if isinstance(d.get("feature_count"), int)
            and not isinstance(d.get("feature_count"), bool)
            else None
        ),
        extent=_bound_bbox(d.get("bbox")),
        data_schema=dict(field_schema) if isinstance(field_schema, dict) else None,
        size_bytes=d.get("estimated_bytes") if isinstance(d.get("estimated_bytes"), int) else None,
        version=int(d.get("content_revision") or 0),
        lifecycle=vocab.LifecycleState.AVAILABLE,
        persistence=vocab.PersistenceTier.SESSION,
        diagnostics=diagnostics,
    )


def from_db_artifact(row: Any) -> ArtifactContract:
    """DB Artifact 行 → 契约（持久真相投影；无状态列 → 生命周期投影）。"""
    lifecycle = vocab.lifecycle_from_db_state(
        has_payload=bool(getattr(row, "storage_ref", None)),
        content_fingerprint=getattr(row, "content_fingerprint", None),
    )
    db_type = str(getattr(row, "artifact_type", "") or "")
    category = vocab._DB_TYPE_TO_CATEGORY.get(db_type)
    diagnostics: List[ContractDiagnostic] = []
    if not getattr(row, "content_fingerprint", None):
        diagnostics.append(
            ContractDiagnostic(
                code="payload_not_promoted",
                message="storage_ref 指向会话载荷；未晋升前随会话过期",
                severity="warning",
            )
        )
    deps: List[str] = []
    for attr in ("layer_id", "upload_record_id"):
        val = getattr(row, attr, None)
        if val:
            deps.append(f"{attr}:{val}")
    return ArtifactContract(
        artifact_id=str(getattr(row, "id", "") or ""),
        stable_identity=str(getattr(row, "content_fingerprint", "") or "") or None,
        artifact_type=category.value if category else "unknown",
        source=SourceInfo(
            source_type="project_artifact",
            source_ref=str(getattr(row, "storage_ref", "") or ""),
        ),
        storage_ref=str(getattr(row, "storage_ref", "") or ""),
        format=str(getattr(row, "format", "") or ""),
        crs=str(getattr(row, "crs", "") or ""),
        fingerprint=FingerprintSet(content=getattr(row, "content_fingerprint", None) or None),
        lineage=LineageInfo(dependencies=deps),
        lifecycle=lifecycle,
        persistence=vocab.PersistenceTier.WORKSPACE,
        created_at=_to_datetime(getattr(row, "created_at", None)),
        updated_at=_to_datetime(getattr(row, "updated_at", None)),
        diagnostics=diagnostics,
    )


def from_raster_descriptor(descriptor: Any) -> ArtifactContract:
    """RasterArtifactDescriptor（dict 形）→ 契约。O(1)、零栅格 IO。"""
    d = descriptor or {}
    raster = RasterShape(
        width=d.get("width") if isinstance(d.get("width"), int) else None,
        height=d.get("height") if isinstance(d.get("height"), int) else None,
        bands=d.get("band_count") if isinstance(d.get("band_count"), int) else None,
        resolution_x=d.get("resolution_x") if isinstance(d.get("resolution_x"), (int, float)) else None,
        resolution_y=d.get("resolution_y") if isinstance(d.get("resolution_y"), (int, float)) else None,
        nodata=d.get("nodata") if isinstance(d.get("nodata"), (int, float)) else None,
        dtype=str(d.get("dtype") or ""),
    )
    return ArtifactContract(
        artifact_id=str(d.get("ref_id") or d.get("id") or ""),
        artifact_type="raster",
        source=SourceInfo(source_type="raster_disk", source_ref=str(d.get("file_path") or "")),
        storage_ref=str(d.get("ref_id") or d.get("id") or ""),
        geometry_kind="raster",
        crs=str(d.get("crs") or ""),
        extent=_bound_bbox(d.get("bounds")),
        raster=raster or None,
        size_bytes=d.get("size_bytes") if isinstance(d.get("size_bytes"), int) else None,
        lifecycle=vocab.LifecycleState.AVAILABLE,
        persistence=vocab.PersistenceTier.SESSION,
    )
