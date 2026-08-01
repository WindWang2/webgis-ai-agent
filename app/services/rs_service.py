"""遥感数据服务 - Sentinel Hub + NASA EarthData"""
import asyncio
import json
import logging
import os
from datetime import date, datetime
from functools import lru_cache
from typing import Optional

import aiohttp
import numpy as np
from app.core.config import settings
from app.tools._utils import asset_href

logger = logging.getLogger(__name__)

_STAC_CATALOG_URL = "https://earth-search.aws.element84.com/v1"

# Pure index formulas for Sentinel-2 bands
INDEX_FORMULAS = {
    "ndvi": (["red", "nir"], lambda r, nir: np.divide(
        nir - r, nir + r,
        out=np.zeros_like(nir - r, dtype=float),
        where=(nir + r) > 0,
    )),
    "ndwi": (["green", "nir"], lambda g, nir: np.divide(
        g - nir, g + nir,
        out=np.zeros_like(g - nir, dtype=float),
        where=(g + nir) > 0,
    )),
    "nbr": (["nir", "swir12"], lambda nir, swir: np.divide(
        nir - swir, nir + swir,
        out=np.zeros_like(nir - swir, dtype=float),
        where=(nir + swir) > 0,
    )),
    "evi": (["blue", "red", "nir"], lambda b, r, nir: 2.5 * np.divide(
        nir - r, nir + 6 * r - 7.5 * b + 1,
        out=np.zeros_like(nir - r, dtype=float),
        where=(nir + 6 * r - 7.5 * b + 1) > 0,
    )),
}


def compute_index_array(index_type: str, **bands) -> "np.ndarray":
    """Compute vegetation/water index array from band NumPy arrays."""
    index_type = index_type.lower()
    if index_type not in INDEX_FORMULAS:
        raise ValueError(f"Unsupported index type '{index_type}'")
    bands_needed, formula = INDEX_FORMULAS[index_type]
    args = [bands[b] for b in bands_needed]
    return formula(*args)



def compute_slope(dem: "np.ndarray", cell_size: float) -> "np.ndarray":
    """Compute slope in degrees using Horn's method (3x3 window)."""
    pad = np.pad(dem, 1, mode="edge")
    dzdx = ((pad[1:-1, 2:] - pad[1:-1, :-2]) / (2 * cell_size) +
             (pad[:-2, 2:] - pad[:-2, :-2]) / (4 * cell_size) +
             (pad[2:, 2:] - pad[2:, :-2]) / (4 * cell_size)) / 2
    dzdy = ((pad[2:, 1:-1] - pad[:-2, 1:-1]) / (2 * cell_size) +
             (pad[2:, :-2] - pad[:-2, :-2]) / (4 * cell_size) +
             (pad[2:, 2:] - pad[:-2, 2:]) / (4 * cell_size)) / 2
    slope_rad = np.arctan(np.sqrt(dzdx ** 2 + dzdy ** 2))
    return np.degrees(slope_rad)


def compute_aspect(dem: "np.ndarray", cell_size: float) -> "np.ndarray":
    """Compute aspect in degrees (0-360, clockwise from North)."""
    pad = np.pad(dem, 1, mode="edge")
    dzdx = (pad[1:-1, 2:] - pad[1:-1, :-2]) / (2 * cell_size)
    dzdy = (pad[2:, 1:-1] - pad[:-2, 1:-1]) / (2 * cell_size)
    aspect = np.degrees(np.arctan2(-dzdy, dzdx))
    aspect = np.where(aspect < 0, aspect + 360, aspect)
    flat = (dzdx == 0) & (dzdy == 0)
    aspect[flat] = np.nan
    return aspect


def compute_hillshade(dem: "np.ndarray", cell_size: float,
                      azimuth: float = 315, altitude: float = 45) -> "np.ndarray":
    """Compute hillshade illumination (0-255)."""
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


