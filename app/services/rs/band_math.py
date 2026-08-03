"""遥感波段计算与地形表面模型纯函数 (Band Math)

提供 NDVI, NDWI, EVI, NBR 纯 NumPy 波段代数计算，
Horn 方法 3x3 坡度/坡向/山体阴影计算，以及基于 Mask 的安全统计指标生成。
"""
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple
import numpy as np


@dataclass
class RasterAnalysisResult:
    """遥感与栅格分析结果统一 Domain 值对象"""
    index_type: str
    array: Optional[np.ndarray] = None
    bounds: Optional[List[float]] = None  # [west, south, east, north] WGS84
    stats: Dict[str, Any] = field(default_factory=dict)
    is_error: bool = False
    error_msg: str = ""
    correction_hint: str = ""

    def to_llm_response(self) -> Dict[str, Any]:
        """格式化供 LLM 上下文阅读的精简响应 payload"""
        if self.is_error:
            res = {"success": False, "error": self.error_msg}
            if self.correction_hint:
                res["correction_hint"] = self.correction_hint
            return res

        return {
            "success": True,
            "index_type": self.index_type,
            "bounds": self.bounds,
            "stats": self.stats,
            "summary": (
                f"完成 {self.index_type.upper()} 栅格分析。"
                f"范围: {self.bounds}, 均值: {self.stats.get('mean', 'N/A')}, "
                f"极值: [{self.stats.get('min', 'N/A')}, {self.stats.get('max', 'N/A')}], "
                f"有效像素率: {self.stats.get('valid_pixel_pct', '100%')}"
            ),
        }


# Pure index formulas for Sentinel-2 bands
INDEX_FORMULAS: Dict[str, Tuple[List[str], Callable[..., np.ndarray]]] = {
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


def compute_index_array(index_type: str, **bands: np.ndarray) -> np.ndarray:
    """按预设公式计算光谱指数数组"""
    idx = index_type.lower()
    if idx not in INDEX_FORMULAS:
        raise ValueError(f"Unsupported index type '{index_type}'")
    bands_needed, formula = INDEX_FORMULAS[idx]
    args = [bands[b] for b in bands_needed]
    return formula(*args)


def compute_slope(dem: np.ndarray, cell_size: float) -> np.ndarray:
    """使用 Horn 方法 (3x3 窗口) 计算坡度 (单位: 度)"""
    pad = np.pad(dem, 1, mode="edge")
    dzdx = ((pad[1:-1, 2:] - pad[1:-1, :-2]) / (2 * cell_size) +
             (pad[:-2, 2:] - pad[:-2, :-2]) / (4 * cell_size) +
             (pad[2:, 2:] - pad[2:, :-2]) / (4 * cell_size)) / 2
    dzdy = ((pad[2:, 1:-1] - pad[:-2, 1:-1]) / (2 * cell_size) +
             (pad[2:, :-2] - pad[:-2, :-2]) / (4 * cell_size) +
             (pad[2:, 2:] - pad[:-2, 2:]) / (4 * cell_size)) / 2
    slope_rad = np.arctan(np.sqrt(dzdx ** 2 + dzdy ** 2))
    return np.degrees(slope_rad)


def compute_aspect(dem: np.ndarray, cell_size: float) -> np.ndarray:
    """计算坡向 (0-360 度，顺时针自正北)"""
    pad = np.pad(dem, 1, mode="edge")
    dzdx = (pad[1:-1, 2:] - pad[1:-1, :-2]) / (2 * cell_size)
    dzdy = (pad[2:, 1:-1] - pad[:-2, 1:-1]) / (2 * cell_size)
    aspect = np.degrees(np.arctan2(-dzdy, dzdx))
    aspect = np.where(aspect < 0, aspect + 360, aspect)
    flat = (dzdx == 0) & (dzdy == 0)
    aspect[flat] = np.nan
    return aspect


def compute_hillshade(dem: np.ndarray, cell_size: float,
                      azimuth: float = 315, altitude: float = 45) -> np.ndarray:
    """计算山体阴影照度 (0-255)"""
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


def compute_raster_stats(array: np.ndarray) -> Dict[str, Any]:
    """忽略 NaN / Mask 计算安全统计指标与有效像素比率"""
    valid_mask = ~np.isnan(array)
    total_cells = array.size
    valid_cells = int(np.sum(valid_mask))
    if valid_cells == 0 or total_cells == 0:
        return {
            "min": None, "max": None, "mean": None, "std": None,
            "total_pixels": total_cells, "valid_pixels": 0, "valid_pixel_pct": "0.0%"
        }

    valid_vals = array[valid_mask]
    v_min = float(np.min(valid_vals))
    v_max = float(np.max(valid_vals))
    v_mean = float(np.mean(valid_vals))
    v_std = float(np.std(valid_vals))
    pct_str = f"{round((valid_cells / total_cells) * 100, 1)}%"

    return {
        "min": round(v_min, 4),
        "max": round(v_max, 4),
        "mean": round(v_mean, 4),
        "std": round(v_std, 4),
        "total_pixels": total_cells,
        "valid_pixels": valid_cells,
        "valid_pixel_pct": pct_str,
    }
