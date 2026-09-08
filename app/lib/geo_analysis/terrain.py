"""Terrain science pure functions（ADR-0099 terrain 域包实现层）。

VNext 地形科学库：TPI / TRI / 粗糙度 / 曲率（Zevenbergen-Thorne）/
视域（扇区视线角扫描）/ D8 流向与汇流累积（拓扑序）/ 逆 D8 流域 /
等值线（matplotlib Agg marching squares）。

Foundation V2（A5 地形水文与地貌量测扩展）：Priority-Flood 填洼
（Barnes 2014）/ D∞ 多向流（Tarboton 1997）/ 流程长度 / Strahler 河流
分级 / 流域形态量测 / TWI-SPI-USLE LS 指数 / 地形开放度（Yokoyama
2002）/ geomorphons 地貌分类（Jasiewicz & Stepinski 2013）/
Weiss 双尺度 TPI 地类分级 / 多方位山体阴影。

Terrain V3：地平线角与天空可视因子（Steyn 1980，与 openness 共族
射线行走）；D8 平地 epsilon 路由（flat_routing="epsilon"，复用
fill_depressions 的 Barnes 2014 机制）。

职责边界（CONTRACT_BACKBONE §1）：本模块只做纯 NumPy/标量数学 ——
不读文件、不写 artifact、不挂证据块（工具层职责）。所有函数

- 接受 2D ``dem`` 数组 + 像元尺寸 + 可选 ``nodata``；
- nodata 感知：``dem == nodata`` 与 NaN/±Inf 一律视为无效像元
  （与 band_math #712 口径一致）；全无效输入 → ``NoValidObservations``；
- 返回 (数组或结果 dict, meta dict)，meta 为确定性纯文本/数值事实
  （无时间戳 —— 重复调用可做 dict 相等性检验）。

边缘策略（默认披露 ``EDGE_POLICY``）：**edge cells use available
neighbors** —— 窗口统计在边界收缩为可得像元，不发明填充值；例外在
各函数 docstring/meta 里显式披露（如曲率模板用 edge 复制延拓、
视线采样 bilinear 需 4 邻域有效）。
"""
from __future__ import annotations

import heapq
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.lib.cancellation import checkpoint

from app.lib.gis.scientific_errors import (
    DegenerateData,
    NoValidObservations,
    ResourceScaleMismatch,
)

__all__ = [
    "EDGE_POLICY",
    "D8_ENCODING",
    "topographic_position_index",
    "terrain_ruggedness_index",
    "roughness",
    "surface_curvature",
    "viewshed",
    "d8_flow",
    "flow_accumulation",
    "upstream_watershed",
    "extract_contours",
    "fill_depressions",
    "dinf_flow_direction",
    "dinf_flow_accumulation",
    "flow_length",
    "extract_streams",
    "stream_order",
    "watershed_morphometry",
    "topographic_wetness_index",
    "stream_power_index",
    "ls_factor",
    "terrain_openness",
    "geomorphons",
    "landform_classification",
    "hillshade_multiazimuth",
    "horizon_angle",
    "sky_view_factor",
]

EDGE_POLICY = "edge cells use available neighbors (window statistics shrink at the border; no padding values are invented)"

MIN_WINDOW = 3
MAX_WINDOW = 101

# Foundation V2（A5）：网格规模护栏（先拒绝、后分配）与射线半径上限。
MAX_HYDRO_CELLS = 50_000_000
MAX_OPENNESS_RADIUS_CELLS = 100
MAX_GEOMORPHON_RADIUS_CELLS = 128

# Terrain V3：地平线角 / 天空可视因子（openness 家族射线行走，同半径包络）。
MAX_HORIZON_RADIUS_CELLS = 100
MAX_HORIZON_AZIMUTHS = 64
# d8 flat_routing="epsilon" 的默认逐像元抬升量（z 单位/像元；Barnes 2014
# 语境的小量 —— float64 下对常见 DEM 量级（10²-10⁴ m）不损失路由单调性）。
DEFAULT_FLAT_EPSILON = 1e-5

# ESRI D8 powers-of-two encoding (row 0 = north, col 0 = west):
# 1=E, 2=SE, 4=S, 8=SW, 16=W, 32=NW, 64=N, 128=NE; 0 = sink/outlet
# (no strictly lower in-grid neighbor: pit, flat, or boundary outflow).
D8_ENCODING: Dict[str, int] = {
    "E": 1, "SE": 2, "S": 4, "SW": 8,
    "W": 16, "NW": 32, "N": 64, "NE": 128,
}

# (name, code, d_row, d_col) — 索引序即 D8 编码序（平局裁决 = 最低索引）。
_D8_NEIGHBORS: Tuple[Tuple[str, int, int, int], ...] = (
    ("E", 1, 0, 1), ("SE", 2, 1, 1), ("S", 4, 1, 0), ("SW", 8, 1, -1),
    ("W", 16, 0, -1), ("NW", 32, -1, -1), ("N", 64, -1, 0), ("NE", 128, -1, 1),
)

_VIEWSHED_SECTOR_CHUNK = 256
_VIEWSHED_ANGLE_TOL = 1e-12


# ── 共用小工具 ────────────────────────────────────────────────────────


def _validate_window(window: Any) -> int:
    """window 必须是 [3, 101] 内的奇整数（TPI/roughness 邻域）。"""
    if isinstance(window, bool) or not isinstance(window, (int, np.integer)):
        if isinstance(window, float) and float(window).is_integer():
            window = int(window)
        else:
            raise ValueError(
                f"window must be an odd integer in [{MIN_WINDOW}, {MAX_WINDOW}], got {window!r}")
    window = int(window)
    if window % 2 != 1 or not (MIN_WINDOW <= window <= MAX_WINDOW):
        raise ValueError(
            f"window must be an odd integer in [{MIN_WINDOW}, {MAX_WINDOW}], got {window}")
    return window


def _prepare(dem: np.ndarray, nodata: Optional[float]) -> Tuple[np.ndarray, np.ndarray]:
    """→ (float64 数组, 有效像元掩膜)；NaN/±Inf 与 nodata 一律无效。"""
    z = np.asarray(dem, dtype=np.float64)
    if z.ndim != 2 or z.size == 0:
        raise NoValidObservations(
            "DEM must be a non-empty 2D array "
            f"(got shape {tuple(z.shape) or 'empty'})")
    if z.shape[0] < 2 or z.shape[1] < 2:
        raise NoValidObservations(
            f"terrain analysis needs at least a 2x2 grid (got {z.shape})")
    valid = np.isfinite(z)
    if nodata is not None:
        valid &= z != float(nodata)
    if not valid.any():
        raise NoValidObservations(
            "DEM has no valid cells after nodata/non-finite masking",
        )
    return z, valid


def _meta_base(algorithm: str, valid: np.ndarray, **facts: Any) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "algorithm": algorithm,
        "edge_policy": EDGE_POLICY,
        "cells_total": int(valid.size),
        "cells_valid": int(valid.sum()),
    }
    meta.update({k: v for k, v in facts.items() if v is not None})
    return meta


def _integral(image: np.ndarray) -> np.ndarray:
    """零填充积分图 ((h+1) x (w+1))，O(N) 求任意 box 和。"""
    out = np.zeros((image.shape[0] + 1, image.shape[1] + 1), dtype=np.float64)
    np.cumsum(np.cumsum(image, axis=0), axis=1, out=out[1:, 1:])
    return out


def _box_sums(
    image: np.ndarray, k: int
) -> Tuple[np.ndarray, np.ndarray]:
    """以积分图求每个像元为中心的 k×k box 的和（边界收缩：只计在界像元）。"""
    h, w = image.shape
    r = k // 2
    ii = _integral(image)
    r0 = np.arange(h)
    c0 = np.arange(w)
    r1 = np.clip(r0 + r + 1, 0, h)
    r0c = np.clip(r0 - r, 0, h)
    c1 = np.clip(c0 + r + 1, 0, w)
    c0c = np.clip(c0 - r, 0, w)
    top = ii[np.ix_(r0c, c0c)]
    left = ii[np.ix_(r0c, c1)]
    upper = ii[np.ix_(r1, c0c)]
    whole = ii[np.ix_(r1, c1)]
    return whole - upper - left + top


# ── 1. TPI / 3. Roughness（窗口统计）─────────────────────────────────


