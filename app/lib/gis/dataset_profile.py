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

V4（ADR-0104 决策 #4）：to_resolver_profile 升级为 resolver/科学前置条件
**完整事实词表**的唯一适配出口（geometry kind、CRS 类、要素/行数、
numeric/categorical/binary 字段、null 率、栅格维度/波段、hasTimeField、
temporalObservationCount、valueVariance、重复坐标证据）。权威规则（单一
语义源，spatial_meta_profiler._derived_field_facts 同规）：

- ``fields_status == "explicit"``（完整 schema / 全量扫描）→ numeric/
  binary 清单**权威**，空也照发（「证据证明缺席」≠「证据缺席」，
  scientific_preconditions 的 *_field_required 门据此区分 deferred 与
  INSUFFICIENT_DATA）；
- 截断/缺席 schema → 只有**非空**清单才发（正向证据）；缺席键 = unknown。
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

# 有界性：画像不是数据搬运工（与 artifacts.py 同一预算哲学）。
MAX_PROFILE_FIELDS = 64
MAX_GEOMETRY_TYPES = 8
_SOURCE_TYPES = (
    "ref_descriptor", "spatial_profile", "artifact_record", "synthetic",
    "profile_v3",
)


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

    source: Literal[
        "ref_descriptor", "spatial_profile", "artifact_record", "synthetic",
        "profile_v3",
    ]
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
    # ── V4 科学事实扩展（全部可选；证据缺席 = None/空 + fields_status）──
    # 0/1 二值域字段（boolean 列 + V3 深扫证实 {0,1} 定义域的数值列）。
    binary_fields: List[str] = Field(default_factory=list)
    # 字段 → null 率（已知时；≤MAX_PROFILE_FIELDS 键，round 6）。
    null_ratios: Dict[str, float] = Field(default_factory=dict)
    # 时间维度事实：None = 无证据（unknown）；True/False = 有证据。
    has_time_field: Optional[bool] = None
    temporal_observation_count: Optional[int] = None
    # 数值场方差证据（V3 Welford 口径的最大数值总体方差：若为 0 则所有
    # 已测数值场皆常量 —— nonzero_variance_required 的「证据证明违反」）。
    value_variance: Optional[float] = None
    # 重复坐标证据（仅深扫口径；scanned_rows==0 时不得虚构）。
    duplicate_coordinate_count: Optional[int] = None
    unique_coordinate_count: Optional[int] = None

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

    @field_validator("binary_fields", "numeric_fields", "categorical_fields")
    @classmethod
    def _bounded_field_lists(cls, v: List[str]) -> List[str]:
        return [str(x) for x in v[:MAX_PROFILE_FIELDS]]

    @field_validator("null_ratios")
    @classmethod
    def _bounded_null_ratios(cls, v: Dict[str, float]) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for k, r in list(v.items())[:MAX_PROFILE_FIELDS]:
            try:
                out[str(k)] = round(float(r), 6)
            except (TypeError, ValueError):
                continue
        return out

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

    def _field_authoritative(self) -> bool:
        """字段清单是否权威（完整 schema / 全量扫描 → 空清单构成缺席证据）。"""
        return self.fields_status == "explicit"

    def to_resolver_profile(self) -> Dict[str, Any]:
        """AlgorithmResolver 既有 camelCase profile 契约的唯一适配出口。

        V4 事实词表（键名 = scientific_preconditions 的既读事实字典，不新造）：
        featureCount / geometryTypes / bbox / crs / crsClass / artifactType /
        fields（含 null_ratio 逐字段证据）/ fields_status / numericFields /
        categoricalFields / binaryFields / hasTimeField /
        temporalObservationCount / valueVariance / bandCount /
        numericSampleCount / duplicateCoordinateCount / uniqueCoordinateCount。

        清单型键的权威规则（模块 docstring）：explicit → 空也发；否则仅
        非空发。其余键只在证据存在时出现 —— 缺席键消费方按 unknown 处理
        （absent evidence ≠ violated evidence）。逐键定型保证确定性：
        同一画像恒产出同一名键集。
        """
        authoritative = self._field_authoritative()
        numeric = [str(f) for f in self.numeric_fields]
        categorical = [str(f) for f in self.categorical_fields]
        binary = [str(f) for f in self.binary_fields]
        profile: Dict[str, Any] = {
            "featureCount": self.feature_count,
            "geometryTypes": list(self.geometry_types),
            "bbox": list(self.bbox) if self.bbox else None,
            "crs": self.crs or None,
            "artifactType": self.artifact_type or None,
            "fields": self._resolver_fields(),
            "fields_status": self.fields_status,
        }
        if numeric or authoritative:
            profile["numericFields"] = numeric
        if categorical or authoritative:
            profile["categoricalFields"] = categorical
        if binary or authoritative:
            profile["binaryFields"] = binary
        if self.crs:
            try:
                from app.lib.gis.crs_safety import classify_crs

                profile["crsClass"] = classify_crs(self.crs)
            except Exception:  # noqa: BLE001 — 分类失败按缺席（消费方兜底）
                pass
        if self.has_time_field is not None:
            profile["hasTimeField"] = bool(self.has_time_field)
            if self.has_time_field and self.temporal_observation_count is not None:
                profile["temporalObservationCount"] = int(self.temporal_observation_count)
        if self.value_variance is not None:
            profile["valueVariance"] = float(self.value_variance)
        if self.raster is not None and self.raster.band_count is not None:
            profile["bandCount"] = int(self.raster.band_count)
        if (numeric or authoritative) and self.feature_count is not None:
            # 上界代理（与 min_numeric_samples 的内置 featureCount 回退同值
            # 同语义 —— 显式化仅为证据可读性）。
            profile["numericSampleCount"] = int(self.feature_count)
        if self.unique_coordinate_count is not None:
            profile["uniqueCoordinateCount"] = int(self.unique_coordinate_count)
        if self.duplicate_coordinate_count is not None:
            profile["duplicateCoordinateCount"] = int(self.duplicate_coordinate_count)
        return profile

    def _resolver_fields(self) -> Dict[str, Any]:
        """fields → resolver 契约形状（type + 已知 null_ratio 逐字段证据）。"""
        out: Dict[str, Any] = {}
        for name, ftype in list(self.fields.items())[:MAX_PROFILE_FIELDS]:
            entry: Dict[str, Any] = {"type": ftype}
            ratio = self.null_ratios.get(str(name))
            if ratio is not None:
                entry["null_ratio"] = ratio
            out[str(name)] = entry
        return out

    # ── metadata-first 构造器（全部零扫描）──────────────────────────
    @classmethod
    def from_ref_descriptor(cls, descriptor: Optional[Dict[str, Any]]) -> "DatasetProfile":
        """RefDescriptor（dict/to_dict 形）→ 画像。O(1)，零 FeatureCollection 读。"""
        d = descriptor or {}
        field_schema = d.get("field_schema")
        fields: Dict[str, str] = {}
        numeric: List[str] = []
        categorical: List[str] = []
        binary: List[str] = []
        null_ratios: Dict[str, float] = {}
        complete = bool(d.get("field_schema_complete", True))
        fc = (
            int(d["feature_count"])
            if isinstance(d.get("feature_count"), int) and not isinstance(d.get("feature_count"), bool)
            else None
        )
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
                elif ftype == "boolean":
                    # boolean 列恒 0/1 二值域（schema 类型即定义域证据）。
                    categorical.append(str(name))
                    binary.append(str(name))
                elif ftype in ("string",):
                    categorical.append(str(name))
                if (
                    isinstance(meta, dict)
                    and fc
                    and isinstance(meta.get("null_count"), int)
                    and not isinstance(meta.get("null_count"), bool)
                ):
                    null_ratios[str(name)] = round(
                        min(meta["null_count"] / fc, 1.0), 6)
        geom_types = d.get("geometry_types")
        crs = d.get("crs")
        return cls(
            source="ref_descriptor",
            feature_count=fc,
            geometry_types=[str(t) for t in (geom_types or [])][:MAX_GEOMETRY_TYPES]
            if isinstance(geom_types, list)
            else [],
            bbox=d.get("bbox") if isinstance(d.get("bbox"), list) else None,
            crs=str(crs or ""),
            fields=fields,
            numeric_fields=numeric[:MAX_PROFILE_FIELDS],
            categorical_fields=categorical[:MAX_PROFILE_FIELDS],
            binary_fields=binary[:MAX_PROFILE_FIELDS],
            null_ratios=null_ratios,
            estimated_bytes=(
                int(d["estimated_bytes"])
                if isinstance(d.get("estimated_bytes"), int) and not isinstance(d.get("estimated_bytes"), bool)
                else None
            ),
            # schema 键被截断（field_schema_complete=False）或缺失 → unknown，
            # 与 profile_from_descriptor 的诚实语义一致。
            fields_status=(
                "explicit"
                if fields and complete
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
        binary: List[str] = []
        null_ratios: Dict[str, float] = {}
        if isinstance(raw_fields, dict) and raw_fields:
            for name, meta in list(raw_fields.items())[:MAX_PROFILE_FIELDS]:
                ftype = str(meta.get("type") or "unknown") if isinstance(meta, dict) else "unknown"
                fields[str(name)] = ftype
                if ftype == "number":
                    numeric.append(str(name))
                elif ftype == "boolean":
                    categorical.append(str(name))
                    binary.append(str(name))
                elif ftype in ("string",):
                    categorical.append(str(name))
                if isinstance(meta, dict):
                    ratio = meta.get("null_ratio")
                    if isinstance(ratio, (int, float)) and not isinstance(ratio, bool):
                        null_ratios[str(name)] = round(float(ratio), 6)
        profile_numeric = p.get("numericFields")
        profile_binary = p.get("binaryFields")
        profile_categorical = p.get("categoricalFields")
        if isinstance(profile_numeric, list):
            numeric = [str(x) for x in profile_numeric] or numeric
        if isinstance(profile_binary, list):
            binary = [str(x) for x in profile_binary]
        if isinstance(profile_categorical, list):
            categorical = [str(x) for x in profile_categorical]
        has_time = p.get("hasTimeField")
        obs = p.get("temporalObservationCount")
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
            binary_fields=binary[:MAX_PROFILE_FIELDS],
            null_ratios=null_ratios,
            has_time_field=bool(has_time) if isinstance(has_time, bool) else None,
            temporal_observation_count=(
                int(obs) if isinstance(obs, (int, float)) and not isinstance(obs, bool) else None
            ),
            fields_status="explicit" if fields else "unknown",
        )

    @classmethod
    def from_profile_v3(cls, profile: Optional[Any]) -> "DatasetProfile":
        """DatasetProfileV3（app/lib/data/profile.py，最深剖析形状）→ 画像。

        V4：V3 此前是孤岛（只经 profile_dataset 工具暴露、从不进 resolver）。
        本构造器把 V3 投影进统一契约，经 to_resolver_profile 供给 resolver
        —— 单一适配出口不变，不产生第二 profile 形状。诚实规则：

        - 栅格目标 feature_count 恒 None（像元数不是要素数，不虚构）；
        - 时间缺席证据只在全量口径（complete/sampled）下成立；
        - 方差取数值场最大总体方差（=0 ⇒ 所有已测数值场皆常量）；
        - 重复坐标证据只在深扫（scanned_rows>0）下发出。
        """
        if profile is None:
            return cls(source="profile_v3")
        vector = getattr(profile, "vector", None)
        raster = getattr(profile, "raster", None)
        table = getattr(profile, "table", None)
        quality = str(getattr(profile, "profile_quality", "partial"))
        full_scan = quality in ("complete", "sampled")

        fields: Dict[str, str] = {}
        numeric: List[str] = []
        categorical: List[str] = []
        binary: List[str] = []
        null_ratios: Dict[str, float] = {}
        max_variance: Optional[float] = None
        row_count: Optional[int] = None
        geometry_types: List[str] = []
        bbox = getattr(profile, "extent", None)
        temporal_fields: List[str] = []
        scanned_rows = 0
        duplicates: Optional[int] = None
        uniques: Optional[int] = None

        source_fields = None
        fields_truncated = False
        if vector is not None:
            source_fields = getattr(vector, "fields", None)
            row_count = int(getattr(vector, "row_count", 0) or 0)
            geometry_types = [str(g) for g in (getattr(vector, "geometry_types", None) or [])]
            scanned_rows = int(getattr(vector, "scanned_rows", 0) or 0)
            temporal_fields = [str(t) for t in (getattr(vector, "temporal_fields", None) or [])]
            fields_truncated = bool(getattr(vector, "fields_truncated", False))
            if scanned_rows > 0:
                duplicates = int(getattr(vector, "duplicate_coordinate_count", 0) or 0)
                uniques = int(getattr(vector, "unique_coordinate_count", 0) or 0)
        elif table is not None:
            source_fields = getattr(table, "columns", None)
            row_count = int(getattr(table, "row_count", 0) or 0)
            scanned_rows = int(getattr(table, "scanned_rows", 0) or 0)
            fields_truncated = bool(getattr(table, "columns_truncated", False))
            temporal_fields = [str(t) for t in (getattr(table, "time_candidates", None) or [])]

        if isinstance(source_fields, dict):
            for name, fp in list(source_fields.items())[:MAX_PROFILE_FIELDS]:
                fname = str(name)
                dtype = str(getattr(fp, "dtype", "unknown") or "unknown")
                fields[fname] = dtype
                if dtype == "number":
                    numeric.append(fname)
                    std = getattr(fp, "std", None)
                    if isinstance(std, (int, float)) and not isinstance(std, bool):
                        var = float(std) * float(std)
                        if max_variance is None or var > max_variance:
                            max_variance = var
                    u = getattr(fp, "unique_count", None)
                    mn = getattr(fp, "min", None)
                    mx = getattr(fp, "max", None)
                    if (
                        isinstance(u, int) and u == 2
                        and isinstance(mn, (int, float)) and isinstance(mx, (int, float))
                        and float(mn) == 0.0 and float(mx) == 1.0
                        and not getattr(fp, "unique_capped", False)
                    ):
                        binary.append(fname)  # 深扫证实的 0/1 定义域
                elif dtype == "boolean":
                    categorical.append(fname)
                    binary.append(fname)
                elif dtype == "string":
                    categorical.append(fname)
                rate = getattr(fp, "null_rate", None)
                if isinstance(rate, (int, float)) and not isinstance(rate, bool):
                    null_ratios[fname] = round(float(rate), 6)

        raster_profile: Optional[RasterProfile] = None
        if raster is not None:
            nodata_list = getattr(raster, "nodata", None) or []
            first_nodata = nodata_list[0] if nodata_list else None
            dtypes = getattr(raster, "dtypes", None) or []
            raster_profile = RasterProfile(
                width=getattr(raster, "width", None),
                height=getattr(raster, "height", None),
                band_count=getattr(raster, "band_count", None),
                nodata=first_nodata if isinstance(first_nodata, (int, float)) else None,
                pixel_size=getattr(raster, "resolution_x", None),
                dtype=str(dtypes[0]) if dtypes else "",
            )

        if has_time := (bool(temporal_fields) or None):
            obs_count = row_count
        else:
            has_time = False if full_scan else None
            obs_count = None

        return cls(
            source="profile_v3",
            artifact_type=str(getattr(profile, "category", "") or ""),
            feature_count=row_count if vector is not None or table is not None else None,
            geometry_types=(geometry_types or ["raster"])[:MAX_GEOMETRY_TYPES]
            if raster is not None and not geometry_types
            else geometry_types[:MAX_GEOMETRY_TYPES],
            bbox=list(bbox) if isinstance(bbox, (list, tuple)) and len(bbox) == 4 else None,
            crs=str(getattr(profile, "crs", "") or ""),
            fields=fields,
            numeric_fields=numeric[:MAX_PROFILE_FIELDS],
            categorical_fields=categorical[:MAX_PROFILE_FIELDS],
            binary_fields=binary[:MAX_PROFILE_FIELDS],
            null_ratios=null_ratios,
            estimated_bytes=getattr(raster, "estimated_bytes", None),
            fields_status=(
                "explicit"
                if fields and not fields_truncated
                else "unknown"
            ),
            raster=raster_profile,
            has_time_field=has_time,
            temporal_observation_count=obs_count,
            value_variance=max_variance,
            duplicate_coordinate_count=duplicates,
            unique_coordinate_count=uniques,
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
