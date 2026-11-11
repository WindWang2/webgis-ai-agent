"""Data Delivery Contracts — 词汇表、硬帽与能力探测（G18）。

单点真相：格式可用性来自**运行时真实驱动探测**（pyogrio/GDAL 驱动表 +
依赖导入），绝不以扩展名声明代替；损失码/错误码是有界词汇表，前端与
工具投影同源。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.schemas.data_delivery_schema import DeliveryFormat

# ── 硬帽（资源纪律：一次一个 writer，批式有界）───────────────────────────

#: 单次交付行数硬帽（估算只是提示；writer 实际产出同样受此约束）。
MAX_ROWS_PER_DELIVERY = 200_000

#: 输出总字节硬帽（压缩后）。
MAX_OUTPUT_BYTES = 512 * 1024 * 1024

#: 临时盘硬帽（staging parquet 中间体 + 输出展开）。
MAX_TEMP_DISK_BYTES = 1024 * 1024 * 1024

#: 字段数硬帽（投影与全字段一致）。
MAX_FIELDS = 256

#: 稳定 ID 列表硬帽（选择集运输上限之外的服务器侧上界）。
MAX_IDS = 500

#: 读取批大小（行；按几何字节可继续细分的起点）。
READ_BATCH_ROWS = 2000

#: DataObject 发布预算（与 fabric parquet lane 同量级；超出 → honest oversized）。
DATA_OBJECT_PUBLISH_BUDGET_BYTES = 256 * 1024 * 1024

#: 安全文件名：ASCII 白名单 + 长度（与 map.py export 同款纪律）。
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

# ── 错误码词汇表（typed；工具/路由/回执共用）─────────────────────────────

ERR_SOURCE_NOT_FOUND = "SOURCE_NOT_FOUND"
ERR_SOURCE_EXPIRED = "SOURCE_EXPIRED"
ERR_SOURCE_REVISION_CHANGED = "SOURCE_REVISION_CHANGED"
ERR_SOURCE_KIND_UNKNOWN = "SOURCE_KIND_UNKNOWN"
ERR_SOURCE_SHAPE_UNSUPPORTED = "SOURCE_SHAPE_UNSUPPORTED"
ERR_FORMAT_UNAVAILABLE = "FORMAT_UNAVAILABLE"
ERR_MISSING_SOURCE_CRS = "MISSING_SOURCE_CRS"
ERR_LOSS_ACK_REQUIRED = "LOSS_ACK_REQUIRED"
ERR_SELECTION_INCOMPLETE = "SELECTION_INCOMPLETE"
ERR_ROW_LIMIT_EXCEEDED = "ROW_LIMIT_EXCEEDED"
ERR_OUTPUT_BUDGET_EXCEEDED = "OUTPUT_BUDGET_EXCEEDED"
ERR_TEMP_DISK_EXCEEDED = "TEMP_DISK_EXCEEDED"
ERR_WRITE_FAILED = "WRITE_FAILED"
ERR_VERIFY_FAILED = "VERIFY_FAILED"
ERR_PUBLISH_FAILED = "PUBLISH_FAILED"
ERR_INVALID_SPEC = "INVALID_SPEC"
ERR_CANCELLED = "CANCELLED"
ERR_UNAUTHORIZED = "UNAUTHORIZED"

# ── 损失码词汇表（预览声明 ↔ oracle 验证一一对应）────────────────────────

LOSS_FIELD_NAME_TRUNCATED = "field_name_truncated"
LOSS_FIELD_NAME_MAPPED = "field_name_mapped"
LOSS_NESTED_VALUE_JSON_ENCODED = "nested_value_json_encoded"
LOSS_COMPLEX_VALUE_JSON_ENCODED = "complex_value_json_encoded"
LOSS_MIXED_GEOMETRY_SINGLE_LAYER = "mixed_geometry_single_layer"
LOSS_GEOMETRY_SPLIT_BY_TYPE = "geometry_split_by_type"
LOSS_COORDINATE_PRECISION_REDUCED = "coordinate_precision_reduced"
LOSS_DATETIME_TO_STRING = "datetime_to_string"
LOSS_BOOL_TO_STRING = "bool_to_string"
LOSS_INT64_DOWNGRADE = "int64_downgrade"
LOSS_CRS_LABEL_UNKNOWN = "crs_label_unknown"
LOSS_GEOMETRY_OMITTED = "geometry_omitted"
LOSS_FORMULA_PREFIX_SANITIZED = "formula_prefix_sanitized"
LOSS_NULL_GEOMETRY_PRESERVED = "null_geometry_preserved"
LOSS_EMPTY_GEOMETRY_PRESERVED = "empty_geometry_preserved"
LOSS_Z_PRESERVED = "z_preserved"

# ── 格式常量 ──────────────────────────────────────────────────────────────

#: 各格式的 MIME（下载路由 Content-Type；shapefile_zip 恒为 zip）。
FORMAT_MIME: Dict[str, str] = {
    "geojson": "application/geo+json",
    "gpkg": "application/geopackage+sqlite3",
    "geoparquet": "application/vnd.apache.parquet",
    "csv": "text/csv; charset=utf-8",
    "shapefile_zip": "application/zip",
    "flatgeobuf": "application/octet-stream",
}

#: 各格式主文件扩展名。
FORMAT_EXT: Dict[str, str] = {
    "geojson": ".geojson",
    "gpkg": ".gpkg",
    "geoparquet": ".parquet",
    "csv": ".csv",
    "shapefile_zip": ".zip",
    "flatgeobuf": ".fgb",
}

#: 坐标语义敏感格式（要求源 CRS 已知，或显式目标 CRS + 可信源）。
GEOJSON_FORMATS = ("geojson",)

#: Shapefile DBF 字段名长度约束（字节）。
SHP_FIELD_NAME_LIMIT = 10


def safe_filename(name: str, default: str = "delivery") -> str:
    """用户文件名 → ASCII 安全主干（与 map.py `_export` 同款纪律）。"""
    stem = _SAFE_FILENAME_RE.sub("_", (name or "").strip()).strip("._")
    return stem[:100] or default


@dataclass
class FormatProbe:
    """单格式能力探测结果（available=False 时必须给出 reason）。"""

    format: str
    available: bool
    constrained: bool = False
    reason: Optional[str] = None
    notes: List[str] = field(default_factory=list)


_PYOGrio_DRIVERS: Dict[str, str] = {
    # format → pyogrio/GDAL 驱动名
    "geojson": "GeoJSON",
    "gpkg": "GPKG",
    "csv": "CSV",
    "shapefile_zip": "ESRI Shapefile",
    "flatgeobuf": "FlatGeobuf",
}

_driver_cache: Optional[Dict[str, str]] = None


def _pyogrio_drivers() -> Dict[str, str]:
    """驱动表（进程内缓存；探测失败 → 空表，全部格式诚实 unavailable）。"""
    global _driver_cache
    if _driver_cache is None:
        try:  # pragma: no cover - 环境差异路径
            from pyogrio import list_drivers

            _driver_cache = dict(list_drivers())
        except Exception:
            _driver_cache = {}
    return _driver_cache


def probe_format(fmt: str) -> FormatProbe:
    """格式真实可用性 —— 以运行时驱动/依赖为准（不装样子）。"""
    if fmt == "geoparquet":
        try:
            from app.services.data_fabric.vector_carrier import arrow_available

            if arrow_available():
                return FormatProbe(fmt, available=True)
            reason = "pyarrow 未安装（GeoParquet 写入器不可用）"
        except Exception as e:  # pragma: no cover
            reason = f"pyarrow 探测失败: {e}"
        return FormatProbe(fmt, available=False, reason=reason)
    driver = _PYOGrio_DRIVERS.get(fmt)
    if driver is None:
        return FormatProbe(fmt, available=False, reason="未知格式")
    table = _pyogrio_drivers()
    cap = table.get(driver)
    if cap and "w" in cap:
        probe = FormatProbe(fmt, available=True, constrained=fmt in ("shapefile_zip", "flatgeobuf"))
        if fmt == "shapefile_zip":
            probe.notes.append("字段名 ≤10 字符（显式映射）；混合几何需分组；日期/布尔/大整数类型受限")
        if fmt == "flatgeobuf":
            probe.notes.append("受限格式：以运行时驱动能力为准")
        return probe
    return FormatProbe(
        fmt,
        available=False,
        reason=f"GDAL 驱动 {driver} 不可写（当前能力: {cap or '缺失'}）",
    )


def probe_all_formats() -> List[FormatProbe]:
    return [probe_format(f) for f in
            ("geojson", "gpkg", "geoparquet", "csv", "shapefile_zip", "flatgeobuf")]


def writer_versions() -> Dict[str, str]:
    """回执记录的 writer/驱动版本（可复现性证据；不含环境秘密）。"""
    out: Dict[str, str] = {}
    try:
        import pyogrio

        out["pyogrio"] = getattr(pyogrio, "__version__", "unknown")
        from osgeo import gdal  # type: ignore

        out["gdal"] = str(getattr(gdal, "__version__", "unknown"))
    except Exception:  # pragma: no cover
        pass
    try:
        import pyarrow

        out["pyarrow"] = getattr(pyarrow, "__version__", "unknown")
    except Exception:  # pragma: no cover
        pass
    try:
        import pyproj

        out["pyproj"] = getattr(pyproj, "__version__", "unknown")
    except Exception:  # pragma: no cover
        pass
    return out
