"""Dataset Profile V3 —— 有界数据剖析契约与纯函数剖析器（§六）。

V3 之前剖析证据有三套重叠形状 + 一个无界全量回退（审计 Agent A/F）：
RefDescriptor（store 时单趟）、spatial_meta_profiler（camelCase、
O(features×fields) 全量收集 + 全排序）、ArtifactRecord.metadata。
``DatasetProfile``（lib/gis）已声明统一契约但只做 O(1) 投影。

本模块给出 V3 剖析的**完整形状**（vector / raster / table + 剖析质量）
与**纯函数有界剖析器**：

- 有界：单趟扫描，行数闸（默认 50k）+ 确定性步长采样（stride = 全量
  行数/扫描行数，非随机——同输入必同剖析）；字段数 ≤64、样本 ≤5、
  唯一值集合封顶（超限记 ``unique_capped``，不冒充精确基数）；
- 诚实：剖不到的证据显式缺省；``profile_quality`` 记录 complete /
  sampled / partial / failed；采样剖的统计只声明覆盖样本，不外推；
- 零 I/O：输入是已就位的 features/rows 或 raster 描述（raster 文件
  读取归 app/services/data_profile 的服务层）；
- 质量检查（§七）消费本模块证据（见 quality.py），不自行扫数据。

统计口径：Welford 单趟均值/方差；数值判定 bool 先于 number（与
RefDescriptor.collect_field_schema 一致）。
"""
from __future__ import annotations

import enum
import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field, field_validator

PROFILE_VERSION = 3

# 有界性预算（与契约层同一哲学：剖析不是数据搬运工）。
DEFAULT_MAX_SCAN_ROWS = 50_000
_MAX_PROFILE_FIELDS = 64
_MAX_SAMPLES = 5
_MAX_GEOMETRY_TYPES = 8
_UNIQUE_SET_CAP = 10_000
_MAX_UNIT_HINTS = 16
_MAX_TEMPORAL_FIELDS = 8

_ISO_DATE_RE = re.compile(
    r"^\d{4}-\d{2}(-\d{2})?([T ]\d{2}:\d{2}(:\d{2})?([+-]\d{2}:?\d{2}|Z)?)?$"
)
# 四位年份限定 1800–2099：任意 4 位数（如邮编 1001、PIN 2015）不再误判
# 为年份样本。
_YEAR_ONLY_RE = re.compile(r"^(?:1[89]|20)\d{2}$")
_TEMPORAL_NAME_RE = re.compile(
    r"(date|time|year|month|day|datum|acquisition|acquired|observed|captured|拍摄|日期|时间|年份)",
    re.IGNORECASE,
)
_UNIT_HINT_RULES: Tuple[Tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"pct|percent|ratio|rate|份额|比例|率$", re.IGNORECASE), "percent/ratio"),
    (re.compile(r"(area(_|-)?km2|km2$)", re.IGNORECASE), "km2"),
    (re.compile(r"(area(_|-)?m2|area(_|-)?sqm|(?<!k)m2$)", re.IGNORECASE), "m2"),
    (re.compile(r"(dist(ance)?|length|km$)", re.IGNORECASE), "km/m"),
    (re.compile(r"(temp(erature)?|气温|温度)", re.IGNORECASE), "celsius"),
    (re.compile(r"(pop(ulation)?|人口)", re.IGNORECASE), "persons"),
    (re.compile(r"(count|num(ber)?$|数量|个数)", re.IGNORECASE), "count"),
    (re.compile(r"(price|cost|usd|cny|rmb|价格)", re.IGNORECASE), "currency"),
    (re.compile(r"(elev(ation)?|alt(itude)?|高程|海拔|dem)", re.IGNORECASE), "meters"),
)


class ProfileQuality(str, enum.Enum):
    """剖析质量（§六：profile quality 必须随 profile 记录）。"""

    COMPLETE = "complete"   # 全量行扫描
    SAMPLED = "sampled"     # 确定性步长采样（只代表样本）
    PARTIAL = "partial"     # 仅描述符级证据（无深扫）
    FAILED = "failed"       # 剖析失败（携带诊断）


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def unit_hint_for_field(name: str) -> str:
    """字段名 → 单位提示（命名约定启发式；命中多条取第一条）。"""
    for pattern, hint in _UNIT_HINT_RULES:
        if pattern.search(str(name)):
            return hint
    return ""


