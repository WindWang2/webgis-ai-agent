"""STAC Client 异步数据拉取与 COG 栅格 IO 原语

包装 pystac_client 与 rasterio_env()，负责从 STAC Catalog
搜索 Sentinel-2 / Landsat 影像并读取对应波段阵列。
"""
import asyncio
import logging
from functools import lru_cache
from typing import Any, Dict, List, Optional, Union
import numpy as np

logger = logging.getLogger(__name__)

_STAC_CATALOG_URL = "https://earth-search.aws.element84.com/v1"


class StacClientPrimitive:
    """STAC 检索与波段读取服务"""

    @lru_cache(maxsize=1)
    def _get_catalog(self):
        import pystac_client
        return pystac_client.Client.open(_STAC_CATALOG_URL)

    async def open_catalog(self):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._get_catalog)

    async def fetch_stac_items_and_bands(
        self,
        collection: str,
        bbox: List[float],
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        max_items: int = 1,
        bands_needed: Optional[Union[Dict[str, str], List[str]]] = None,
        ds_factor: int = 4,
        empty_error_msg: str = "No data found",
    ) -> Dict[str, Any]:
        """从 STAC Catalog 检索 item 并按需要读取波段 NumPy 数组"""
        try:
            catalog = await self.open_catalog()
            datetime_param = f"{date_from}/{date_to}" if date_from and date_to else None

            def _do_search():
                search = catalog.search(
                    collections=[collection],
                    bbox=bbox,
                    datetime=datetime_param,
                    max_items=max_items,
                )
                return list(search.items())

            items = await asyncio.to_thread(_do_search)
            if not items:
                return {"error": empty_error_msg, "items": []}

            result: Dict[str, Any] = {
                "items": items,
                "first_item": items[0],
                "bands": {},
            }

            if bands_needed:
                item = items[0]

                if isinstance(bands_needed, list):
                    band_mapping = {b: b for b in bands_needed}
                else:
                    band_mapping = bands_needed

                def _read_bands():
                    import rasterio
                    from rasterio.enums import Resampling
                    from app.lib.geo_analysis.raster_math import rasterio_env

                    bands_dict: Dict[str, np.ndarray] = {}
                    cell_size_m = None
                    cell_size_x_m = None
                    with rasterio_env():
                        for name, asset_key in band_mapping.items():
                            if asset_key not in item.assets:
                                logger.warning(f"Asset '{asset_key}' not found in STAC item {item.id}")
                                continue
                            href = item.assets[asset_key].href
                            with rasterio.open(href) as ds:
                                out_h = max(1, ds.height // ds_factor)
                                out_w = max(1, ds.width // ds_factor)
                                data = ds.read(
                                    1,
                                    out_shape=(out_h, out_w),
                                    resampling=Resampling.bilinear,
                                ).astype(float)
                                bands_dict[name] = data
                                # Effective pixel size after downsampling, in
                                # metres (R-F02): terrain derivatives
                                # (compute_slope/hillshade) need the *actual*
                                # pixel size of the array they receive. The
                                # previous code always passed 30 m even though
                                # the DEM is read at ds_factor=2 (~60 m),
                                # overstating slopes ~2x. Geographic DEMs report
                                # pixel size in degrees -> convert at the
                                # equator rate (~111320 m/deg, matching the
                                # Copernicus GLO-30 "30 m" label convention).
                                if cell_size_m is None and ds.transform.a:
                                    px = abs(ds.transform.a) * ds_factor
                                    if ds.crs is not None and ds.crs.is_geographic:
                                        # #379: 地理 CRS 下经度向的地面尺寸随
                                        # cos(lat) 收缩 —— 赤道比例 (~111320
                                        # m/deg) 会把东西向坡度低估 ~cos(lat)。
                                        # 用 AOI 中心纬度校正 x 方向；y (经线)
                                        # 方向不受影响。投影 CRS 两方向同为米，
                                        # 不设置 cell_size_x_m (派生函数回退
                                        # cell_size，行为不变)。
                                        lat = (bbox[1] + bbox[3]) / 2.0
                                        px *= 111320.0
                                        cell_size_x_m = float(
                                            px * abs(np.cos(np.radians(lat))))
                                    cell_size_m = float(px)
                    return bands_dict, cell_size_m, cell_size_x_m

            bands_dict, cell_size_m, cell_size_x_m = await asyncio.to_thread(_read_bands)
            result["bands"] = bands_dict
            if cell_size_m is not None:
                result["cell_size_m"] = cell_size_m
            if cell_size_x_m is not None:
                result["cell_size_x_m"] = cell_size_x_m

            return result
        except Exception as e:
            logger.error(f"STAC fetch error for collection={collection} bbox={bbox}: {e}")
            return {"error": str(e), "items": []}


stac_primitive = StacClientPrimitive()
