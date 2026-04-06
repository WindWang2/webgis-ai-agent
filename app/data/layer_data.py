"""
图层数据读取模块
从各种格式文件中读取矢量数据为 GeoJSON 特征列表
支持: GeoJSON, Shapefile, KML, GPX, GeoParquet
"""
import json
import logging
from typing import List, Dict, Any, Optional
from pathlib import Path

import geopandas as gpd

logger = logging.getLogger(__name__)


def read_geojson(file_path: str) -> List[Dict]:
    """读取 GeoJSON 文件返回特征列表"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if data.get("type") == "FeatureCollection":
            return data.get("features", [])
        elif data.get("type") == "Feature":
            return [data]
        else:
            logger.warning(f"Unexpected GeoJSON type: {data.get('type')}")
            return []
    except Exception as e:
        logger.error(f"Failed to read GeoJSON {file_path}: {e}")
        return []


def read_with_geopandas(file_path: str) -> List[Dict]:
    """使用 GeoPandas 读取各种支持的格式，转换为 GeoJSON 特征列表"""
    try:
        gdf = gpd.read_file(file_path)
        # 转换为 GeoJSON 特征列表
        features = gdf.__geo_interface__.get("features", [])
        logger.info(f"Read {len(features)} features from {file_path}")
        return features
    except Exception as e:
        logger.error(f"Failed to read {file_path} with GeoPandas: {e}")
        return []


def get_layer_features(
    source_url: str,
    source_format: str
) -> List[Dict]:
    """
    根据源 URL 和格式，读取图层特征列表
    
    Args:
        source_url: 文件路径（相对于 DATA_DIR 或者绝对路径）
        source_format: 文件格式 (geojson, shapefile, kml, gpx, parquet)
    
    Returns:
        List[Dict]: GeoJSON 特征列表，空列表表示读取失败
    """
    from app.core.config import settings
    
    # 处理路径：如果不是绝对路径，相对于 DATA_DIR
    path = Path(source_url)
    if not path.is_absolute():
        path = Path(settings.DATA_DIR) / path
    
    if not path.exists():
        logger.error(f"File not found: {path}")
        return []
    
    # 根据格式选择读取方式
    source_format = source_format.lower()
    if source_format == "geojson":
        return read_geojson(str(path))
    else:
        # 其他格式都用 GeoPandas 读取
        return read_with_geopandas(str(path))


__all__ = ["get_layer_features", "read_geojson", "read_with_geopandas"]