def looks_temporal(name: str, samples: Sequence[Any]) -> bool:
    """字段是否时间语义：命名启发 或 样本值解析为 ISO 日期/四位年份。"""
    if _TEMPORAL_NAME_RE.search(str(name)):
        return True
    for s in samples:
        if isinstance(s, str):
            if _ISO_DATE_RE.match(s) or _YEAR_ONLY_RE.match(s):
                return True
        elif isinstance(s, (int, float)) and not isinstance(s, bool):
            v = float(s)
            if 1800 <= v <= 2200 and float(v).is_integer():
                return True  # 年份样例
    return False


class _Welford:
    """单趟均值/方差（数值稳定；无需缓冲值）。"""

    __slots__ = ("n", "mean", "m2")

    def __init__(self) -> None:
        self.n = 0
        self.mean = 0.0
        self.m2 = 0.0

    def add(self, value: float) -> None:
        self.n += 1
        delta = value - self.mean
        self.mean += delta / self.n
        self.m2 += delta * (value - self.mean)

    def stats(self) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
        """(min 不在这；返回 n, mean, std, var)。min/max 由调用方维护。"""
        if self.n == 0:
            return None, None, None, None
        var = self.m2 / self.n  # 总体方差（描述性统计口径）
        return self.n, self.mean, math.sqrt(var) if var > 0 else 0.0, var


class FieldProfile(BaseModel):
    """单字段剖析（§六 vector/table 共用）。"""

    name: str
    dtype: str = "string"                     # number / boolean / string / mixed / unknown
    null_count: int = 0
    null_rate: Optional[float] = None         # 相对已扫描行
    unique_count: Optional[int] = None        # 封顶集合内精确值；超限 → ≥cap（见 unique_capped）
    unique_capped: bool = False
    min: Optional[float] = None
    max: Optional[float] = None
    mean: Optional[float] = None
    std: Optional[float] = None
    samples: List[Any] = Field(default_factory=list)
    temporal_hint: bool = False
    unit_hint: str = ""

    @field_validator("samples")
    @classmethod
    def _bounded_samples(cls, v: List[Any]) -> List[Any]:
        return list(v)[:_MAX_SAMPLES]

    @field_validator("name")
    @classmethod
    def _bounded_name(cls, v: str) -> str:
        return str(v)[:128]


class VectorProfileData(BaseModel):
    """矢量剖析（§六 vector 清单 + 坐标可行性证据）。"""

    row_count: int = 0                        # 全量行数（len，零扫描成本）
    scanned_rows: int = 0
    geometry_types: List[str] = Field(default_factory=list)
    geometry_type_counts: Dict[str, int] = Field(default_factory=dict)
    extent: Optional[List[float]] = None      # 扫描集 bbox [minx, miny, maxx, maxy]
    empty_geometry_count: int = 0             # 扫描集内 geometry 缺失/空坐标
    impossible_coordinate_count: int = 0      # 扫描集内经纬度出界（假 CRS 的强信号）
    zero_zero_coordinate_count: int = 0       # (0,0) 点（常见空值填充）
    fields: Dict[str, FieldProfile] = Field(default_factory=dict)
    fields_truncated: bool = False
    temporal_fields: List[str] = Field(default_factory=list)

    @field_validator("extent")
    @classmethod
    def _extent_shape(cls, v: Optional[List[float]]) -> Optional[List[float]]:
        if v is None or len(v) != 4:
            return None
        try:
            return [float(x) for x in v]
        except (TypeError, ValueError):
            return None

    @field_validator("geometry_types")
    @classmethod
    def _bounded_geometry_types(cls, v: List[str]) -> List[str]:
        return [str(t) for t in v[:_MAX_GEOMETRY_TYPES]]

    @field_validator("geometry_type_counts")
    @classmethod
    def _bounded_counts(cls, v: Dict[str, int]) -> Dict[str, int]:
        return dict(list(v.items())[:_MAX_GEOMETRY_TYPES])

    @field_validator("fields")
    @classmethod
    def _bounded_fields(cls, v: Dict[str, FieldProfile]) -> Dict[str, FieldProfile]:
        return dict(list(v.items())[:_MAX_PROFILE_FIELDS])

    @field_validator("temporal_fields")
    @classmethod
    def _bounded_temporal(cls, v: List[str]) -> List[str]:
        return [str(t) for t in v[:_MAX_TEMPORAL_FIELDS]]


