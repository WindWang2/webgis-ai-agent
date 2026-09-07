"""GIS 数据文件解析服务"""
import json
import logging
import shutil
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import geopandas as gpd
import rasterio

logger = logging.getLogger(__name__)

# 支持的格式
VECTOR_FORMATS = {".geojson", ".json", ".shp", ".zip", ".kml", ".gpkg", ".csv"}
RASTER_FORMATS = {".tif", ".tiff"}
VECTOR_EXTENSIONS = {".shp", ".shx", ".dbf", ".prj", ".cpg", ".sbn", ".sbx"}

# 文件大小限制 (字节)
MAX_VECTOR_SIZE = 50 * 1024 * 1024   # 50MB
MAX_RASTER_SIZE = 200 * 1024 * 1024  # 200MB

# CSV 经纬度列名候选
LNG_COLUMNS = {"lng", "lon", "long", "longitude", "x", "经度"}
LAT_COLUMNS = {"lat", "latitude", "y", "纬度"}

# 编码回退链（审计 R3，有界：只认解码错误，不做 chardet 猜测）：
# 请求编码解码失败 → 尝试链上下一个；链尾仍失败 → ParseError（400 + 修复建议）。
_CSV_ENCODING_FALLBACK = {"utf-8": "gb18030"}


def _quality_remediation(code: str) -> str:
    """引用 app/lib/data/quality.py 的修复建议文本（单一事实源，不复制）。"""
    from app.lib.data.quality import QualityIssueCode, _REMEDIATIONS

    try:
        return _REMEDIATIONS.get(QualityIssueCode(code), "")
    except ValueError:
        return ""


class ParseError(Exception):
    """数据解析错误"""
    pass


def _validate_shapefile_zip(file_path: Path) -> None:
    """Validate a zip archive contains a valid shapefile and is safe to process."""
    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            names = zf.namelist()

            # Reject path traversal
            for name in names:
                if name.startswith("/") or ".." in name:
                    raise ParseError(f"Zip 包含不安全路径: {name}")

            # Check required components
            extensions = {Path(n).suffix.lower() for n in names if not n.endswith("/")}
            required = {".shp", ".dbf"}
            missing = required - extensions
            if missing:
                raise ParseError(f"Shapefile zip 缺少必要组件: {', '.join(sorted(missing))}")

            # Check uncompressed size (zip bomb protection)
            total_uncompressed = sum(info.file_size for info in zf.infolist())
            if total_uncompressed > MAX_VECTOR_SIZE:
                raise ParseError(
                    f"Zip 解压后大小 ({total_uncompressed / 1024 / 1024:.1f}MB) 超过限制 ({MAX_VECTOR_SIZE / 1024 / 1024:.0f}MB)"
                )
    except ParseError:
        raise
    except zipfile.BadZipFile:
        raise ParseError("无效的 Zip 文件")


def _detect_csv_columns(df) -> Tuple[Optional[str], Optional[str]]:
    """自动检测 CSV 中的经纬度列"""
    lng_col = lat_col = None
    for c in df.columns:
        low = c.strip().lower()
        if low in LNG_COLUMNS:
            lng_col = c
        if low in LAT_COLUMNS:
            lat_col = c
    return lng_col, lat_col


def _get_format(ext: str) -> Tuple[str, str]:
    """根据扩展名返回 (file_type, format)"""
    ext = ext.lower()
    if ext in {".tif", ".tiff"}:
        return "raster", "geotiff"
    if ext in {".shp", ".zip"}:
        return "vector", "shapefile"
    if ext in {".kml"}:
        return "vector", "kml"
    if ext in {".gpkg"}:
        return "vector", "gpkg"
    if ext in {".csv"}:
        return "vector", "csv"
    if ext in {".geojson", ".json"}:
        return "vector", "geojson"
    raise ParseError(f"不支持的文件格式: {ext}")


def _geojson_declares_crs(file_path: Path) -> bool:
    """GeoJSON 文件头部是否含显式 ``crs`` 成员（有界嗅探：前 64KB）。

    GDAL/pyogrio 读取无 ``crs`` 成员的 GeoJSON 时会按 RFC 7946 直接给出
    EPSG:4326 —— ``gdf.crs`` 无法区分「文件声明了」与「规范缺省」。crs
    成员按规范位于顶层（features 之前），头部嗅探足够可靠；误报方向是
    把缺省标成 declared（properties 里恰有名为 crs 的字段），保守无害。
    """
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            head = f.read(65536)
    except OSError:
        return False
    return '"crs"' in head


