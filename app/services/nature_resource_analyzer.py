"""
遥感影像分析服务 - 自然资源监测
支持 NDVI, NDWI, NBR, EVI 等通用指数计算及波段自动适配

Runtime V3（ADR-0089）：计算下沉到共享窗口化执行底座
（``app.lib.geo_analysis.raster_windowed.windowed_band_index``）——内存
O(window)（预算推导窗口），不再整幅 ``src.read()``；NDVI 公式 truth 仍是
``app.services.rs.band_math.INDEX_FORMULAS``（零分母 → NaN，绝不伪装成 0）。
对外 API 契约不变：失败返回 ``{"success": False, "error": ...}``。
"""
import os
import logging
import time
import uuid
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# ==================== 卫星波段预设字典 (Satellite Band Presets) ====================
# 说明: 1-based index (对应 rasterio/gdal 波段顺序)
SATELLITE_PRESETS = {
    "sentinel-2": {
        "red": 4,      # B4: Red
        "nir": 8,      # B8: NIR (10m)
        "blue": 2,     # B2: Blue
        "green": 3,    # B3: Green
        "swir1": 11,   # B11: SWIR
    },
    "landsat-8-9": {
        "red": 4,      # B4: Red
        "nir": 5,      # B5: NIR
        "blue": 2,     # B2: Blue
        "green": 3,    # B3: Green
        "swir1": 6,    # B6
    },
    "generic-rgb-nir": {
        "red": 1,
        "green": 2,
        "blue": 3,
        "nir": 4,
    }
}

class NatureResourceAnalyzer:
    """自然资源遥感分析器"""

    @staticmethod
    def auto_detect_bands(src) -> Dict[str, int]:
        """
        根据影像特征智能猜测波段映射 (Smart Guess Logic)
        """
        count = src.count
        logger.info(f"[NatureResourceAnalyzer] Detecting bands for {count} bands image")

        # 常见 4 波段影像 (高分/多光谱) -> RGB + NIR
        if count == 4:
            return {"red": 1, "nir": 4, "green": 2, "blue": 3, "source": "guess-4band-rgbn"}

        # 常见 3 波段 -> RGB (无法进行 NDVI)
        if count == 3:
            return {"red": 1, "green": 2, "blue": 3, "source": "guess-3band-rgb"}

        # 哨兵/陆地卫星通常波段较多，默认尝试匹配常用索引
        if count >= 11:
            return {**SATELLITE_PRESETS["sentinel-2"], "source": "preset-sentinel2"}

        return {"source": "unknown"}

    @classmethod
    def calculate_index(
        cls,
        tif_path: str,
        index_type: str = "ndvi",
        red_band: Optional[int] = None,
        nir_band: Optional[int] = None,
        green_band: Optional[int] = None,
        blue_band: Optional[int] = None,
        swir_band: Optional[int] = None,
        output_dir: Optional[str] = None,
    ) -> Dict:
        """窗口化计算本地 GeoTIFF 的光谱指数（NDVI/NDWI/NBR/EVI）并落盘。

        Contract: 失败时返回 {"success": False, "error": "..."}，不抛异常。
        输出 float32 / nodata -9999（#537 头/字节一致契约）；产物带
        descriptor（写者已知，零重开）、内容指纹与 quality evidence。
        """
        import rasterio

        from app.lib.geo_analysis.raster_windowed import (
            INDEX_BAND_ROLES,
            windowed_band_index,
        )

        idx = index_type.lower()
        if idx not in INDEX_BAND_ROLES:
            return {
                "success": False,
                "error": f"不支持的指数类型 '{index_type}'，可用: {sorted(INDEX_BAND_ROLES)}",
            }

        try:
            from app.utils.path import validate_data_path

            resolved_tif_path = validate_data_path(tif_path)
            if output_dir:
                output_dir = validate_data_path(output_dir)
            else:
                from app.core.config import settings

                output_dir = os.path.join(settings.DATA_DIR, "analysis_results")
        except ValueError as ve:
            return {"success": False, "error": f"路径安全错误: {ve}"}

        if not os.path.exists(resolved_tif_path):
            return {"success": False, "error": "输入影像文件不存在"}

        try:
            with rasterio.open(resolved_tif_path) as src:
                detected = cls.auto_detect_bands(src)

            # 显式波段参数优先；缺省按角色从探测结果/预设取。
            band_map = {
                "red": red_band or detected.get("red"),
                "nir": nir_band or detected.get("nir"),
                "green": green_band or detected.get("green"),
                "blue": blue_band or detected.get("blue"),
                "swir1": swir_band or detected.get("swir1"),
            }
            missing = [r for r in INDEX_BAND_ROLES[idx] if not band_map.get(r)]
            if missing:
                return {
                    "success": False,
                    "error": (
                        f"无法确定波段角色 {missing}。影像包含 "
                        f"{detected.get('source', '未知波段布局')}，"
                        f"请手动指定 {'/'.join(missing)} 波段索引。"
                    ),
                }

            os.makedirs(output_dir, exist_ok=True)
            filename = f"{idx.upper()}_{int(time.time())}_{uuid.uuid4().hex[:6]}.tif"
            result_path = os.path.join(output_dir, filename)

            res = windowed_band_index(
                resolved_tif_path, idx, band_map=band_map, out_path=result_path
            )
            stats = res["stats"]
            with rasterio.open(resolved_tif_path) as src:
                bbox = [
                    float(src.bounds.left), float(src.bounds.bottom),
                    float(src.bounds.right), float(src.bounds.top),
                ]
                crs = str(src.crs)
                input_grid = {
                    "input_width": src.width,
                    "input_height": src.height,
                    "input_crs": crs,
                }

            finite = [v for v in (stats.get("min"), stats.get("max"), stats.get("mean"))
                      if v is not None]
            return {
                "success": True,
                "result_path": res["output_path"],
                "filename": filename,
                "stats": {
                    "min": finite[0] if finite else None,
                    "max": stats.get("max"),
                    "mean": stats.get("mean"),
                },
                "detected_bands": detected,
                "bbox": bbox,
                "crs": crs,
                "index_type": idx,
                "band_map": res["band_map"],
                "content_fingerprint": res["content_fingerprint"],
                "descriptor": res["descriptor"].to_dict(),
                "quality_evidence": {
                    "algorithm": res["algorithm"],
                    "parameters": {"band_map": res["band_map"]},
                    **input_grid,
                    "output_width": res["descriptor"].width,
                    "output_height": res["descriptor"].height,
                    "output_crs": crs,
                    "resampled": False,
                    "reprojected": False,
                    "valid_pixel_count": stats.get("valid_pixel_count"),
                    "nodata_pixel_count": stats.get("nodata_pixel_count"),
                },
            }
        except Exception as e:
            logger.error(f"{index_type.upper()} calculation failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    @classmethod
    def calculate_ndvi(
        cls,
        tif_path: str,
        red_band: Optional[int] = None,
        nir_band: Optional[int] = None,
        output_dir: Optional[str] = None
    ) -> Dict:
        """计算归一化植被指数 (NDVI)：(NIR - Red) / (NIR + Red)。

        保留既有 API（run_ndvi_analysis 调用方）：失败返回
        {"success": False, "error": ...}；成功 payload 字段不变，新增
        descriptor / content_fingerprint / quality_evidence（增量字段）。
        """
        return cls.calculate_index(
            tif_path, "ndvi",
            red_band=red_band, nir_band=nir_band, output_dir=output_dir,
        )