class RasterBandStats(BaseModel):
    band: int
    dtype: str = ""
    min: Optional[float] = None
    max: Optional[float] = None
    mean: Optional[float] = None
    std: Optional[float] = None
    valid_pixel_ratio: Optional[float] = None  # 非 nodata 占比（降采样读口径）


class RasterProfileData(BaseModel):
    """栅格剖析（§六 raster 清单；服务层从文件头 + 降采样读填充）。"""

    width: Optional[int] = None
    height: Optional[int] = None
    band_count: Optional[int] = None
    dtypes: List[str] = Field(default_factory=list)
    crs: str = ""
    extent: Optional[List[float]] = None
    resolution_x: Optional[float] = None
    resolution_y: Optional[float] = None
    nodata: Optional[List[Optional[float]]] = None
    overviews: Optional[int] = None
    compression: str = ""
    estimated_bytes: Optional[int] = None
    band_stats: List[RasterBandStats] = Field(default_factory=list)
    acquisition_time: Optional[datetime] = None   # 时间元数据（§二十八）
    temporal_resolution: str = ""                 # 如 "16d"、"1y"；无证据留空

    @field_validator("band_stats")
    @classmethod
    def _bounded_bands(cls, v: List[RasterBandStats]) -> List[RasterBandStats]:
        return list(v)[:8]

    @field_validator("extent")
    @classmethod
    def _extent_shape(cls, v: Optional[List[float]]) -> Optional[List[float]]:
        if v is None or len(v) != 4:
            return None
        try:
            return [float(x) for x in v]
        except (TypeError, ValueError):
            return None


class TableProfileData(BaseModel):
    """表格剖析（§六 table 清单 + 候选键/坐标/时间候选）。"""

    row_count: int = 0
    scanned_rows: int = 0
    columns: Dict[str, FieldProfile] = Field(default_factory=dict)
    columns_truncated: bool = False
    candidate_keys: List[str] = Field(default_factory=list)        # 高唯一度、低缺失列
    coordinate_candidates: List[str] = Field(default_factory=list) # lng/lat/x/y 命名列
    time_candidates: List[str] = Field(default_factory=list)

    @field_validator("columns")
    @classmethod
    def _bounded_columns(cls, v: Dict[str, FieldProfile]) -> Dict[str, FieldProfile]:
        return dict(list(v.items())[:_MAX_PROFILE_FIELDS])

    @field_validator("candidate_keys")
    @classmethod
    def _bounded_keys(cls, v: List[str]) -> List[str]:
        return [str(k) for k in v[:8]]

    @field_validator("coordinate_candidates")
    @classmethod
    def _bounded_coords(cls, v: List[str]) -> List[str]:
        return [str(c) for c in v[:8]]

    @field_validator("time_candidates")
    @classmethod
    def _bounded_times(cls, v: List[str]) -> List[str]:
        return [str(t) for t in v[:8]]