def parse_vector(
    file_path: Path,
    upload_dir: Path,
    upload_id: str,
    *,
    crs: Optional[str] = None,
) -> Dict[str, Any]:
    """解析矢量文件，转为 GeoJSON 存储

    ``crs``：用户显式声明的坐标系（仅对无 CRS 元数据的格式有意义，当前为
    CSV 点表）。None = 未声明，解析器保持诚实未知/既有默认并披露来源。
    """
    ext = file_path.suffix.lower()

    if ext == ".csv":
        return _parse_csv(file_path, upload_dir, upload_id, crs=crs)

    if ext == ".zip":
        _validate_shapefile_zip(file_path)

    # 读取矢量数据
    try:
        if ext == ".zip":
            gdf = gpd.read_file(file_path, engine="pyogrio")
        elif ext == ".shp":
            gdf = gpd.read_file(file_path, engine="pyogrio")
        elif ext == ".kml":
            gdf = gpd.read_file(file_path, driver="KML", engine="pyogrio")
        else:
            gdf = gpd.read_file(file_path, engine="pyogrio")
    except Exception as e:
        raise ParseError(f"矢量文件读取失败: {e}")

    if gdf.empty:
        raise ParseError("文件中没有要素数据")

    # 统一转 EPSG:4326，保存原始 CRS。
    # GeoJSON without a CRS is lon/lat WGS84 (RFC 7946). A shapefile / GPKG /
    # KML / other vector missing CRS is NOT — treating metre-scale projected
    # coordinates as lon/lat silently drops them on the wrong hemisphere.
    # 审计 R4（CRS honesty）：无论哪个分支，meta 都必须披露 crs_source ——
    # source（源元数据确认）/ rfc7946_default（RFC 7946 缺省，附 crs_assumed）。
    original_crs = None
    crs_source = "source"
    crs_assumed: Optional[str] = None
    if gdf.crs is not None:
        original_crs = str(gdf.crs)
        if ext in {".geojson", ".json"} and not _geojson_declares_crs(file_path):
            # GDAL 对无 crs 成员的 GeoJSON 依 RFC 7946 读取即得 4326 ——
            # 这是规范缺省而非源数据声明，必须披露（审计 R4）。
            crs_source = "rfc7946_default"
            crs_assumed = "EPSG:4326"
        try:
            gdf = gdf.to_crs(epsg=4326)
        except Exception as e:
            raise ParseError(f"坐标转换失败 (原始 CRS: {gdf.crs}): {e}")
        else:
            crs_str = "EPSG:4326"
    elif ext in {".geojson", ".json"}:
        # RFC 7946：无 CRS 成员的 GeoJSON 就是 WGS84 经纬度 —— 行为不变，
        # 但必须披露这是规范缺省而非源数据声明。
        crs_str = "EPSG:4326"
        crs_source = "rfc7946_default"
        crs_assumed = "EPSG:4326"
    else:
        raise ParseError(
            "文件缺少坐标参考系（CRS）。Shapefile 需包含 .prj，"
            "或请在上传前为数据指定 CRS。"
        )

    # 获取几何类型
    geom_types = gdf.geometry.type.unique()
    geometry_type = geom_types[0] if len(geom_types) == 1 else "Mixed"
    feature_count = len(gdf)

    # 计算边界
    bounds = gdf.total_bounds.tolist()  # [minx, miny, maxx, maxy]
    bbox = [bounds[0], bounds[1], bounds[2], bounds[3]]

    # 保存为 GeoJSON
    output_path = upload_dir / "original.geojson"
    gdf.to_file(output_path, driver="GeoJSON")

    # 提取属性字段
    attr_cols = [c for c in gdf.columns if c != gdf.geometry.name]

    return {
        "file_type": "vector",
        "format": _get_format(ext)[1],
        "crs": crs_str,
        "original_crs": original_crs if original_crs and original_crs != "EPSG:4326" else None,
        "crs_source": crs_source,
        "crs_assumed": crs_assumed,
        "geometry_type": geometry_type,
        "feature_count": feature_count,
        "bbox": bbox,
        "attributes": attr_cols,
        "output_path": str(output_path),
    }


