"""遥感数据 FC 工具"""
import json
import logging
from typing import Optional
from app.tools.registry import ToolRegistry, tool
from app.services.rs_service import rs_service
from app.tools._utils import parse_bbox

logger = logging.getLogger(__name__)


def register_rs_tools(registry: ToolRegistry):
    """注册遥感数据工具"""

    @tool(registry, name="fetch_sentinel",
           description=(
               "Sentinel-2 卫星影像快视图获取：从 AWS STAC 拉取指定 bbox + 日期窗内最少云覆盖的一景影像缩略图。"
               "\n何时用：用户想『看下某区域近期的卫星影像』；为后续 compute_ndvi / detect_vegetation_change 选片；"
               "做时序对比的基础查询。"
               "\n何时不用：(1) 要做 NDVI 计算 — 直接调 compute_ndvi (一步到位)；"
               "(2) 要本地高分影像 NDVI — 上传 TIFF 后用 analyze_vegetation_index；"
               "(3) bbox 极大 (跨省) — STAC 检索会超时，按区县切分。"
               "\n关键约束：bbox=[west,south,east,north] WGS84；日期窗建议 1–3 个月以提高有云容忍度。"
           ),
           tier=2, domains=["raster"],
           param_descriptions={
               "bbox": "边界框 [west, south, east, north]，如 [116.2, 39.7, 116.6, 40.1]",
               "date_from": "起始日期 YYYY-MM-DD",
               "date_to": "结束日期 YYYY-MM-DD",
               "bands": "波段组合：'true-color'(默认) / 'false-color' / 'ndvi'",
           })
    async def fetch_sentinel(bbox: str, date_from: str, date_to: str, bands: str = "true-color") -> dict:
        try:
            parts = parse_bbox(bbox)
            return await rs_service.fetch_sentinel_thumbnail(parts, date_from, date_to, bands)
        except ValueError as e:
            return {"error": str(e)}
        except (RuntimeError, OSError) as e:
            return {"error": str(e)}

    @tool(registry, name="compute_ndvi",
           description=(
               "在线 NDVI 计算 (Sentinel-2)：给 bbox + 日期窗，自动从 STAC 拉 B04/B08 并算 NDVI，返回统计 + 覆盖率分类。"
               "\n何时用：『北京海淀区上个月植被覆盖如何』『查 XX 区 NDVI 趋势』；"
               "不需要落地 TIFF、只要统计指标 (mean / vegetation_coverage_pct)。"
               "\n何时不用：(1) 已上传本地遥感影像 — 用 analyze_vegetation_index (Celery 异步，结果落地为资产)；"
               "(2) 要看两期对比 — 用 detect_vegetation_change；"
               "(3) 要 NDWI / NBR / EVI — 用 compute_vegetation_index (统一入口，可选 index_type)。"
               "\n关键约束：bbox 不要过大（>1°× 1° 易触发下采样导致精度损失）；日期窗 1–3 个月以容忍有云。"
           ),
           tier=2, domains=["raster"],
           param_descriptions={
               "bbox": "边界框 [west, south, east, north] WGS84",
               "date_from": "起始日期 YYYY-MM-DD",
               "date_to": "结束日期 YYYY-MM-DD",
           })
    async def compute_ndvi(bbox: str, date_from: str, date_to: str) -> dict:
        try:
            parts = parse_bbox(bbox)
            return await rs_service.compute_ndvi(parts, date_from, date_to)
        except ValueError as e:
            return {"error": str(e)}
        except (RuntimeError, OSError) as e:
            return {"error": str(e)}

    @tool(registry, name="fetch_dem",
           description=(
               "DEM 高程数据获取 (Copernicus 30m)：拉指定 bbox 内的数字高程模型 TIFF + 统计 + 缩略图。"
               "\n何时用：(a) 山区/选址分析的地形底图；(b) zonal_stats 求行政区平均海拔；"
               "(c) 接下来要算坡度坡向 (compute_terrain) 但只想先看一眼 DEM 范围与极值。"
               "\n何时不用：(1) 直接要坡度坡向产品 — 用 compute_terrain (一步到位含 slope/aspect/hillshade)；"
               "(2) bbox 跨多省 — 数据下载会超时，按市级切分。"
               "\n关键约束：bbox 单位为 WGS84 度；分辨率 30m，对城市精细地形不够（用专题 DEM 替代）。"
           ),
           tier=2, domains=["raster"],
           param_descriptions={
               "bbox": "边界框 [west, south, east, north] WGS84",
           })
    async def fetch_dem(bbox: str) -> dict:
        try:
            parts = parse_bbox(bbox)
            return await rs_service.fetch_dem(parts)
        except ValueError as e:
            return {"error": str(e)}
        except (RuntimeError, OSError) as e:
            return {"error": str(e)}
