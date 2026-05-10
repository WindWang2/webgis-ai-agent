"""地形分析与遥感指数工具 — 坡度/坡向/山体阴影、多源植被指数"""
import logging
from typing import Any

from app.tools.registry import ToolRegistry, tool
from app.services.rs_service import rs_service
from app.tools._utils import parse_bbox

logger = logging.getLogger(__name__)


def register_terrain_tools(registry: ToolRegistry):

    @tool(registry, name="compute_terrain",
           description="地形分析：计算坡度、坡向、山体阴影，基于 Copernicus DEM 30m 数据",
           param_descriptions={
               "bbox": "边界框 [west, south, east, north]，如 [116.2, 39.7, 116.6, 40.1]",
               "products": "分析产品列表，可选: 'slope'(坡度), 'aspect'(坡向), 'hillshade'(山体阴影)，默认全部",
           })
    async def compute_terrain(bbox: str, products: list[str] | None = None) -> dict:
        try:
            parts = parse_bbox(bbox)
            return await rs_service.compute_terrain(parts, products)
        except ValueError as e:
            return {"error": str(e)}
        except (RuntimeError, OSError) as e:
            return {"error": str(e)}

    @tool(registry, name="compute_vegetation_index",
           description="遥感植被指数计算：NDVI(植被)、NDWI(水体)、NBR(燃烧)、EVI(增强植被)",
           param_descriptions={
               "bbox": "边界框 [west, south, east, north]",
               "date_from": "起始日期 YYYY-MM-DD",
               "date_to": "结束日期 YYYY-MM-DD",
               "index_type": "指数类型: 'ndvi'(默认), 'ndwi', 'nbr', 'evi'",
           })
    async def compute_vegetation_index(bbox: str, date_from: str, date_to: str,
                                        index_type: str = "ndvi") -> dict:
        try:
            parts = parse_bbox(bbox)
            return await rs_service.compute_vegetation_index(parts, date_from, date_to, index_type)
        except ValueError as e:
            return {"error": str(e)}
        except (RuntimeError, OSError) as e:
            return {"error": str(e)}
