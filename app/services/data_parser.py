"""GIS 数据文件解析服务"""
import json
import logging
import os
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import geopandas as gpd
import numpy as np
import rasterio

logger = logging.getLogger(__name__)

# 支持的格式
VECTOR_FORMATS = {".geojson", ".json", ".shp", ".kml", ".gpkg", ".csv"}
RASTER_FORMATS = {".tif", ".tiff"}
VECTOR_EXTENSIONS = {".shp", ".shx", ".dbf", ".prj", ".cpg", ".sbn", ".sbx"}

# 文件大小限制 (字节)
MAX_VECTOR_SIZE = 50 * 1024 * 1024   # 50MB
MAX_RASTER_SIZE = 200 * 1024 * 1024  # 200MB

# CSV 经纬度列名候选
LNG_COLUMNS = {"lng", "lon", "long", "longitude", "x", "经度"}
LAT_COLUMNS = {"lat", "latitude", "y", "纬度"}


class ParseError(Exception):
    """数据解析错误"""
    pass


def _detect_csv_columns(df) -> Tuple[Optional[str], Optional[str]]:
    """自动检测 CSV 中的经纬度列"""
    columns = {c.strip().lower() for c in df.columns}
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
    if ext in {".shp"}:
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


def parse_vector(
    file_path: Path,
    upload_dir: Path,
    upload_id: str,
) -> Dict[str, Any]:
    """解析矢量文件，转为 GeoJSON 存储"""
    ext = file_path.suffix.lower()

    if ext == ".csv":
        return _parse_csv(file_path, upload_dir, upload_id)

    # 读取矢量数据
    try:
        if ext == ".shp":
            # shapefile: 解压 zip 后读取 .shp
            gdf = gpd.read_file(file_path, engine="pyogrio")
        elif ext == ".kml":
            gdf = gpd.read_file(file_path, driver="KML", engine="pyogrio")
        else:
            gdf = gpd.read_file(file_path, engine="pyogrio")
    except Exception as e:
        raise ParseError(f"矢量文件读取失败: {e}")

    if gdf.empty:
        raise ParseError("文件中没有要素数据")

    # 统一转 EPSG:4326
    if gdf.crs is not None:
        try:
            gdf = gdf.to_crs(epsg=4326)
        except Exception as e:
            logger.warning(f"坐标转换失败，保留原始 CRS: {e}")
            crs_str = str(gdf.crs)
        else:
            crs_str = "EPSG:4326"
    else:
        crs_str = "EPSG:4326"

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
        "geometry_type": geometry_type,
        "feature_count": feature_count,
        "bbox": bbox,
        "attributes": attr_cols,
        "output_path": str(output_path),
    }


def _parse_csv(
    file_path: Path,
    upload_dir: Path,
    upload_id: str,
) -> Dict[str, Any]:
    """解析 CSV 文件，自动检测经纬度列"""
    import pandas as pd

    df = pd.read_csv(file_path)
    if df.empty:
        raise ParseError("CSV 文件中没有数据行")

    lng_col, lat_col = _detect_csv_columns(df)
    if not lng_col or not lat_col:
        available = ", ".join(df.columns.tolist())
        raise ParseError(
            f"无法自动检测经纬度列。可用列: {available}\n"
            f"支持的列名: 经度({', '.join(LNG_COLUMNS)}), 纬度({', '.join(LAT_COLUMNS)})"
        )

    # 转为 GeoDataFrame
    gdf = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df[lng_col].astype(float), df[lat_col].astype(float)),
        crs="EPSG:4326",
    )

    # 去掉无效几何
    gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.notna()]
    if gdf.empty:
        raise ParseError("CSV 中没有有效的坐标数据")

    feature_count = len(gdf)
    bounds = gdf.total_bounds.tolist()
    bbox = [bounds[0], bounds[1], bounds[2], bounds[3]]

    output_path = upload_dir / "original.geojson"
    gdf.to_file(output_path, driver="GeoJSON")

    attr_cols = [c for c in gdf.columns if c != gdf.geometry.name]

    return {
        "file_type": "vector",
        "format": "csv",
        "crs": "EPSG:4326",
        "geometry_type": "Point",
        "feature_count": feature_count,
        "bbox": bbox,
        "attributes": attr_cols,
        "output_path": str(output_path),
    }


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

    # 复制原始文件到上传目录
    output_path = upload_dir / "original.tif"
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
