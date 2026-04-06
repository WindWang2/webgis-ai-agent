"""遥感数据 FC 工具"""
import json
import logging
from typing import Optional
from app.tools.registry import ToolRegistry, tool
from app.services.rs_service import rs_service

logger = logging.getLogger(__name__)


def register_rs_tools(registry: ToolRegistry):
    """注册遥感数据工具"""

    @tool(registry, name="fetch_sentinel",
           description="获取 Sentinel-2 卫星影像数据列表",
           param_descriptions={
               "bbox": "边界框 [west, south, east, north]，如 [116.2, 39.7, 116.6, 40.1]",
               "date_from": "起始日期 YYYY-MM-DD",
               "date_to": "结束日期 YYYY-MM-DD",
               "bands": "波段组合，默认 true-color"
           })
    async def fetch_sentinel(bbox: str, date_from: str, date_to: str, bands: str = "true-color") -> dict:
        try:
            parts = [float(x.strip()) for x in bbox.strip("[]()").split(",")]
            if len(parts) != 4:
                return {"error": "bbox 格式错误"}
            return await rs_service.fetch_sentinel_thumbnail(parts, date_from, date_to, bands)
        except Exception as e:
            return {"error": str(e)}

    @tool(registry, name="compute_ndvi",
           description="计算指定区域的 NDVI（归一化植被指数）",
           param_descriptions={
               "bbox": "边界框 [west, south, east, north]",
               "date_from": "起始日期 YYYY-MM-DD",
               "date_to": "结束日期 YYYY-MM-DD"
           })
    async def compute_ndvi(bbox: str, date_from: str, date_to: str) -> dict:
        try:
            parts = [float(x.strip()) for x in bbox.strip("[]()").split(",")]
            if len(parts) != 4:
                return {"error": "bbox 格式错误"}
            return await rs_service.compute_ndvi(parts, date_from, date_to)
        except Exception as e:
            return {"error": str(e)}

    @tool(registry, name="fetch_dem",
           description="获取 DEM 高程数据",
           param_descriptions={
               "bbox": "边界框 [west, south, east, north]"
           })
    async def fetch_dem(bbox: str) -> dict:
        try:
            parts = [float(x.strip()) for x in bbox.strip("[]()").split(",")]
            if len(parts) != 4:
                return {"error": "bbox 格式错误"}
            return await rs_service.fetch_dem(parts)
        except Exception as e:
            return {"error": str(e)}
