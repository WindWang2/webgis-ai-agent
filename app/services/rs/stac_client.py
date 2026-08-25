"""STAC Client 异步数据拉取与 COG 栅格 IO 原语

包装 pystac_client 与 rasterio_env()，负责从 STAC Catalog
搜索 Sentinel-2 / Landsat 影像并读取对应波段阵列。
"""
import asyncio
import logging
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple, Union
import numpy as np

logger = logging.getLogger(__name__)

_STAC_CATALOG_URL = "https://earth-search.aws.element84.com/v1"

# DEM elevation sentinels used by spectral_engine.compute_terrain's nodata
# mask; legitimate elevations never reach this depth (Dead Sea shore ≈ −430 m).
DEM_SENTINEL_NODATA = -9999.0


def _nan_block_mean(src: np.ndarray, out_h: int, out_w: int) -> np.ndarray:
    """Average-resample ``src`` to (out_h, out_w), excluding NaN pixels (#1002).

    Pure-numpy stand-in for GDAL's nodata-aware ``average`` resampling on
    datasets that declare no nodata: each destination pixel is the mean of its
    source block's valid (non-NaN) pixels. Blocks are contiguous index ranges
    (sizes differ by at most 1 when the ratio is not integer, so every source
    row/column is used); blocks with no valid pixel stay NaN for the
    downstream mask.
    """
    h, w = src.shape
    out_h = min(out_h, h)
    out_w = min(out_w, w)
    row_starts = np.arange(out_h) * h // out_h
    col_starts = np.arange(out_w) * w // out_w
    valid = np.isfinite(src)
    filled = np.where(valid, src, 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        sums = np.add.reduceat(
            np.add.reduceat(filled, row_starts, axis=0), col_starts, axis=1)
        counts = np.add.reduceat(
            np.add.reduceat(valid.astype(np.int64), row_starts, axis=0),
            col_starts, axis=1,
        )
        return sums / counts


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
        mask_sentinel_nodata: bool = False,
    ) -> Dict[str, Any]:
        """从 STAC Catalog 检索 item 并按需要读取波段 NumPy 数组

        读取波段时按 bbox ∩ 影像足迹做窗口化读取 (crop)，返回结果附带
        "bounds": 实际读取窗口的 WGS84 范围 [w, s, e, n] (非请求 bbox，
        统计与栅格叠加配准到真实数据 footprint)。

        mask_sentinel_nodata: 未声明 nodata 的数据集 (如部分 DEM) 在降采样前
        先做 <=DEM_SENTINEL_NODATA→NaN 掩膜再 average 重采样 (#1002)。仅
        DEM/单波段高程路径应开启 —— 反射率波段不存在该哨兵约定。"""
        try:
            catalog = await self.open_catalog()
            datetime_param = f"{date_from}/{date_to}" if date_from and date_to else None

            def _cloud_cover(item) -> float:
                props = getattr(item, "properties", None) or {}
                cc = props.get("eo:cloud_cover") if hasattr(props, "get") else None
                try:
                    return float(cc) if cc is not None else float("inf")
                except (TypeError, ValueError):
                    return float("inf")

            def _do_search():
                # #618-18: ask the catalog for the least-cloudy scene; fall
                # back to a local sort when the client rejects sortby.
                kwargs: Dict[str, Any] = {
                    "collections": [collection],
                    "bbox": bbox,
                    "datetime": datetime_param,
                    "max_items": max_items,
                    "sortby": [{"field": "properties.eo:cloud_cover", "direction": "asc"}],
                }
                try:
                    search = catalog.search(**kwargs)
                except TypeError:
                    kwargs.pop("sortby", None)
                    search = catalog.search(**kwargs)
                found = list(search.items())
                found.sort(key=_cloud_cover)
                return found

            items = await asyncio.to_thread(_do_search)
            if not items:
                return {"error": empty_error_msg, "items": []}

            cc = _cloud_cover(items[0])
            result: Dict[str, Any] = {
                "items": items,
                "first_item": items[0],
                "bands": {},
                "cloud_cover": None if cc == float("inf") else cc,
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
                        # #577: 单次计算的多波段必须落在统一的目标网格上 —— NBR
                        # 混合 10m B08 与 20m B12,若逐波段按各自窗口推导
                        # out_shape,两数组形状不同 (或形状偶合但网格错位),波段
                        # 代数必然失败。第一遍开全部波段求裁剪窗口与原生像元大小,
                        # 以最高分辨率波段为参考网格;第二遍所有波段读取时统一
                        # 重采样到该网格。
                        opened: List[Tuple[str, Any]] = []
                        try:
                            band_windows: Dict[str, Any] = {}
                            ref_name: Optional[str] = None
                            ref_px_area: Optional[float] = None
                            for name, asset_key in band_mapping.items():
                                if asset_key not in item.assets:
                                    logger.warning(f"Asset '{asset_key}' not found in STAC item {item.id}")
                                    continue
                                href = item.assets[asset_key].href
                                ds = rasterio.open(href)
                                opened.append((name, ds))
                                if ds.crs is not None:
                                    crs_s = ds.crs.to_string()
                                    if first_crs is None:
                                        first_crs = crs_s
                                    elif crs_s != first_crs:
                                        # 同景各波段应同 CRS;不同时按各自窗口读取
                                        logger.warning(
                                            f"Band '{name}' CRS {crs_s} 与首波段 "
                                            f"{first_crs} 不同，按各自窗口读取")
                                win = _crop_window(ds)
                                band_windows[name] = win
                                # 参考网格 = 原生像元面积最小的 (最高分辨率) 波段;
                                # 缺 transform 元数据时退回首波段。
                                if ds.transform.a:
                                    px_area = abs(ds.transform.a * ds.transform.e)
                                    if ref_px_area is None or px_area < ref_px_area:
                                        ref_name, ref_px_area = name, px_area
                                elif ref_name is None:
                                    ref_name = name

                            if ref_name is not None:
                                ref_win = band_windows[ref_name]
                                out_h = max(1, int(ref_win.height) // ds_factor)
                                out_w = max(1, int(ref_win.width) // ds_factor)
                                for name, ds in opened:
                                    win = band_windows[name]
                                    # #578: nodata 哨兵必须在重采样之前剔除 ——
                                    # bilinear 会把 -9999 与相邻有效高程平均成
                                    # "看似合法" 的中间值,重采样后才做 <= 哨兵掩码
                                    # 拦不住 (如 -1599.75 通过 -9999 判定)。数据集
                                    # 声明 nodata 时改用 GDAL nodata-aware 的
                                    # average 重采样 (只对有效像元加权平均;整窗全
                                    # nodata 时输出哨兵值供下游掩膜);未声明且调用
                                    # 方开启 mask_sentinel_nodata (DEM 路径, #1002)
                                    # 时读取原生窗口先 <= 哨兵→NaN 掩膜,再用
                                    # NaN 感知的块均值 average 降采样;其余退回
                                    # bilinear 保持原行为。
                                    if ds.nodata is not None:
                                        data = ds.read(
                                            1,
                                            window=win,
                                            out_shape=(out_h, out_w),
                                            resampling=Resampling.average,
                                        ).astype(float)
                                    elif mask_sentinel_nodata:
                                        full = ds.read(1, window=win).astype(float)
                                        full[full <= DEM_SENTINEL_NODATA] = np.nan
                                        data = _nan_block_mean(full, out_h, out_w)
                                    else:
                                        data = ds.read(
                                            1,
                                            window=win,
                                            out_shape=(out_h, out_w),
                                            resampling=Resampling.bilinear,
                                        ).astype(float)
                                    bands_dict[name] = data
                                    if name == ref_name:
                                        # 输出网格足迹 = 参考窗口 transform 按
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
                                        if ds.transform.a:
                                            px_y = abs(ds.transform.e) * (win.height / out_h)
                                            px_x = abs(ds.transform.a) * (win.width / out_w)
                                            if ds.crs is not None and ds.crs.is_geographic:
                                                # #379: 地理 CRS 下经度向地面尺寸随
                                                # cos(lat) 收缩 —— 赤道比例会把东西向
                                                # 坡度低估 ~cos(lat);用读取窗口中心
                                                # 纬度校正 x 方向,y (经线) 不受影响。
                                                # 投影 CRS 两方向同为米,不设置
                                                # cell_size_x_m (派生函数回退 cell_size)。
                                                lat = (data_bounds[1] + data_bounds[3]) / 2.0
                                                px_y *= 111320.0
                                                px_x *= 111320.0 * abs(np.cos(np.radians(lat)))
                                                cell_size_x_m = float(px_x)
                                            cell_size_m = float(px_y)
                        finally:
                            for _, ds in opened:
                                ds.close()
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
