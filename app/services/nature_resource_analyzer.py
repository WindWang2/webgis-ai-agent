"""
遥感影像分析服务 - 自然资源监测
支持 NDVI, NDWI 等通用指数计算及波段自动适配
"""
import os
import logging
import numpy as np
import rasterio
from typing import Dict, Optional, Tuple, List
from pathlib import Path
import uuid
import time

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
        "green": 3,    # B green
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
    def auto_detect_bands(src: rasterio.DatasetReader) -> Dict[str, int]:
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
    def calculate_ndvi(
        cls, 
        tif_path: str, 
        red_band: Optional[int] = None, 
        nir_band: Optional[int] = None,
        output_dir: Optional[str] = None
    ) -> Dict:
        """
        计算归一化植被指数 (NDVI)
        公式: (NIR - Red) / (NIR + Red)
        """
        if not os.path.exists(tif_path):
            return {"success": False, "error": "输入影像文件不存在"}

        try:
            with rasterio.open(tif_path) as src:
                # 自动分配波段
                detected = cls.auto_detect_bands(src)
                r_idx = red_band or detected.get("red")
                n_idx = nir_band or detected.get("nir")

                if not r_idx or not n_idx:
                    return {
                        "success": False, 
                        "error": f"无法确定波段索引。影像包含 {src.count} 个波段，请手动指定红光和近红外波段。"
                    }

                # 读取数据
                logger.info(f"Calculating NDVI using Red(B{r_idx}) and NIR(B{n_idx})")
                red = src.read(r_idx).astype(float)
                nir = src.read(n_idx).astype(float)

                # 计算 NDVI (处理除零)
                denom = nir + red
                # 避免除以零且处理无效数据
                ndvi = np.divide((nir - red), denom, out=np.zeros_like(nir), where=denom != 0)
                
                # 获取元数据以便保存
                meta = src.meta.copy()
                meta.update({
                    'driver': 'GTiff',
                    'dtype': 'float32',
                    'count': 1,
                    'nodata': -9999
                })

                # 生成输出路径
                os.makedirs(output_dir, exist_ok=True)
                filename = f"NDVI_{int(time.time())}_{uuid.uuid4().hex[:6]}.tif"
                result_path = os.path.join(output_dir, filename)

                with rasterio.open(result_path, 'w', **meta) as dst:
                    dst.write(ndvi.astype(np.float32), 1)

                return {
                    "success": True,
                    "result_path": result_path,
                    "filename": filename,
                    "stats": {
                        "min": float(np.min(ndvi)),
                        "max": float(np.max(ndvi)),
                        "mean": float(np.mean(ndvi)),
                    },
                    "detected_bands": detected,
                    "bbox": [src.bounds.left, src.bounds.bottom, src.bounds.right, src.bounds.top],
                    "crs": str(src.crs)
                }

        except Exception as e:
            logger.error(f"NDVI calculation failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