def topographic_position_index(
    dem: np.ndarray, window: int = 3, nodata: Optional[float] = None,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """TPI（Weiss 2001）：z − 邻域窗口均值。

    窗口均值**包含中心像元**（与 SAGA 局部均值同口径；meta 披露）。
    线性坡面上 TPI ≡ 0；山脊 > 0、谷地 < 0。与像元尺寸无关（z 同量纲）。
    """
    window = _validate_window(window)
    z, valid = _prepare(dem, nodata)
    masked = np.where(valid, z, 0.0)
    s = _box_sums(masked, window)
    c = _box_sums(valid.astype(np.float64), window)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(c > 0, s / np.maximum(c, 1.0), np.nan)
    tpi = np.where(valid & (c > 0), z - mean, np.nan)
    meta = _meta_base(
        "terrain.tpi", valid, window=window, window_includes_center=True,
        units="same as input elevation",
        method="TPI = z - mean(k x k window including center) (Weiss 2001)",
    )
    return tpi, meta


def roughness(
    dem: np.ndarray, window: int = 3, nodata: Optional[float] = None,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """粗糙度：邻域窗口内高程标准差（总体 std，ddof=0）。

    注：Wilson et al. (2007) 的 roughness 原口径是 max−min；本实现
    采用窗口标准差（也是常见 GIS 实现口径），引用 wilson2007 指
    「多尺度地形分析」语境而非公式本身。
    """
    window = _validate_window(window)
    z, valid = _prepare(dem, nodata)
    zv = np.where(valid, z, 0.0)
    s1 = _box_sums(zv, window)
    s2 = _box_sums(zv * zv, window)
    c = _box_sums(valid.astype(np.float64), window)
    with np.errstate(invalid="ignore", divide="ignore"):
        var = s2 / np.maximum(c, 1.0) - (s1 / np.maximum(c, 1.0)) ** 2
    std = np.sqrt(np.maximum(var, 0.0))
    out = np.where(valid & (c > 0), std, np.nan)
    meta = _meta_base(
        "terrain.roughness", valid, window=window,
        units="same as input elevation",
        method="roughness = population stddev over k x k window "
                      "(stddev convention; cf. Wilson et al. 2007)",
        numerical_tolerance=(
            "integral-image variance can lose precision when window means are"
            " large relative to their spread; integer-valued fixtures are exact"),
    )
    return out, meta


# ── 2. TRI（固定 8 邻域）──────────────────────────────────────────────


def terrain_ruggedness_index(
    dem: np.ndarray, nodata: Optional[float] = None,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """TRI（Riley et al. 1999 sqrt-of-sum 口径；Wilson et al. 2007 的口径

    是均方差 —— 本实现取 Riley 形式）：
    sqrt(Σ (z − z_neighbor)²) over 8 个直接邻域（边界收缩为可得邻域）。"""
    z, valid = _prepare(dem, nodata)
    acc = np.zeros_like(z)
    for _, _, dr, dc in _D8_NEIGHBORS:
        zs = np.full_like(z, np.nan)
        r0, r1 = max(0, -dr), min(z.shape[0], z.shape[0] - dr)
        c0, c1 = max(0, -dc), min(z.shape[1], z.shape[1] - dc)
        zs[r0:r1, c0:c1] = z[r0 + dr:r1 + dr, c0 + dc:c1 + dc]
        vs = np.zeros_like(valid)
        vs[r0:r1, c0:c1] = valid[r0 + dr:r1 + dr, c0 + dc:c1 + dc]
        d = np.where(vs, z - zs, 0.0)
        acc += d * d
    tri = np.where(valid, np.sqrt(acc), np.nan)
    meta = _meta_base(
        "terrain.tri", valid, window=3,
        units="same as input elevation",
        method="TRI = sqrt(sum of squared diffs to the 8 immediate neighbors) (Riley 1999)",
    )
    return tri, meta


# ── 4. 曲率（Zevenbergen & Thorne 1987 二阶差分）──────────────────────


def surface_curvature(
    dem: np.ndarray, cell_size: float,
    cell_size_x: Optional[float] = None,
    nodata: Optional[float] = None,
) -> Tuple[Dict[str, np.ndarray], Dict[str, Any]]:
    """平面/剖面曲率（Zevenbergen & Thorne 1987 二阶导数族）。

    约定（精确文档，测试按此断言）：

    - Dxx = (z[j-1] - 2 z[j] + z[j+1]) / cell_x²   —— z = x² 时恰为 +2
    - Dyy = (z[i-1] - 2 z[i] + z[i+1]) / cell_y²
    - Dxy = (z[i+1,j+1] + z[i-1,j-1] - z[i-1,j+1] - z[i+1,j-1]) / (4 cell_x cell_y)
    - 梯度 G = dz/dx、H = dz/dy（中心差分）
    - profile = (G²·Dxx + 2GH·Dxy + H²·Dyy) / (G² + H²)
      —— 最陡下降方向的二阶方向导数；> 0 凸（水流减速），< 0 凹（加速）
    - plan = (H²·Dxx - 2GH·Dxy + G²·Dyy) / (G² + H²)
      —— 等高线方向（垂直于最陡方向）的二阶导数；> 0 分散、< 0 汇聚
    - 梯度为 0（平地）→ NaN（与 band_math 坡向平地 NaN 同口径）

    边缘策略例外：3×3 二阶差分模板用 edge 复制延拓（与
    band_math.compute_slope 的 mode="edge" 相同）—— 边界像元退化为单侧
    差分；任一模板邻域无效（nodata）→ NaN。
    """
    if cell_size is None or cell_size <= 0 or (
            cell_size_x is not None and cell_size_x <= 0):
        raise ValueError(
            f"cell sizes must be positive (got cell_size={cell_size!r}, "
            f"cell_size_x={cell_size_x!r})")
    cx = float(cell_size_x if cell_size_x is not None else cell_size)
    cy = float(cell_size)
    z, valid = _prepare(dem, nodata)
    zp = np.pad(np.where(valid, z, np.nan), 1, mode="edge")
    # edge 复制延拓不保留 NaN —— 单独延拓有效掩膜。
    vp = np.pad(valid, 1, mode="edge")
    zc = zp[1:-1, 1:-1]
    dxx = (zp[1:-1, :-2] - 2 * zc + zp[1:-1, 2:]) / (cx * cx)
    dyy = (zp[:-2, 1:-1] - 2 * zc + zp[2:, 1:-1]) / (cy * cy)
    dxy = (zp[2:, 2:] + zp[:-2, :-2] - zp[:-2, 2:] - zp[2:, :-2]) / (4 * cx * cy)
    g = (zp[1:-1, 2:] - zp[1:-1, :-2]) / (2 * cx)
    h = (zp[2:, 1:-1] - zp[:-2, 1:-1]) / (2 * cy)
    support = (vp[1:-1, :-2] & vp[1:-1, 2:] & vp[:-2, 1:-1] & vp[2:, 1:-1]
               & vp[2:, 2:] & vp[:-2, :-2] & vp[:-2, 2:] & vp[2:, :-2] & valid)
    denom = g * g + h * h
    with np.errstate(invalid="ignore", divide="ignore"):
        profile = np.where(
            support & (denom > 0),
            (g * g * dxx + 2 * g * h * dxy + h * h * dyy) / np.where(denom > 0, denom, 1.0),
            np.nan)
        plan = np.where(
            support & (denom > 0),
            (h * h * dxx - 2 * g * h * dxy + g * g * dyy) / np.where(denom > 0, denom, 1.0),
            np.nan)
    meta = _meta_base(
        "terrain.curvature", valid,
        cell_size=cy, cell_size_x=cx,
        units="z_units * cell^-2 (metres^-1 scaled by z units; conventionally x100)",
        method="Zevenbergen-Thorne 1987 second differences; profile along steepest descent, plan along contour",
        edge_policy=(
            "curvature edge policy: 3x3 stencil edge-replicated (one-sided "
            "differences at borders, same as band_math Horn slope); cells with "
            "any invalid stencil neighbor are NaN; flat gradient cells are NaN"),
        sign_convention=(
            "profile > 0 convex (flow decelerating) / < 0 concave; "
            "plan > 0 diverging / < 0 converging; z = x^2 fixture: profile = +2, plan = 0"),
    )
    return {"plan": plan, "profile": profile}, meta


# ── 5. 视域（扇区视线角扫描）──────────────────────────────────────────


def _bilinear_sample(
    z: np.ndarray, valid: np.ndarray, cols: np.ndarray, rows: np.ndarray,
) -> np.ndarray:
    """向量 bilinear 采样；越界或任一角无效 → NaN。"""
    h, w = z.shape
    c0 = np.floor(cols).astype(np.int64)
    r0 = np.floor(rows).astype(np.int64)
    fc = cols - c0
    fr = rows - r0
    c1 = np.clip(c0 + 1, 0, w - 1)
    r1 = np.clip(r0 + 1, 0, h - 1)
    c0c = np.clip(c0, 0, w - 1)
    r0c = np.clip(r0, 0, h - 1)
    inside = (c0 >= 0) & (c0 <= w - 1) & (r0 >= 0) & (r0 <= h - 1) \
        & (cols >= 0) & (cols <= w - 1) & (rows >= 0) & (rows <= h - 1)

    def _g(rr, cc):
        ok = valid[rr, cc]
        return np.where(ok, z[rr, cc], np.nan)

    v00, v01 = _g(r0c, c0c), _g(r0c, c1)
    v10, v11 = _g(r1, c0c), _g(r1, c1)
    top = v00 * (1 - fc) + v01 * fc
    bot = v10 * (1 - fc) + v11 * fc
    out = top * (1 - fr) + bot * fr
    return np.where(inside & np.isfinite(out), out, np.nan)


def _world_to_cell(
    transform: Sequence[float], x: float, y: float,
) -> Tuple[float, float]:
    """世界坐标 → (col, row)（**像元中心索引空间**：整数 = 像元中心）。

    science-v3 审计复核：GDAL 6 参数仿射原点是 UL **角点**（实测
    ``t*(0,0)``=栅格角、``src.xy(0,0)``=首像元中心），故逆变换后须减
    0.5 才与数组索引语义（整数 = 像元中心）一致 —— 与 ``_cell_to_world``
    互为正逆变换。
    """
    a, b, c, d, e, f = (float(v) for v in transform[:6])
    det = a * e - b * d
    if det == 0:
        raise ValueError(f"degenerate raster transform (det=0): {tuple(transform[:6])}")
    col = (e * (x - c) - b * (y - f)) / det - 0.5
    row = (a * (y - f) - d * (x - c)) / det - 0.5
    return col, row


def _cell_to_world(
    transform: Sequence[float], cols: np.ndarray, rows: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray]:
    """(col, row)（像元中心索引空间）→ 世界坐标。

    输入索引是「整数 = 像元中心」的数组/等值线索引语义；GDAL 仿射把
    (col+0.5, row+0.5) 映射到该像元中心，故先加 0.5 再过仿射。
    """
    a, b, c, d, e, f = (float(v) for v in transform[:6])
    xs = a * (cols + 0.5) + b * (rows + 0.5) + c
    ys = d * (cols + 0.5) + e * (rows + 0.5) + f
    return xs, ys


def viewshed(
    dem: np.ndarray, cell_size: float,
    cell_size_x: Optional[float] = None,
    *,
    observer: Optional[Tuple[float, float]] = None,
    observer_xy: Optional[Tuple[float, float]] = None,
    transform: Optional[Sequence[float]] = None,
    observer_height: float = 2.0,
    target_height: float = 0.0,
    max_distance: float = 5000.0,
    nodata: Optional[float] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """布尔视域：扇区化视线角扫描（R3 型判据的向量化实现）。

    判据（R3 型近似）：观察点 O（高程 = 观察点地形 + observer_height），
    目标 T（= 目标地形 + target_height）。T 可见 ⇔ 沿 O→T 射线的中间
    地形采样（~1 像元步长，**扇区中心方向** bilinear 采样 —— 非逐目标
    精确射线，扇区角离散是公开近似）的仰角都 ≤ 目标仰角（相切记为
    可见，容差 1e-12 rad）。无地球曲率/大气折射（meta 披露）。

    扇区宽 ≈ 最大距离处 1 像元弧长；目标按仰角落入扇区，与该扇区
    距离 bin 前的运行最大仰角比较 —— 2000×2000 窗口秒级。

    observer=(row, col) 数组坐标，或 observer_xy=(x, y) 世界坐标 +
    transform（rasterio 6 参数）。max_distance 单位米；cell_size 为 y
    (北南) 向地面米尺寸、cell_size_x 为 x (东西) 向（地理栅格由调用方
    传入 cos(lat) 修正后的值，band_math 同政策）。
    """
    if max_distance <= 0:
        raise ValueError(f"max_distance must be positive (got {max_distance!r})")
    if observer_height < 0 or target_height < 0:
        raise ValueError("observer_height/target_height must be >= 0")
    if cell_size <= 0 or (cell_size_x is not None and cell_size_x <= 0):
        raise ValueError("cell sizes must be positive")
    cx = float(cell_size_x if cell_size_x is not None else cell_size)
    cy = float(cell_size)

    if observer is None:
        if observer_xy is None or transform is None:
            raise ValueError("viewshed needs observer=(row, col) or observer_xy=(x, y) + transform")
        obs_col, obs_row = _world_to_cell(transform, float(observer_xy[0]), float(observer_xy[1]))
    else:
        obs_row, obs_col = float(observer[0]), float(observer[1])
    z, valid = _prepare(dem, nodata)
    h, w = z.shape
    # science-v3 审计：viewshed 此前无 lib 护栏 —— 距离/方位/bin/仰角
    # 等 ~6-8 个 (h,w) 工作数组在 250M 像元读护栏下可达 10+ GB；
    # 统一纳入像元包络（先拒绝后分配）。
    _guard_cells((h, w), "terrain.viewshed")
    if not (-0.5 <= obs_row <= h - 0.5 and -0.5 <= obs_col <= w - 0.5):
        raise ValueError(
            f"observer ({obs_row:.3f}, {obs_col:.3f}) is outside the DEM "
            f"(shape {(h, w)})")

    z_obs = float(_bilinear_sample(z, valid, np.array([obs_col]), np.array([obs_row]))[0])
    if not math.isfinite(z_obs):
        rr = int(np.clip(round(obs_row), 0, h - 1))
        cc = int(np.clip(round(obs_col), 0, w - 1))
        if not valid[rr, cc]:
            raise NoValidObservations("observer cell is nodata — viewshed undefined")
        z_obs = float(z[rr, cc])
    z_obs_total = z_obs + float(observer_height)

    rows_i = np.arange(h, dtype=np.float64)[:, None]
    cols_i = np.arange(w, dtype=np.float64)[None, :]
    dx_m = (cols_i - obs_col) * cx
    dy_m = (rows_i - obs_row) * cy
    dist = np.hypot(dx_m, dy_m)

    step = 0.5 * (cx + cy)  # ~1 cell 步长（各向异性像元的均值）
    k_max = int(math.ceil(max_distance / step))
    # 网格内最大距离（角点）截断射线长度 —— 网格外采样无意义。
    corner_d = float(max(
        math.hypot((0 - obs_col) * cx, (0 - obs_row) * cy),
        math.hypot((w - 1 - obs_col) * cx, (0 - obs_row) * cy),
        math.hypot((0 - obs_col) * cx, (h - 1 - obs_row) * cy),
        math.hypot((w - 1 - obs_col) * cx, (h - 1 - obs_row) * cy),
    ))
    k_eff = max(1, min(k_max, int(math.ceil(corner_d / step)) + 1))
    n_sectors = max(64, int(math.ceil(2 * math.pi * k_eff)))
    d_theta = 2 * math.pi / n_sectors

    # 目标像元 → (sector, bin, elevation angle)，按扇区排序以便分块处理。
    theta = np.arctan2(dy_m, dx_m)
    sector = ((theta + math.pi) / d_theta).astype(np.int64) % n_sectors
    with np.errstate(invalid="ignore"):
        bin_k = np.ceil(dist / step).astype(np.int64)
    bin_k = np.clip(bin_k, 1, k_eff)
    target_z = z + float(target_height)
    alpha = np.arctan2(target_z - z_obs_total, dist)

    visible = np.zeros((h, w), dtype=bool)
    in_range = valid & (dist <= max_distance)
    cand = np.flatnonzero(in_range.ravel())
    vis_flat = visible.ravel()
    if cand.size:
        sec_flat = sector.ravel()
        order = np.argsort(sec_flat[cand], kind="stable")
        cand_sorted = cand[order]
        secs = sec_flat[cand_sorted]
        bounds = np.searchsorted(secs, np.arange(n_sectors + 1))
        bk = bin_k.ravel()[cand_sorted]
        al = alpha.ravel()[cand_sorted]

        # 扇区分块：每块同时算射线采样（≤ chunk x k_eff）与该块内目标的
        # 可见性 —— 峰值内存 O(chunk x k_eff)，不物化全扇区矩阵。
        js = np.arange(1, k_eff + 1, dtype=np.float64) * step
        for s0 in range(0, n_sectors, _VIEWSHED_SECTOR_CHUNK):
            checkpoint()  # science-v4 W10：扇区块边界取消点
            s1 = min(s0 + _VIEWSHED_SECTOR_CHUNK, n_sectors)
            thetas = -math.pi + (np.arange(s0, s1, dtype=np.float64) + 0.5) * d_theta
            sx = np.cos(thetas)[:, None] * js[None, :]
            sy = np.sin(thetas)[:, None] * js[None, :]
            sample_cols = obs_col + sx / cx
            sample_rows = obs_row + sy / cy
            terrain = _bilinear_sample(
                z, valid, sample_cols.ravel(), sample_rows.ravel()
            ).reshape(sample_cols.shape)
            beta = np.arctan2(terrain - z_obs_total, js[None, :])
            beta = np.where(np.isfinite(beta), beta, -np.inf)
            run_max = np.empty((s1 - s0, k_eff + 1))
            run_max[:, 0] = -np.inf
            np.maximum.accumulate(beta, axis=1, out=run_max[:, 1:])
            for s in range(s0, s1):
                lo, hi = bounds[s], bounds[s + 1]
                if lo == hi:
                    continue
                blocking = run_max[s - s0, np.clip(bk[lo:hi] - 1, 0, k_eff)]
                vis_flat[cand_sorted[lo:hi]] = al[lo:hi] >= blocking - _VIEWSHED_ANGLE_TOL
    vis_flat.reshape(h, w)[
        int(round(np.clip(obs_row, 0, h - 1))),
        int(round(np.clip(obs_col, 0, w - 1)))] = True

    n_valid = int(valid.sum())
    visible_fraction = float(visible.sum()) / n_valid if n_valid else 0.0
    result = {
        "visible": visible,
        "visible_fraction": round(visible_fraction, 6),
        "visible_area_m2": float(visible.sum()) * cx * cy,
    }
    meta = _meta_base(
        "terrain.viewshed", valid,
        observer_row_col=(round(obs_row, 6), round(obs_col, 6)),
        observer_height=float(observer_height),
        target_height=float(target_height),
        max_distance_m=float(max_distance),
        cell_size=cy, cell_size_x=cx,
        earth_curvature_refraction="not applied (flat-earth line of sight)",
        method=(
            "angular sector sweep: target visible iff its elevation angle exceeds"
            " the running max terrain angle along its sector ray (1-cell bilinear"
            " sampling); tangency counts as visible (tol 1e-12 rad)"),
        edge_policy=(
            "viewshed edge policy: bilinear ray samples need 4 valid corner"
            " neighbors; invalid samples do not block sight; nodata target cells"
            " are not visible and are excluded from visible_fraction"),
        visible_fraction_basis="visible cells / valid cells",
    )
    return result, meta


# ── 6. D8 流向 / 汇流累积（拓扑序）───────────────────────────────────


def _neighbor_distances(cx: float, cy: float) -> np.ndarray:
    dist = np.empty(8)
    for idx, (_, _, dr, dc) in enumerate(_D8_NEIGHBORS):
        dx = abs(dc) * cx
        dy = abs(dr) * cy
        dist[idx] = math.hypot(dx, dy)
    return dist


def d8_flow(
    dem: np.ndarray, cell_size: float,
    cell_size_x: Optional[float] = None,
    nodata: Optional[float] = None,
    *,
    flat_routing: str = "none",
    flat_epsilon: float = DEFAULT_FLAT_EPSILON,
) -> Tuple[Dict[str, np.ndarray], Dict[str, Any]]:
    """D8 单向流（ESRI 2 的幂编码；O'Callaghan & Mark / Tarboton 1997 语境）。

    语义（精确）：

    - 坡降 slope_k = (z − z_k) / dist(中心, k)，dist 按 x/y 像元地面
      尺寸（各向异性地理栅格可区分）取米制欧氏距离；
    - 取最大**严格为正**坡降；并列最陡 → 最低索引邻域（E=1 起的编码序，
      确定性裁决）；
    - 无严格更低邻域（洼地/平地/边界外流）→ 编码 0 = sink/outlet
      （boundary = outlet）；
    - nodata 邻域不参与；全 nodata 输入 → NoValidObservations。

    平地路由（``flat_routing``，V3 additive 参数）：

    - ``"none"``（默认）：行为与历史版本一致 —— 平地/洼地即汇（code 0），
      不发明路由（防拓扑环）；
    - ``"epsilon"``：先经 ``fill_depressions(epsilon=flat_epsilon)``（Barnes
      2014 Priority-Flood 机制，terrain.sink_fill 同款）得到严格单调可排
      的填充面，再在其上路由 —— 平地/洼地获得 epsilon 梯度并排向溢流
      出口。``result["dem"]`` 即该填充面（汇流累积的拓扑序要求接收者
      在**同一表面**上严格更低）；meta 披露路由模式与填充像元数。
    """
    if cell_size <= 0 or (cell_size_x is not None and cell_size_x <= 0):
        raise ValueError("cell sizes must be positive")
    cx = float(cell_size_x if cell_size_x is not None else cell_size)
    cy = float(cell_size)
    if flat_routing not in ("none", "epsilon"):
        raise ValueError(
            f"flat_routing must be 'none' or 'epsilon' (got {flat_routing!r})")
    if flat_routing == "epsilon" and not (float(flat_epsilon) > 0):
        raise ValueError(
            f"flat_epsilon must be > 0 when flat_routing='epsilon' "
            f"(got {flat_epsilon!r})")
    z, valid = _prepare(dem, nodata)

    if flat_routing == "epsilon":
        filled, fill_meta = fill_depressions(
            z, cy, cell_size_x=cx, epsilon=float(flat_epsilon), nodata=nodata)
        z_route = filled
    else:
        fill_meta = None
        z_route = z
    h, w = z_route.shape
    dists = _neighbor_distances(cx, cy)

    direction = np.zeros((h, w), dtype=np.int16)
    best_slope = np.zeros((h, w), dtype=np.float64)
    for idx, (_, code, dr, dc) in enumerate(_D8_NEIGHBORS):
        zs = np.full_like(z_route, np.nan)
        vs = np.zeros_like(valid)
        r0, r1 = max(0, -dr), min(h, h - dr)
        c0, c1 = max(0, -dc), min(w, w - dc)
        zs[r0:r1, c0:c1] = z_route[r0 + dr:r1 + dr, c0 + dc:c1 + dc]
        vs[r0:r1, c0:c1] = valid[r0 + dr:r1 + dr, c0 + dc:c1 + dc]
        slope = np.where(vs, (z_route - zs) / dists[idx], -np.inf)
        # 严格 >：并列最陡保留先遍历（最低索引）邻域。
        take = valid & (slope > best_slope)
        best_slope = np.where(take, slope, best_slope)
        direction = np.where(take, np.int16(code), direction)

    # 严格更低邻域才构成接收者：best_slope > 0。
    receiver = np.full(h * w, -1, dtype=np.int64)
    has_recv = valid & (best_slope > 0)
    rows_f, cols_f = np.nonzero(has_recv)
    for idx, (_, code, dr, dc) in enumerate(_D8_NEIGHBORS):
        sel = direction[rows_f, cols_f] == code
        if sel.any():
            rr = rows_f[sel] + dr
            cc = cols_f[sel] + dc
            receiver[rows_f[sel] * w + cols_f[sel]] = rr * w + cc

    result = {
        "direction": direction,          # ESRI code; 0 = sink/outlet/nodata
        "receiver": receiver,            # flat index of receiver; -1 = none
        "valid": valid,
        "dem": z_route,                  # 路由面高程（flow_accumulation 拓扑序用；
                                         # flat_routing="epsilon" 时 = epsilon 填充面）
    }
    if flat_routing == "epsilon":
        flats_note = (
            "flat_routing='epsilon': routed on the Priority-Flood epsilon-filled "
            f"surface (Barnes et al. 2014, epsilon={float(flat_epsilon):g} z-units "
            "per cell, terrain.sink_fill machinery); flats/pits drain toward their "
            "spill outlet instead of staying sinks (code 0)")
        routing_meta: Dict[str, Any] = {
            "flat_routing": "epsilon",
            "flat_epsilon": float(flat_epsilon),
            "filled_cell_count": int(fill_meta["filled_cell_count"]),
            "dem_note": (
                "result['dem'] is the epsilon-filled routing surface: "
                "topological accumulation requires receivers strictly lower "
                "on the same surface"),
        }
    else:
        flats_note = (
            "flats/pits are sinks (code 0) under the default flat_routing='none'; "
            "flat_routing='epsilon' routes flats via the Barnes-2014 epsilon-"
            "filled surface instead")
        routing_meta = {"flat_routing": "none"}
    meta = _meta_base(
        "terrain.flow_d8", valid,
        cell_size=cy, cell_size_x=cx,
        encoding="ESRI powers-of-two: 1=E, 2=SE, 4=S, 8=SW, 16=W, 32=NW, 64=N, 128=NE; 0 = sink/outlet (no strictly lower in-grid neighbor)",
        tie_break="steepest-descent ties resolved to the lowest-index neighbor (E first)",
        flats=flats_note,
        boundary="grid boundary is the outlet: flow that would leave the grid terminates (cells only route to in-grid neighbors)",
        **routing_meta,
    )
    return result, meta


def flow_accumulation(
    d8: Dict[str, np.ndarray],
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """D8 汇流累积（上游贡献像元数，**不含自身**；起点 0）。

    拓扑序：按高程降序处理（接收者必严格更低 → 处理某像元时其全部
    上游已终结）；排序 O(N log N) + 单遍 O(N) 松弛。输入为
    ``d8_flow`` 的结果 dict（复用其 direction/receiver/dem）。
    """
    direction = d8["direction"]
    receiver = d8["receiver"]
    valid = d8["valid"]
    z = np.asarray(d8["dem"], dtype=np.float64)
    w = direction.shape[1]
    acc = np.zeros(direction.shape, dtype=np.int64)

    cells = np.flatnonzero(valid.ravel())
    # 高程降序（同高程按 (row, col) 兜底 —— 确定性）；接收者严格更低，
    # 故处理到某像元时其全部上游必然已处理。
    elev = z.ravel()[cells]
    rows_c = cells // w
    cols_c = cells % w
    order = np.lexsort((cols_c, rows_c, -elev))
    contrib = cells[order]
    recv_flat = receiver[contrib]
    acc_flat = acc.ravel()
    for src, dst in zip(contrib.tolist(), recv_flat.tolist()):
        if dst >= 0:
            acc_flat[dst] += acc_flat[src] + 1
    meta = {
        "algorithm": "terrain.flow_accumulation",
        "method": "topological accumulation in descending elevation order (D8 single-flow)",
        "counting_convention": "number of upstream contributing cells, self excluded (outlet of a full N-cell basin = N-1)",
        "cells_valid": int(valid.sum()),
    }
    return acc, meta


def upstream_watershed(
    d8: Dict[str, np.ndarray],
    pour_cells: Sequence[Tuple[int, int]],
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """逆 D8 BFS：汇入任一 pour point 的全部上游像元掩膜（含 pour point 自身）。"""
    receiver = d8["receiver"]
    valid = d8["valid"]
    h, w = valid.shape
    seeds: List[int] = []
    for rc in pour_cells:
        r, c = int(round(float(rc[0]))), int(round(float(rc[1])))
        if not (0 <= r < h and 0 <= c < w):
            raise ValueError(
                f"pour point {(r, c)} is outside the grid (shape {(h, w)})")
        if not valid[r, c]:
            raise NoValidObservations(
                f"pour point {(r, c)} is a nodata cell — watershed undefined")
        seeds.append(r * w + c)

    # 上游邻接表：孩子按 receiver 分组（向量化 BFS 用 searchsorted）。
    flowing = np.flatnonzero(receiver >= 0)
    parents = receiver[flowing]
    order = np.argsort(parents, kind="stable")
    sorted_cells = flowing[order]
    sorted_parents = parents[order]

    mask_flat = np.zeros(h * w, dtype=bool)
    mask_flat[np.asarray(seeds, dtype=np.int64)] = True
    frontier = np.asarray(seeds, dtype=np.int64)
    while frontier.size:
        lo = np.searchsorted(sorted_parents, frontier, side="left")
        hi = np.searchsorted(sorted_parents, frontier, side="right")
        counts = hi - lo
        total = int(counts.sum())
        if total == 0:
            break
        offsets = np.arange(total) - np.repeat(np.cumsum(counts) - counts, counts)
        children = sorted_cells[np.repeat(lo, counts) + offsets]
        children = children[~mask_flat[children]]
        if children.size:
            mask_flat[children] = True
        frontier = children

    mask = mask_flat.reshape(h, w)
    meta = {
        "algorithm": "terrain.watershed",
        "method": "reverse-D8 BFS over upstream adjacency (all cells whose flow path reaches a pour point)",
        "pour_cells": [(int(r), int(c)) for r, c in pour_cells],
        "cells_in_watershed": int(mask.sum()),
        "includes_pour_point": True,
    }
    return mask, meta


# ── 8. 等值线（matplotlib Agg marching squares）──────────────────────


def _resolve_levels(
    z: np.ndarray, valid: np.ndarray,
    levels: Optional[Sequence[float]], n_levels: int, interval: Optional[float],
) -> Tuple[List[float], str]:
    vmin = float(z[valid].min())
    vmax = float(z[valid].max())
    if levels is not None and len(levels) > 0:
        lv = sorted(float(v) for v in levels)
        return lv, "explicit levels"
    if interval is not None and interval > 0:
        # 起点取 vmin：levels = vmin, vmin+interval, ... ≤ vmax（含端点级）。
        count = int(math.floor((vmax - vmin) / interval + 0.5)) + 1
        lv = [vmin + k * interval for k in range(count)]
        lv = [v for v in lv if v <= vmax + interval * 1e-9]
        return lv, "interval-based levels from vmin"
    n = max(2, int(n_levels))
    step_l = (vmax - vmin) / (n - 1) if n > 1 and vmax > vmin else 0.0
    if step_l <= 0:
        return [vmin], "degenerate single level (constant surface)"
    return [vmin + k * step_l for k in range(n)], "n_levels equal-interval"


def extract_contours(
    dem: np.ndarray,
    transform: Optional[Sequence[float]] = None,
    levels: Optional[Sequence[float]] = None,
    n_levels: int = 10,
    interval: Optional[float] = None,
    nodata: Optional[float] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """等值线提取 → GeoJSON-like FeatureCollection（LineString + "level"）。

    - matplotlib Agg（无显示环境）：marching squares；
    - nodata/非有限像元 → NaN（等值线在该处断开）；
    - 水平选取优先级：显式 levels > interval（自 vmin 起等间隔，含端点级）
      > n_levels（vmin..vmax 等间隔）；
    - 变换 (a,b,c,d,e,f)：数组 (col,row) → 世界 (x,y)
      x = a·col + b·row + c, y = d·col + e·row + f；缺省为单位像元坐标；
    - 空段（如 level == vmax 的退化等值线）不产要素；
      端点级（level == vmin/vmax）沿线边界绘制 —— 与 mpl 行为一致。
    """
    z, valid = _prepare(dem, nodata)
    z_plot = np.where(valid, z, np.nan)
    lv, policy = _resolve_levels(z, valid, levels, n_levels, interval)
    lv = [v for v in lv if math.isfinite(v)]
    if not lv:
        raise NoValidObservations("no finite contour levels could be resolved")

    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    fig = Figure()
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    cs = ax.contour(z_plot, levels=lv)
    fig.clear()

    identity = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
    tf = tuple(float(v) for v in transform[:6]) if transform is not None else identity

    features: List[Dict[str, Any]] = []
    levels_drawn: List[float] = []
    for level, segments in zip(cs.levels, cs.allsegs):
        emitted = 0
        for seg in segments:
            if len(seg) < 2:
                continue
            cols = seg[:, 0]
            rows = seg[:, 1]
            xs, ys = _cell_to_world(tf, cols, rows)
            coords = [
                [round(float(x), 6), round(float(y), 6)]
                for x, y in zip(xs, ys)
            ]
            if len(coords) < 2:
                continue
            features.append({
                "type": "Feature",
                "properties": {"level": round(float(level), 6)},
                "geometry": {"type": "LineString", "coordinates": coords},
            })
            emitted += 1
        if emitted:
            levels_drawn.append(float(level))

    fc: Dict[str, Any] = {"type": "FeatureCollection", "features": features}
    meta = {
        "algorithm": "terrain.contours",
        "method": "matplotlib Agg marching-squares contours on the cell grid; vertices mapped to world coords via the raster transform",
        "levels_policy": policy,
        "levels_requested": [round(float(v), 6) for v in lv],
        "levels_drawn": [round(float(v), 6) for v in levels_drawn],
        "feature_count": len(features),
        "transform": [round(float(v), 9) for v in tf],
        "nodata_breaks_lines": True,
        "cells_valid": int(valid.sum()),
        "cells_total": int(valid.size),
    }
    return fc, meta


# ── 组合便捷入口（工具层用）─────────────────────────────────────────


def watershed(
    dem: np.ndarray, cell_size: float,
    pour_points: Sequence[Tuple[float, float]],
    transform: Optional[Sequence[float]] = None,
    cell_size_x: Optional[float] = None,
    nodata: Optional[float] = None,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """DEM + pour point(s) → 上游贡献掩膜（d8_flow + upstream BFS 组合）。

    pour_points 为 (row, col) 数组坐标；或（给 transform 时）(x, y) 世界
    坐标 —— 自动换算到最近像元中心。
    """
    cells: List[Tuple[int, int]] = []
    for pt in pour_points:
        if transform is not None:
            col, row = _world_to_cell(transform, float(pt[0]), float(pt[1]))
        else:
            row, col = float(pt[0]), float(pt[1])
        cells.append((int(round(row)), int(round(col))))
    d8, d8_meta = d8_flow(dem, cell_size, cell_size_x=cell_size_x, nodata=nodata)
    mask, w_meta = upstream_watershed(d8, cells)
    meta = dict(w_meta)
    meta["d8"] = {k: d8_meta[k] for k in ("encoding", "tie_break", "flats", "boundary")}
    meta["algorithm"] = "terrain.watershed"
    return mask, meta


# ══ Foundation V2（A5）：水文与地貌量测扩展 ══════════════════════════


def _validate_cell_sizes(
    cell_size: float, cell_size_x: Optional[float],
) -> Tuple[float, float]:
    if cell_size is None or cell_size <= 0 or (
            cell_size_x is not None and cell_size_x <= 0):
        raise ValueError(
            f"cell sizes must be positive (got cell_size={cell_size!r}, "
            f"cell_size_x={cell_size_x!r})")
    return float(cell_size), float(cell_size_x if cell_size_x is not None else cell_size)


def _guard_cells(shape: Tuple[int, int], algorithm: str,
                 cap: Optional[int] = None) -> None:
    """规模护栏：在分配任何大数组**之前**拒绝（estimate-before-allocate）。

    cap 缺省读模块常量 ``MAX_HYDRO_CELLS``（调用时读取 —— 测试可注入）。
    """
    limit = MAX_HYDRO_CELLS if cap is None else cap
    n = int(shape[0]) * int(shape[1])
    if n > limit:
        raise ResourceScaleMismatch(
            f"{algorithm}: grid {tuple(shape)} has {n} cells above the safe "
            f"envelope ({limit})",
            estimated=f"{n} cells", limit=f"{limit} cells",
            correction_hint="tile the DEM or coarsen resolution before this analysis")


def _topology_order(z: np.ndarray, valid: np.ndarray,
                    descending: bool) -> np.ndarray:
    """有效像元的确定性拓扑序（高程降/升序；同高程按 (row, col) 兜底）。"""
    h, w = z.shape
    cells = np.flatnonzero(valid.ravel())
    rows_c = cells // w
    cols_c = cells % w
    elev = z.ravel()[cells]
    key = -elev if descending else elev
    return cells[np.lexsort((cols_c, rows_c, key))]


def _child_table(receiver: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """逆 D8 邻接表：按 parent flat index 排序的 (children, parents)。"""
    flowing = np.flatnonzero(receiver >= 0)
    parents = receiver[flowing]
    order = np.argsort(parents, kind="stable")
    return flowing[order], parents[order]


# ── V2-1. Priority-Flood 填洼（Barnes et al. 2014）───────────────────


def fill_depressions(
    dem: np.ndarray, cell_size: float,
    cell_size_x: Optional[float] = None,
    *,
    epsilon: float = 0.0,
    nodata: Optional[float] = None,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """Priority-Flood 填洼（Barnes, Lehman & Mulla 2014；O(N log N) heapq）。

    种子 = 网格边界上的有效像元 + 与 nodata/非有限像元相邻的有效像元
    （nodata 视作排水边界）；自种子向内漫水，洼地被抬升到溢流高程。
    ``epsilon > 0`` 时逐像元抬升 ``max(z[n], filled[cur] + epsilon)`` →
    填后表面严格单调可排（平地获得 epsilon 梯度，无二次洼地）；
    ``epsilon == 0``（默认）为纯填洼：平地/洼地仍为汇（与 d8_flow 的
    「不发明路由」语义衔接 —— 先 fill 再 d8 是推荐组合）。

    meta 报告 filled_volume（Σ(filled−z)，z_units·m²）、filled_cell_count、
    max_fill_depth。确定性：堆并列用自增计数器裁决（结果本身与弹出序无关 ——
    Priority-Flood 填充面是唯一的）。护栏：网格 ≤ 50M 像元（先拒绝后分配）。
    """
    cy, cx = _validate_cell_sizes(cell_size, cell_size_x)
    if epsilon < 0:
        raise ValueError(f"epsilon must be >= 0 (got {epsilon!r})")
    z_raw = np.asarray(dem)
    if getattr(z_raw, "ndim", 0) != 2:
        raise NoValidObservations(
            f"DEM must be a 2D array (got ndim {getattr(z_raw, 'ndim', 0)})")
    _guard_cells(z_raw.shape, "terrain.sink_fill")
    z, valid = _prepare(dem, nodata)
    h, w = z.shape
    del z_raw

    filled = z.copy()
    # 种子 = 网格边界上的有效像元 + 与无效像元（nodata/非有限）相邻的
    # 有效像元（8 邻域；网格外/nodata 视作排水出口 —— 不发明填充边界值）。
    invalid = ~valid
    border = ~_border_inside(h, w)
    near_invalid = np.zeros((h, w), dtype=bool)
    for _, _, dr, dc in _D8_NEIGHBORS:
        r0, r1 = max(0, -dr), min(h, h - dr)
        c0, c1 = max(0, -dc), min(w, w - dc)
        shifted = np.zeros((h, w), dtype=bool)
        shifted[r0:r1, c0:c1] = invalid[r0 + dr:r1 + dr, c0 + dc:c1 + dc]
        near_invalid |= shifted
    seed = valid & (border | near_invalid)
    del invalid, border, near_invalid

    known = seed.copy()
    heap: List[Tuple[float, int, int]] = []
    counter = 0
    for flat in np.flatnonzero(seed.ravel()).tolist():
        heapq.heappush(heap, (float(filled.ravel()[flat]), counter, flat))
        counter += 1

    n_cells = h * w
    _pop_count = 0
    while heap:
        # science-v4 W10：堆循环取消检查点（64K 弹出粒度，>10M 像元可中断）
        _pop_count += 1
        if _pop_count % 8192 == 0:
            checkpoint()
        elev, _, cur = heapq.heappop(heap)
        cur_r, cur_c = divmod(cur, w)
        for _, _, dr, dc in _D8_NEIGHBORS:
            nb_r, nb_c = cur_r + dr, cur_c + dc
            if not (0 <= nb_r < h and 0 <= nb_c < w):
                continue
            nb = nb_r * w + nb_c
            if not valid.ravel()[nb] or known.ravel()[nb]:
                continue
            known.ravel()[nb] = True
            target = elev + epsilon
            z_nb = float(z.ravel()[nb])
            filled.ravel()[nb] = z_nb if z_nb > target else target
            heapq.heappush(heap, (filled.ravel()[nb], counter, nb))
            counter += 1
    del known, heap

    lift = filled - z
    meta = _meta_base(
        "terrain.sink_fill", valid,
        cell_size=cy, cell_size_x=cx,
        epsilon=float(epsilon),
        method=(
            "Priority-Flood depression filling (Barnes et al. 2014, heapq); "
            "seeds = boundary + nodata-adjacent valid cells; epsilon>0 yields a "
            "monotonically draining surface"),
        edge_policy=(
            "sink-fill edge policy: cells adjacent to nodata/non-finite cells act "
            "as drainage seeds (nodata is an outlet); no padding values invented"),
        filled_volume=round(float(lift.sum()), 9),
        filled_volume_units="z_units * m2 (metric cells)",
        filled_cell_count=int(np.count_nonzero(lift > 0.0)),
        max_fill_depth=round(float(lift.max()) if valid.any() else 0.0, 9),
        grid_cells=int(n_cells),
    )
    return filled, meta


def _border_inside(h: int, w: int) -> np.ndarray:
    """True=非边界像元（边界一圈 False）。"""
    inside = np.ones((h, w), dtype=bool)
    inside[0, :] = False
    inside[-1, :] = False
    inside[:, 0] = False
    inside[:, -1] = False
    return inside


# ── V2-2. D∞ 多向流（Tarboton 1997）──────────────────────────────────

# D∞ 哨兵：有效像元但无严格下降（平地/洼地）→ 角度 -1（弧度）；
# 无效像元（nodata/非有限）→ NaN。诚实披露：函数内部**不做**填洼 ——
# 平地/洼地是哨兵 -1，推荐先 fill_depressions(epsilon>0) 再算 D∞。
_DINF_NO_FLOW = -1.0

# CCW（自东逆时针）排列的 _D8_NEIGHBORS 索引：E, NE, N, NW, W, SW, S, SE。
_DINF_CCW: Tuple[int, ...] = (0, 7, 6, 5, 4, 3, 2, 1)


def dinf_flow_direction(
    dem: np.ndarray, cell_size: float,
    cell_size_x: Optional[float] = None,
    nodata: Optional[float] = None,
) -> Tuple[Dict[str, np.ndarray], Dict[str, Any]]:
    """D∞ 多向流方向（Tarboton 1997，8 三角面最陡下降）。

    每像元在 8 个三角面（相邻两邻域与中心构成的平面）上解平面梯度：

    - 面内最陡下降方向落在面的角域内 → 取平面梯度方向与幅值；
    - 落在角域外 → 截断到较陡的边（该边邻域方向的坡降）；
    - 严格为正的下降才计流；面并列取最低面索引（确定性）；
    - 角度弧度制 ∈ [0, 2π)（数学约定：x=东、y=北，atan2(vy, vx)）；
    - 平地/洼地 → 角度 -1（哨兵；本函数**不填洼**，组合
      ``fill_depressions(epsilon>0)`` 先行是推荐用法）；nodata → NaN；
    - 边缘策略：缺任一角邻域的面跳过（只用可得邻域，不发明填充值）。

    返回 dict：angle/slope（幅值）、receiver_a/frac_a、receiver_b/frac_b
    （下游两邻域 flat 索引与角度比例分配权重；无流时 receiver=-1）。
    汇流分配 = 面内角度比例（β 靠近哪条边哪条边分得多；Tarboton 1997）。
    """
    cy, cx = _validate_cell_sizes(cell_size, cell_size_x)
    z_raw = np.asarray(dem)
    if getattr(z_raw, "ndim", 0) != 2:
        raise NoValidObservations(
            f"DEM must be a 2D array (got ndim {getattr(z_raw, 'ndim', 0)})")
    _guard_cells(z_raw.shape, "terrain.dinf_flow")
    z, valid = _prepare(dem, nodata)
    h, w = z.shape

    # 每个邻域的米制向量（东、北）与方位角（数学约定）。
    vec_x = np.empty(8)
    vec_y = np.empty(8)
    vec_d = np.empty(8)
    for idx, (_, _, dr, dc) in enumerate(_D8_NEIGHBORS):
        vec_x[idx] = dc * cx
        vec_y[idx] = -dr * cy
        vec_d[idx] = math.hypot(vec_x[idx], vec_y[idx])

    angle = np.full((h, w), np.nan)
    slope_out = np.full((h, w), np.nan)
    recv_a = np.full(h * w, -1, dtype=np.int64)
    recv_b = np.full(h * w, -1, dtype=np.int64)
    frac_a = np.zeros(h * w, dtype=np.float64)
    frac_b = np.zeros(h * w, dtype=np.float64)

    for k in range(8):
        i1 = _DINF_CCW[k]
        i2 = _DINF_CCW[(k + 1) % 8]
        _, _, dr1, dc1 = _D8_NEIGHBORS[i1]
        _, _, dr2, dc2 = _D8_NEIGHBORS[i2]
        z1 = np.full_like(z, np.nan)
        z2 = np.full_like(z, np.nan)
        v1 = np.zeros_like(valid)
        v2 = np.zeros_like(valid)
        r0, r1 = max(0, -dr1), min(h, h - dr1)
        c0, c1 = max(0, -dc1), min(w, w - dc1)
        z1[r0:r1, c0:c1] = z[r0 + dr1:r1 + dr1, c0 + dc1:c1 + dc1]
        v1[r0:r1, c0:c1] = valid[r0 + dr1:r1 + dr1, c0 + dc1:c1 + dc1]
        r0, r1 = max(0, -dr2), min(h, h - dr2)
        c0, c1 = max(0, -dc2), min(w, w - dc2)
        z2[r0:r1, c0:c1] = z[r0 + dr2:r1 + dr2, c0 + dc2:c1 + dc2]
        v2[r0:r1, c0:c1] = valid[r0 + dr2:r1 + dr2, c0 + dc2:c1 + dc2]

        facet = valid & v1 & v2
        if not facet.any():
            continue
        s1 = np.where(facet, (z - z1) / vec_d[i1], 0.0)
        s2 = np.where(facet, (z - z2) / vec_d[i2], 0.0)
        det = vec_x[i1] * vec_y[i2] - vec_x[i2] * vec_y[i1]
        with np.errstate(invalid="ignore", divide="ignore"):
            p = ((z1 - z) * vec_y[i2] - (z2 - z) * vec_y[i1]) / det
            q = (vec_x[i1] * (z2 - z) - vec_x[i2] * (z1 - z)) / det
        g = np.hypot(p, q)
        beta = np.arctan2(-q, -p)
        theta1 = math.atan2(vec_y[i1], vec_x[i1]) % (2.0 * math.pi)
        theta2 = math.atan2(vec_y[i2], vec_x[i2]) % (2.0 * math.pi)
        span = (theta2 - theta1) % (2.0 * math.pi)
        d_ang = (beta - theta1) % (2.0 * math.pi)

        # 面内 → 平面梯度；角域外 → 截断到较陡边（s 并列取边 1）。
        inside = (d_ang <= span) & (g > 0.0)
        facet_slope = np.where(inside, g, np.where(s1 >= s2, s1, s2))
        facet_angle = np.where(inside, beta, np.where(s1 >= s2, theta1, theta2))
        cand = facet & (facet_slope > 0)
        if not cand.any():
            continue

        rows_c, cols_c = np.nonzero(cand)
        ang = facet_angle[rows_c, cols_c] % (2.0 * math.pi)
        slp = facet_slope[rows_c, cols_c]
        # 面内分配比例（角度比例）；截断到边 → 全量走该边。
        d_in = (ang - theta1) % (2.0 * math.pi)
        w1 = np.clip(1.0 - d_in / span, 0.0, 1.0)
        w2 = 1.0 - w1
        # 面索引升序遍历 + 严格 >：并列最陡面保留最低面索引（确定性）。
        prev = slope_out[rows_c, cols_c]
        upd = np.isnan(prev) | (slp > prev)
        rows_u = rows_c[upd]
        cols_u = cols_c[upd]
        flat_u = rows_u * w + cols_u
        angle[rows_u, cols_u] = ang[upd]
        slope_out[rows_u, cols_u] = slp[upd]
        recv_a[flat_u] = (rows_u + dr1) * w + (cols_u + dc1)
        recv_b[flat_u] = (rows_u + dr2) * w + (cols_u + dc2)
        frac_a[flat_u] = w1[upd]
        frac_b[flat_u] = w2[upd]

    # 无严格下降的有效像元 → 哨兵 -1（平地/洼地/边界外流终止）。
    no_flow = valid & np.isnan(angle)
    angle[no_flow] = _DINF_NO_FLOW
    slope_out[no_flow] = 0.0

    result = {
        "angle": angle,        # 弧度 [0, 2π)；-1 = 平地/洼地（无下降）；NaN = 无效
        "slope": slope_out,    # 最陡下降幅值（dz/dm）；0 = 无流；NaN = 无效
        "receiver_a": recv_a,  # 下游邻域 1 flat 索引；-1 = 无
        "receiver_b": recv_b,  # 下游邻域 2 flat 索引；-1 = 无
        "frac_a": frac_a,
        "frac_b": frac_b,
        "valid": valid,
        "dem": z,
    }
    meta = _meta_base(
        "terrain.dinf_flow", valid,
        cell_size=cy, cell_size_x=cx,
        method=(
            "D-infinity flow (Tarboton 1997): steepest descent over 8 triangular "
            "facets; angle in radians [0, 2*pi), math convention (x=east, y=north)"),
        no_flow_sentinel="-1.0 rad = valid cell with no strictly positive descent (flat/pit); NaN = nodata",
        flow_split="fractional split between the two bracketing facet edges, proportional to in-facet angle",
        flats=(
            "no depression filling inside (honest: flats/pits keep the -1 sentinel); "
            "compose with fill_depressions(epsilon>0) first for monotone drainage"),
        edge_policy=(
            "dinf edge policy: facets needing an out-of-grid or nodata corner are "
            "skipped; only available neighbors are used"),
    )
    return result, meta


def dinf_flow_accumulation(
    dinf: Dict[str, np.ndarray],
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """D∞ 汇流累积（比例分流；拓扑序 = 高程降序）。

    每像元向下游两邻域按角度比例分配 ``(acc + 1)``（上游贡献像元数，
    自身计 1）；接收者严格更低 → 单遍 O(N) 松弛（排序 O(N log N)）。
    计数口径与 d8 版一致：全流域出口处累积 = N−1（分数之和，比例分流
    下为期望值）。守恒性质：Σacc = N_valid − sink 数（流只终止于汇）。
    """
    valid = dinf["valid"]
    z = np.asarray(dinf["dem"], dtype=np.float64)
    recv_a = dinf["receiver_a"]
    recv_b = dinf["receiver_b"]
    frac_a = dinf["frac_a"]
    frac_b = dinf["frac_b"]
    acc = np.zeros(z.shape, dtype=np.float64)
    acc_flat = acc.ravel()

    contrib = _topology_order(z, valid, descending=True)
    ra = recv_a[contrib]
    rb = recv_b[contrib]
    fa = frac_a[contrib]
    fb = frac_b[contrib]
    for i, src in enumerate(contrib.tolist()):
        # science-v4 W10：拓扑循环取消检查点（64K 像元粒度）
        if i % 65536 == 65535:
            checkpoint()
        unit = acc_flat[src] + 1.0
        if ra[i] >= 0 and fa[i] > 0.0:
            acc_flat[ra[i]] += unit * fa[i]
        if rb[i] >= 0 and fb[i] > 0.0:
            acc_flat[rb[i]] += unit * fb[i]

    meta = {
        "algorithm": "terrain.dinf_accumulation",
        "method": "topological accumulation in descending elevation order (D-infinity fractional splitting)",
        "counting_convention": (
            "fractional upstream contributing cells (self included in each unit "
            "passed on; outlet of a full N-cell single-sink basin sums to N-1)"),
        "cells_valid": int(valid.sum()),
    }
    return acc, meta


# ── V2-3. 流程长度（D8 编码复用）─────────────────────────────────────


def flow_length(
    d8: Dict[str, np.ndarray],
    mode: str = "downstream",
    cell_size: float = 1.0,
    cell_size_x: Optional[float] = None,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """流程长度（米；基于 ``d8_flow`` 的接收者索引，各向异性像元感知）。

    - ``mode="downstream"``：每像元沿流路到出口的累计距离
      （升序高程拓扑松弛：dist[src] = step + dist[receiver]）；
    - ``mode="upstream"``：每像元距其最远分水岭（山脊源）的累计距离
      （降序高程松弛：len[p] = max(len[child] + step(child→p))；
      **max** 口径 —— 文档化：长度 = 最长上游路径）；
    - 步长 = hypot(Δcol·cx, Δrow·cy)（米制；地理栅格由调用方传入
      cos(lat) 修正后的 cx）；
    - 无接收者（汇/出口）→ downstream 距离 0；无上游 → upstream 0。
    """
    cy, cx = _validate_cell_sizes(cell_size, cell_size_x)
    if mode not in ("downstream", "upstream"):
        raise ValueError(f"mode must be 'downstream' or 'upstream' (got {mode!r})")
    direction = d8["direction"]
    receiver = d8["receiver"]
    valid = d8["valid"]
    z = np.asarray(d8["dem"], dtype=np.float64)
    h, w = direction.shape
    _guard_cells((h, w), "terrain.flow_length")

    n = h * w
    flat = np.arange(n, dtype=np.int64)
    has_recv = receiver >= 0
    rows = flat // w
    cols = flat % w
    recv_r = np.where(has_recv, receiver, flat) // w
    recv_c = np.where(has_recv, receiver, flat) % w
    step = np.hypot((recv_c - cols) * cx, (recv_r - rows) * cy)
    step = np.where(has_recv, step, 0.0)

    out = np.zeros(n, dtype=np.float64)
    if mode == "downstream":
        order = _topology_order(z, valid, descending=False)
        for src in order.tolist():
            dst = receiver[src]
            if dst >= 0:
                out[src] = step[src] + out[dst]
    else:
        order = _topology_order(z, valid, descending=True)
        children, parents = _child_table(receiver)
        for parent in order.tolist():
            lo = np.searchsorted(parents, parent, side="left")
            hi = np.searchsorted(parents, parent, side="right")
            if hi > lo:
                out[parent] = float(np.max(out[children[lo:hi]] + step[children[lo:hi]]))

    result = out.reshape(h, w)
    where_valid = np.where(valid, result, np.nan)
    meta = _meta_base(
        "terrain.flow_length", valid,
        cell_size=cy, cell_size_x=cx,
        mode=mode,
        method=(
            "flow length along D8 receivers: downstream = distance to outlet, "
            "upstream = max distance from ridgeline source (topological relaxation)"),
        length_convention=(
            "downstream: sum of metric cell-to-cell steps to the outlet (sinks = 0); "
            "upstream: MAX upstream path length from the farthest ridge source"),
        max_length_m=round(float(np.nanmax(where_valid)) if valid.any() else 0.0, 6),
        mean_length_m=round(float(np.nanmean(where_valid)) if valid.any() else 0.0, 6),
    )
    return where_valid, meta


# ── V2-4. 河网提取与 Strahler 分级（Strahler 1957）───────────────────


def extract_streams(
    flow_accum: np.ndarray, threshold: float,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """河网像元掩膜：``flow_accum >= threshold``（确定性阈值口径）。

    threshold 单位 = 上游贡献像元数（与 flow_accumulation 同口径）。
    全部输入像元参与（NaN → 非河网）；返回 bool 掩膜 + 统计 meta。
    """
    acc = np.asarray(flow_accum, dtype=np.float64)
    if acc.ndim != 2 or acc.size == 0:
        raise NoValidObservations(
            f"flow_accum must be a non-empty 2D array (got shape {tuple(acc.shape)})")
    if not (threshold >= 1):
        raise ValueError(
            f"threshold must be >= 1 upstream cell (got {threshold!r})")
    finite = np.isfinite(acc)
    mask = finite & (acc >= float(threshold))
    meta = {
        "algorithm": "terrain.streams",
        "method": "stream cells = flow accumulation >= threshold (D8/D∞ accumulation input)",
        "threshold": float(threshold),
        "stream_cells": int(mask.sum()),
        "cells_total": int(acc.size),
        "cells_valid_accum": int(finite.sum()),
    }
    return mask, meta


def stream_order(
    d8: Dict[str, np.ndarray], flow_accum: np.ndarray, threshold: float,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """Strahler 河流分级（Strahler 1957；拓扑序 = 高程降序）。

    河网像元 = ``flow_accum >= threshold``。处理序（降序高程）保证某像元
    被处理时其全部上游河段已终结：

    - 无上游河段（源头）→ order 1；
    - 上游最高级 m 唯一 → order m；并列（≥2 条 m 级汇入）→ order m+1
      （二元树汇流升级的 Strahler 语义）；
    - 非河网像元 → 0。

    meta 报告 order_distribution（各等级河网像元数）与 max_order。
    """
    acc = np.asarray(flow_accum, dtype=np.float64)
    direction = d8["direction"]
    receiver = d8["receiver"]
    valid = d8["valid"]
    z = np.asarray(d8["dem"], dtype=np.float64)
    h, w = direction.shape
    if acc.shape != (h, w):
        raise ValueError(
            f"flow_accum shape {acc.shape} does not match d8 grid {(h, w)}")
    if not (threshold >= 1):
        raise ValueError(f"threshold must be >= 1 upstream cell (got {threshold!r})")
    _guard_cells((h, w), "terrain.strahler")

    streams = np.isfinite(acc) & (acc >= float(threshold)) & valid
    order = np.zeros((h, w), dtype=np.int16)
    children, parents = _child_table(receiver)
    for parent in _topology_order(z, valid & streams, descending=True).tolist():
        lo = np.searchsorted(parents, parent, side="left")
        hi = np.searchsorted(parents, parent, side="right")
        if hi == lo:
            order.ravel()[parent] = 1  # 源头
            continue
        child_orders = order.ravel()[children[lo:hi]]
        child_orders = child_orders[child_orders > 0]
        if child_orders.size == 0:
            order.ravel()[parent] = 1
            continue
        m = int(child_orders.max())
        order.ravel()[parent] = m if int(np.count_nonzero(child_orders == m)) == 1 else m + 1

    codes = order[streams]
    unique, counts = np.unique(codes, return_counts=True)
    meta = _meta_base(
        "terrain.strahler", valid,
        threshold=float(threshold),
        method=(
            "Strahler stream order on cells with accumulation >= threshold: order = "
            "max(upstream) if the max is unique else max+1 (Strahler 1957)"),
        processing_order="descending elevation (receivers are strictly lower; deterministic (row, col) tie-break)",
        stream_cells=int(streams.sum()),
        max_order=int(codes.max()) if codes.size else 0,
        order_distribution={str(int(o)): int(n) for o, n in zip(unique, counts)},
    )
    return order, meta


# ── Science V4（W8）：breaching / HAND / Shreve / Pfafstetter ─────────────

def breach_depressions(
    dem: np.ndarray, cell_size: float,
    cell_size_x: Optional[float] = None,
    *,
    nodata: Optional[float] = None,
    max_breach_depth: Optional[float] = None,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """Depression breaching（最小代价切沟）：切穿洼地出口而非整体填平。

    算法（确定性）：
    1. Priority-Flood 填洼（复用 :func:`fill_depressions`）识别洼地像元
       （filled > z）与其溢流出口；
    2. 逐洼地：从 pit 沿**填后表面**的 D8 接收者链走到出口（填后表面
       天然经溢流口排水）；沿路径切沟 —— 每个路径像元取
       ``min(原高程, 上游切沟高程 − epsilon)``，形成 pit→出口 的严格
       下降通道；
    3. 洼地非路径像元保持原高程（对比 fill：长浅洼地不被整体抬升）。

    ``max_breach_depth`` 限制单像元最大下切深度（超过的洼地回退为填洼，
    诚实计数披露）。返回 ``(breached, meta)``（breached_cells /
    carved_volume / n_depressions / fallback_filled_cells）。
    """
    z, valid = _prepare(dem, nodata)
    h, w = z.shape
    _guard_cells((h, w), "terrain.breach")
    filled, fill_meta = fill_depressions(z, cell_size, cell_size_x, nodata=nodata)
    relief = float(np.nanmax(z[valid]) - np.nanmin(z[valid])) if valid.any() else 1.0
    eps = max(1e-9, 1e-6 * relief)
    breached = z.copy()
    # 洼地像元（被填洼抬升者）
    depressed = valid & (filled > z + 1e-12)
    if not depressed.any():
        meta = _meta_base(
            "terrain.breach", valid,
            method="priority-flood identified no depressions; identity copy",
            breached_cells=0, carved_volume=0.0, n_depressions=0,
            fallback_filled_cells=0,
        )
        return breached, meta
    # 路由面用 **epsilon 填洼**（严格可排）——纯填洼的平地无法给 pit→出口
    # 接收者链（D8 flat_routing=none 语义），路径走不到出口。
    routing, _ = fill_depressions(z, cell_size, cell_size_x, epsilon=eps, nodata=nodata)
    d8, _ = d8_flow(routing, cell_size, cell_size_x, nodata=nodata)
    receiver = d8["receiver"]
    receiver_valid = d8["valid"]
    flat = receiver.ravel()
    rvalid = receiver_valid.ravel()
    zflat = z.ravel()
    fflat = filled.ravel()
    bflat = breached.ravel()
    dep_flat = depressed.ravel()

    # pit = 洼地内的原始高程局部极小（8 邻域；洼地水位抬升的原点）
    from scipy.ndimage import minimum_filter

    z_pad = np.where(valid, z, np.inf)
    z_min_nb = minimum_filter(z_pad, size=3, mode="nearest")
    pit_mask = depressed & (z <= z_min_nb + 1e-12)
    n_depressions = int(pit_mask.sum())
    breached_cells = 0
    carved_volume = 0.0
    fallback_filled_cells = 0
    max_depth_hit = 0.0
    for pit in np.nonzero(pit_mask.ravel())[0]:
        # epsilon 填面上接收者链 pit → 出口（离开洼地即出口）
        path = [int(pit)]
        cur = int(pit)
        seen = {cur}
        while rvalid[cur]:
            nxt = int(flat[cur])
            if nxt == cur or nxt in seen:
                break
            path.append(nxt)
            seen.add(nxt)
            cur = nxt
            if not dep_flat[cur]:
                break
        # 路径切沟：pit→出口方向严格**下降**（切沟低于 pit 高程 − k·eps），
        # 使 pit 及沿途洼地获得通往边界的下降链；近 pit 像元原高程已低于
        # 切沟线则保持原高程（min 语义 → 最小开挖量）。
        chain = zflat[path[0]]
        depths = []
        too_deep = False
        for k_i in range(1, len(path)):
            chain = chain - eps
            orig = zflat[path[k_i]]
            new_z = min(orig, chain)
            if max_breach_depth is not None and (orig - new_z) > max_breach_depth:
                too_deep = True
                break
            depths.append((path[k_i], new_z, orig))
        if too_deep:
            # 超深回退填洼（诚实计数）：该洼地用填后表面
            for cell_i in path:
                if dep_flat[cell_i]:
                    bflat[cell_i] = fflat[cell_i]
                    fallback_filled_cells += 1
            continue
        for cell_i, new_z, orig_z in depths:
            if new_z < bflat[cell_i]:
                bflat[cell_i] = new_z
            if dep_flat[cell_i] and new_z < orig_z:
                breached_cells += 1
                carved_volume += max(orig_z - new_z, 0.0)
                max_depth_hit = max(max_depth_hit, orig_z - new_z)
    meta = _meta_base(
        "terrain.breach", valid,
        method=(
            "priority-flood identifies depressions; carve a strictly "
            "descending channel from pit to spill point along the filled-"
            "surface D8 path (Lindsay 2016 selective breaching, simplified)"),
        epsilon=eps,
        breached_cells=int(breached_cells),
        carved_volume=round(float(carved_volume), 6),
        n_depressions=n_depressions,
        fallback_filled_cells=int(fallback_filled_cells),
        max_breach_depth_applied=max_depth_hit,
        fill_meta_volume=fill_meta.get("filled_volume"),
    )
    return breached, meta


def hand(
    dem: np.ndarray, cell_size: float,
    cell_size_x: Optional[float] = None,
    *,
    stream_threshold: float = 1000.0,
    nodata: Optional[float] = None,
    d8: Optional[Dict[str, np.ndarray]] = None,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """HAND（Height Above Nearest Drainage，最近排水高程）。

    定义：像元高程减去其 D8 下游链上**第一个河网像元**的高程。
    单遍逆拓扑（降序高程）：hand[cell] = 0（河网）或
    hand[recv] + (z[cell] − z[recv])（望远镜求和至河网）。
    边界排出但未遇河网的像元 → NaN（诚实缺省，计数披露）。
    """
    z, valid = _prepare(dem, nodata)
    h, w = z.shape
    _guard_cells((h, w), "terrain.hand")
    if d8 is None:
        filled, _ = fill_depressions(z, cell_size, cell_size_x, nodata=nodata)
        d8, _ = d8_flow(filled, cell_size, cell_size_x, nodata=nodata)
    acc, _ = flow_accumulation(d8)
    streams_mask, _ = extract_streams(acc, threshold=stream_threshold)
    streams_mask &= valid
    receiver = d8["receiver"]
    rvalid = d8["valid"]
    hand_out = np.full((h, w), np.nan, dtype=np.float64)
    zflat = z.ravel()
    flat_recv = receiver.ravel()
    flat_valid = rvalid.ravel()
    sm = streams_mask.ravel()
    hd = hand_out.ravel()
    vflat = valid.ravel()
    # 升序高程处理（接收者严格更低 → **先**结算，望远镜求和成立）
    for parent in _topology_order(z, valid, descending=False).tolist():
        if not vflat[parent]:
            continue
        if sm[parent]:
            hd[parent] = 0.0
            continue
        if flat_valid[parent]:
            recv = int(flat_recv[parent])
            if np.isfinite(hd[recv]):
                hd[parent] = hd[recv] + (zflat[parent] - zflat[recv])
                continue
        # 边界排出且未遇河网（或上游未解析）：NaN（诚实缺省）
    unresolvable = int((vflat & ~sm & ~np.isfinite(hd)).sum())
    meta = _meta_base(
        "terrain.hand", valid,
        method=(
            "HAND = z(cell) − z(first stream cell along the D8 downstream "
            "chain); single reverse-topological pass (Rennó et al. 2008)"),
        stream_threshold=float(stream_threshold),
        stream_cells=int(streams_mask.sum()),
        unresolvable_cells=unresolvable,
        hand_range=[
            round(float(np.nanmin(hand_out[valid])), 4) if valid.any() else None,
            round(float(np.nanmax(hand_out[valid])), 4) if valid.any() else None,
        ],
    )
    return hand_out, meta


def shreve_magnitude(
    d8: Dict[str, np.ndarray], flow_accum: np.ndarray, threshold: float,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """Shreve 河流量级（Shreve 1966）：量级 = 上游量级之和（源头 = 1）。

    与 Strahler（并列最高级才升级）互补：Shreve 量级线性计上游链接数，
    是排水强度的一阶代理。复用 stream_order 的拓扑序机器（升序改降序
    语义一致：接收者严格更低）。
    """
    acc = np.asarray(flow_accum, dtype=np.float64)
    direction = d8["direction"]
    receiver = d8["receiver"]
    valid = d8["valid"]
    z = np.asarray(d8["dem"], dtype=np.float64)
    h, w = direction.shape
    if acc.shape != (h, w):
        raise ValueError(
            f"flow_accum shape {acc.shape} does not match d8 grid {(h, w)}")
    if not (threshold >= 1):
        raise ValueError(f"threshold must be >= 1 upstream cell (got {threshold!r})")
    _guard_cells((h, w), "terrain.shreve")

    streams = np.isfinite(acc) & (acc >= float(threshold)) & valid
    magnitude = np.zeros((h, w), dtype=np.int32)
    children, parents = _child_table(receiver)
    for parent in _topology_order(z, valid & streams, descending=True).tolist():
        lo = np.searchsorted(parents, parent, side="left")
        hi = np.searchsorted(parents, parent, side="right")
        child_mags = magnitude.ravel()[children[lo:hi]]
        child_mags = child_mags[child_mags > 0]
        magnitude.ravel()[parent] = int(child_mags.sum()) if child_mags.size else 1
    codes = magnitude[streams]
    unique, counts = np.unique(codes, return_counts=True)
    meta = _meta_base(
        "terrain.shreve", valid,
        threshold=float(threshold),
        method=(
            "Shreve magnitude: cell magnitude = sum of upstream magnitudes "
            "(headwater = 1) on cells with accumulation >= threshold"),
        stream_cells=int(streams.sum()),
        max_magnitude=int(codes.max()) if codes.size else 0,
        magnitude_distribution_top={
            str(int(o)): int(n) for o, n in sorted(zip(unique, counts))[-8:]
        },
    )
    return magnitude, meta


def pfafstetter_codes(
    d8: Dict[str, np.ndarray], flow_accum: np.ndarray, threshold: float,
    outlet: Tuple[float, float],
    *,
    transform: Optional[Sequence[float]] = None,
    max_tributaries: int = 4,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """单级 Pfafstetter 编码（干流 + 4 大支流，奇偶交错约定）。

    从出口沿干流上溯（每步取上游**汇流最大**的河网像元 = 干流规则）；
    干流按里程等分 5 段：偶数段 2,4,6,8,10；沿线 4 大支流（按 junction
    汇流降序）取奇数 1,3,5,7 —— 支流子流域 = 其 junction 控制的上游
    河网像元（下游-first 归属）。非河网像元 = 0。

    级别披露：本实现为**单级** Pfafstetter（多级递归子盆地编码未实现，
    属 descriptor limitation）；max_tributaries ∈ [2, 6]。
    """
    if not (2 <= int(max_tributaries) <= 6):
        raise ValueError(f"max_tributaries must be in [2, 6] (got {max_tributaries!r})")
    acc = np.asarray(flow_accum, dtype=np.float64)
    direction = d8["direction"]
    receiver = d8["receiver"]
    valid = d8["valid"]
    h, w = direction.shape
    if acc.shape != (h, w):
        raise ValueError(
            f"flow_accum shape {acc.shape} does not match d8 grid {(h, w)}")
    _guard_cells((h, w), "terrain.pfafstetter")
    # outlet：(row, col) 数组坐标；或（给 transform 时）(x, y) 世界坐标
    # —— 与 watershed 同一约定（_world_to_cell 像元中心索引空间）。
    if transform is not None:
        ocol, orow = _world_to_cell(transform, float(outlet[0]), float(outlet[1]))
        row, col = int(round(float(orow))), int(round(float(ocol)))
    else:
        row, col = int(outlet[0]), int(outlet[1])
    out_flat = row * w + col
    if not (0 <= out_flat < h * w):
        raise DegenerateData(
            f"pfafstetter outlet ({row}, {col}) outside grid {(h, w)}",
            correction_hint="pour point 必须落在栅格范围内")

    streams = np.isfinite(acc) & (acc >= float(threshold)) & valid
    flat_streams = streams.ravel()
    if not flat_streams[out_flat]:
        raise DegenerateData(
            "出口像元不在河网上（accumulation < threshold）",
            correction_hint="降低 stream_threshold 或移动出口到主河道")
    # 上游河网邻接表（逆 D8）
    children, parents = _child_table(receiver)
    acc_flat = acc.ravel()

    def upstream_stream_cells(cell: int) -> list:
        lo = np.searchsorted(parents, cell, side="left")
        hi = np.searchsorted(parents, cell, side="right")
        return [int(c) for c in children[lo:hi] if flat_streams[int(c)]]

    # 干流：出口上溯，每步取汇流最大的上游
    mainstem = [out_flat]
    cur = out_flat
    seen = {cur}
    while True:
        ups = [c for c in upstream_stream_cells(cur) if c not in seen]
        if not ups:
            break
        cur = max(ups, key=lambda c: (acc_flat[c], -c))
        mainstem.append(cur)
        seen.add(cur)
    # 干流上的支流 junction（沿途非干流上游）
    tributaries = []
    for pos, cell in enumerate(mainstem):
        for c in upstream_stream_cells(cell):
            if c not in seen:
                tributaries.append((acc_flat[c], -pos, c, pos))
    tributaries.sort(reverse=True)
    chosen = tributaries[:int(max_tributaries)]

    codes = np.zeros((h, w), dtype=np.int16)
    flat_codes = codes.ravel()

    def assign_basin(junction: int, code: int) -> None:
        """junction 控制的上游河网子流域（下游-first，BFS 防环）。"""
        stack = [junction]
        local_seen = {junction}
        while stack:
            cell = stack.pop()
            if flat_codes[cell] == 0:
                flat_codes[cell] = code
            for c in upstream_stream_cells(cell):
                if c not in local_seen and flat_codes[c] == 0:
                    local_seen.add(c)
                    stack.append(c)

    n_seg = int(max_tributaries) + 1
    # 干流偶数编码（按位置等分：段 k → 2(k+1)）
    for pos, cell in enumerate(mainstem):
        if flat_codes[cell] == 0:
            seg = min(int(pos * n_seg / len(mainstem)), n_seg - 1)
            flat_codes[cell] = 2 * (seg + 1)
    # 支流奇数编码（按汇流降序：1,3,5,…）
    for rank, (_acc_v, _negpos, c, pos) in enumerate(chosen):
        assign_basin(c, 2 * rank + 1)
    coded = codes[streams]
    unique, counts = np.unique(coded, return_counts=True)
    meta = _meta_base(
        "terrain.pfafstetter", valid,
        threshold=float(threshold),
        method=(
            "single-level Pfafstetter: mainstem = largest-accumulation "
            "upstream walk from outlet; even codes 2..2n along the mainstem, "
            "odd codes 1..(n−1) for the largest tributaries at junctions"),
        outlet=[int(row), int(col)],
        mainstem_cells=len(mainstem),
        tributary_codes={str(2 * i + 1): int(acc_flat[c]) for i, (_a, _p, c, _pos) in enumerate(chosen)},
        max_tributaries=int(max_tributaries),
        hierarchy_note="single-level (multi-level recursive sub-basin coding not implemented)",
        code_distribution={str(int(c0)): int(n) for c0, n in zip(unique, counts)},
    )
    return codes, meta


# ── V2-5. 流域形态量测（Strahler 1957 水文地貌）──────────────────────


def watershed_morphometry(
    dem: np.ndarray, d8: Dict[str, np.ndarray],
    pour_point: Tuple[float, float],
    cell_size: float,
    cell_size_x: Optional[float] = None,
    *,
    transform: Optional[Sequence[float]] = None,
    stream_threshold: Optional[float] = None,
    nodata: Optional[float] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """流域形态量测（Strahler 1957 水文地貌学口径；复用 upstream_watershed）。

    - 圈定：pour point 逆 D8 BFS 上流域（(row, col) 数组坐标，或给
      transform 时的 (x, y) 世界坐标 —— 与 ``watershed`` 同约定）；
    - area_m2 / area_km2：像元数 × 各向异性像元面积；
    - perimeter_m：流域边界 4 邻域边缘长度和（水平边 = cx，垂直边 = cy；
      网格外视作流域外）；
    - basin_length_m：流域内 **max upstream 流程长度**（最长山脊→出口
      路径；max 口径在 flow_length 文档化）；
    - form_factor = area / basin_length²；elongation_ratio = 2·sqrt(area/π)
      / basin_length（Strahler 1957）；relief = max−min z；relief_ratio =
      relief / basin_length；
    - 给 stream_threshold 时：Strahler 河网（流域内）→ stream_length_m
      （河网像元到其流域内河网接收者的步长和）与 drainage_density
      （km/km²）；缺省披露「未计算」。

    返回 (metrics dict, meta dict)。
    """
    cy, cx = _validate_cell_sizes(cell_size, cell_size_x)
    z, valid = _prepare(dem, nodata)
    h, w = z.shape
    if d8["valid"].shape != (h, w):
        raise ValueError(
            f"d8 grid {d8['valid'].shape} does not match dem shape {(h, w)}")
    _guard_cells((h, w), "terrain.morphometry")

    if transform is not None:
        col, row = _world_to_cell(transform, float(pour_point[0]), float(pour_point[1]))
    else:
        row, col = float(pour_point[0]), float(pour_point[1])
    basin, w_meta = upstream_watershed(d8, [(int(round(row)), int(round(col)))])

    n_basin = int(basin.sum())
    if n_basin == 0:
        raise NoValidObservations("pour point produced an empty watershed")
    area_m2 = n_basin * cx * cy
    z_basin = z[basin]
    relief = float(z_basin.max() - z_basin.min())

    padded = np.zeros((h + 2, w + 2), dtype=bool)
    padded[1:-1, 1:-1] = basin
    v_edges = int(np.count_nonzero(basin & ~padded[:-2, 1:-1])) \
        + int(np.count_nonzero(basin & ~padded[2:, 1:-1]))
    h_edges = int(np.count_nonzero(basin & ~padded[1:-1, :-2])) \
        + int(np.count_nonzero(basin & ~padded[1:-1, 2:]))
    perimeter_m = v_edges * cx + h_edges * cy

    fl, _ = flow_length(d8, mode="upstream", cell_size=cy, cell_size_x=cx)
    basin_length = float(np.nanmax(np.where(basin, fl, np.nan)))
    if basin_length <= 0:
        basin_length = max(math.hypot(cx, cy), 1e-12)  # 单像元流域退化保护

    metrics: Dict[str, Any] = {
        "pour_point_row_col": (int(round(row)), int(round(col))),
        "cell_count": n_basin,
        "area_m2": round(area_m2, 6),
        "area_km2": round(area_m2 / 1e6, 9),
        "perimeter_m": round(perimeter_m, 6),
        "basin_length_m": round(basin_length, 6),
        "form_factor": round(area_m2 / (basin_length * basin_length), 9),
        "elongation_ratio": round(2.0 * math.sqrt(area_m2 / math.pi) / basin_length, 9),
        "relief_m": round(relief, 6),
        "relief_ratio": round(relief / basin_length, 9),
    }

    if stream_threshold is not None:
        acc, _acc_meta = flow_accumulation(d8)
        order, _s_meta = stream_order(d8, acc, stream_threshold)
        in_stream = basin & (order > 0)
        receiver = d8["receiver"]
        flat_idx = np.arange(h * w, dtype=np.int64)
        has_recv = receiver >= 0
        rows_f = flat_idx // w
        cols_f = flat_idx % w
        rr = np.where(has_recv, receiver, flat_idx) // w
        cc = np.where(has_recv, receiver, flat_idx) % w
        step_flat = np.hypot((cc - cols_f) * cx, (rr - rows_f) * cy)
        step_flat = np.where(has_recv, step_flat, 0.0).reshape(h, w)
        recv2 = receiver.reshape(h, w)
        downstream_stream = np.zeros((h, w), dtype=bool)
        ok = recv2 >= 0
        downstream_stream[ok] = (order.ravel()[recv2[ok]] > 0)
        stream_len_m = float(np.sum(step_flat[in_stream & downstream_stream]))
        metrics["stream_cells"] = int(in_stream.sum())
        metrics["max_stream_order"] = int(order[in_stream].max()) if in_stream.any() else 0
        metrics["stream_length_m"] = round(stream_len_m, 6)
        metrics["drainage_density_km_per_km2"] = round(
            1000.0 * stream_len_m / area_m2, 9)
    else:
        metrics["drainage_density_km_per_km2"] = None

    meta = {
        "algorithm": "terrain.morphometry",
        "method": (
            "watershed morphometry (Strahler 1957): area/perimeter from the "
            "reverse-D8 upstream mask; basin length = MAX upstream flow length; "
            "form factor = area/length^2; elongation = 2*sqrt(area/pi)/length"),
        "pour_point": w_meta["pour_cells"],
        "basin_length_convention": (
            "max upstream flow length within the basin (longest ridge-to-outlet "
            "D8 path), documented max convention"),
        "drainage_density": (
            f"streams from accumulation >= {stream_threshold} (pass stream_threshold "
            "to enable)" if stream_threshold is None
            else f"stream length / area; streams = accumulation >= {stream_threshold}"),
        "cells_valid": int(d8["valid"].sum()),
    }
    return metrics, meta


# ── V2-6/7/8. 湿度与侵蚀指数（TWI / SPI / USLE LS）───────────────────


def _specific_catchment_area(
    flow_accum: np.ndarray, cx: float, cy: float, contour_width: float,
) -> np.ndarray:
    """比集水面积 SCA = (accum + 1)·cell_area / contour_width（米）。

    口径（文档化约定）：accum 为上游贡献像元数（不含自身）→ +1 计入
    自身；等流宽度 contour_width = cell_size（y 向像元尺寸；κ 汇流
    系数恒取 1 —— 无多向流分解，flat 口径披露在 meta）。
    """
    return (np.asarray(flow_accum, dtype=np.float64) + 1.0) * (cx * cy) / contour_width


def _slope_to_radians(slope: np.ndarray, slope_units: str) -> np.ndarray:
    s = np.asarray(slope, dtype=np.float64)
    if slope_units == "degrees":
        return np.radians(s)
    if slope_units == "radians":
        return s
    if slope_units == "percent":
        return np.arctan(s / 100.0)
    raise ValueError(
        f"slope_units must be 'degrees', 'radians' or 'percent' (got {slope_units!r})")


def _aligned(a: np.ndarray, b: np.ndarray, name_a: str, name_b: str) -> None:
    if np.asarray(a).shape != np.asarray(b).shape:
        raise ValueError(
            f"{name_a} shape {np.asarray(a).shape} does not match "
            f"{name_b} shape {np.asarray(b).shape} — inputs must be aligned grids")


_TAN_BETA_FLOOR = 1e-6


def topographic_wetness_index(
    slope: np.ndarray, flow_accum: np.ndarray,
    cell_size: float, cell_size_x: Optional[float] = None,
    *,
    slope_units: str = "degrees",
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """地形湿润指数 TWI = ln(SCA / tanβ)（Beven & Kirkby 1979）。

    - SCA = (accum + 1)·cell_area / contour_width，等流宽度 = cell_size
      （y 向像元尺寸；κ=1 的 flat 口径，meta 披露 —— D8 单向流下 SCA
      是随像元面积变化的近似，非完备集水面积）；
    - β 由 slope（degrees/radians/percent）换算；tanβ 下限 1e-6（近平地
      保护 —— 平地处 TWI 被截断为上界，meta 披露 floor）；
    - slope/accum 形状必须一致；任一非有限 → NaN。
    """
    cy, cx = _validate_cell_sizes(cell_size, cell_size_x)
    _aligned(slope, flow_accum, "slope", "flow_accum")
    beta = _slope_to_radians(slope, slope_units)
    sca = _specific_catchment_area(flow_accum, cx, cy, cy)
    with np.errstate(invalid="ignore", over="ignore"):
        tan_beta = np.maximum(np.tan(beta), _TAN_BETA_FLOOR)
        twi = np.log(sca / tan_beta)
    meta = _meta_base(
        "terrain.twi", np.isfinite(twi),
        cell_size=cy, cell_size_x=cx,
        method="TWI = ln(SCA / tan(beta)) (Beven & Kirkby 1979)",
        sca_convention="SCA = (accum + 1) * cell_area / contour_width; contour_width = cell_size (y); kappa = 1 (flat convention, single-flow approximation)",
        tan_beta_floor=_TAN_BETA_FLOOR,
        slope_units=slope_units,
        limitations_extra="flat cells (beta -> 0) clamp tan(beta) at the floor: TWI is an upper bound there, not a physically resolved wetness",
    )
    return twi, meta


def stream_power_index(
    slope: np.ndarray, flow_accum: np.ndarray,
    cell_size: float, cell_size_x: Optional[float] = None,
    *,
    slope_units: str = "degrees",
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """水流功率指数 SPI = SCA·tanβ（与 TWI 同 SCA 口径；Beven-Kirkby 派生）。

    SCA = (accum + 1)·cell_area / contour_width（等流宽度 = cell_size）；
    tanβ 无下限（平地 → SPI 0）。slope/accum 必须同形。
    """
    cy, cx = _validate_cell_sizes(cell_size, cell_size_x)
    _aligned(slope, flow_accum, "slope", "flow_accum")
    beta = _slope_to_radians(slope, slope_units)
    sca = _specific_catchment_area(flow_accum, cx, cy, cy)
    with np.errstate(invalid="ignore", over="ignore"):
        spi = sca * np.tan(beta)
    meta = _meta_base(
        "terrain.spi", np.isfinite(spi),
        cell_size=cy, cell_size_x=cx,
        method="SPI = SCA * tan(beta) (specific catchment area x slope tangent)",
        sca_convention="SCA = (accum + 1) * cell_area / contour_width; contour_width = cell_size (y); kappa = 1 (flat convention, single-flow approximation)",
        slope_units=slope_units,
    )
    return spi, meta


# McCool 1987 m 系数表（坡度 % 分档；两法共用，文档化）。升序声明、
# 升序覆盖：最终 slope_pct < 1 → 0.2，< 3 → 0.3，< 5 → 0.4，否则 0.5。
_MCCOOL_M_TABLE = ((1.0, 0.2), (3.0, 0.3), (5.0, 0.4))
_MCCOOL_M_DEFAULT = 0.5  # slope >= 5%
_DESMET_N = 1.3  # Desmet & Govers (1996) 指数 n（McCool 1989 语境）
_LS_EDGE = 22.13  # USLE 标准径流小区坡长（米）


def ls_factor(
    slope: np.ndarray,
    flow_length_m: float = 100.0,
    cell_size: float = 1.0,
    cell_size_x: Optional[float] = None,
    *,
    method: str = "mccool",
    slope_units: str = "percent",
    flow_accum: Optional[np.ndarray] = None,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """USLE LS 因子（Wischmeier & Smith 1978；Desmet & Govers 1996）。

    - ``method="mccool"``（默认）：
      LS = (λ/22.13)^m · (65.41·sin²θ + 4.56·sinθ + 0.065)，
      λ = slope_length_m（米；标量或与 slope 同形数组 —— 建议传 upstream
      流程长度），m 取 McCool 1987 坡度分档表：
      <1% → 0.2；1–3% → 0.3；3–5% → 0.4；≥5% → 0.5（θ = 坡度角）；
    - ``method="desmet_govers"``：
      LS = (m+1)·(SCA/22.13)^m·(sinβ/0.0896)^n，n = 1.3（m 表同上）；
      SCA = (accum + 1)·cell_area / contour_width（等流宽度 = cell_size；
      κ=1 flat 口径）—— **需要 flow_accum**（缺省抛 ValueError）；
    - slope_units: percent（默认）/ degrees / radians；非有限 → NaN。
    """
    cy, cx = _validate_cell_sizes(cell_size, cell_size_x)
    if method not in ("mccool", "desmet_govers"):
        raise ValueError(f"method must be 'mccool' or 'desmet_govers' (got {method!r})")
    s = np.asarray(slope, dtype=np.float64)
    theta = _slope_to_radians(s, slope_units)
    slope_pct = np.tan(theta) * 100.0
    # 升序覆盖：最后应用的是最小分档（<1% → 0.2）—— 高档先写、低档覆盖。
    m = np.full(s.shape, float(_MCCOOL_M_DEFAULT))
    for bound, m_val in reversed(_MCCOOL_M_TABLE):
        m = np.where(slope_pct < bound, float(m_val), m)
    sin_t = np.sin(theta)
    with np.errstate(invalid="ignore", over="ignore"):
        if method == "mccool":
            lam = np.asarray(flow_length_m, dtype=np.float64)
            if lam.ndim == 0:
                lam_full = np.full(s.shape, float(lam))
            else:
                _aligned(lam, s, "flow_length_m", "slope")
                lam_full = lam
            ls = np.power(lam_full / _LS_EDGE, m) * (
                65.41 * sin_t * sin_t + 4.56 * sin_t + 0.065)
            length_note = (
                f"slope length = flow_length_m parameter "
                f"({float(flow_length_m)} m scalar or per-cell array)")
        else:
            if flow_accum is None:
                raise ValueError(
                    "method='desmet_govers' requires flow_accum (D8 accumulation grid)")
            _aligned(flow_accum, s, "flow_accum", "slope")
            sca = _specific_catchment_area(flow_accum, cx, cy, cy)
            ls = (m + 1.0) * np.power(sca / _LS_EDGE, m) * np.power(
                sin_t / 0.0896, _DESMET_N)
            length_note = "slope length replaced by SCA = (accum + 1) * cell_area / contour_width (contour width = cell_size, kappa = 1)"

    meta = _meta_base(
        "terrain.ls_factor", np.isfinite(ls),
        cell_size=cy, cell_size_x=cx,
        method=method,
        formula=(
            "mccool: LS = (L/22.13)^m * (65.41*sin^2 + 4.56*sin + 0.065); "
            "desmet_govers: LS = (m+1) * (SCA/22.13)^m * (sin/0.0896)^1.3"),
        m_table="McCool 1987: m = 0.2 (<1%), 0.3 (1-3%), 0.4 (3-5%), 0.5 (>=5%)",
        desmet_n=_DESMET_N,
        slope_units=slope_units,
        length_convention=length_note,
        method_references_note="Wischmeier & Smith 1978 + Desmet & Govers 1996 (n = 1.3)",
    )
    return ls, meta


# ── V2-9. 地形开放度（Yokoyama et al. 2002）──────────────────────────


def terrain_openness(
    dem: np.ndarray, cell_size: float,
    cell_size_x: Optional[float] = None,
    *,
    radius_cells: int = 8,
    azimuth_count: int = 16,
    nodata: Optional[float] = None,
) -> Tuple[Dict[str, np.ndarray], Dict[str, Any]]:
    """正/负地形开放度（Yokoyama, Shirasawa & Pike 2002，度）。

    沿 ``azimuth_count`` 个方位（自北顺时针等角距），对 d = 1..R 像元
    步长取仰角极值：

    - 正开放度 Φ₊ = mean_φ max_d arctan((z₀ − z(d))/d_m)（沿方位看出的
      最大俯角 —— 山脊/开阔地形高）；平地 ≡ 0；
    - 负开放度 Φ₋ = mean_φ max_d arctan((z(d) − z₀)/d_m)（最大仰角 ——
      谷地/封闭地形高；与 Φ₊ 同式取负差）；
    - d_m = 圆整偏移的实际米制距离（各向异性感知）；某方位全程无有效
      采样（栅格角隅）→ 该方位从均值剔除；无任何有效方位 → NaN；
    - 输出 NaN = 无效像元。

    护栏：radius_cells ≤ 100、网格 ≤ 50M 像元（先拒绝后分配）。
    """
    cy, cx = _validate_cell_sizes(cell_size, cell_size_x)
    if isinstance(radius_cells, bool) or not isinstance(radius_cells, (int, np.integer)) \
            or not (1 <= int(radius_cells)):
        raise ValueError(f"radius_cells must be a positive integer (got {radius_cells!r})")
    radius_cells = int(radius_cells)
    if radius_cells > MAX_OPENNESS_RADIUS_CELLS:
        raise ResourceScaleMismatch(
            f"terrain.openness: radius_cells {radius_cells} > "
            f"{MAX_OPENNESS_RADIUS_CELLS} (ray walk memory/time envelope)",
            estimated=f"radius_cells={radius_cells}",
            limit=f"radius_cells<={MAX_OPENNESS_RADIUS_CELLS}",
            correction_hint="reduce the openness search radius")
    if isinstance(azimuth_count, bool) or not isinstance(azimuth_count, (int, np.integer)) \
            or not (4 <= int(azimuth_count) <= 64):
        raise ValueError(
            f"azimuth_count must be an integer in [4, 64] (got {azimuth_count!r})")
    azimuth_count = int(azimuth_count)

    z_raw = np.asarray(dem)
    if getattr(z_raw, "ndim", 0) != 2:
        raise NoValidObservations(
            f"DEM must be a 2D array (got ndim {getattr(z_raw, 'ndim', 0)})")
    _guard_cells(z_raw.shape, "terrain.openness")
    z, valid = _prepare(dem, nodata)
    h, w = z.shape

    n_az = azimuth_count
    # P0 内存重构（science-v3 审计）：此前物化 pos_max/neg_max/az_has 三个
    # (n_az,h,w) 栈 —— 64 方位 × 50M 像元 ≈ 25-77 GB 在「像元护栏内」爆炸。
    # 逐方位 running max（(h,w) 瞬态）+ (h,w) 累加器，峰值 O(h·w)；
    # 求和顺序（j 升序）与原 axis=0 归约一致（n_az ≤ 64 单 block 顺序求和）。
    pos_sum = np.zeros((h, w), dtype=np.float64)
    neg_sum = np.zeros((h, w), dtype=np.float64)
    az_count = np.zeros((h, w), dtype=np.float64)

    z0 = np.where(valid, z, 0.0)
    for j in range(n_az):
        az = 2.0 * math.pi * j / n_az  # 自北顺时针
        pos_max = np.full((h, w), -np.inf)
        neg_max = np.full((h, w), -np.inf)
        az_has = np.zeros((h, w), dtype=bool)
        for k in range(1, radius_cells + 1):
            dc = int(round(k * math.sin(az)))
            dr = -int(round(k * math.cos(az)))
            if dc == 0 and dr == 0:
                continue
            dist = math.hypot(dc * cx, dr * cy)
            r0, r1 = max(0, -dr), min(h, h - dr)
            c0, c1 = max(0, -dc), min(w, w - dc)
            zd = np.full((h, w), np.nan)
            vd = np.zeros((h, w), dtype=bool)
            zd[r0:r1, c0:c1] = z[r0 + dr:r1 + dr, c0 + dc:c1 + dc]
            vd[r0:r1, c0:c1] = valid[r0 + dr:r1 + dr, c0 + dc:c1 + dc]
            usable = valid & vd
            if not usable.any():
                continue
            with np.errstate(invalid="ignore"):
                up = np.degrees(np.arctan((zd - z0) / dist))
                down = np.degrees(np.arctan((z0 - zd) / dist))
            up = np.where(usable, up, -np.inf)
            down = np.where(usable, down, -np.inf)
            np.fmax(pos_max, down, out=pos_max)
            np.fmax(neg_max, up, out=neg_max)
            az_has |= usable
        pos_sum += np.where(az_has, pos_max, 0.0)
        neg_sum += np.where(az_has, neg_max, 0.0)
        az_count += az_has

    has_any = az_count > 0
    denom = np.where(has_any, az_count, 1.0)
    positive = np.where(valid & has_any, pos_sum / denom, np.nan)
    negative = np.where(valid & has_any, neg_sum / denom, np.nan)

    result = {"positive": positive, "negative": negative}
    meta = _meta_base(
        "terrain.openness", valid,
        cell_size=cy, cell_size_x=cx,
        radius_cells=radius_cells, azimuth_count=n_az,
        method=(
            "openness (Yokoyama et al. 2002): mean over azimuths of the max "
            "arctan((z0 - z(d))/d) angle within the radius; positive = downward "
            "(open terrain), negative = upward (enclosed terrain), degrees"),
        units="degrees",
        distance_convention="actual metric distance of the rounded per-step offsets (anisotropic cell sizes honoured)",
        azimuth_policy="azimuths with no valid in-grid sample are dropped from the mean; cells with none are NaN",
        edge_policy=EDGE_POLICY,
    )
    return result, meta


# ── V3-1/2. 地平线角与天空可视因子（Steyn 1980；openness 家族射线行走）──


def _horizon_single_azimuth(
    z: np.ndarray, valid: np.ndarray,
    cx: float, cy: float,
    az_deg: float, radius_cells: int,
) -> np.ndarray:
    """单方位地平线角（度）—— ``horizon_angle`` 与 ``sky_view_factor``
    共用的唯一射线行走实现（两算法不重复逻辑）。

    - 罗盘度方位自北顺时针，按 k = 1..R 像元步长取圆整偏移（与 openness
      同口径），距离 = 偏移的实际米制欧氏距离（各向异性像元感知）；
    - 仰角 = arctan((z(d) − z₀)/d_m)，只取正值参与 running max（初始化 0
      兜底 —— 地平线角不为负；平地 ≡ 0，浮点精确）；
    - 射线在首个 nodata/非有限/出界采样处停止（其后更远采样不再参与：
      数据外视作无遮挡，截断语义由调用方在 edge_policy 披露）；
    - 返回 (h, w) float64；无效中心像元保持 0（调用方掩成 NaN）。
    """
    h, w = z.shape
    horiz = np.zeros((h, w), dtype=np.float64)
    z0 = np.where(valid, z, 0.0)
    az = math.radians(float(az_deg))
    alive = valid.copy()  # 中心无效的像元不参与任何射线
    for k in range(1, radius_cells + 1):
        if not alive.any():
            break
        dc = int(round(k * math.sin(az)))
        dr = -int(round(k * math.cos(az)))
        if dc == 0 and dr == 0:
            continue
        dist = math.hypot(dc * cx, dr * cy)
        r0, r1 = max(0, -dr), min(h, h - dr)
        c0, c1 = max(0, -dc), min(w, w - dc)
        vd = np.zeros((h, w), dtype=bool)
        vd[r0:r1, c0:c1] = valid[r0 + dr:r1 + dr, c0 + dc:c1 + dc]
        contrib = alive & vd
        if contrib.any():
            zd = np.full((h, w), np.nan)
            zd[r0:r1, c0:c1] = z[r0 + dr:r1 + dr, c0 + dc:c1 + dc]
            with np.errstate(invalid="ignore"):
                ang = np.degrees(np.arctan((zd - z0) / dist))
            np.fmax(horiz, np.where(contrib, ang, -np.inf), out=horiz)
        alive &= vd  # 首个无效采样处截断射线（stop-at-nodata 政策）
    return horiz


# 方位×像元联合包络：horizon_angle / sky_view_factor 的 API 契约返回
# 逐方位栅格 —— n_az×h×w 的输出体量本身是结果的一部分。联合积超过该
# 上限时类型化拒绝（先拒绝不 OOM；修正建议=减方位或降分辨率）。
MAX_HORIZON_AZIMUTH_CELLS = 64_000_000


def _guard_horizon_stack(n_az: int, shape: Tuple[int, int], algorithm: str) -> None:
    total = n_az * shape[0] * shape[1]
    if total > MAX_HORIZON_AZIMUTH_CELLS:
        raise ResourceScaleMismatch(
            f"{algorithm}: azimuths×cells = {n_az}×{shape[0]}×{shape[1]} = "
            f"{total} 超过联合包络 {MAX_HORIZON_AZIMUTH_CELLS} "
            f"（逐方位栅格输出体量是结果的一部分）",
            estimated=f"{total} azimuth-cells (float64)",
            limit=f"azimuths*cells<={MAX_HORIZON_AZIMUTH_CELLS}",
            correction_hint="减少方位数或降低 DEM 分辨率",
        )


def _validate_horizon_radius(max_search_radius: Any, algorithm: str) -> int:
    """射线半径护栏（horizon_angle / sky_view_factor 共用；先拒绝后分配）。"""
    if isinstance(max_search_radius, bool) \
            or not isinstance(max_search_radius, (int, np.integer)) \
            or not (1 <= int(max_search_radius)):
        raise ValueError(
            f"max_search_radius must be a positive integer (got {max_search_radius!r})")
    radius = int(max_search_radius)
    if radius > MAX_HORIZON_RADIUS_CELLS:
        raise ResourceScaleMismatch(
            f"{algorithm}: max_search_radius {radius} > "
            f"{MAX_HORIZON_RADIUS_CELLS} (ray walk memory/time envelope)",
            estimated=f"max_search_radius={radius}",
            limit=f"max_search_radius<={MAX_HORIZON_RADIUS_CELLS}",
            correction_hint="reduce the horizon search radius")
    return radius


def _validate_azimuth_list(azimuths: Sequence[float]) -> List[float]:
    """方位列表护栏：≥1 个、≤ 上限、有限罗盘度 ∈ [0, 360)。"""
    az_list = [float(a) for a in azimuths]
    if not az_list:
        raise ValueError("azimuths must contain at least one compass bearing")
    if len(az_list) > MAX_HORIZON_AZIMUTHS:
        raise ValueError(
            f"azimuths must contain at most {MAX_HORIZON_AZIMUTHS} bearings "
            f"(got {len(az_list)})")
    for a in az_list:
        if not math.isfinite(a) or not (0.0 <= a < 360.0):
            raise ValueError(
                f"azimuths must be finite compass degrees in [0, 360) (got {a!r})")
    return az_list


def horizon_angle(
    dem: np.ndarray, cell_size: float,
    cell_size_x: Optional[float] = None,
    *,
    azimuths: Sequence[float] = (0.0, 45.0, 90.0, 135.0, 180.0, 225.0, 270.0, 315.0),
    max_search_radius: int = 100,
    nodata: Optional[float] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """地平线角（度；Steyn 1980 的输入量，openness 家族射线行走）。

    - 每方位（罗盘度，自北顺时针）沿 1 像元步长射线取
      max_k arctan((z(k) − z₀)/d_m) 的**正**仰角；全下行钳 0（地平线角
      不为负）；平地 ≡ 0（浮点精确）；
    - 射线遇 nodata/非有限像元即停；半径外/数据缝后的地形视作无遮挡
      （截断 = 0，edge policy 披露 —— 诚实低估而非发明遮挡）；
    - 输出 dict：``azimuths``（罗盘度列表）、``horizon``（方位键 → 地平线
      角栅格，度）、``max``（逐像元跨方位 max，度）；NaN = 无效像元。

    护栏：max_search_radius ≤ 100、方位 1..64、网格 ≤ 50M 像元、
    方位×像元联合包络（先拒绝后分配）。确定性。
    """
    cy, cx = _validate_cell_sizes(cell_size, cell_size_x)
    radius = _validate_horizon_radius(max_search_radius, "terrain.horizon_angle")
    az_list = _validate_azimuth_list(azimuths)

    z_raw = np.asarray(dem)
    if getattr(z_raw, "ndim", 0) != 2:
        raise NoValidObservations(
            f"DEM must be a 2D array (got ndim {getattr(z_raw, 'ndim', 0)})")
    _guard_cells(z_raw.shape, "terrain.horizon_angle")
    z, valid = _prepare(dem, nodata)
    _guard_horizon_stack(len(az_list), z.shape, "terrain.horizon_angle")

    # P0 内存重构（science-v3 审计）：逐方位流式构造输出 dict，不再物化
    # (n_az,h,w) 中间栈 —— 峰值 ≈ 输出体量 + 单方位瞬态。
    horizon: Dict[str, Any] = {}
    max_arr: Optional[np.ndarray] = None
    for az_deg in az_list:
        h_row = _horizon_single_azimuth(z, valid, cx, cy, az_deg, radius)
        horizon[f"{float(az_deg):g}"] = np.where(valid, h_row, np.nan)
        max_arr = h_row if max_arr is None else np.fmax(max_arr, h_row)
    assert max_arr is not None  # az_list ≥ 1（_validate_azimuth_list 保证）
    result = {
        "azimuths": [round(float(a), 6) for a in az_list],
        "horizon": horizon,
        "max": np.where(valid, max_arr, np.nan),
    }
    meta = _meta_base(
        "terrain.horizon_angle", valid,
        cell_size=cy, cell_size_x=cx,
        azimuths=[round(float(a), 6) for a in az_list],
        radius_cells=radius,
        method=(
            "horizon angle (Steyn 1980 input quantity): per-azimuth running max "
            "of the positive elevation angle arctan((z(d) - z0)/d) along 1-cell "
            "step rays within the radius; degrees"),
        units="degrees",
        distance_convention=(
            "actual metric distance of the rounded per-step offsets "
            "(anisotropic cell sizes honoured)"),
        edge_policy=(
            "horizon edge policy: rays stop at the first nodata/non-finite "
            "sample or the grid edge; the unobserved rest of a truncated ray "
            "counts as unobstructed (0) — disclosed underestimation beyond "
            "data gaps, no padding values invented"),
        clamping=(
            "descending-only rays clamp at 0 (a horizon angle cannot be "
            "negative); flat ground -> 0 exactly"),
    )
    return result, meta


def sky_view_factor(
    dem: np.ndarray, cell_size: float,
    cell_size_x: Optional[float] = None,
    *,
    n_azimuths: int = 16,
    max_search_radius: int = 100,
    nodata: Optional[float] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """天空可视因子 SVF（Steyn 1980）：SVF = (1/N) Σ_i cos²(ψ_i)。

    - ψ_i = 与 ``horizon_angle`` 共用射线行走（``_horizon_rasters``）得到的
      逐方位地平线角（度）；N 个等角距方位自北顺时针；
    - 平地 ψ ≡ 0 → SVF ≡ 1.0（浮点精确）；深洼/封闭谷地 → SVF → 0；
    - 结果同时返回逐方位地平线角栅格（``horizon``，度）供复用。

    护栏：max_search_radius ≤ 100、n_azimuths ∈ [4, 64]、网格 ≤ 50M 像元
    （先拒绝后分配）。确定性。
    """
    cy, cx = _validate_cell_sizes(cell_size, cell_size_x)
    radius = _validate_horizon_radius(max_search_radius, "terrain.sky_view_factor")
    if isinstance(n_azimuths, bool) or not isinstance(n_azimuths, (int, np.integer)) \
            or not (4 <= int(n_azimuths) <= MAX_HORIZON_AZIMUTHS):
        raise ValueError(
            f"n_azimuths must be an integer in [4, {MAX_HORIZON_AZIMUTHS}] "
            f"(got {n_azimuths!r})")
    n_az = int(n_azimuths)

    z_raw = np.asarray(dem)
    if getattr(z_raw, "ndim", 0) != 2:
        raise NoValidObservations(
            f"DEM must be a 2D array (got ndim {getattr(z_raw, 'ndim', 0)})")
    _guard_cells(z_raw.shape, "terrain.sky_view_factor")
    z, valid = _prepare(dem, nodata)

    az_list = [360.0 * j / n_az for j in range(n_az)]
    _guard_horizon_stack(n_az, z.shape, "terrain.sky_view_factor")

    # P0 内存重构（science-v3 审计）：逐方位流式累加 cos² 并构造输出
    # dict，不再物化 (n_az,h,w) 的 horiz/cos2 中间栈。
    cos2_sum = np.zeros(z.shape, dtype=np.float64)
    horizon: Dict[str, Any] = {}
    for az_deg in az_list:
        h_row = _horizon_single_azimuth(z, valid, cx, cy, az_deg, radius)
        horizon[f"{float(az_deg):g}"] = np.where(valid, h_row, np.nan)
        with np.errstate(invalid="ignore"):
            cos2_sum += np.cos(np.radians(h_row)) ** 2
    svf_raw = cos2_sum / n_az
    svf = np.where(valid, svf_raw, np.nan)
    result = {
        "svf": svf,
        "azimuths": [round(float(a), 6) for a in az_list],
        "horizon": horizon,
    }
    meta = _meta_base(
        "terrain.sky_view_factor", valid,
        cell_size=cy, cell_size_x=cx,
        n_azimuths=n_az, radius_cells=radius,
        method=(
            "sky view factor (Steyn 1980): SVF = (1/N) * sum_i cos^2(psi_i) "
            "over N evenly spaced azimuths, psi_i = horizon angle from the "
            "shared terrain.horizon_angle ray walk"),
        units="ratio (0 = fully obstructed sky, 1 = fully open sky)",
        flat_reference="flat ground has psi = 0 everywhere -> SVF = 1.0 exactly",
        edge_policy=(
            "svf edge policy: inherits the horizon ray policy — rays stop at "
            "nodata/grid edge and a truncated rest-of-ray counts as "
            "unobstructed (cos^2 = 1); disclosed overestimate of sky "
            "openness near data gaps"),
    )
    return result, meta


# ── V2-10. Geomorphons（Jasiewicz & Stepinski 2013）──────────────────

GEOMORPHON_CLASSES: Tuple[str, ...] = (
    "flat", "summit", "ridge", "shoulder", "spur",
    "slope", "hollow", "footslope", "valley", "depression",
)


def _geomorphon_class(dn: np.ndarray, up: np.ndarray) -> np.ndarray:
    """(最长 −1 环长, 最长 +1 环长) → 10 类编码（1..10）。

    规范决策表（Jasiewicz & Stepinski 2013 图 3 / GRASS r.geomorphon
    同款级联；评审 R2 MAJOR-3 对齐）：

        dn == 8 → summit(2)；up == 8 → depression(10)；
        dn ≥ 6 → ridge(3)；up ≥ 6 → valley(9)；
        dn ≥ 3 且 up ≥ 3 → slope(6)（双侧 135°-225° 环并存）；
        dn == 5 → shoulder(4)；up == 5 → footslope(8)；
        dn ∈ {3,4} → spur(5)；up ∈ {3,4} → hollow(7)；否则 flat(1)。

    级联顺序（低优先级先写、后写覆盖）：hollow → spur → footslope →
    shoulder → slope → valley → ridge → depression → summit；带内界由
    ``dn≥6``/``up≥6`` 分支先行截住（dn+up ≤ 8，dn≥6 ⇒ up≤2，反之亦然）。
    """
    out = np.full(dn.shape, 1, dtype=np.int8)          # flat（兜底）
    out = np.where(up >= 3, 7, out)                    # hollow (up 3..4)
    out = np.where(dn >= 3, 5, out)                    # spur (dn 3..4)
    out = np.where(up >= 5, 8, out)                    # footslope (up == 5)
    out = np.where(dn >= 5, 4, out)                    # shoulder (dn == 5)
    out = np.where((dn >= 3) & (up >= 3), 6, out)      # slope（双 135°-225° 环）
    out = np.where(up >= 6, 9, out)                    # valley (up 6..7)
    out = np.where(dn >= 6, 3, out)                    # ridge (dn 6..7)
    out = np.where(up == 8, 10, out)                   # depression
    out = np.where(dn == 8, 2, out)                    # summit
    return out.astype(np.int8)


def geomorphons(
    dem: np.ndarray, cell_size: float,
    cell_size_x: Optional[float] = None,
    *,
    lookup_radius_cells: int = 8,
    flatten: float = 0.0,
    far: float = 0.0,
    nodata: Optional[float] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Geomorphons 地貌形态分类（Jasiewicz & Stepinski 2013）。

    每像元沿 8 方位（自北起 45° 步进）做视线扫描至 ``lookup_radius_cells``：

    - 每腿 zenith = max_d arctan((z(d) − z₀)/d)（地形更高），nadir =
      max_d arctan((z₀ − z(d))/d)（地形更低），度；
    - 三元码：zenith > flatten 且 zenith ≥ nadir → +1（更高）；否则
      nadir > flatten → −1（更低）；否则 0（平）—— ``flatten`` 为平地
      容差（度）；``far`` > 0 时跳过 ≤ far 像元的近场采样（skip 半径）；
    - 8 码环上最长 −1 环 / +1 环 → 10 类（决策表见 _geomorphon_class，
      代码 1..10 = flat/summit/ridge/shoulder/spur/slope/hollow/footslope/
      valley/depression；代码 0 = 无效像元）；
    - 无有效采样的腿按 0（平）计（meta 披露 —— 栅格角隅诚实退化）。

    确定性；护栏：lookup_radius_cells ≤ 128、网格 ≤ 50M 像元。
    """
    cy, cx = _validate_cell_sizes(cell_size, cell_size_x)
    if isinstance(lookup_radius_cells, bool) \
            or not isinstance(lookup_radius_cells, (int, np.integer)) \
            or not (1 <= int(lookup_radius_cells)):
        raise ValueError(
            f"lookup_radius_cells must be a positive integer (got {lookup_radius_cells!r})")
    lookup_radius_cells = int(lookup_radius_cells)
    if lookup_radius_cells > MAX_GEOMORPHON_RADIUS_CELLS:
        raise ResourceScaleMismatch(
            f"terrain.geomorphons: lookup_radius_cells {lookup_radius_cells} > "
            f"{MAX_GEOMORPHON_RADIUS_CELLS}",
            estimated=f"lookup_radius_cells={lookup_radius_cells}",
            limit=f"lookup_radius_cells<={MAX_GEOMORPHON_RADIUS_CELLS}",
            correction_hint="reduce the geomorphon lookup radius")
    if flatten < 0:
        raise ValueError(f"flatten must be >= 0 degrees (got {flatten!r})")
    if far < 0 or far >= lookup_radius_cells:
        raise ValueError(
            f"far must be in [0, lookup_radius_cells) (got far={far!r}, "
            f"lookup={lookup_radius_cells})")

    z_raw = np.asarray(dem)
    if getattr(z_raw, "ndim", 0) != 2:
        raise NoValidObservations(
            f"DEM must be a 2D array (got ndim {getattr(z_raw, 'ndim', 0)})")
    _guard_cells(z_raw.shape, "terrain.geomorphons")
    z, valid = _prepare(dem, nodata)
    h, w = z.shape

    codes = np.zeros((8, h, w), dtype=np.int8)
    z0 = np.where(valid, z, 0.0)
    for leg in range(8):
        az = math.radians(45.0 * leg)  # 自北顺时针
        zenith = np.full((h, w), -np.inf)
        nadir = np.full((h, w), -np.inf)
        leg_has = np.zeros((h, w), dtype=bool)
        for k in range(int(math.floor(far)) + 1, lookup_radius_cells + 1):
            dc = int(round(k * math.sin(az)))
            dr = -int(round(k * math.cos(az)))
            if dc == 0 and dr == 0:
                continue
            dist = math.hypot(dc * cx, dr * cy)
            r0, r1 = max(0, -dr), min(h, h - dr)
            c0, c1 = max(0, -dc), min(w, w - dc)
            zd = np.full((h, w), np.nan)
            vd = np.zeros((h, w), dtype=bool)
            zd[r0:r1, c0:c1] = z[r0 + dr:r1 + dr, c0 + dc:c1 + dc]
            vd[r0:r1, c0:c1] = valid[r0 + dr:r1 + dr, c0 + dc:c1 + dc]
            usable = valid & vd
            if not usable.any():
                continue
            with np.errstate(invalid="ignore"):
                up = np.degrees(np.arctan((zd - z0) / dist))
                down = np.degrees(np.arctan((z0 - zd) / dist))
            zenith = np.fmax(zenith, np.where(usable, up, -np.inf))
            nadir = np.fmax(nadir, np.where(usable, down, -np.inf))
            leg_has |= usable
        with np.errstate(invalid="ignore"):
            higher = (zenith > flatten) & (zenith >= nadir)
            lower = (~higher) & (nadir > flatten)
        code = np.zeros((h, w), dtype=np.int8)
        code[higher] = 1
        code[lower] = -1
        code[~leg_has] = 0  # 全程无采样（角隅）→ 平（披露）
        codes[leg] = code

    # 环上最长同值游程（向量化：起点 0..7 × 窗口长度 1..8 的循环移位与）。
    dn = np.zeros((h, w), dtype=np.int8)
    up = np.zeros((h, w), dtype=np.int8)
    for sign, acc_arr in ((-1, dn), (1, up)):
        eq = (codes == sign)
        for start in range(8):
            run = eq[start].copy()
            for offset in range(8):
                if offset > 0:
                    run = run & eq[(start + offset) % 8]
                    if not run.any():
                        break
                np.maximum(acc_arr, np.where(run, offset + 1, 0), out=acc_arr)

    klass = np.where(valid, _geomorphon_class(dn, up), 0).astype(np.int8)

    valid_k = klass[klass > 0]
    unique, counts = np.unique(valid_k, return_counts=True)
    distribution = {GEOMORPHON_CLASSES[int(o) - 1]: int(n)
                    for o, n in zip(unique, counts)}
    result = {
        "classes": klass,           # 1..10（顺序见 GEOMORPHON_CLASSES）；0 = 无效
        "class_names": GEOMORPHON_CLASSES,
        "ternary_legs": codes,
        "dn_run": dn,
        "up_run": up,
    }
    meta = _meta_base(
        "terrain.geomorphons", valid,
        cell_size=cy, cell_size_x=cx,
        lookup_radius_cells=lookup_radius_cells,
        flatten_degrees=float(flatten), far_cells=float(far),
        method=(
            "geomorphons (Jasiewicz & Stepinski 2013): 8 line-of-sight ternary "
            "codes from zenith/nadir angles vs the flatten tolerance; longest "
            "cyclic -1/+1 runs map to 10 landform classes"),
        class_codes={str(i + 1): name for i, name in enumerate(GEOMORPHON_CLASSES)},
        class_distribution=distribution,
        decision_table=(
            "dn==8 summit; up==8 depression; dn>=6 ridge; up>=6 valley; dn>=3&up>=3 "
            "slope; dn==5 shoulder; up==5 footslope; dn 3-4 spur; up 3-4 hollow; "
            "else flat (Jasiewicz-Stepinski 2013 / GRASS r.geomorphon canonical)"),
        unsampled_leg_policy="legs with no valid in-grid sample count as 0 (flat) — disclosed corner degradation",
        edge_policy=EDGE_POLICY,
    )
    return result, meta


# ── V2-11. Weiss 双尺度 TPI 地类分级（Weiss 2001）────────────────────


LANDFORM_CLASSES: Tuple[str, ...] = (
    "canyons_deeply_incised",          # 1
    "midslope_drainages_shallow_vals",  # 2
    "upland_drainages_mountain_vals",   # 3
    "plains_small",                     # 4
    "open_slopes",                      # 5
    "upper_slopes_mesas",               # 6
    "local_ridges_in_valleys",          # 7
    "midslope_ridges_small_hills",      # 8
    "mountain_tops_high_ridges",        # 9
    "plains",                           # 10
)


def landform_classification(
    dem: np.ndarray, cell_size: float,
    cell_size_x: Optional[float] = None,
    *,
    tpi_window_small: int = 3,
    tpi_window_large: int = 25,
    elevation_tolerance: float = 0.1,
    nodata: Optional[float] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """双尺度 TPI 地类分级（Weiss 2001 决策表；10 类）。

    s = TPI(window_small)/SD_s、l = TPI(window_large)/SD_l（标准化 TPI；
    SD 为全图有效像元 TPI 标准差，SD = 0 → DegenerateData）；
    p = 高程百分位（0..1，平均秩）。决策表（文档化，Weiss 2001 海报
    口径 —— 中性带 |TPI/SD| < 1，elevation_tolerance 截 percentile）：

    1 canyons (s≤−1, l≤−1)｜2 midslope drainages (s≤−1, −1<l<1)｜
    3 upland drainages (s≤−1, l≥1)｜4 plains small (|s|<1, l<1, p≤tol)｜
    5 open slopes (|s|<1, |l|<1, tol<p<1−tol)｜6 upper slopes/mesas
    (|s|<1 且 [|l|<1, p≥1−tol 或 l≥1])｜7 local ridges in valleys
    (s≥1, l≤−1)｜8 midslope ridges (s≥1, −1<l<1)｜9 mountain tops
    (s≥1, l≥1)｜10 plains (|s|<1, l≤−1, p>tol)。代码 0 = 无效像元。

    TPI 含中心像元（与 topographic_position_index 同口径）；边缘收缩
    （EDGE_POLICY）。输出类图 + 类分布 meta。
    """
    cy, cx = _validate_cell_sizes(cell_size, cell_size_x)
    del cy, cx  # TPI 与像元尺寸无关；仅做参数一致性校验
    if not (0.0 <= float(elevation_tolerance) <= 0.5):
        raise ValueError(
            f"elevation_tolerance must be in [0, 0.5] (got {elevation_tolerance!r})")
    ws = _validate_window(tpi_window_small)
    wl = _validate_window(tpi_window_large)
    if wl < ws:
        raise ValueError(
            f"tpi_window_large ({wl}) must be >= tpi_window_small ({ws})")
    z, valid = _prepare(dem, nodata)
    _guard_cells(z.shape, "terrain.landform")
    tpi_s, _ = topographic_position_index(z, window=ws, nodata=nodata)
    tpi_l, _ = topographic_position_index(z, window=wl, nodata=nodata)

    sd_s = float(np.nanstd(tpi_s[valid]))
    sd_l = float(np.nanstd(tpi_l[valid]))
    if sd_s <= 0 or sd_l <= 0:
        raise DegenerateData(
            "TPI standard deviation is 0 — standardized classification "
            "thresholds are undefined for a constant surface",
            correction_hint="classify a DEM with relief, or use slope-based classes")
    s_arr = tpi_s / sd_s
    l_arr = tpi_l / sd_l

    from scipy.stats import rankdata

    z_valid = z[valid]
    pct = rankdata(z_valid, method="average")
    pct = pct / max(1, z_valid.size - 1)
    p = np.zeros(z.shape)
    p[valid] = pct
    tol = float(elevation_tolerance)

    neutral_s = (s_arr > -1) & (s_arr < 1)
    one = (s_arr <= -1) & (l_arr <= -1)
    two = (s_arr <= -1) & (l_arr > -1) & (l_arr < 1)
    three = (s_arr <= -1) & (l_arr >= 1)
    four = neutral_s & (l_arr < 1) & (p <= tol)
    five = neutral_s & (np.abs(l_arr) < 1) & (p > tol) & (p < 1 - tol)
    six = neutral_s & (((np.abs(l_arr) < 1) & (p >= 1 - tol)) | (l_arr >= 1))
    seven = (s_arr >= 1) & (l_arr <= -1)
    eight = (s_arr >= 1) & (l_arr > -1) & (l_arr < 1)
    nine = (s_arr >= 1) & (l_arr >= 1)
    ten = neutral_s & (l_arr <= -1) & (p > tol)

    klass = np.zeros(z.shape, dtype=np.int8)
    for code, cond in ((1, one), (2, two), (3, three), (4, four), (5, five),
                       (6, six), (7, seven), (8, eight), (9, nine), (10, ten)):
        klass[cond & valid] = code

    valid_k = klass[klass > 0]
    unique, counts = np.unique(valid_k, return_counts=True)
    distribution = {LANDFORM_CLASSES[int(o) - 1]: int(n)
                    for o, n in zip(unique, counts)}
    result = {
        "classes": klass,          # 1..10（顺序见 LANDFORM_CLASSES）；0 = 无效
        "class_names": LANDFORM_CLASSES,
        "tpi_standardized_small": s_arr,
        "tpi_standardized_large": l_arr,
        "elevation_percentile": np.where(valid, p, np.nan),
    }
    meta = _meta_base(
        "terrain.landform", valid,
        tpi_window_small=ws, tpi_window_large=wl,
        elevation_tolerance=tol,
        tpi_window_includes_center=True,
        sd_small=round(sd_s, 9), sd_large=round(sd_l, 9),
        method=(
            "two-scale standardized TPI landform classes (Weiss 2001): "
            "|TPI/SD| < 1 is the neutral band; flat band split by elevation "
            "percentile with the given tolerance"),
        class_codes={str(i + 1): name for i, name in enumerate(LANDFORM_CLASSES)},
        class_distribution=distribution,
        decision_table=(
            "1 canyon s<=-1,l<=-1; 2 midslope drainage s<=-1,-1<l<1; 3 upland "
            "valley s<=-1,l>=1; 4 plains-small |s|<1,l<1,p<=tol; 5 open slope "
            "|l|<1,middle p; 6 mesa/upper |l|<1,p>=1-tol or l>=1; 7 local ridge "
            "s>=1,l<=-1; 8 midslope ridge s>=1,-1<l<1; 9 mountaintop s>=1,l>=1; "
            "10 plains |s|<1,l<=-1,p>tol"),
        edge_policy=EDGE_POLICY,
    )
    return result, meta


# ── V2-12. 多方位山体阴影 ────────────────────────────────────────────


def hillshade_multiazimuth(
    dem: np.ndarray, cell_size: float,
    cell_size_x: Optional[float] = None,
    *,
    altitude: float = 45.0,
    azimuths: Sequence[float] = (315.0, 135.0),
    combine: str = "mean",
    nodata: Optional[float] = None,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """多方位山体阴影（0-255；罗盘光照模型，#379 修复语义）。

    单方位公式与 ``app/services/rs/band_math.compute_hillshade`` 逐位一致
    （照度 = sin(alt)·cos(θ) + cos(alt)·sin(θ)·cos(az − aspect)；aspect
    罗盘角；3×3 Horn 梯度 edge 复制延拓 —— band_math 是真相源，此处仅
    复算以避免 services 依赖）。``combine="mean"`` 取多方位均值（去阴影），
    ``"min"`` 取逐像元最小（经典多方位制图）。NaN 像元传播为 NaN（与
    band_math 渲染掩膜一致）。

    altitude ∈ (0, 90]；azimuths 为罗盘度列表（≥1 个）；确定性。
    """
    cy, cx = _validate_cell_sizes(cell_size, cell_size_x)
    if not (0 < float(altitude) <= 90):
        raise ValueError(f"altitude must be in (0, 90] degrees (got {altitude!r})")
    az_list = [float(a) for a in azimuths]
    if not az_list:
        raise ValueError("azimuths must contain at least one compass bearing")
    if combine not in ("mean", "min"):
        raise ValueError(f"combine must be 'mean' or 'min' (got {combine!r})")

    z, valid = _prepare(dem, nodata)
    _guard_cells(z.shape, "terrain.hillshade_multi")

    # 与 band_math.compute_hillshade 逐位相同的单方位实现（真相源注释）。
    pad = np.pad(z, 1, mode="edge")
    dzdx = (pad[1:-1, 2:] - pad[1:-1, :-2]) / (2 * cx)
    dzdy = (pad[2:, 1:-1] - pad[:-2, 1:-1]) / (2 * cy)
    slope_rad = np.arctan(np.sqrt(dzdx ** 2 + dzdy ** 2))
    aspect_rad = np.arctan2(-dzdx, dzdy)  # 罗盘坡向（顺时针自北）
    alt_rad = np.radians(float(altitude))
    sin_alt = np.sin(alt_rad)
    cos_alt = np.cos(alt_rad)
    shade_slope_part = sin_alt * np.cos(slope_rad)
    shade_aspect_part = cos_alt * np.sin(slope_rad)
    shades = []
    for az in az_list:
        az_rad = np.radians(az)
        hs = shade_slope_part + shade_aspect_part * np.cos(az_rad - aspect_rad)
        shades.append(np.clip(hs * 255.0, 0.0, 255.0))

    stack = np.stack(shades, axis=0)
    shade = np.mean(stack, axis=0) if combine == "mean" else np.min(stack, axis=0)

    meta = _meta_base(
        "terrain.hillshade_multi", valid,
        cell_size=cy, cell_size_x=cx,
        altitude_degrees=float(altitude),
        azimuths=[round(a, 6) for a in az_list],
        combine=combine,
        method=(
            "multi-azimuth hillshade: per-azimuth compass illumination identical to "
            "band_math.compute_hillshade (Horn gradient, #379 compass semantics); "
            "combined by mean or per-cell min"),
        truth_source="app/services/rs/band_math.py compute_hillshade (replicated to avoid a lib->services dependency)",
        units="0-255 illumination",
        edge_policy="3x3 Horn stencil edge-replicated (same as band_math slope/hillshade)",
    )
    return shade, meta


# ── Science V4（W9）：hypsometry / solar radiation ─────────────────────────

def hypsometry(
    dem: np.ndarray, cell_size: float,
    cell_size_x: Optional[float] = None,
    *,
    n_levels: int = 100,
    nodata: Optional[float] = None,
) -> Tuple[Tuple[np.ndarray, np.ndarray], Dict[str, Any]]:
    """高程面积曲线（hypsometric curve）与高程积分（Strahler 1952）。

    曲线 = (归一化高程 e, 高于 e 的面积占比 a(e))，n_levels 级确定性直方；
    高程积分 HI = ∫a de（矩形 = 1：侵蚀循环阶段的机器可读代理——
    HI≈1.5·... 口径为 [0,1] 矩形归一）。返回 ``(curve, meta)``。
    """
    z, valid = _prepare(dem, nodata)
    h, w = z.shape
    _guard_cells((h, w), "terrain.hypsometry")
    if not (2 <= int(n_levels) <= 1000):
        raise ValueError(f"n_levels must be in [2, 1000] (got {n_levels!r})")
    zv = z[valid]
    if zv.size == 0:
        raise NoValidObservations("hypsometry: no valid cells")
    zmin, zmax = float(zv.min()), float(zv.max())
    if zmax <= zmin:
        curve = (np.array([0.0, 1.0]), np.array([1.0, 0.0]))
        meta = _meta_base(
            "terrain.hypsometry", valid,
            method="constant surface (degenerate): integral undefined → 0.0",
            hypsometric_integral=0.0, n_levels=[zmin, zmax],
            elevation_range=[zmin, zmax], degenerate=True,
        )
        return curve, meta
    # 高于 e 的面积占比（确定性直方；每级中点）
    counts, edges = np.histogram(zv, bins=int(n_levels), range=(zmin, zmax))
    total = float(counts.sum())
    mids = (edges[:-1] + edges[1:]) / 2.0
    above = np.cumsum(counts[::-1])[::-1] / total  # a(e_mid)：e 以上占比
    elev_norm = (mids - zmin) / (zmax - zmin)
    # HI = ∫₀¹ a(e) de（梯形；a 已随 e 增单调降至 ~0）
    hi = float(np.trapezoid(above, elev_norm))
    curve = (elev_norm, above)
    meta = _meta_base(
        "terrain.hypsometry", valid,
        method=(
            "hypsometric curve a(e) = area fraction above normalized "
            "elevation e; integral = ∫a de (rectangle = 1; Strahler 1952)"),
        hypsometric_integral=round(hi, 6),
        n_levels=int(n_levels),
        elevation_range=[round(zmin, 4), round(zmax, 4)],
    )
    return curve, meta


def solar_radiation(
    dem: np.ndarray, cell_size: float,
    cell_size_x: Optional[float] = None,
    *,
    latitude_deg: float = 30.0,
    day_of_year: int = 172,
    transmissivity: float = 0.75,
    nodata: Optional[float] = None,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """晴空日总辐照量近似（FAO-56 大气顶辐射 × 地形入射修正）。

    - 大气顶日辐射 Ra（FAO-56 eq. 21：dr / δ / ωs 解析式，Gsc=0.0820）；
    - 晴空透射 Rso = transmissivity · Ra（缺省 0.75，海拔修正未含——披露）；
    - 地形入射修正：坡度/坡向（np.gradient 米制）上的近似投影因子
      f = cos(β) + sin(β)·cos(az_sun − aspect)，钳 ≥ 0.15（散射底——
      近似语义，approximation_class=heuristic，披露：无地平线遮蔽积分）。
    返回 ``(insolation (MJ m⁻² day⁻¹), meta)``。
    """
    z, valid = _prepare(dem, nodata)
    h, w = z.shape
    _guard_cells((h, w), "terrain.solar_radiation")
    if not (-90.0 <= float(latitude_deg) <= 90.0):
        raise ValueError(f"latitude_deg out of range: {latitude_deg!r}")
    if not (1 <= int(day_of_year) <= 366):
        raise ValueError(f"day_of_year out of range: {day_of_year!r}")
    if not (0.1 <= float(transmissivity) <= 1.0):
        raise ValueError(f"transmissivity out of range: {transmissivity!r}")

    doy = float(int(day_of_year))
    phi = math.radians(float(latitude_deg))
    dr = 1.0 + 0.033 * math.cos(2.0 * math.pi * doy / 365.0)
    delta = 0.409 * math.sin(2.0 * math.pi * doy / 365.0 - 1.39)
    x = -math.tan(phi) * math.tan(delta)
    omega_s = math.acos(max(-1.0, min(1.0, x)))
    Gsc = 0.0820
    ra = (24.0 * 60.0 / math.pi) * Gsc * dr * (
        omega_s * math.sin(phi) * math.sin(delta)
        + math.cos(phi) * math.cos(delta) * math.sin(omega_s))
    clear_sky = float(transmissivity) * ra  # MJ m⁻² day⁻¹

    cy, cx = _validate_cell_sizes(cell_size, cell_size_x)
    gy, gx = np.gradient(z, cy, cx)
    slope = np.arctan(np.sqrt(gx ** 2 + gy ** 2))
    aspect = np.arctan2(-gx, -gy)  # 下坡向（数学方位角；0=北 → 转 0=东约定）
    # 太阳方位近似：正午太阳方位 = 赤纬决定（北半球夏偏南）；取
    # az_sun = π + delta（弧度，0=北顺时针）作为日积分代表方位（近似披露）。
    az_sun = math.pi + delta
    aspect_from_south = aspect + math.pi / 2.0 - az_sun
    incidence = np.cos(slope) + np.sin(slope) * np.cos(aspect_from_south)
    factor = np.clip(incidence, 0.15, None)
    insolation = np.where(valid, clear_sky * factor, np.nan)

    meta = _meta_base(
        "terrain.solar_radiation", valid,
        method=(
            "clear-sky daily insolation: FAO-56 extraterrestrial Ra × "
            "transmissivity × terrain incidence factor "
            "(cos β + sin β·cos(az_sun − aspect), clipped ≥ 0.15 diffuse floor)"),
        model="FAO-56 Ra + heuristic incidence (no horizon/shadow integration)",
        latitude_deg=float(latitude_deg),
        day_of_year=int(day_of_year),
        transmissivity=float(transmissivity),
        extraterrestrial_ra=round(float(ra), 4),
        insolation_range=[
            round(float(np.nanmin(insolation[valid])), 4),
            round(float(np.nanmax(insolation[valid])), 4),
        ],
        mean_insolation=round(float(np.nanmean(insolation[valid])), 4),
    )
    return insolation, meta


# ── Science V4（W10）：分块 Priority-Flood（大栅格通道）─────────────────────

PF_CHUNK_BANDS = 8            # 缺省列带数
_PF_CHUNK_MIN_COLS = 64       # 低于此列数不分块（全量路径更高效）


def fill_depressions_chunked(
    dem: np.ndarray, cell_size: float,
    cell_size_x: Optional[float] = None,
    *,
    epsilon: float = 0.0,
    nodata: Optional[float] = None,
    n_bands: int = PF_CHUNK_BANDS,
    max_sweeps: int = 8,
    cancellable_check: bool = True,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """列带（band）分块 Priority-Flood + 交替方向迭代到收敛。

    语义：每带用与 :func:`fill_depressions` 完全同一 heapq 泛洪机器
    （单一事实源）；排水种子 = 网格真边界 + nodata 邻接 + 邻带裁决缘
    （带内竖直边缘不是排水口——seam correctness 关键）。带缘以邻带当前
    填充列做排水裁决。**近似语义（approximate）**：带固定点 ≠ 全局
    最小-最大路径解——seam 处可欠填或过填，偏差以参考解的最大填深为界
    （conformance 钉死 |chunked − full| ≤ reference max_fill_depth）；
    需要精确解时使用全量路径（reference variant）。heap 峰值内存
    O(带宽×H)——大栅格低堆占用通道。
    """
    z, valid = _prepare(dem, nodata)
    h, w = z.shape
    _guard_cells((h, w), "terrain.sink_fill_chunked")
    if not (1 <= int(n_bands) <= 64):
        raise ValueError(f"n_bands must be in [1, 64] (got {n_bands!r})")
    if w < _PF_CHUNK_MIN_COLS:
        n_bands = 1
    band_w = int(math.ceil(w / int(n_bands)))
    filled = z.copy()
    sweeps = 0
    changed = 0
    for sweep in range(int(max_sweeps)):
        if cancellable_check:
            checkpoint()
        sweeps += 1
        changed = 0
        order = (range(int(n_bands)) if sweep % 2 == 0
                 else range(int(n_bands) - 1, -1, -1))
        for b in order:
            c0 = b * band_w
            c1 = min(c0 + band_w, w)
            # Jacobi 迭代：每带从**原始 z** 重新泛洪（只升不降的泛洪不能
            # 从上一轮的过填值收敛回真实解——过填必须允许被修正）。
            band_z = z[:, c0:c1].copy()
            band_valid = valid[:, c0:c1]
            bh, bw = band_z.shape
            invalid = ~band_valid
            near_invalid = np.zeros((bh, bw), dtype=bool)
            for _, _, dr, dc in _D8_NEIGHBORS:
                r0, r1 = max(0, -dr), min(bh, bh - dr)
                cc0, cc1 = max(0, -dc), min(bw, bw - dc)
                shifted = np.zeros((bh, bw), dtype=bool)
                shifted[r0:r1, cc0:cc1] = invalid[r0 + dr:r1 + dr, cc0 + dc:cc1 + dc]
                near_invalid |= shifted
            seed = band_valid & near_invalid
            seed[0, :] |= band_valid[0, :]
            seed[-1, :] |= band_valid[-1, :]
            if b == 0:
                seed[:, 0] |= band_valid[:, 0]
            if b == int(n_bands) - 1:
                seed[:, -1] |= band_valid[:, -1]
            for edge, outside in ((0, c0 - 1), (bw - 1, c1)):
                if 0 <= outside < w:
                    # 邻带裁决 = 邻带**当前累计**填充值（收敛解的下界估计，
                    # 单调升 → 整体单调收敛到全量 flood 不动点）
                    nb_col = filled[:, outside]
                    verdict = np.maximum(band_z[:, edge], nb_col)
                    changed += int(np.count_nonzero(
                        band_valid[:, edge] & (np.abs(verdict - filled[:, c0:c1][:, edge]) > 1e-12)))
                    band_z[:, edge] = verdict
                    seed[:, edge] |= band_valid[:, edge]
            known = seed.copy()
            heap: List[Tuple[float, int, int]] = []
            counter = 0
            for flat_i in np.flatnonzero(seed.ravel()).tolist():
                heapq.heappush(heap, (float(band_z.ravel()[flat_i]), counter, flat_i))
                counter += 1
            pops = 0
            while heap:
                pops += 1
                if cancellable_check and pops % 65536 == 0:
                    checkpoint()
                elev, _, cur = heapq.heappop(heap)
                cur_r, cur_c = divmod(cur, bw)
                for _, _, dr, dc in _D8_NEIGHBORS:
                    nb_r, nb_c = cur_r + dr, cur_c + dc
                    if not (0 <= nb_r < bh and 0 <= nb_c < bw):
                        continue
                    nb = nb_r * bw + nb_c
                    if not band_valid.ravel()[nb] or known.ravel()[nb]:
                        continue
                    known.ravel()[nb] = True
                    target = elev + epsilon
                    z_nb = float(band_z.ravel()[nb])
                    new_v = z_nb if z_nb > target else target
                    band_z.ravel()[nb] = new_v
                    heapq.heappush(heap, (band_z.ravel()[nb], counter, nb))
                    counter += 1
            diff_cells = int(np.count_nonzero(
                band_valid & (np.abs(band_z - filled[:, c0:c1]) > 1e-12)))
            changed += diff_cells
            filled[:, c0:c1] = band_z
        if changed == 0:
            break
    lift = filled - z
    meta = _meta_base(
        "terrain.sink_fill_chunked", valid,
        cell_size=cell_size,
        epsilon=float(epsilon),
        method=(
            "banded priority-flood (approximate): per-band heapq flood, "
            "neighbour-band verdicts on band edges; over-fill possible at "
            "seams (verdict = neighbour water level) — use full "
            "fill_depressions for the exact reference"),
        n_bands=int(n_bands),
        max_sweeps=int(max_sweeps),
        sweeps_executed=int(sweeps),
        converged=bool(changed == 0),
        approximation=(
            "band fixed-point ≠ global min-max path: seams may under/over-fill "
            "(bounded by reference max_fill_depth; exact = fill_depressions)"),
        filled_volume=round(float(lift.sum()), 9),
        filled_cell_count=int(np.count_nonzero(lift > 0.0)),
        max_fill_depth=round(float(lift.max()) if valid.any() else 0.0, 9),
    )
    return filled, meta