def _read_csv_bounded(
    file_path: Path,
    *,
    encoding: str,
) -> Tuple[Any, Optional[str]]:
    """读 CSV，带**有界**编码回退链（utf-8 → gb18030）。

    - 只处理 UnicodeDecodeError（解码失败），不做 chardet 猜测、不吞其它异常；
    - 请求编码成功 → 原样返回（utf-8 文件行为逐字节不变，fallback=None）；
    - 解码失败且有回退项 → 用回退编码重试，返回 (df, fallback_encoding)；
    - 链尾仍解码失败 → ParseError（映射 400，附 quality.py ENCODING_ISSUES
      的修复建议文本），不再让 UnicodeDecodeError 逃逸成 HTTP 500。
    """
    import pandas as pd

    try:
        return pd.read_csv(file_path, encoding=encoding), None
    except UnicodeDecodeError:
        fallback = _CSV_ENCODING_FALLBACK.get(encoding.lower())
        if not fallback:
            raise ParseError(
                f"文件不是有效的 {encoding} 编码，且没有可用的回退编码。"
                f"{_quality_remediation('encoding_issues')}"
            ) from None
        try:
            df = pd.read_csv(file_path, encoding=fallback)
        except UnicodeDecodeError:
            raise ParseError(
                f"文件既不是 {encoding} 也不是 {fallback} 编码，无法解码。"
                f"{_quality_remediation('encoding_issues')}"
            ) from None
        return df, fallback


def _parse_csv(
    file_path: Path,
    upload_dir: Path,
    upload_id: str,
    *,
    encoding: str = "utf-8",
    crs: Optional[str] = None,
) -> Dict[str, Any]:
    """解析 CSV 文件，自动检测经纬度列

    - ``encoding``：文本编码（默认 utf-8；utf-8 解码失败自动回退 gb18030
      并在 meta 披露 ``encoding``/``encoding_fallback``）；
    - ``crs``：用户声明的坐标系。**CSV 没有 CRS 元数据** —— 未声明时不再
      谎称确认了 EPSG:4326：meta 写 ``crs_source: "assumed"`` +
      ``crs_assumed: "EPSG:4326"`` + CRS_MISSING 类警告，``crs`` 如实为
      None（质量检查可据此提示补声明）。
    """
    import numpy as np
    import pandas as pd

    df, fallback_encoding = _read_csv_bounded(file_path, encoding=encoding)
    encoding_meta: Dict[str, Any] = {}
    if fallback_encoding is not None:
        # 回退发生过 → 必须披露（下游 / 用户需要知道真实编码）
        encoding_meta["encoding"] = fallback_encoding
        encoding_meta["encoding_fallback"] = True
    if df.empty:
        raise ParseError("CSV 文件中没有数据行")

    lng_col, lat_col = _detect_csv_columns(df)
    if not lng_col or not lat_col:
        available = ", ".join(df.columns.tolist())
        raise ParseError(
            f"无法自动检测经纬度列。可用列: {available}\n"
            f"支持的列名: 经度({', '.join(LNG_COLUMNS)}), 纬度({', '.join(LAT_COLUMNS)})"
        )

    # 转为 GeoDataFrame. Coerce non-numeric / blank cells to NaN (instead of a
    # raw ValueError -> HTTP 500), then drop every row whose coordinates are not
    # finite. A ``POINT (nan nan)`` is neither empty nor NA, so the old filter
    # kept it and downstream consumers (mvt/utm/stats) silently propagated NaN.
    lng = pd.to_numeric(df[lng_col], errors="coerce")
    lat = pd.to_numeric(df[lat_col], errors="coerce")

    # CRS 诚实语义（审计 §三）：声明 → 可信转换；未声明 → 披露假设 + 警告，
    # meta.crs = None（绝不静默把 assumed 写成 confirmed）。
    warnings: List[str] = []
    declared_crs = (crs or "").strip() or None
    original_crs: Optional[str] = None
    if declared_crs:
        crs_source = "declared"
        crs_assumed = None
        try:
            gdf = gpd.GeoDataFrame(
                df,
                geometry=gpd.points_from_xy(lng, lat),
                crs=declared_crs,
            )
        except Exception as e:
            raise ParseError(f"无效的 CRS 声明: {declared_crs} ({e})")
        crs_str = str(gdf.crs)
        if crs_str.upper() != "EPSG:4326":
            original_crs = crs_str
            try:
                gdf = gdf.to_crs(epsg=4326)
            except Exception as e:
                raise ParseError(f"坐标转换失败 (声明 CRS: {declared_crs}): {e}")
            crs_str = "EPSG:4326"
    else:
        crs_source = "assumed"
        crs_assumed = "EPSG:4326"
        crs_str = None
        warnings.append(
            "CRS_MISSING: CSV 无坐标参考系信息，坐标按 EPSG:4326 假设处理"
            f"（未确认）。{_quality_remediation('crs_missing')}"
        )
        # 工作假设仍是 4326（出界检测 / bbox / 落图需要），但已显式披露，
        # 且响应与 DB 行都不再谎称 confirmed 4326。
        gdf = gpd.GeoDataFrame(
            df,
            geometry=gpd.points_from_xy(lng, lat),
            crs="EPSG:4326",
        )

    # 去掉无效几何 (NaN/Inf coordinates included). Build the mask as a Series
    # aligned to df's index so it stays correct even if read_csv gains an
    # index_col or the frame is reindexed before masking.
    finite = pd.Series(
        np.isfinite(lng.to_numpy()) & np.isfinite(lat.to_numpy()), index=df.index
    )
    gdf = gdf[finite & ~gdf.geometry.is_empty & gdf.geometry.notna()]
    if gdf.empty:
        raise ParseError("CSV 中没有有效的坐标数据")

    feature_count = len(gdf)
    bounds = gdf.total_bounds.tolist()
    bbox = [bounds[0], bounds[1], bounds[2], bounds[3]]

    output_path = upload_dir / "original.geojson"
    gdf.to_file(output_path, driver="GeoJSON")

    attr_cols = [c for c in gdf.columns if c != gdf.geometry.name]

    meta: Dict[str, Any] = {
        "file_type": "vector",
        "format": "csv",
        "crs": crs_str,
        "original_crs": original_crs,
        "crs_source": crs_source,
        "crs_assumed": crs_assumed,
        "geometry_type": "Point",
        "feature_count": feature_count,
        "bbox": bbox,
        "attributes": attr_cols,
        "output_path": str(output_path),
    }
    if warnings:
        meta["warnings"] = warnings
    meta.update(encoding_meta)
    return meta


