"""地形分析与遥感指数工具 — 坡度/坡向/山体阴影、多源植被指数"""
import logging
from typing import Any

from app.tools.registry import ToolRegistry, tool
from app.services.rs_service import rs_service
from app.tools._utils import parse_bbox

logger = logging.getLogger(__name__)


def register_terrain_tools(registry: ToolRegistry):

    @tool(registry, name="compute_terrain",
           description=(
               "地形分析（slope/aspect/hillshade）：基于 Copernicus DEM 30m 数据，一步生成多产品。"
               "\n何时用：山区选址；坡度大于 X° 的危险区域识别；hillshade 用于专题图美化底图。"
               "\n何时不用：(1) 只要原始 DEM — 用 fetch_dem；(2) 城市地形（精度需求 > 30m）— 当前不支持。"
               "\n关键约束：products 可选 slope/aspect/hillshade，默认全部；bbox 跨省会超时。"
           ),
           tier=2, domains=["raster"],
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
           description=(
               "在线遥感指数统一入口：NDVI(植被)/NDWI(水体)/NBR(燃烧)/EVI(增强植被)，自动 STAC 拉 Sentinel-2 波段并计算。"
               "\n何时用：除 NDVI 外的指数（NDWI 水体面积、NBR 火烧迹地、EVI 高生物量）；指数随场景动态选择时。"
               "\n何时不用：(1) 只算 NDVI — 直接 compute_ndvi（接口更窄、参数更少）；"
               "(2) 要双时相对比 — 用 detect_vegetation_change；"
               "(3) 本地 TIFF 处理 — 用 analyze_vegetation_index。"
               "\n关键约束：index_type ∈ {ndvi, ndwi, nbr, evi}；返回 {stats, classification, bbox}。"
           ),
           tier=2, domains=["raster"],
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
