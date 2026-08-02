"""遥感数据服务 - RemoteSensingService Adapter (向后兼容).

内部委托给 deep domain engine `SpectralRasterEngine` (app.services.rs)。
保留所有旧的导出的函数名与结构，保持外部 import 完全兼容。
"""
import asyncio
import logging
from functools import lru_cache
from typing import Optional

from app.core.config import settings
from app.tools._utils import asset_href

# ─── 从 app.services.rs 子包 re-export 核心纯函数 ───────────
from app.services.rs.band_math import (
    INDEX_FORMULAS,
    compute_index_array,
    compute_slope,
    compute_aspect,
    compute_hillshade,
)
from app.services.rs.spectral_engine import spectral_engine, SpectralRasterEngine

logger = logging.getLogger(__name__)

_STAC_CATALOG_URL = "https://earth-search.aws.element84.com/v1"


class RemoteSensingService:
    """遥感数据服务 Adapter"""

    def __init__(self):
        self.engine = spectral_engine

    @lru_cache(maxsize=1)
    def _get_catalog(self):
        import pystac_client
        return pystac_client.Client.open(_STAC_CATALOG_URL)

    async def _open_catalog(self):
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
        return await self.engine.stac.fetch_stac_items_and_bands(
            collection=collection,
            bbox=bbox,
            date_from=date_from,
            date_to=date_to,
            max_items=max_items,
            bands_needed=bands_needed,
            ds_factor=ds_factor,
            empty_error_msg=empty_error_msg,
        )

    async def fetch_sentinel_thumbnail(
        self,
        bbox: list,
        date_from: str,
        date_to: str,
        bands: str = "true-color",
        width: int = 512,
        height: int = 512,
    ) -> dict:
        if not settings.SENTINELHUB_CLIENT_ID:
            return await self._fetch_sentinel_public(bbox, date_from, date_to)
        return {"status": "configured", "message": "Sentinel Hub API 已配置，待实现具体调用"}

    async def _fetch_sentinel_public(self, bbox: list, date_from: str, date_to: str) -> dict:
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
        res = await self.engine.compute_index(bbox, date_from, date_to, index_type="ndvi")
        if res.is_error:
            return {"error": res.error_msg}
        return {
            "status": "ok",
            "bbox": bbox,
            "ndvi_stats": res.stats,
            "vegetation_coverage": round(float((res.array > 0.3).sum() / res.array.size * 100), 1) if res.array is not None else 0.0,
            "raster_source": {
                "array": res.array,
                "bounds": res.bounds,
                "suggested_palette": "Viridis",
            },
        }

    async def fetch_dem(self, bbox: list) -> dict:
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
        res = await self.engine.compute_terrain(bbox, products=products)
        if res.is_error:
            return {"error": res.error_msg}
        return {
            "status": "ok",
            "source": "Copernicus DEM GLO-30",
            "bbox": bbox,
            "elevation": {"stats": res.stats},
        }

    @staticmethod
    def _compute_slope(dem: "np.ndarray", cell_size: float) -> "np.ndarray":
        return compute_slope(dem, cell_size)

    @staticmethod
    def _compute_aspect(dem: "np.ndarray", cell_size: float) -> "np.ndarray":
        return compute_aspect(dem, cell_size)

    @staticmethod
    def _compute_hillshade(dem: "np.ndarray", cell_size: float,
                           azimuth: float = 315, altitude: float = 45) -> "np.ndarray":
        return compute_hillshade(dem, cell_size, azimuth, altitude)

    async def compute_vegetation_index(self, bbox: list, date_from: str, date_to: str,
                                        index_type: str = "ndvi") -> dict:
        res = await self.engine.compute_index(bbox, date_from, date_to, index_type=index_type)
        if res.is_error:
            return {"error": res.error_msg}
        return {
            "status": "ok",
            "index_type": index_type.upper(),
            "stats": res.stats,
            "bbox": bbox,
            "raster_source": {
                "array": res.array,
                "bounds": res.bounds,
                "suggested_palette": "Viridis",
            },
        }


rs_service = RemoteSensingService()
