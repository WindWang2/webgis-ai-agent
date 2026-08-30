"""DatasetProfile —— 数据集派生画像的统一有界契约（V2 P1）。

现状：RefDescriptor（snake_case，store() 时一次遍历）、Spatial Meta Profile
（camelCase，全量 profiler）、ArtifactRecord（registry 记录）三套重叠形状
各自演化，AlgorithmResolver 只认其中一种（camelCase profile dict）。本模块
把它们收编为**一个**有界 pydantic 契约：

- 它是**派生 metadata**，不是第二数据真相（数据本体仍由 ref/session store
  承载；ADR-0082 invariant 不变）；
- metadata-first：构造器零扫描 —— 从 descriptor / 既有 profile / registry
  记录 O(1) 投影；**绝不读 FeatureCollection、绝不加载 raster**；
- 未知就是未知：fields_status="unknown" / bbox=None 如实缺省，不虚构
  （data_fabric/metadata.py 的 truthful-normalizer 同一原则）；
- to_resolver_profile() 是 resolver 既有 camelCase 入参的唯一适配出口 ——
  resolver 契约保持稳定，新生产方经本契约供数。
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

# 有界性：画像不是数据搬运工（与 artifacts.py 同一预算哲学）。
MAX_PROFILE_FIELDS = 64
MAX_GEOMETRY_TYPES = 8
_SOURCE_TYPES = ("ref_descriptor", "spatial_profile", "artifact_record", "synthetic")


class RasterProfile(BaseModel):
    """栅格画像子结构（band/nodata/分辨率等；矢量数据恒 None）。"""

    width: Optional[int] = None
    height: Optional[int] = None
    band_count: Optional[int] = None
    nodata: Optional[float] = None
    pixel_size: Optional[float] = None          # 单位随 CRS（度或米），如实透传
    dtype: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in self.model_dump().items() if v not in (None, "")}


class DatasetProfile(BaseModel):
    """统一数据集画像（有界、派生、零扫描）。"""

    source: Literal["ref_descriptor", "spatial_profile", "artifact_record", "synthetic"]
    artifact_type: str = ""                     # 已知时填写（注册词表校验交由消费方）
    feature_count: Optional[int] = None
    geometry_types: List[str] = Field(default_factory=list)
    bbox: Optional[List[float]] = None          # [minx, miny, maxx, maxy]
    crs: str = ""                               # 未知留空（不虚构 EPSG:4326）
    fields: Dict[str, str] = Field(default_factory=dict)  # name → 粗类型
    numeric_fields: List[str] = Field(default_factory=list)
    categorical_fields: List[str] = Field(default_factory=list)
    estimated_bytes: Optional[int] = None
    fields_status: Literal["explicit", "unknown"] = "unknown"
    raster: Optional[RasterProfile] = None
    time_field: str = ""

    @field_validator("geometry_types")
    @classmethod
    def _bounded_geometry_types(cls, v: List[str]) -> List[str]:
        return [str(t) for t in v[:MAX_GEOMETRY_TYPES]]

    @field_validator("fields")
    @classmethod
    def _bounded_fields(cls, v: Dict[str, str]) -> Dict[str, str]:
        if len(v) > MAX_PROFILE_FIELDS:
            keep = list(v.items())[:MAX_PROFILE_FIELDS]
            return dict(keep)
        return v

    @field_validator("bbox")
    @classmethod
    def _bbox_shape(cls, v: Optional[List[float]]) -> Optional[List[float]]:
        if v is None:
            return None
        if len(v) != 4:
            return None
        try:
            return [float(v[0]), float(v[1]), float(v[2]), float(v[3])]
        except (TypeError, ValueError):
            return None

    # ── 派生只读视图 ────────────────────────────────────────────────
    @property
    def geometry_kind(self) -> str:
        """主几何族（与 resolver/_dominant_geometry 同一归约口径）。"""
        types = set(self.geometry_types)
        if types & {"Point", "MultiPoint"}:
            return "point"
        if types & {"Polygon", "MultiPolygon"}:
            return "polygon"
        if types & {"LineString", "MultiLineString"}:
            return "line"
        if "raster" in types or (self.raster is not None and not types):
            return "raster"
        return "unknown"

    @property
    def is_empty(self) -> bool:
        return self.feature_count == 0

    def to_resolver_profile(self) -> Dict[str, Any]:
        """AlgorithmResolver 既有 camelCase profile 契约的唯一适配出口。

        逐字段定型（artifactType 为 V2 新增键 —— resolver 的 input-type
        检查只在它已知时生效，旧调用方不带该键则行为逐位不变）。
        """
        return {
            "featureCount": self.feature_count,
            "geometryTypes": list(self.geometry_types),
            "bbox": list(self.bbox) if self.bbox else None,
            "crs": self.crs or None,
            "artifactType": self.artifact_type or None,
            "fields": {
                name: {"type": ftype}
                for name, ftype in list(self.fields.items())[:MAX_PROFILE_FIELDS]
            },
            "fields_status": self.fields_status,
        }

    # ── metadata-first 构造器（全部零扫描）──────────────────────────
    @classmethod
    def from_ref_descriptor(cls, descriptor: Optional[Dict[str, Any]]) -> "DatasetProfile":
        """RefDescriptor（dict/to_dict 形）→ 画像。O(1)，零 FeatureCollection 读。"""
        d = descriptor or {}
        field_schema = d.get("field_schema")
        fields: Dict[str, str] = {}
        numeric: List[str] = []
        categorical: List[str] = []
        if isinstance(field_schema, dict) and field_schema:
            for name, meta in list(field_schema.items())[:MAX_PROFILE_FIELDS]:
                ftype = "unknown"
                if isinstance(meta, dict):
                    ftype = str(meta.get("type") or "unknown")
                elif isinstance(meta, str):
                    ftype = meta
                fields[str(name)] = ftype
                if ftype == "number":
                    numeric.append(str(name))
                elif ftype in ("string", "boolean"):
                    categorical.append(str(name))
        geom_types = d.get("geometry_types")
        return cls(
            source="ref_descriptor",
            feature_count=(
                int(d["feature_count"])
                if isinstance(d.get("feature_count"), int) and not isinstance(d.get("feature_count"), bool)
                else None
            ),
            geometry_types=[str(t) for t in (geom_types or [])][:MAX_GEOMETRY_TYPES]
            if isinstance(geom_types, list)
            else [],
            bbox=d.get("bbox") if isinstance(d.get("bbox"), list) else None,
            crs=str(d.get("crs") or ""),
            fields=fields,
            numeric_fields=numeric[:MAX_PROFILE_FIELDS],
            categorical_fields=categorical[:MAX_PROFILE_FIELDS],
            estimated_bytes=(
                int(d["estimated_bytes"])
                if isinstance(d.get("estimated_bytes"), int) and not isinstance(d.get("estimated_bytes"), bool)
                else None
            ),
            # schema 键被截断（field_schema_complete=False）或缺失 → unknown，
            # 与 profile_from_descriptor 的诚实语义一致。
            fields_status=(
                "explicit"
                if fields and d.get("field_schema_complete", True)
                else "unknown"
            ),
        )

    @classmethod
    def from_spatial_profile(cls, profile: Optional[Dict[str, Any]]) -> "DatasetProfile":
        """Spatial Meta Profile（camelCase dict，全量 profiler 产物）→ 画像。"""
        p = profile or {}
        raw_fields = p.get("fields")
        fields: Dict[str, str] = {}
        numeric: List[str] = []
        categorical: List[str] = []
        if isinstance(raw_fields, dict) and raw_fields:
            for name, meta in list(raw_fields.items())[:MAX_PROFILE_FIELDS]:
                ftype = str(meta.get("type") or "unknown") if isinstance(meta, dict) else "unknown"
                fields[str(name)] = ftype
                if ftype == "number":
                    numeric.append(str(name))
                elif ftype in ("string", "boolean"):
                    categorical.append(str(name))
        return cls(
            source="spatial_profile",
            feature_count=(
                int(p["featureCount"])
                if isinstance(p.get("featureCount"), (int, float))
                and not isinstance(p.get("featureCount"), bool)
                else None
            ),
            geometry_types=[str(t) for t in (p.get("geometryTypes") or [])][:MAX_GEOMETRY_TYPES]
            if isinstance(p.get("geometryTypes"), list)
            else [],
            bbox=p.get("bbox") if isinstance(p.get("bbox"), list) else None,
            crs=str(p.get("crs") or ""),
            fields=fields,
            numeric_fields=numeric[:MAX_PROFILE_FIELDS],
            categorical_fields=categorical[:MAX_PROFILE_FIELDS],
            fields_status="explicit" if fields else "unknown",
        )

    @classmethod
    def from_artifact_record(cls, record: Any) -> "DatasetProfile":
        """ArtifactRecord（registry 记录或其 to_dict）→ 画像（O(1)）。"""
        r = record
        metadata = getattr(r, "metadata", None) or (r.get("metadata") if isinstance(r, dict) else None) or {}
        artifact_type = getattr(r, "artifact_type", None)
        if artifact_type is None and isinstance(r, dict):
            artifact_type = r.get("artifact_type")
        geom_types = metadata.get("geometry_types") or []
        return cls(
            source="artifact_record",
            artifact_type=str(artifact_type or ""),
            feature_count=getattr(r, "feature_count", None),
            geometry_types=[str(t) for t in geom_types][:MAX_GEOMETRY_TYPES]
            if isinstance(geom_types, list)
            else [],
            bbox=list(getattr(r, "bbox", None) or []) or None,
            crs=str(getattr(r, "crs", "") or ""),
            fields_status="unknown",
        )

    @classmethod
    def from_raster_descriptor(cls, descriptor: Optional[Dict[str, Any]]) -> "DatasetProfile":
        """RasterArtifactDescriptor（dict/to_dict 形）→ 画像。O(1)，零栅格 IO。

        Runtime V3（ADR-0089）：栅格产物画像此前无生产方（RasterProfile 是
        死结构）。窗口化写者的 descriptor（写者已知，零重开）经本构造器进入
        契约验证/规划层。栅格产物 feature_count 语义为像元数——只对分类
        栅格有意义，这里如实置 None（不虚构）。
        """
        d = descriptor or {}
        raster = RasterProfile(
            width=d.get("width") or None,
            height=d.get("height") or None,
            band_count=d.get("band_count") or None,
            nodata=d.get("nodata") if isinstance(d.get("nodata"), (int, float)) else None,
            pixel_size=(d.get("resolution_x") if isinstance(d.get("resolution_x"), (int, float)) else None),
            dtype=str(d.get("dtype") or ""),
        )
        bounds = d.get("bounds")
        return cls(
            source="ref_descriptor",
            geometry_types=["raster"],
            bbox=[float(b) for b in bounds] if isinstance(bounds, (list, tuple)) and len(bounds) == 4 else None,
            crs=str(d.get("crs") or ""),
            raster=raster,
        )
