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

    # science-v3 审计（HIGH）：按波段数位置猜测（含 S2 预设）是
    # 「位置猜波段」风险集中点 —— guess/preset 来源的波段在 strict 模式
    # 下类型化拒绝，非 strict 模式必须以 warning 强制披露。
    _GUESS_SOURCES = ("guess-", "preset-")

    @classmethod
    def _guessed_roles(
        cls, detected: Dict[str, int], explicit: Dict[str, Optional[int]],
        required_roles,
    ) -> Dict[str, int]:
        """必需角色中由位置猜测填充（且调用方未显式给出）的 {role: band}。"""
        source = str(detected.get("source", ""))
        if not source.startswith(cls._GUESS_SOURCES):
            return {}
        return {
            role: detected[role]
            for role in required_roles
            if role in detected and not explicit.get(role)
        }

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
        strict_band_semantics: bool = True,
    ) -> Dict:
        """窗口化计算本地 GeoTIFF 的光谱指数（NDVI/NDWI/NBR/EVI）并落盘。

        Contract: 失败时返回 {"success": False, "error": "..."}，不抛异常。
        输出 float32 / nodata -9999（#537 头/字节一致契约）；产物带
        descriptor（写者已知，零重开）、内容指纹与 quality evidence。

        strict_band_semantics（science-v3 审计 HIGH 修复，默认 True）：
        波段角色不能靠波段数位置猜测 —— 缺省角色若只能由 guess/preset
        来源填充，strict 模式类型化拒绝（要求显式传波段索引）；False 时
        放行但强制在 payload 与 quality_evidence 中披露 guess 来源。
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

            explicit_args = {
                "red": red_band, "nir": nir_band, "green": green_band,
                "blue": blue_band, "swir1": swir_band,
            }
            required_roles = INDEX_BAND_ROLES[idx]
            # science-v3 审计 HIGH：guess/preset 来源的角色（调用方未显式
            # 传参）在 strict 模式下类型化拒绝 —— 位置猜波段不再静默放行。
            guessed = cls._guessed_roles(detected, explicit_args, required_roles)
            if guessed and strict_band_semantics:
                return {
                    "success": False,
                    "error": (
                        f"波段角色 {sorted(guessed)} 来自位置猜测"
                        f"（{detected.get('source')}），已按 strict 波段语义拒绝。"
                        f"请显式指定 {'/'.join(sorted(guessed))} 波段索引，"
                        "或确认影像波段布局后重试（strict_band_semantics=False "
                        "可放行但结果会携带 guess 披露）。"
                    ),
                    "error_type": "band_semantics_guess_rejected",
                    "guessed_roles": guessed,
                    "detected_bands": detected,
                }

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
            from app.lib.geo_analysis.raster_windowed import INDEX_VALID_RANGE
            valid_range = INDEX_VALID_RANGE.get(idx)
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
            quality_evidence = {
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
            }
            payload = {
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
                "valid_range": list(valid_range) if valid_range else None,
                "content_fingerprint": res["content_fingerprint"],
                "descriptor": res["descriptor"].to_dict(),
                "quality_evidence": quality_evidence,
            }
            if guessed:
                # 非 strict 放行路径：guess 来源必须强制披露（quality
                # evidence 的 warnings 是下游可机器消费的通道）。
                warnings = [
                    f"波段角色 {sorted(guessed)} 来自位置猜测"
                    f"（{detected.get('source')}）：结果科学有效性依赖猜测正确性。"
                ]
                payload["band_semantics_warnings"] = warnings
                quality_evidence["warnings"] = warnings
            return payload
        except Exception as e:
            logger.error(f"{index_type.upper()} calculation failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    @classmethod
    def calculate_ndvi(
        cls,
        tif_path: str,
        red_band: Optional[int] = None,
        nir_band: Optional[int] = None,
        output_dir: Optional[str] = None,
        strict_band_semantics: bool = True,
    ) -> Dict:
        """计算归一化植被指数 (NDVI)：(NIR - Red) / (NIR + Red)。

        保留既有 API（run_ndvi_analysis 调用方）：失败返回
        {"success": False, "error": ...}；成功 payload 字段不变，新增
        descriptor / content_fingerprint / quality_evidence（增量字段）。
        """
        return cls.calculate_index(
            tif_path, "ndvi",
            red_band=red_band, nir_band=nir_band, output_dir=output_dir,
            strict_band_semantics=strict_band_semantics,
        )
