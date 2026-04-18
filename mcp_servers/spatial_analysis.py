#!/usr/bin/env python3
"""Spatial Analysis MCP Server - 专业空间分析算子库

包含算子：
- analyze_terrain: 地形特征提取 (坡度, 坡向, 山体阴影)
- detect_raster_change: 栅格时序变化检测
- calculate_zonal_stats: 区域统计 (矢量范围内栅格统计)
"""
import os
import json
import logging
import numpy as np
import rasterio
from typing import Dict, Any, Optional, List
from mcp.server.fastmcp import FastMCP

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("spatial-analysis")

mcp = FastMCP("spatial-analysis")

def _ensure_dir(path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)

@mcp.tool()
def analyze_terrain(path: str, mode: str = "slope", azimuth: float = 315.0, altitude: float = 45.0) -> Dict[str, Any]:
    """基于 DEM 执行地形分析。
    path: DEM 文件的物理路径。
    mode: 分析模式：'slope' (坡度), 'aspect' (坡向), 'hillshade' (山体阴影)。
    """
    logger.info(f"Analyzing terrain: {path}, mode: {mode}")
    output_dir = "data/analysis_results"
    os.makedirs(output_dir, exist_ok=True)
    out_filename = f"terrain_{mode}_{os.path.basename(path)}"
    out_path = os.path.join(output_dir, out_filename)

    try:
        with rasterio.open(path) as src:
            elev = src.read(1)
            res = src.res[0]
            
            # 使用梯度计算
            x, y = np.gradient(elev, res)
            
            if mode == "slope":
                slope = np.arctan(np.sqrt(x*x + y*y)) * (180 / np.pi)
                result_array = slope.astype(np.float32)
            elif mode == "aspect":
                aspect = np.arctan2(-x, y) * (180 / np.pi)
                result_array = aspect.astype(np.float32)
            elif mode == "hillshade":
                az_rad = azimuth * np.pi / 180.0
                alt_rad = altitude * np.pi / 180.0
                slope_rad = np.arctan(np.sqrt(x*x + y*y))
                aspect_rad = np.arctan2(-x, y)
                shaded = np.sin(alt_rad) * np.cos(slope_rad) + \
                         np.cos(alt_rad) * np.sin(slope_rad) * \
                         np.cos(az_rad - aspect_rad)
                result_array = (255 * (shaded + 1) / 2).astype(np.uint8)
            else:
                return {"error": f"Unsupported mode: {mode}"}

            meta = src.meta.copy()
            meta.update(dtype=result_array.dtype, count=1)
            with rasterio.open(out_path, 'w', **meta) as dst:
                dst.write(result_array, 1)

        return {
            "status": "success",
            "path": out_path,
            "mode": mode,
            "stats": {
                "min": float(np.min(result_array)),
                "max": float(np.max(result_array)),
                "mean": float(np.mean(result_array))
            }
        }
    except Exception as e:
        logger.error(f"Terrain analysis failed: {e}")
        return {"error": str(e)}

@mcp.tool()
def detect_raster_change(base_path: str, comp_path: str, threshold: float = 0.1) -> Dict[str, Any]:
    """计算两个时期栅格的差异变化（如 NDVI 差异）。
    base_path: 基期数据路径。
    comp_path: 对比期数据路径。
    """
    logger.info(f"Detecting change: {base_path} vs {comp_path}")
    output_dir = "data/analysis_results"
    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, f"change_{os.path.basename(comp_path)}")

    try:
        with rasterio.open(base_path) as s1, rasterio.open(comp_path) as s2:
            r1 = s1.read(1)
            r2 = s2.read(1, out_shape=s1.shape)
            diff = r2 - r1
            
            # 分类：1 (增长), -1 (退化), 0 (无变化)
            change = np.zeros(diff.shape, dtype=np.int8)
            change[diff > threshold] = 1
            change[diff < -threshold] = -1
            
            meta = s1.meta.copy()
            meta.update(dtype=np.int8, count=1)
            with rasterio.open(out_path, 'w', **meta) as dst:
                dst.write(change, 1)

            res_sq = s1.res[0] * s1.res[1]
            return {
                "status": "success",
                "path": out_path,
                "stats": {
                    "growth_area_m2": float(np.count_nonzero(change == 1) * res_sq),
                    "degrade_area_m2": float(np.count_nonzero(change == -1) * res_sq),
                    "no_change_ratio": float(np.count_nonzero(change == 0) / change.size)
                }
            }
    except Exception as e:
        logger.error(f"Change detection failed: {e}")
        return {"error": str(e)}

@mcp.tool()
def calculate_zonal_stats(raster_path: str, vector_path: str, column: str = "id") -> Dict[str, Any]:
    """执行区域统计：计算矢量面要素内部的栅格值统计（均值/总和等）。
    raster_path: 栅格数据路径 (如 NDVI)。
    vector_path: 矢量数据点路径 (如 行政区划 GeoJSON)。
    """
    logger.info(f"Zonal stats: {raster_path} for {vector_path}")
    import geopandas as gpd
    from rasterio.mask import mask
    
    try:
        gdf = gpd.read_file(vector_path).to_crs(epsg=4326) # 统一 CRS
        results = []
        
        with rasterio.open(raster_path) as src:
            # 确保 CRS 对齐
            if src.crs != gdf.crs:
                gdf = gdf.to_crs(src.crs)
            
            for _, row in gdf.iterrows():
                try:
                    out_image, _ = mask(src, [row.geometry], crop=True)
                    data = out_image[0]
                    valid_data = data[data != src.nodata]
                    
                    stats = {
                        "id": row.get(column, "unknown"),
                        "count": int(valid_data.size),
                        "mean": float(np.mean(valid_data)) if valid_data.size > 0 else 0,
                        "sum": float(np.sum(valid_data)) if valid_data.size > 0 else 0,
                        "max": float(np.max(valid_data)) if valid_data.size > 0 else 0
                    }
                    results.append(stats)
                except Exception:
                    continue
                    
        return {
            "status": "success",
            "results": results,
            "total_count": len(results)
        }
    except Exception as e:
        logger.error(f"Zonal stats failed: {e}")
        return {"error": str(e)}

if __name__ == "__main__":
    mcp.run(transport="stdio")