def parse_raster(
    file_path: Path,
    upload_dir: Path,
    upload_id: str,
) -> Dict[str, Any]:
    """解析栅格文件，保存并提取元信息"""
    try:
        with rasterio.open(file_path) as src:
            crs_str = str(src.crs) if src.crs else "未知"
            bounds = src.bounds  # BoundingBox(left, bottom, right, top)
            bbox = [bounds.left, bounds.bottom, bounds.right, bounds.top]
            band_count = src.count
            width, height = src.width, src.height
            dtype = src.dtypes[0] if src.dtypes else "unknown"
            transform = list(src.transform[:6]) if src.transform else None
    except Exception as e:
        raise ParseError(f"栅格文件读取失败: {e}")

    # 复制原始文件到上传目录（避免同名 SameFileError）
    output_path = upload_dir / "original.tif"
    if file_path.resolve() != output_path.resolve():
        shutil.copy2(file_path, output_path)

    return {
        "file_type": "raster",
        "format": "geotiff",
        "crs": crs_str,
        "geometry_type": "raster",
        "feature_count": 0,
        "bbox": bbox,
        "band_count": band_count,
        "width": width,
        "height": height,
        "dtype": dtype,
        "transform": transform,
        "output_path": str(output_path),
    }


def save_meta(upload_dir: Path, meta: Dict[str, Any]) -> None:
    """保存元信息到 meta.json"""
    meta_path = upload_dir / "meta.json"
    # 移除 output_path 等内部字段
    export_meta = {k: v for k, v in meta.items() if k != "output_path"}
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(export_meta, f, ensure_ascii=False, indent=2)


def get_upload_dir(base_dir: str, upload_id: str) -> Path:
    """获取上传目录路径，自动创建"""
    upload_dir = Path(base_dir) / "uploads" / upload_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir
