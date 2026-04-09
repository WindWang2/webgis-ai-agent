"""遥感数据服务 - Sentinel Hub + NASA EarthData"""
import json
import logging
import os
from typing import Optional
from datetime import date, datetime
import aiohttp
from app.core.config import settings

logger = logging.getLogger(__name__)


def _asset_href(assets: dict, key: str) -> str:
    """兼容 pystac Asset 对象和旧版 dict 两种格式取 href"""
    asset = assets.get(key)
    if asset is None:
        return ""
    if hasattr(asset, "href"):
        return asset.href or ""
    if isinstance(asset, dict):
        return asset.get("href", "")
    return ""


class RemoteSensingService:
    """遥感数据服务"""

    async def fetch_sentinel_thumbnail(
        self,
        bbox: list,  # [west, south, east, north]
        date_from: str,
        date_to: str,
        bands: str = "true-color",
        width: int = 512,
        height: int = 512,
    ) -> dict:
        """
        获取 Sentinel 影像缩略图
        使用 Sentinel Hub Process API 或公开的 WMS
        """
        if not settings.SENTINELHUB_CLIENT_ID:
            # 如果没有配置 key，使用公开的 Sentinel-2 COG 标注
            return await self._fetch_sentinel_public(bbox, date_from, date_to)

        # 有 key 时用正式 API
        return {"status": "configured", "message": "Sentinel Hub API 已配置，待实现具体调用"}

    async def _fetch_sentinel_public(self, bbox: list, date_from: str, date_to: str) -> dict:
        """使用 Element84 公开 STAC catalog 获取 Sentinel-2 数据"""
        try:
            import pystac_client
            
            catalog = pystac_client.Client.open(
                "https://earth-search.aws.element84.com/v1"
            )
            
            search = catalog.search(
                collections=["sentinel-2-l2a"],
                bbox=bbox,
                datetime=f"{date_from}/{date_to}",
                max_items=5,
            )
            
            items = list(search.items())
            if not items:
                return {"status": "no_data", "message": "指定区域和时间范围内无数据"}
            
            results = []
            for item in items:
                results.append({
                    "id": item.id,
                    "datetime": str(item.datetime),
                    "bbox": item.bbox,
                    "cloud_cover": item.properties.get("eo:cloud_cover", "N/A"),
                    "assets": {
                        "thumbnail": _asset_href(item.assets, "thumbnail"),
                        "visual": _asset_href(item.assets, "visual"),
                        "B04": _asset_href(item.assets, "red"),
                        "B03": _asset_href(item.assets, "green"),
                        "B02": _asset_href(item.assets, "blue"),
                        "B08": _asset_href(item.assets, "nir"),
                    },
                })
            
            return {
                "status": "ok",
                "count": len(results),
                "items": results,
                "source": "Element84 Sentinel-2 L2A",
            }
        except ImportError:
            return {"error": "pystac-client not installed. Run: pip install pystac-client"}
        except Exception as e:
            logger.error(f"Sentinel fetch error: {e}")
            return {"error": str(e)}

    async def compute_ndvi(
        self,
        bbox: list,
        date_from: str,
        date_to: str,
    ) -> dict:
        """计算 NDVI（需要 rasterio 和 COG 链接）"""
        try:
            import pystac_client
            import rasterio
            import numpy as np
            from rasterio.enums import Resampling
            
            catalog = pystac_client.Client.open(
                "https://earth-search.aws.element84.com/v1"
            )
            
            search = catalog.search(
                collections=["sentinel-2-l2a"],
                bbox=bbox,
                datetime=f"{date_from}/{date_to}",
                max_items=1,
            )
            
            items = list(search.items())
            if not items:
                return {"error": "No data found"}
            
            item = items[0]
            red_url = _asset_href(item.assets, "red")
            nir_url = _asset_href(item.assets, "nir")
            
            if not red_url or not nir_url:
                return {"error": "Missing band assets", "available": list(item.assets.keys())}
            
            # 读取波段数据（降采样以节省内存）
            with rasterio.open(red_url) as red_src:
                red = red_src.read(1, out_shape=(1, red_src.height // 4, red_src.width // 4),
                                   resampling=Resampling.average)
            with rasterio.open(nir_url) as nir_src:
                nir = nir_src.read(1, out_shape=(1, nir_src.height // 4, nir_src.width // 4),
                                   resampling=Resampling.average)
            
            # NDVI = (NIR - Red) / (NIR + Red)
            ndvi = np.where(
                (nir + red) > 0,
                (nir.astype(float) - red.astype(float)) / (nir.astype(float) + red.astype(float)),
                0,
            )
            
            return {
                "status": "ok",
                "item_id": item.id,
                "datetime": str(item.datetime),
                "cloud_cover": item.properties.get("eo:cloud_cover", "N/A"),
                "ndvi_stats": {
                    "min": round(float(ndvi.min()), 4),
                    "max": round(float(ndvi.max()), 4),
                    "mean": round(float(ndvi.mean()), 4),
                    "std": round(float(ndvi.std()), 4),
                },
                "vegetation_coverage": round(float((ndvi > 0.3).sum() / ndvi.size * 100), 1),
                "bbox": bbox,
            }
        except ImportError as e:
            return {"error": f"Missing dependency: {e}. Install pystac-client and rasterio."}
        except Exception as e:
            logger.error(f"NDVI error: {e}")
            return {"error": str(e)}

    async def fetch_dem(self, bbox: list) -> dict:
        """获取 DEM 高程数据（使用公开的 Copernicus DEM）"""
        try:
            import pystac_client
            
            catalog = pystac_client.Client.open(
                "https://earth-search.aws.element84.com/v1"
            )
            
            # 搜索 Copernicus DEM（如果可用）
            search = catalog.search(
                collections=["cop-dem-glo-30"],
                bbox=bbox,
                max_items=5,
            )
            
            items = list(search.items())
            if not items:
                return {"error": "No DEM data found for this area"}
            
            results = []
            for item in items:
                results.append({
                    "id": item.id,
                    "bbox": item.bbox,
                    "assets": {
                        "dem": _asset_href(item.assets, "data"),
                    },
                })
            
            return {
                "status": "ok",
                "source": "Copernicus DEM GLO-30",
                "count": len(results),
                "items": results,
            }
        except Exception as e:
            logger.error(f"DEM fetch error: {e}")
            return {"error": str(e)}


rs_service = RemoteSensingService()