class DatasetProfileV3(BaseModel):
    """统一剖析产物（一个目标一份；可缓存、绑定来源修订）。"""

    profile_version: int = PROFILE_VERSION
    target_ref: str = ""                      # 剖析对象指针（ref:/upload id/文件路径）
    artifact_id: str = ""
    category: str = "unknown"                 # 粗类 token（词表）
    crs: str = ""                             # 便捷字段（子结构内亦各持）
    extent: Optional[List[float]] = None
    vector: Optional[VectorProfileData] = None
    raster: Optional[RasterProfileData] = None
    table: Optional[TableProfileData] = None
    profile_quality: ProfileQuality = ProfileQuality.PARTIAL
    sample_size: int = 0                      # 采样口径的样本行数
    source_fingerprint: Optional[str] = None  # 绑定的内容指纹（修订变化 → 失效）
    source_revision: int = 0                  # 绑定的 content_revision
    created_at: datetime = Field(default_factory=_utcnow)
    diagnostics: List[str] = Field(default_factory=list)

    @field_validator("extent")
    @classmethod
    def _extent_shape(cls, v: Optional[List[float]]) -> Optional[List[float]]:
        if v is None or len(v) != 4:
            return None
        try:
            return [float(x) for x in v]
        except (TypeError, ValueError):
            return None

    @field_validator("category")
    @classmethod
    def _category_token(cls, v: str) -> str:
        from app.lib.data.vocabulary import coerce_category

        token = coerce_category(v)
        if token is None:
            raise ValueError(f"unregistered category: {v!r}")
        return token

    @field_validator("diagnostics")
    @classmethod
    def _bounded_diagnostics(cls, v: List[str]) -> List[str]:
        return [str(d)[:200] for d in v[:8]]

    def summary(self, *, max_chars: int = 1200) -> Dict[str, Any]:
        """LLM 友好的有界摘要（§三十二：模型看到的是画像，不是数据）。"""
        view: Dict[str, Any] = {
            "target": self.target_ref or self.artifact_id,
            "category": self.category,
            "profile_quality": self.profile_quality.value,
            "crs": self.crs or None,
            "extent": self.extent,
        }
        if self.vector is not None:
            vp = self.vector
            view["row_count"] = vp.row_count
            view["geometry_types"] = vp.geometry_types
            view["empty_geometry_count"] = vp.empty_geometry_count or None
            fields = {}
            for name, fp in list(vp.fields.items())[:12]:
                entry: Dict[str, Any] = {"type": fp.dtype}
                if fp.dtype == "number" and fp.min is not None:
                    entry["range"] = [fp.min, fp.max]
                if fp.null_rate is not None and fp.null_rate >= 0.5:
                    entry["null_rate"] = round(fp.null_rate, 3)
                if fp.unit_hint:
                    entry["unit_hint"] = fp.unit_hint
                fields[name] = entry
            view["fields"] = fields
            if vp.fields_truncated:
                view["fields_truncated"] = True
            if vp.temporal_fields:
                view["temporal_fields"] = vp.temporal_fields
        if self.raster is not None:
            r = self.raster
            view["raster"] = {
                k: getattr(r, k)
                for k in ("width", "height", "band_count", "dtypes", "compression")
                if getattr(r, k) not in (None, [], "")
            }
        if self.table is not None:
            t = self.table
            view["row_count"] = t.row_count
            view["columns"] = list(t.columns.keys())[:12]
            if t.candidate_keys:
                view["candidate_keys"] = t.candidate_keys
            if t.coordinate_candidates:
                view["coordinate_candidates"] = t.coordinate_candidates
            if t.time_candidates:
                view["time_candidates"] = t.time_candidates
        import json as _json

        text = _json.dumps(view, ensure_ascii=False, default=str)
        if len(text) > max_chars:
            view.pop("fields", None)
            view.pop("raster", None)
        return view


# ── 纯函数有界剖析器 ────────────────────────────────────────────────


def _field_type_of(types: set) -> str:
    if types == {"number"}:
        return "number"
    if types == {"boolean"}:
        return "boolean"
    if types == {"string"}:
        return "string"
    if not types:
        return "unknown"
    return "mixed"


def _iter_leaf_coords(coordinates: Any):
    """任意嵌套 GeoJSON coordinates → 叶子 (x, y)。与 ref_descriptor 同语义。"""
    if not isinstance(coordinates, list):
        return
    if coordinates and isinstance(coordinates[0], (int, float)):
        if len(coordinates) >= 2:  # 畸形单元素 position 不致命（如实跳过）
            yield (coordinates[0], coordinates[1])
        return
    for child in coordinates:
        yield from _iter_leaf_coords(child)


