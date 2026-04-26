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


    async def compute_terrain(self, bbox: list, products: list[str] | None = None) -> dict:
        """Download DEM tile and compute terrain derivatives (slope, aspect, hillshade)."""
        if products is None:
            products = ["slope", "aspect", "hillshade"]
        try:
            import pystac_client
            import rasterio
            import numpy as np
            from rasterio.enums import Resampling
            from rasterio.warp import transform_bounds

            catalog = pystac_client.Client.open(
                "https://earth-search.aws.element84.com/v1"
            )
            search = catalog.search(
                collections=["cop-dem-glo-30"],
                bbox=bbox,
                max_items=1,
            )
            items = list(search.items())
            if not items:
                return {"error": "指定区域无 DEM 数据"}

            dem_url = _asset_href(items[0].assets, "data")
            if not dem_url:
                return {"error": "DEM 数据链接不可用"}

            # Read DEM (downsample for performance)
            ds_factor = 2
            with rasterio.open(dem_url) as src:
                dem = src.read(1, out_shape=(1, src.height // ds_factor, src.width // ds_factor),
                               resampling=Resampling.average)
                dem_transform = src.transform
                cell_size = abs(dem_transform.a) * ds_factor  # meters per pixel
                crs = str(src.crs)

            dem = dem.astype(float)
            nodata = dem <= -9999
            dem[nodata] = np.nan

            result = {
                "status": "ok",
                "source": "Copernicus DEM GLO-30",
                "item_id": items[0].id,
                "cell_size_m": round(cell_size, 1),
                "bbox": bbox,
            }

            if "slope" in products:
                slope = self._compute_slope(dem, cell_size)
                valid = slope[~np.isnan(slope)]
                result["slope"] = {
                    "unit": "degrees",
                    "stats": {
                        "min": round(float(np.nanmin(slope)), 2),
                        "max": round(float(np.nanmax(slope)), 2),
                        "mean": round(float(np.nanmean(slope)), 2),
                        "std": round(float(np.nanstd(slope)), 2),
                    },
                    "class_distribution": {
                        "flat_0-5°": round(float((valid < 5).sum() / len(valid) * 100), 1),
                        "gentle_5-15°": round(float(((valid >= 5) & (valid < 15)).sum() / len(valid) * 100), 1),
                        "moderate_15-30°": round(float(((valid >= 15) & (valid < 30)).sum() / len(valid) * 100), 1),
                        "steep_30-45°": round(float(((valid >= 30) & (valid < 45)).sum() / len(valid) * 100), 1),
                        "very_steep_45°+": round(float((valid >= 45).sum() / len(valid) * 100), 1),
                    },
                }

            if "aspect" in products:
                aspect = self._compute_aspect(dem, cell_size)
                valid = aspect[~np.isnan(aspect)]
                # Classify into 8 compass directions
                bins = [0, 45, 90, 135, 180, 225, 270, 315, 360]
                labels = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
                counts, _ = np.histogram(valid, bins=bins)
                dist = {labels[i]: round(float(counts[i] / len(valid) * 100), 1) for i in range(8)}
                result["aspect"] = {
                    "unit": "degrees",
                    "stats": {
                        "mean": round(float(np.nanmean(aspect)), 1),
                        "dominant_direction": labels[np.argmax(counts)],
                    },
                    "direction_distribution": dist,
                }

            if "hillshade" in products:
                hs = self._compute_hillshade(dem, cell_size)
                result["hillshade"] = {
                    "stats": {
                        "min": round(float(np.nanmin(hs)), 2),
                        "max": round(float(np.nanmax(hs)), 2),
                        "mean": round(float(np.nanmean(hs)), 2),
                    },
                    "sun_azimuth": 315,
                    "sun_altitude": 45,
                }

            # Elevation summary always included
            result["elevation"] = {
                "stats": {
                    "min": round(float(np.nanmin(dem)), 1),
                    "max": round(float(np.nanmax(dem)), 1),
                    "mean": round(float(np.nanmean(dem)), 1),
                    "std": round(float(np.nanstd(dem)), 1),
                }
            }

            return result
        except ImportError as e:
            return {"error": f"缺少依赖: {e}"}
        except Exception as e:
            logger.error(f"Terrain computation error: {e}")
            return {"error": str(e)}

    @staticmethod
    def _compute_slope(dem: "np.ndarray", cell_size: float) -> "np.ndarray":
        """Compute slope in degrees using Horn's method (3x3 window)."""
        import numpy as np
        pad = np.pad(dem, 1, mode="edge")
        dzdx = ((pad[1:-1, 2:] - pad[1:-1, :-2]) / (2 * cell_size) +
                 (pad[:-2, 2:] - pad[:-2, :-2]) / (4 * cell_size) +
                 (pad[2:, 2:] - pad[2:, :-2]) / (4 * cell_size)) / 2
        dzdy = ((pad[2:, 1:-1] - pad[:-2, 1:-1]) / (2 * cell_size) +
                 (pad[2:, :-2] - pad[:-2, :-2]) / (4 * cell_size) +
                 (pad[2:, 2:] - pad[:-2, 2:]) / (4 * cell_size)) / 2
        slope_rad = np.arctan(np.sqrt(dzdx ** 2 + dzdy ** 2))
        return np.degrees(slope_rad)

    @staticmethod
    def _compute_aspect(dem: "np.ndarray", cell_size: float) -> "np.ndarray":
        """Compute aspect in degrees (0-360, clockwise from North)."""
        import numpy as np
        pad = np.pad(dem, 1, mode="edge")
        dzdx = (pad[1:-1, 2:] - pad[1:-1, :-2]) / (2 * cell_size)
        dzdy = (pad[2:, 1:-1] - pad[:-2, 1:-1]) / (2 * cell_size)
        aspect = np.degrees(np.arctan2(-dzdy, dzdx))
        aspect = np.where(aspect < 0, aspect + 360, aspect)
        flat = (dzdx == 0) & (dzdy == 0)
        aspect[flat] = np.nan
        return aspect

    @staticmethod
    def _compute_hillshade(dem: "np.ndarray", cell_size: float,
                           azimuth: float = 315, altitude: float = 45) -> "np.ndarray":
        """Compute hillshade illumination (0-255)."""
        import numpy as np
        pad = np.pad(dem, 1, mode="edge")
        dzdx = (pad[1:-1, 2:] - pad[1:-1, :-2]) / (2 * cell_size)
        dzdy = (pad[2:, 1:-1] - pad[:-2, 1:-1]) / (2 * cell_size)
        slope_rad = np.arctan(np.sqrt(dzdx ** 2 + dzdy ** 2))
        aspect_rad = np.arctan2(-dzdy, dzdx)
        az_rad = np.radians(360 - azimuth)
        alt_rad = np.radians(altitude)
        hs = (np.sin(alt_rad) * np.cos(slope_rad) +
              np.cos(alt_rad) * np.sin(slope_rad) * np.cos(az_rad - aspect_rad))
        return np.clip(hs * 255, 0, 255)

    async def compute_vegetation_index(self, bbox: list, date_from: str, date_to: str,
                                        index_type: str = "ndvi") -> dict:
        """Compute vegetation/water indices from Sentinel-2 bands."""
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
                return {"error": "指定区域和时间范围无 Sentinel-2 数据"}

            item = items[0]
            asset_map = {
                "blue": "B02",
                "green": "B03",
                "red": "B04",
                "nir": "B08",
                "swir11": "B11",
                "swir12": "B12",
            }
            stac_keys = {
                "blue": "blue",
                "green": "green",
                "red": "red",
                "nir": "nir",
                "swir11": "swir16",
                "swir12": "swir22",
            }

            def read_band(band_name: str) -> "np.ndarray":
                stac_key = stac_keys[band_name]
                url = _asset_href(item.assets, stac_key)
                if not url:
                    return None
                with rasterio.open(url) as src:
                    return src.read(1, out_shape=(1, src.height // 4, src.width // 4),
                                    resampling=Resampling.average).astype(float)

            index_type = index_type.lower()
            formulas = {
                "ndvi": (["red", "nir"], lambda r, nir: (nir - r) / np.where((nir + r) > 0, nir + r, 1)),
                "ndwi": (["green", "nir"], lambda g, nir: (g - nir) / np.where((g + nir) > 0, g + nir, 1)),
                "nbr": (["nir", "swir12"], lambda nir, swir: (nir - swir) / np.where((nir + swir) > 0, nir + swir, 1)),
                "evi": (["blue", "red", "nir"], lambda b, r, nir: 2.5 * (nir - r) / np.where((nir + 6 * r - 7.5 * b + 1) > 0, nir + 6 * r - 7.5 * b + 1, 1)),
            }

            if index_type not in formulas:
                return {"error": f"不支持的指数类型 '{index_type}'，可用: {list(formulas.keys())}"}

            bands_needed, formula = formulas[index_type]
            bands = {}
            for bname in bands_needed:
                arr = read_band(bname)
                if arr is None:
                    return {"error": f"波段 {bname} ({asset_map[bname]}) 不可用", "available": list(item.assets.keys())}
                bands[bname] = arr

            index_vals = formula(**bands)
            index_name = index_type.upper()

            # Classification thresholds
            if index_type == "ndvi":
                coverage = round(float((index_vals > 0.3).sum() / index_vals.size * 100), 1)
                classification = {"vegetation_coverage_pct": coverage}
            elif index_type == "ndwi":
                coverage = round(float((index_vals > 0).sum() / index_vals.size * 100), 1)
                classification = {"water_coverage_pct": coverage}
            elif index_type == "nbr":
                severity = {
                    "unburned": round(float((index_vals > 0.1).sum() / index_vals.size * 100), 1),
                    "low_severity": round(float(((index_vals >= -0.1) & (index_vals <= 0.1)).sum() / index_vals.size * 100), 1),
                    "moderate_severity": round(float(((index_vals >= -0.27) & (index_vals < -0.1)).sum() / index_vals.size * 100), 1),
                    "high_severity": round(float((index_vals < -0.27).sum() / index_vals.size * 100), 1),
                }
                classification = {"burn_severity": severity}
            else:
                classification = {}

            return {
                "status": "ok",
                "index_type": index_name,
                "item_id": item.id,
                "datetime": str(item.datetime),
                "cloud_cover": item.properties.get("eo:cloud_cover", "N/A"),
                "stats": {
                    "min": round(float(np.nanmin(index_vals)), 4),
                    "max": round(float(np.nanmax(index_vals)), 4),
                    "mean": round(float(np.nanmean(index_vals)), 4),
                    "std": round(float(np.nanstd(index_vals)), 4),
                },
                "classification": classification,
                "bbox": bbox,
                "description": {
                    "ndvi": "归一化植被指数，范围 -1~1，>0.3 表示有植被覆盖",
                    "ndwi": "归一化水体指数，范围 -1~1，>0 表示水体",
                    "nbr": "归一化燃烧比，用于火灾监测，<-0.27 表示严重燃烧",
                    "evi": "增强植被指数，对高生物量区域更敏感，范围通常 0~1",
                }[index_type],
            }
        except ImportError as e:
            return {"error": f"缺少依赖: {e}"}
        except Exception as e:
            logger.error(f"Vegetation index error: {e}")
            return {"error": str(e)}


rs_service = RemoteSensingService()
