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
        """从 STAC Catalog 检索 item 并按需要读取波段 NumPy 数组

        读取波段时按 bbox ∩ 影像足迹做窗口化读取 (crop)，返回结果附带
        "bounds": 实际读取窗口的 WGS84 范围 [w, s, e, n] (非请求 bbox，
        统计与栅格叠加配准到真实数据 footprint)。"""
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
                    import math
                    import rasterio
                    from rasterio.crs import CRS
                    from rasterio.enums import Resampling
                    from rasterio.warp import transform_bounds
                    from rasterio.windows import Window, from_bounds
                    from app.lib.geo_analysis.raster_math import rasterio_env

                    bands_dict: Dict[str, np.ndarray] = {}
                    cell_size_m = None
                    cell_size_x_m = None
                    data_bounds = None  # 实际读取窗口的地理范围 [w,s,e,n] (WGS84)
                    first_crs = None

                    def _crop_window(ds):
                        """请求 bbox ∩ 影像足迹 -> 像素窗口 (在影像 CRS 中)。

                        #381: 旧实现整景读取 (仅 out_shape 抽稀)，却把请求 AOI
                        的 bbox 贴到整 tile 数组上 —— 统计描述整个景幅、渲染的
                        栅格叠加被挤压/位移到错误 footprint。bbox 是 WGS84，
                        影像可能是投影 CRS (Sentinel-2 L2A 为 UTM)，先把 bbox
                        变换到影像 CRS 求交，再 snap 到像素网格并裁剪到景幅。
                        """
                        xmin, ymin, xmax, ymax = bbox
                        if ds.crs is not None:
                            try:
                                xmin, ymin, xmax, ymax = transform_bounds(
                                    CRS.from_epsg(4326), ds.crs,
                                    bbox[0], bbox[1], bbox[2], bbox[3],
                                    densify_pts=21,
                                )
                            except Exception:
                                logger.warning(
                                    f"无法将 bbox 变换到影像 CRS {ds.crs}，按同 CRS 处理")
                        left = max(xmin, ds.bounds.left)
                        bottom = max(ymin, ds.bounds.bottom)
                        right = min(xmax, ds.bounds.right)
                        top = min(ymax, ds.bounds.top)
                        if left >= right or bottom >= top:
                            raise ValueError(
                                f"bbox {bbox} 与影像 {item.id} 足迹无重叠，无法裁剪")
                        win = from_bounds(left, bottom, right, top, ds.transform)
                        # offsets 向下取整、长度向上取整：保证窗口覆盖 bbox。
                        # 加浮点容差 (亚像元) 避免 (x - origin)/px 的二进制舍入
                        # 噪声把恰好落在像素边界的 bbox 扩出 1 像素。
                        snap_eps = 1e-9
                        win = Window(math.floor(win.col_off + snap_eps),
                                     math.floor(win.row_off + snap_eps),
                                     math.ceil(win.width - snap_eps),
                                     math.ceil(win.height - snap_eps))
                        win = win.intersection(Window(0, 0, ds.width, ds.height))
                        if win.width <= 0 or win.height <= 0:
                            raise ValueError(
                                f"bbox {bbox} 与影像 {item.id} 无重叠像素，无法裁剪")
                        return win

                    with rasterio_env():
                        for name, asset_key in band_mapping.items():
                            if asset_key not in item.assets:
                                logger.warning(f"Asset '{asset_key}' not found in STAC item {item.id}")
                                continue
                            href = item.assets[asset_key].href
                            with rasterio.open(href) as ds:
                                if ds.crs is not None:
                                    crs_s = ds.crs.to_string()
                                    if first_crs is None:
                                        first_crs = crs_s
                                    elif crs_s != first_crs:
                                        # 同景各波段应同 CRS；不同时按各自窗口读取
                                        logger.warning(
                                            f"Band '{name}' CRS {crs_s} 与首波段 "
                                            f"{first_crs} 不同，按各自窗口读取")
                                win = _crop_window(ds)
                                out_h = max(1, int(win.height) // ds_factor)
                                out_w = max(1, int(win.width) // ds_factor)
                                data = ds.read(
                                    1,
                                    window=win,
                                    out_shape=(out_h, out_w),
                                    resampling=Resampling.bilinear,
                                ).astype(float)
                                bands_dict[name] = data
                                if data_bounds is None:
                                    # 输出网格足迹 = 窗口 transform 按
                                    # out_shape 比例缩放后的外边界 (非请求 bbox)。
                                    wt = ds.window_transform(win)
                                    raw_bounds = [
                                        wt.c, wt.f + wt.e * win.height,
                                        wt.c + wt.a * win.width, wt.f,
                                    ]
                                    if ds.crs is not None and not ds.crs.is_geographic:
                                        raw_bounds = transform_bounds(
                                            ds.crs, CRS.from_epsg(4326),
                                            *raw_bounds, densify_pts=21)
                                    data_bounds = [float(v) for v in raw_bounds]
                                # Effective pixel size after windowed
                                # downsampling, in metres (R-F02): terrain
                                # derivatives (compute_slope/hillshade) need
                                # the *actual* pixel size of the array they
                                # receive (window width / out_w, not ds_factor
                                # outright, so flooring of out_shape stays
                                # exact). Geographic DEMs report pixel size in
                                # degrees -> convert at the equator rate
                                # (~111320 m/deg, matching the Copernicus
                                # GLO-30 "30 m" label convention).
                                if cell_size_m is None and ds.transform.a:
                                    px_y = abs(ds.transform.e) * (win.height / out_h)
                                    px_x = abs(ds.transform.a) * (win.width / out_w)
                                    if ds.crs is not None and ds.crs.is_geographic:
                                        # #379: 地理 CRS 下经度向地面尺寸随
                                        # cos(lat) 收缩 —— 赤道比例会把东西向
                                        # 坡度低估 ~cos(lat)；用读取窗口中心
                                        # 纬度校正 x 方向，y (经线) 不受影响。
                                        # 投影 CRS 两方向同为米，不设置
                                        # cell_size_x_m (派生函数回退 cell_size)。
                                        lat = (data_bounds[1] + data_bounds[3]) / 2.0
                                        px_y *= 111320.0
                                        px_x *= 111320.0 * abs(np.cos(np.radians(lat)))
                                        cell_size_x_m = float(px_x)
                                    cell_size_m = float(px_y)
                    return bands_dict, cell_size_m, cell_size_x_m, data_bounds

            bands_dict, cell_size_m, cell_size_x_m, data_bounds = await asyncio.to_thread(_read_bands)
            result["bands"] = bands_dict
            if cell_size_m is not None:
                result["cell_size_m"] = cell_size_m
            if cell_size_x_m is not None:
                result["cell_size_x_m"] = cell_size_x_m
            if data_bounds is not None:
                result["bounds"] = data_bounds

            return result
        except Exception as e:
            logger.error(f"STAC fetch error for collection={collection} bbox={bbox}: {e}")
            return {"error": str(e), "items": []}


stac_primitive = StacClientPrimitive()
