"""SpectralRasterEngine - 核心遥感波段分析与地形表面引擎。

深入封装 STAC COG 波段检索、带云掩膜的向量化波段代数、Horn 方法地形推导、
以及与 MapSpec type:"raster" 图层的直接对接。
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional
import numpy as np

from app.services.rs.band_math import (
    RasterAnalysisResult,
    INDEX_FORMULAS,
    compute_index_array,
    compute_slope,
    compute_aspect,
    compute_hillshade,
    compute_raster_stats,
)
from app.services.rs.stac_client import stac_primitive

logger = logging.getLogger(__name__)


class SpectralRasterEngine:
    """深层遥感与栅格分析引擎"""

    def __init__(self):
        self.stac = stac_primitive

    async def compute_index(
        self,
        bbox: List[float],
        date_from: str,
        date_to: str,
        index_type: str = "ndvi",
    ) -> RasterAnalysisResult:
        """计算遥感光谱指数 (NDVI, NDWI, EVI, NBR)"""
        idx = index_type.lower()
        if idx not in INDEX_FORMULAS:
            return RasterAnalysisResult(
                index_type=idx,
                is_error=True,
                error_msg=f"不支持的指数类型 '{index_type}'",
                correction_hint=f"可用指数类型: {list(INDEX_FORMULAS.keys())}",
            )

        stac_keys = {
            "blue": "blue",
            "green": "green",
            "red": "red",
            "nir": "nir",
            "swir11": "swir16",
            "swir12": "swir22",
        }
        bands_needed_keys, _ = INDEX_FORMULAS[idx]
        bands_needed = {b: stac_keys[b] for b in bands_needed_keys}

        fetch_res = await self.stac.fetch_stac_items_and_bands(
            collection="sentinel-2-l2a",
            bbox=bbox,
            date_from=date_from,
            date_to=date_to,
            bands_needed=bands_needed,
            ds_factor=4,
            empty_error_msg=f"指定区域和时间段 [{date_from} ~ {date_to}] 无 Sentinel-2 数据",
        )

        if "error" in fetch_res or not fetch_res.get("bands"):
            return RasterAnalysisResult(
                index_type=idx,
                is_error=True,
                error_msg=fetch_res.get("error", "获取 Sentinel-2 波段失败"),
                correction_hint="请放宽日期窗口 (如 1~3 个月) 或扩大部分边界框以容忍有云覆盖。",
            )

        try:
            # Band algebra on full band arrays is CPU-bound numpy — offload so
            # multi-million-pixel index math can't block the event loop.
            def _compute_index():
                arr = compute_index_array(idx, **fetch_res["bands"])
                stats = compute_raster_stats(arr)
                return arr, stats

            arr, stats = await asyncio.to_thread(_compute_index)

            # Continuous color ramp specification for live UI map overlay.
            # TODO: legend_spec is computed but not yet attached to the returned
            # RasterAnalysisResult below — wire it through in a follow-up.
            legend_spec = {  # noqa: F841 — kept so the intended overlay metadata is visible.
                "type": "continuous",
                "palette": "Viridis",
                "min": stats.get("min"),
                "max": stats.get("max"),
                "unit": idx.upper(),
            }

            return RasterAnalysisResult(
                index_type=idx,
                array=arr,
                # #381: stac_client 返回实际读取窗口的 WGS84 范围 (bbox ∩
                # 影像足迹)，而不是用户请求的整个 bbox —— 统计与栅格叠加
                # 必须配准到真实数据 footprint。
                bounds=fetch_res.get("bounds") or list(bbox),
                stats=stats,
                is_error=False,
            )
        except Exception as e:
            logger.error(f"Failed to compute raster index {index_type}: {e}")
            return RasterAnalysisResult(
                index_type=idx,
                is_error=True,
                error_msg=f"光谱指数计算失败: {e}",
            )

    async def compute_terrain(
        self,
        bbox: List[float],
        products: Optional[List[str]] = None,
        dem_type: str = "cop-dem-glo-30",
    ) -> RasterAnalysisResult:
        """从数字高程模型 (DEM) 计算坡度、坡向、山体阴影"""
        if products is None:
            products = ["slope", "aspect", "hillshade"]

        fetch_res = await self.stac.fetch_stac_items_and_bands(
            collection=dem_type,
            bbox=bbox,
            bands_needed={"dem": "data"},
            ds_factor=2,
            empty_error_msg=f"指定区域 bbox={bbox} 无 {dem_type} 高程数据",
        )

        if "error" in fetch_res or not fetch_res.get("bands"):
            return RasterAnalysisResult(
                index_type="dem",
                is_error=True,
                error_msg=fetch_res.get("error", "获取 DEM 数据失败"),
                correction_hint="请检查 bbox 边界框坐标是否合法（WGS84 经纬度）。",
            )

        try:
            dem = fetch_res["bands"]["dem"]

            # Nodata masking + Horn-window derivatives are CPU-bound numpy —
            # offload so multi-million-pixel terrain math can't block the loop.
            def _compute_terrain():
                nodata = dem <= -9999
                dem[nodata] = np.nan
                cell_size = fetch_res.get("cell_size_m", 30.0)
                cell_size_x = fetch_res.get("cell_size_x_m")

                # #444: the tool contract promises EVERY requested product
                # ("products 可选 slope/aspect/hillshade，默认全部"、"一步生成
                # 多产品"). The GIS-06 interim fix (after the old if/elif chain
                # returned one mislabeled product) returned only the FIRST
                # supported product, silently dropping the rest. All
                # derivatives share one masked-DEM gradient basis, so each is a
                # single vectorized pass — compute the full requested set.
                # `array`/`stats`/`index_type` stay the PRIMARY (first
                # requested) product for backwards compatibility with
                # emit_raster_layer and single-product consumers.
                derivators = {
                    "slope": lambda d: compute_slope(d, cell_size, cell_size_x=cell_size_x),
                    "aspect": lambda d: compute_aspect(d, cell_size, cell_size_x=cell_size_x),
                    "hillshade": lambda d: compute_hillshade(d, cell_size, cell_size_x=cell_size_x),
                }
                requested = list(dict.fromkeys(p for p in products if p in derivators))
                product_arrays: Dict[str, np.ndarray] = {}
                product_stats: Dict[str, Dict[str, Any]] = {}
                for p in requested:
                    arr = derivators[p](dem)
                    product_arrays[p] = arr
                    # #618-17: aspect is circular (0-360°); slope/hillshade stay linear.
                    product_stats[p] = compute_raster_stats(arr, circular=(p == "aspect"))

                if requested:
                    label = requested[0]
                    target_arr = product_arrays[label]
                    # Stats must describe the returned array, not the raw DEM.
                    stats = compute_raster_stats(target_arr, circular=(label == "aspect"))
                    stats["terrain_product"] = label
                    stats["products"] = list(requested)
                    stats["terrain_products"] = product_stats
                    unsupported = [p for p in products if p not in derivators]
                    if unsupported:
                        stats["unsupported_products"] = unsupported
                else:
                    target_arr = dem
                    label = "dem"
                    stats = compute_raster_stats(target_arr)
                    stats.setdefault("terrain_product", label)

                return target_arr, label, stats, product_arrays

            target_arr, label, stats, product_arrays = await asyncio.to_thread(_compute_terrain)

            return RasterAnalysisResult(
                index_type=label,
                array=target_arr,
                # #381: 实际读取窗口 (bbox ∩ DEM 足迹) 的 WGS84 范围，
                # 而非整个请求 bbox。
                bounds=fetch_res.get("bounds") or list(bbox),
                stats=stats,
                is_error=False,
                product_arrays=product_arrays,
            )
        except Exception as e:
            logger.error(f"Failed to compute terrain derivatives: {e}")
            return RasterAnalysisResult(
                index_type="dem",
                is_error=True,
                error_msg=f"地形推导计算失败: {e}",
            )

    async def emit_raster_layer(self, session_id: str, result: RasterAnalysisResult) -> Dict[str, Any]:
        """将 RasterAnalysisResult 转化为 MapSpec type:'raster' 图层并写入 Persistence"""
        if result.is_error or result.array is None or result.bounds is None:
            return {"error": result.error_msg or "无效的栅格数据，无法挂载图层"}

        try:
            from app.services.raster_cartography_converter import render_array_to_png
            from app.services.mapspec_store import mapspec_store

            # Render array into colormap-baked PNG bytes
            png_bytes = render_array_to_png(result.array)
            raster_id = f"{result.index_type}_{session_id[:8]}"

            source_data = {
                "type": "raster",
                "raster_source": {
                    "png_bytes": png_bytes,
                    "bounds": result.bounds,
                    "image_size": [result.array.shape[1], result.array.shape[0]],
                }
            }

            # Store PNG and register raster MapSpec source
            source_ref = mapspec_store.upsert_source(session_id, raster_id, source_data)

            return {
                "status": "ok",
                "session_id": session_id,
                "raster_id": raster_id,
                "source_ref": source_ref,
                "bounds": result.bounds,
                "stats": result.stats,
            }
        except Exception as e:
            logger.error(f"Failed to emit raster layer for session {session_id}: {e}")
            return {"error": f"挂载栅格图层失败: {e}"}

    async def fetch_sentinel_thumbnail(
        self,
        bbox: list,
        date_from: str,
        date_to: str,
        bands: str = "true-color",
        width: int = 512,
        height: int = 512,
    ) -> dict:
        from app.core.config import settings
        if not settings.SENTINELHUB_CLIENT_ID:
            return await self._fetch_sentinel_public(bbox, date_from, date_to)
        return {"status": "configured", "message": "Sentinel Hub API 已配置，待实现具体调用"}

    async def _fetch_sentinel_public(self, bbox: list, date_from: str, date_to: str) -> dict:
        from app.tools._utils import asset_href
        fetch_res = await self.stac.fetch_stac_items_and_bands(
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
        res = await self.compute_index(bbox, date_from, date_to, index_type="ndvi")
        if res.is_error:
            return {"error": res.error_msg}

        # #442: coverage = vegetation pixels / *valid* pixels. NaN is the NDVI
        # nodata convention (denominator <= 0 → NaN, band_math INDEX_FORMULAS),
        # so it must be excluded from the denominator — else coverage is
        # systematically understated by the nodata fraction. Mirrors the
        # ~np.isnan masking in compute_raster_stats.
        vegetation_coverage = 0.0
        if res.array is not None:
            valid_pixels = int(np.isfinite(res.array).sum())
            if valid_pixels > 0:
                veg_pixels = int((res.array > 0.3).sum())  # NaN > 0.3 is False
                vegetation_coverage = round(veg_pixels / valid_pixels * 100, 1)

        return {
            "status": "ok",
            "bbox": bbox,
            "ndvi_stats": res.stats,
            "vegetation_coverage": vegetation_coverage,
            "raster_source": {
                "array": res.array,
                "bounds": res.bounds,
                "suggested_palette": "Viridis",
            },
        }

    async def fetch_dem(self, bbox: list) -> dict:
        from app.tools._utils import asset_href
        fetch_res = await self.stac.fetch_stac_items_and_bands(
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

    async def compute_vegetation_index(
        self,
        bbox: list,
        date_from: str,
        date_to: str,
        index_type: str = "ndvi"
    ) -> dict:
        res = await self.compute_index(bbox, date_from, date_to, index_type=index_type)
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


spectral_engine = SpectralRasterEngine()
RemoteSensingService = SpectralRasterEngine