class _FieldAccumulator:
    """单字段累积器（有界唯一集合 + Welford + 样本）。"""

    __slots__ = ("types", "null_count", "min", "max", "welford", "unique", "unique_capped", "samples", "temporal", "seen")

    def __init__(self) -> None:
        self.types: set = set()
        self.null_count = 0
        self.min: Optional[float] = None
        self.max: Optional[float] = None
        self.welford = _Welford()
        self.unique: set = set()
        self.unique_capped = False
        self.samples: List[Any] = []
        self.temporal = False
        self.seen = 0

    def observe(self, value: Any) -> None:
        self.seen += 1
        if value is None:
            self.null_count += 1
            return
        if isinstance(value, bool):
            self.types.add("boolean")
            if len(self.unique) >= _UNIQUE_SET_CAP:
                self.unique_capped = True
            else:
                self.unique.add(value)
        elif isinstance(value, (int, float)):
            self.types.add("number")
            try:
                fv = float(value)
            except (TypeError, ValueError):
                return
            if len(self.unique) >= _UNIQUE_SET_CAP:
                self.unique_capped = True
            else:
                self.unique.add(fv)
            if not math.isfinite(fv):
                return
            if self.min is None or fv < self.min:
                self.min = fv
            if self.max is None or fv > self.max:
                self.max = fv
            self.welford.add(fv)
        else:
            self.types.add("string")
            if isinstance(value, str):
                if len(self.unique) >= _UNIQUE_SET_CAP:
                    self.unique_capped = True
                else:
                    self.unique.add(value)
                if len(self.samples) < _MAX_SAMPLES and value not in self.samples:
                    self.samples.append(value)
                if looks_temporal("", [value]):
                    self.temporal = True

    def build(self, name: str, scanned_rows: int) -> FieldProfile:
        dtype = _field_type_of(self.types)
        _, mean, std, _ = self.welford.stats()
        # 数值字段不做值级年份启发（高程 2000 / 计数 2015 / ID 2010 会误报
        # temporal）——数值列只按命名判定（下方 _TEMPORAL_NAME_RE）。
        # 缺失率含「键缺席」的行：observe 只在键存在时被调用，
        # absent = scanned - seen（稀疏 schema 的字段不再显出假低缺失率）。
        absent = max(scanned_rows - self.seen, 0)
        profile = FieldProfile(
            name=name,
            dtype=dtype,
            null_count=self.null_count,
            null_rate=((self.null_count + absent) / scanned_rows) if scanned_rows else None,
            unique_count=(len(self.unique) if not self.unique_capped else _UNIQUE_SET_CAP)
            if dtype in ("string", "number", "boolean") else None,
            unique_capped=self.unique_capped,
            min=self.min,
            max=self.max,
            mean=mean if dtype == "number" else None,
            std=std if dtype == "number" else None,
            samples=list(self.samples),
            temporal_hint=self.temporal or bool(_TEMPORAL_NAME_RE.search(name)),
            unit_hint=unit_hint_for_field(name),
        )
        return profile


# 地理/投影判别（§二十七）：只认 CRS 身份证据，不认基准名子串 ——
# "WGS 84 / UTM zone 50N" 含基准名却是投影系，"+proj=utm +datum=WGS84"
# 同理。无法判别（无任何 token）→ None：调用方跳过经纬度判界，
# 绝不默认投影或经纬度。
_GEOGRAPHIC_TOKEN_RE = re.compile(
    r"epsg:?(4326|4490|4269|4214|4610)(?!\d)|geogcs|longlat|crs84|gcs_",
    re.IGNORECASE,
)
_PROJECTED_TOKEN_RE = re.compile(
    r"utm|mercator|lambert|albers|epsg:?(3857|326\d\d|327\d\d|454\d|452\d)(?!\d)",
    re.IGNORECASE,
)


def classify_crs_kind(crs: str) -> Optional[str]:
    """CRS 字符串 → "geographic" | "projected" | None（不可判别）。"""
    token = str(crs or "").strip()
    if not token:
        return None
    is_geo = bool(_GEOGRAPHIC_TOKEN_RE.search(token))
    is_proj = bool(_PROJECTED_TOKEN_RE.search(token))
    if is_proj:
        # 复合命名（"WGS 84 / UTM zone 50N"、proj4 +datum）以投影 token 为准
        return "projected"
    if is_geo:
        return "geographic"
    return None