class RemoteSensingService:
    """遥感数据服务"""

    @lru_cache(maxsize=1)
    def _get_catalog(self):
        """Synchronous catalog open — cached, called via asyncio.to_thread."""
        import pystac_client
        return pystac_client.Client.open(_STAC_CATALOG_URL)

    async def _open_catalog(self):
        """Async wrapper: offload blocking Client.open to thread pool."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._get_catalog)

    async def _fetch_stac_items_and_bands(
        self,
        collection: str,
        bbox: list,
        date_from: str | None = None,
        date_to: str | None = None,
        max_items: int = 1,
        bands_needed: dict[str, str] | list[str] | None = None,
        ds_factor: int = 4,
        empty_error_msg: str = "No data found",
    ) -> dict:
        """
        Internal STAC fetch primitive: searches catalog, reads requested bands into NumPy arrays.
        Returns dict with "items" list, "first_item", and "bands" dict {"red": ndarray, ...},
        or an error dict {"error": "..."}.
        """
        try:
            import pystac_client
            catalog = await self._open_catalog()

            datetime_param = f"{date_from}/{date_to}" if date_from and date_to else None
            search = catalog.search(
                collections=[collection],
                bbox=bbox,
                datetime=datetime_param,
                max_items=max_items,
            )
            items = list(search.items())
            if not items:
                return {"error": empty_error_msg}

            result = {
                "items": items,
                "first_item": items[0],
                "bands": {},
            }

            if bands_needed:
                import rasterio
                from rasterio.enums import Resampling

                item = items[0]
                bands_dict = {}
                band_pairs = bands_needed.items() if isinstance(bands_needed, dict) else [(b, b) for b in bands_needed]

                for bname, stac_key in band_pairs:
                    url = asset_href(item.assets, stac_key)
                    if not url:
                        return {"error": f"Missing band asset: {stac_key}", "available": list(item.assets.keys())}
                    with rasterio.open(url) as src:
                        out_shape = (1, src.height // ds_factor, src.width // ds_factor)
                        arr = src.read(1, out_shape=out_shape, resampling=Resampling.average).astype(float)
                        bands_dict[bname] = arr
                        if "cell_size_m" not in result:
                            result["cell_size_m"] = abs(src.transform.a) * ds_factor
                            result["crs"] = str(src.crs)
                result["bands"] = bands_dict

            return result
        except ImportError as e:
            return {"error": f"Missing dependency: {e}. Install pystac-client and rasterio."}
        except Exception as e:
            logger.error(f"STAC fetch error ({collection}): {e}")
            return {"error": str(e)}

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
        fetch_res = await self._fetch_stac_items_and_bands(
            collection="sentinel-2-l2a",
            bbox=bbox,
            date_from=date_from,
            date_to=date_to,
            max_items=5,
            empty_error_msg="指定区域和时间范围内无数据",
        )
        if "error" in fetch_res:
            if fetch_res["error"] == "指定区域和时间范围内无数据":
                return {"status": "no_data", "message": fetch_res["error"]}
            return fetch_res

        results = []
        for item in fetch_res["items"]:
            results.append({
                "id": item.id,
                "datetime": str(item.datetime),
                "bbox": item.bbox,
                "cloud_cover": item.properties.get("eo:cloud_cover", "N/A"),
                "assets": {
                    "thumbnail": asset_href(item.assets, "thumbnail"),
                    "visual": asset_href(item.assets, "visual"),
                    "B04": asset_href(item.assets, "red"),
                    "B03": asset_href(item.assets, "green"),
                    "B02": asset_href(item.assets, "blue"),
                    "B08": asset_href(item.assets, "nir"),
                },
            })

        return {
            "status": "ok",
            "count": len(results),
            "items": results,
            "source": "Element84 Sentinel-2 L2A",
        }

    async def compute_ndvi(
        self,
        bbox: list,
        date_from: str,
        date_to: str,
    ) -> dict:
        """计算 NDVI（需要 rasterio 和 COG 链接）"""
        fetch_res = await self._fetch_stac_items_and_bands(
            collection="sentinel-2-l2a",
            bbox=bbox,
            date_from=date_from,
            date_to=date_to,
            bands_needed={"red": "red", "nir": "nir"},
            ds_factor=4,
            empty_error_msg="No data found",
        )
        if "error" in fetch_res:
            return fetch_res

        item = fetch_res["first_item"]
        ndvi = compute_index_array("ndvi", **fetch_res["bands"])

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
            "raster_source": {
                "array": ndvi,
                "bounds": list(bbox),
                "band_stats": {
                    "min": float(ndvi.min()),
                    "max": float(ndvi.max()),
                },
                "suggested_palette": "Viridis",
            },
        }

    async def fetch_dem(self, bbox: list) -> dict:
        """获取 DEM 高程数据（使用公开的 Copernicus DEM）"""
        fetch_res = await self._fetch_stac_items_and_bands(
            collection="cop-dem-glo-30",
            bbox=bbox,
            max_items=5,
            empty_error_msg="No DEM data found for this area",
        )
        if "error" in fetch_res:
            return fetch_res

        results = []
        for item in fetch_res["items"]:
            results.append({
                "id": item.id,
                "bbox": item.bbox,
                "assets": {
                    "dem": asset_href(item.assets, "data"),
                },
            })

        return {
            "status": "ok",
            "source": "Copernicus DEM GLO-30",
            "count": len(results),
            "items": results,
        }

    async def compute_terrain(self, bbox: list, products: list[str] | None = None) -> dict:
        """Download DEM tile and compute terrain derivatives (slope, aspect, hillshade)."""
        if products is None:
            products = ["slope", "aspect", "hillshade"]

        fetch_res = await self._fetch_stac_items_and_bands(
            collection="cop-dem-glo-30",
            bbox=bbox,
            bands_needed={"dem": "data"},
            ds_factor=2,
            empty_error_msg="指定区域无 DEM 数据",
        )
        if "error" in fetch_res:
            return fetch_res

        item = fetch_res["first_item"]
        dem = fetch_res["bands"]["dem"]
        cell_size = fetch_res["cell_size_m"]

        nodata = dem <= -9999
        dem[nodata] = np.nan

        result = {
            "status": "ok",
            "source": "Copernicus DEM GLO-30",
            "item_id": item.id,
            "cell_size_m": round(cell_size, 1),
            "bbox": bbox,
        }

        if "slope" in products:
            slope = compute_slope(dem, cell_size)
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
            aspect = compute_aspect(dem, cell_size)
            valid = aspect[~np.isnan(aspect)]
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
            hs = compute_hillshade(dem, cell_size)
            result["hillshade"] = {
                "stats": {
                    "min": round(float(np.nanmin(hs)), 2),
                    "max": round(float(np.nanmax(hs)), 2),
                    "mean": round(float(np.nanmean(hs)), 2),
                },
                "sun_azimuth": 315,
                "sun_altitude": 45,
            }

        result["elevation"] = {
            "stats": {
                "min": round(float(np.nanmin(dem)), 1),
                "max": round(float(np.nanmax(dem)), 1),
                "mean": round(float(np.nanmean(dem)), 1),
                "std": round(float(np.nanstd(dem)), 1),
            }
        }

        return result

    @staticmethod
    def _compute_slope(dem: "np.ndarray", cell_size: float) -> "np.ndarray":
        """Compute slope in degrees using Horn's method (3x3 window)."""
        return compute_slope(dem, cell_size)

    @staticmethod
    def _compute_aspect(dem: "np.ndarray", cell_size: float) -> "np.ndarray":
        """Compute aspect in degrees (0-360, clockwise from North)."""
        return compute_aspect(dem, cell_size)

    @staticmethod
    def _compute_hillshade(dem: "np.ndarray", cell_size: float,
                           azimuth: float = 315, altitude: float = 45) -> "np.ndarray":
        """Compute hillshade illumination (0-255)."""
        return compute_hillshade(dem, cell_size, azimuth, altitude)

    async def compute_vegetation_index(self, bbox: list, date_from: str, date_to: str,
                                        index_type: str = "ndvi") -> dict:
        """Compute vegetation/water indices from Sentinel-2 bands."""
        index_type = index_type.lower()
        if index_type not in INDEX_FORMULAS:
            return {"error": f"不支持的指数类型 '{index_type}'，可用: {list(INDEX_FORMULAS.keys())}"}

        stac_keys = {
            "blue": "blue",
            "green": "green",
            "red": "red",
            "nir": "nir",
            "swir11": "swir16",
            "swir12": "swir22",
        }
        bands_needed_keys, _ = INDEX_FORMULAS[index_type]
        bands_needed = {b: stac_keys[b] for b in bands_needed_keys}

        fetch_res = await self._fetch_stac_items_and_bands(
            collection="sentinel-2-l2a",
            bbox=bbox,
            date_from=date_from,
            date_to=date_to,
            bands_needed=bands_needed,
            ds_factor=4,
            empty_error_msg="指定区域和时间范围无 Sentinel-2 数据",
        )
        if "error" in fetch_res:
            return fetch_res

        item = fetch_res["first_item"]
        index_vals = compute_index_array(index_type, **fetch_res["bands"])
        index_name = index_type.upper()

        _CLASSIFIERS = {
            "ndvi": lambda vals: {"vegetation_coverage_pct": round(float((vals > 0.3).sum() / vals.size * 100), 1)},
            "ndwi": lambda vals: {"water_coverage_pct": round(float((vals > 0).sum() / vals.size * 100), 1)},
            "nbr": lambda vals: {
                "burn_severity": {
                    "unburned": round(float((vals > 0.1).sum() / vals.size * 100), 1),
                    "low_severity": round(float(((vals >= -0.1) & (vals <= 0.1)).sum() / vals.size * 100), 1),
                    "moderate_severity": round(float(((vals >= -0.27) & (vals < -0.1)).sum() / vals.size * 100), 1),
                    "high_severity": round(float((vals < -0.27).sum() / vals.size * 100), 1),
                }
            },
        }
        classify = _CLASSIFIERS.get(index_type)
        classification = classify(index_vals) if classify else {}

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


rs_service = RemoteSensingService()
