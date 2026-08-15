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


# Pure index formulas for Sentinel-2 bands.
# Where-guarded divide: pixels where the denominator is <= 0 (notably
# Sentinel-2 L2A nodata, where bands are 0) are left at NaN, not 0, so they
# are excluded by compute_raster_stats instead of diluting vegetation/water
# coverage as plausible-looking 0.0 indices (audit B-F09).
INDEX_FORMULAS: Dict[str, Tuple[List[str], Callable[..., np.ndarray]]] = {
    "ndvi": (["red", "nir"], lambda r, nir: np.divide(
        nir - r, nir + r,
        out=np.full_like(nir - r, np.nan, dtype=float),
        where=(nir + r) > 0,
    )),
    "ndwi": (["green", "nir"], lambda g, nir: np.divide(
        g - nir, g + nir,
        out=np.full_like(g - nir, np.nan, dtype=float),
        where=(g + nir) > 0,
    )),
    "nbr": (["nir", "swir12"], lambda nir, swir: np.divide(
        nir - swir, nir + swir,
        out=np.full_like(nir - swir, np.nan, dtype=float),
        where=(nir + swir) > 0,
    )),
    "evi": (["blue", "red", "nir"], lambda b, r, nir: 2.5 * np.divide(
        nir - r, nir + 6 * r - 7.5 * b + 1,
        out=np.full_like(nir - r, np.nan, dtype=float),
        where=(nir + 6 * r - 7.5 * b + 1) > 0,
    )),
}


# Sentinel-2 L2A reflectance spans [0, 1]; DN assets span [0, 10000].
# Anything reliably above the reflectance range is DN-scaled.
_DN_SCALE_THRESHOLD = 1.5


def _maybe_dn_to_reflectance(arr: np.ndarray) -> np.ndarray:
    """#382: EVI 的 +1 与 -7.5·b 项只在反射率单位下物理成立。

    Sentinel-2 L2A 常以 DN (0-10000) 下发。比值型指数 (NDVI/NDWI/NBR)
    对线性缩放不变，EVI 不是 —— DN 输入会让 EVI ~3.5× 虚高、超出物理
    [-1,1] 区间。对 DN 状输入除以 10000 归一化。
    """
    finite = arr[np.isfinite(arr)]
    if finite.size and float(finite.max()) > _DN_SCALE_THRESHOLD:
        return arr / 10000.0
    return arr


def compute_index_array(index_type: str, **bands: np.ndarray) -> np.ndarray:
    """按预设公式计算光谱指数数组"""
    idx = index_type.lower()
    if idx not in INDEX_FORMULAS:
        raise ValueError(f"Unsupported index type '{index_type}'")
    bands_needed, formula = INDEX_FORMULAS[idx]
    args = [bands[b] for b in bands_needed]
    if idx == "evi":
        args = [_maybe_dn_to_reflectance(a) for a in args]
    return formula(*args)


def compute_slope(dem: np.ndarray, cell_size: float,
                  cell_size_x: Optional[float] = None) -> np.ndarray:
    """使用 Horn 方法 (3x3 窗口) 计算坡度 (单位: 度)

    cell_size_x: 可选 —— 东西 (x) 方向的像元地面尺寸 (米)。地理坐标
    DEM (如 Copernicus GLO-30, EPSG:4326) 的经度向地面距离随纬度收缩
    ~cos(lat)，若 x/y 用同一赤道比例会低估东西向坡度；由调用方 (stac_client)
    传入经纬度向校正后的值，默认 None 时回退 cell_size (行为不变)。
    """
    pad = np.pad(dem, 1, mode="edge")
    dx = cell_size_x if cell_size_x is not None else cell_size
    dzdx = ((pad[1:-1, 2:] - pad[1:-1, :-2]) / (2 * dx) +
             (pad[:-2, 2:] - pad[:-2, :-2]) / (4 * dx) +
             (pad[2:, 2:] - pad[2:, :-2]) / (4 * dx)) / 2
    dzdy = ((pad[2:, 1:-1] - pad[:-2, 1:-1]) / (2 * cell_size) +
             (pad[2:, :-2] - pad[:-2, :-2]) / (4 * cell_size) +
             (pad[2:, 2:] - pad[:-2, 2:]) / (4 * cell_size)) / 2
    slope_rad = np.arctan(np.sqrt(dzdx ** 2 + dzdy ** 2))
    return np.degrees(slope_rad)


def compute_aspect(dem: np.ndarray, cell_size: float,
                   cell_size_x: Optional[float] = None) -> np.ndarray:
    """计算坡向 (0-360 度，顺时针自正北) —— 下坡方位 (aspect = 水流方向)。

    #379: 旧实现 arctan2(-dzdy, dzdx) 返回的是数学角 (自东逆时针)，
    与文档承诺的罗盘角 (顺时针自正北) 相反：东抬升得 0° (真 270°)、
    北抬升得 90° (真 180°)。ESRI 等价式 aspect = degrees(atan2(-dzdx, dzdy))
    % 360：东抬升 -> 270° (下坡西)，北抬升 -> 180° (下坡南)，南抬升 -> 0°，
    西抬升 -> 90°。cell_size_x 语义同 compute_slope。
    """
    pad = np.pad(dem, 1, mode="edge")
    dx = cell_size_x if cell_size_x is not None else cell_size
    dzdx = (pad[1:-1, 2:] - pad[1:-1, :-2]) / (2 * dx)
    dzdy = (pad[2:, 1:-1] - pad[:-2, 1:-1]) / (2 * cell_size)
    aspect = np.mod(np.degrees(np.arctan2(-dzdx, dzdy)), 360.0)
    flat = (dzdx == 0) & (dzdy == 0)
    aspect[flat] = np.nan
    return aspect


def compute_hillshade(dem: np.ndarray, cell_size: float,
                      azimuth: float = 315, altitude: float = 45,
                      cell_size_x: Optional[float] = None) -> np.ndarray:
    """计算山体阴影照度 (0-255)

    #379: 旧实现把数学角 aspect 喂给 cos(az_rad - aspect_rad) 并镜像
    radians(360 - azimuth)，导致光照相对配置的太阳方位旋转 ~90°
    (如太阳 315° 时北向坡被算成暗面)。修正后 aspect 为罗盘角，
    太阳方位直接使用罗盘 azimuth、不再镜像：两角同为顺时针自正北，
    同坐标系相减。推导 (南北半球一致，仅依赖局部梯度符号)：
    照度 = sin(alt)·cos(θ) + cos(alt)·sin(θ)·cos(az - aspect)。
    cell_size_x 语义同 compute_slope。
    """
    pad = np.pad(dem, 1, mode="edge")
    dx = cell_size_x if cell_size_x is not None else cell_size
    dzdx = (pad[1:-1, 2:] - pad[1:-1, :-2]) / (2 * dx)
    dzdy = (pad[2:, 1:-1] - pad[:-2, 1:-1]) / (2 * cell_size)
    slope_rad = np.arctan(np.sqrt(dzdx ** 2 + dzdy ** 2))
    aspect_rad = np.arctan2(-dzdx, dzdy)  # 罗盘坡向 (顺时针自正北) 弧度
    az_rad = np.radians(azimuth)          # 罗盘太阳方位，与 aspect 同基准，不镜像
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