def profile_features(
    features: Sequence[Any],
    *,
    crs: str = "",
    max_scan_rows: int = DEFAULT_MAX_SCAN_ROWS,
) -> Tuple[VectorProfileData, ProfileQuality]:
    """矢量要素剖析（§六 vector）。单趟有界扫描；超行数闸 → 确定性步长采样。

    返回 (剖析, 剖析质量)。row_count 恒为全量 len（零扫描成本）；其余
    统计在扫描集上计算——采样时只声明样本覆盖（不外推）。
    """
    total = len(features) if isinstance(features, (list, tuple)) else 0
    stride = 1
    if total > max_scan_rows > 0:
        stride = math.ceil(total / max_scan_rows)
    quality = ProfileQuality.COMPLETE if stride == 1 else ProfileQuality.SAMPLED

    geom_type_counts: Dict[str, int] = {}
    minx = miny = math.inf
    maxx = maxy = -math.inf
    empty_geometry = 0
    impossible_coords = 0
    zero_zero = 0
    accs: Dict[str, _FieldAccumulator] = {}
    fields_truncated = False
    scanned = 0

    def _observe_point(x: Any, y: Any) -> None:
        nonlocal minx, miny, maxx, maxy, impossible_coords, zero_zero
        try:
            fx, fy = float(x), float(y)
        except (TypeError, ValueError):
            return
        if not (math.isfinite(fx) and math.isfinite(fy)):
            return
        if fx == 0.0 and fy == 0.0:
            zero_zero += 1
        if not (-180.0 <= fx <= 180.0 and -90.0 <= fy <= 90.0):
            # 仅在可判别为经纬度系（或 CRS 未声明）时按经纬度口径判界；
            # 投影 CRS（米制）出界正常；不可判别的 CRS 不做经纬度假设。
            kind = classify_crs_kind(crs)
            # 未声明 CRS → 按 GeoJSON/RFC7946 经纬度默认口径判界（出界即
            # 假 CRS 证据）；声明了但认不出 → 不做任何口径假设。
            if kind == "geographic" or not str(crs or "").strip():
                impossible_coords += 1
        if fx == 0.0 and fy == 0.0:
            return  # (0,0) 缺失值填充不进 extent（否则 bbox 伸到几内亚湾）
        if fx < minx:
            minx = fx
        if fy < miny:
            miny = fy
        if fx > maxx:
            maxx = fx
        if fy > maxy:
            maxy = fy

    for i in range(0, total, stride):
        feature = features[i]
        scanned += 1
        if not isinstance(feature, dict):
            empty_geometry += 1
            continue
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict):
            empty_geometry += 1
        else:
            gtype = str(geometry.get("type") or "unknown")
            geom_type_counts[gtype] = geom_type_counts.get(gtype, 0) + 1
            coords = geometry.get("coordinates")
            has_leaf = False
            if gtype == "GeometryCollection":
                for sub in geometry.get("geometries") or []:
                    if isinstance(sub, dict):
                        for x, y in _iter_leaf_coords(sub.get("coordinates")):
                            has_leaf = True
                            _observe_point(x, y)
            else:
                for x, y in _iter_leaf_coords(coords):
                    has_leaf = True
                    _observe_point(x, y)
            if not has_leaf:
                empty_geometry += 1
        props = feature.get("properties")
        if isinstance(props, dict):
            if len(props) > _MAX_PROFILE_FIELDS and len(accs) >= _MAX_PROFILE_FIELDS:
                fields_truncated = True
            else:
                for k, v in props.items():
                    if not isinstance(k, str) or not k:
                        continue
                    acc = accs.get(k)
                    if acc is None:
                        if len(accs) >= _MAX_PROFILE_FIELDS:
                            fields_truncated = True
                            break
                        acc = accs[k] = _FieldAccumulator()
                    acc.observe(v)

    bbox: Optional[List[float]] = None
    if scanned and minx != math.inf:
        bbox = [minx, miny, maxx, maxy]

    fields = {
        name: acc.build(name, scanned) for name, acc in accs.items()
    }
    temporal_fields = [n for n, f in fields.items() if f.temporal_hint]

    vp = VectorProfileData(
        row_count=total,
        scanned_rows=scanned,
        geometry_types=sorted(geom_type_counts.keys()),
        geometry_type_counts=geom_type_counts,
        extent=bbox,
        empty_geometry_count=empty_geometry,
        impossible_coordinate_count=impossible_coords,
        zero_zero_coordinate_count=zero_zero,
        fields=fields,
        fields_truncated=fields_truncated,
        temporal_fields=temporal_fields,
    )
    return vp, quality




def profile_rows(
    rows: Sequence[Any],
    *,
    max_scan_rows: int = DEFAULT_MAX_SCAN_ROWS,
) -> Tuple[TableProfileData, ProfileQuality]:
    """表格剖析（§六 table）：行/列统计 + 候选键/坐标列/时间列。"""
    total = len(rows) if isinstance(rows, (list, tuple)) else 0
    stride = 1
    if total > max_scan_rows > 0:
        stride = math.ceil(total / max_scan_rows)
    quality = ProfileQuality.COMPLETE if stride == 1 else ProfileQuality.SAMPLED

    accs: Dict[str, _FieldAccumulator] = {}
    columns_truncated = False
    scanned = 0
    for i in range(0, total, stride):
        row = rows[i]
        scanned += 1
        if not isinstance(row, dict):
            continue
        if len(row) > _MAX_PROFILE_FIELDS and len(accs) >= _MAX_PROFILE_FIELDS:
            columns_truncated = True
            continue
        for k, v in row.items():
            if not isinstance(k, str) or not k:
                continue
            acc = accs.get(k)
            if acc is None:
                if len(accs) >= _MAX_PROFILE_FIELDS:
                    columns_truncated = True
                    break
                acc = accs[k] = _FieldAccumulator()
            acc.observe(v)

    columns = {name: acc.build(name, scanned) for name, acc in accs.items()}
    candidate_keys = [
        name
        for name, f in columns.items()
        if f.dtype in ("string", "number")
        and f.null_rate is not None
        and f.null_rate < 0.01
        and f.unique_count is not None
        and scanned > 0
        and f.unique_count >= scanned * 0.95
        and not f.unique_capped
    ]
    _coord_names = ("lng", "lon", "long", "longitude", "经度", "x", "lat", "latitude", "纬度", "y")
    coordinate_candidates = [
        name for name in columns if name.strip().lower() in _coord_names
    ]
    time_candidates = [
        name for name, f in columns.items() if f.temporal_hint
    ]
    tp = TableProfileData(
        row_count=total,
        scanned_rows=scanned,
        columns=columns,
        columns_truncated=columns_truncated,
        candidate_keys=candidate_keys,
        coordinate_candidates=coordinate_candidates,
        time_candidates=time_candidates,
    )
    return tp, quality


def profile_from_field_schema(
    field_schema: Optional[Dict[str, Any]],
    *,
    complete: bool = True,
    row_count: int = 0,
    geometry_types: Optional[List[str]] = None,
    bbox: Optional[List[float]] = None,
    crs: str = "",
) -> Tuple[VectorProfileData, ProfileQuality]:
    """RefDescriptor.field_schema → 剖析（零扫描投影；PARTIAL 语义）。

    descriptor 只有 type/null_count/min/max/样本 —— mean/std/基数是
    未知（None），不虚构。``complete=False``（descriptor 键截断）时
    fields_truncated=True。
    """
    fields: Dict[str, FieldProfile] = {}
    for name, meta in (field_schema or {}).items():
        if not isinstance(name, str):
            continue
        meta = meta if isinstance(meta, dict) else {}
        ftype = str(meta.get("type") or "unknown")
        null_count = meta.get("null_count") if isinstance(meta.get("null_count"), int) else 0
        fp = FieldProfile(
            name=name,
            dtype=ftype,
            null_count=null_count,
            null_rate=(null_count / row_count) if row_count else None,
            min=meta.get("min") if isinstance(meta.get("min"), (int, float)) else None,
            max=meta.get("max") if isinstance(meta.get("max"), (int, float)) else None,
            samples=list(meta.get("sampleValues") or [])[:_MAX_SAMPLES],
            temporal_hint=looks_temporal(name, meta.get("sampleValues") or []),
            unit_hint=unit_hint_for_field(name),
        )
        fields[name] = fp
    vp = VectorProfileData(
        row_count=row_count,
        scanned_rows=0,
        geometry_types=[str(g) for g in (geometry_types or [])],
        # 计数不做逐类型拆分（无证据）：descriptor 只给类型存在性，
        # 编造 "每类型 × row_count" 会与深扫口径冲突（诚实缺省）。
        geometry_type_counts={},
        fields=fields,
        fields_truncated=not complete,
        temporal_fields=[n for n, f in fields.items() if f.temporal_hint],
    )
    return vp, ProfileQuality.PARTIAL
