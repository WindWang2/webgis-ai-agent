"""Terrain science pure functions（ADR-0099 terrain 域包实现层）。

VNext 地形科学库：TPI / TRI / 粗糙度 / 曲率（Zevenbergen-Thorne）/
视域（扇区视线角扫描）/ D8 流向与汇流累积（拓扑序）/ 逆 D8 流域 /
等值线（matplotlib Agg marching squares）。

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

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.lib.gis.scientific_errors import NoValidObservations

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
]

EDGE_POLICY = "edge cells use available neighbors (window statistics shrink at the border; no padding values are invented)"

MIN_WINDOW = 3
MAX_WINDOW = 101

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
    """rasterio 仿射 (a,b,c,d,e,f) → (col, row)（解 2x2 线性系统）。"""
    a, b, c, d, e, f = (float(v) for v in transform[:6])
    det = a * e - b * d
    if det == 0:
        raise ValueError(f"degenerate raster transform (det=0): {tuple(transform[:6])}")
    col = (e * (x - c) - b * (y - f)) / det
    row = (a * (y - f) - d * (x - c)) / det
    return col, row


def _cell_to_world(
    transform: Sequence[float], cols: np.ndarray, rows: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray]:
    a, b, c, d, e, f = (float(v) for v in transform[:6])
    xs = a * cols + b * rows + c
    ys = d * cols + e * rows + f
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
) -> Tuple[Dict[str, np.ndarray], Dict[str, Any]]:
    """D8 单向流（ESRI 2 的幂编码；O'Callaghan & Mark / Tarboton 1997 语境）。

    语义（精确）：

    - 坡降 slope_k = (z − z_k) / dist(中心, k)，dist 按 x/y 像元地面
      尺寸（各向异性地理栅格可区分）取米制欧氏距离；
    - 取最大**严格为正**坡降；并列最陡 → 最低索引邻域（E=1 起的编码序，
      确定性裁决）；
    - 无严格更低邻域（洼地/平地/边界外流）→ 编码 0 = sink/outlet
      （boundary = outlet）。平地不做 epsilon 梯度路由（防拓扑环，
      limitations 披露）；
    - nodata 邻域不参与；全 nodata 输入 → NoValidObservations。
    """
    if cell_size <= 0 or (cell_size_x is not None and cell_size_x <= 0):
        raise ValueError("cell sizes must be positive")
    cx = float(cell_size_x if cell_size_x is not None else cell_size)
    cy = float(cell_size)
    z, valid = _prepare(dem, nodata)
    h, w = z.shape
    dists = _neighbor_distances(cx, cy)

    direction = np.zeros((h, w), dtype=np.int16)
    best_slope = np.zeros((h, w), dtype=np.float64)
    for idx, (_, code, dr, dc) in enumerate(_D8_NEIGHBORS):
        zs = np.full_like(z, np.nan)
        vs = np.zeros_like(valid)
        r0, r1 = max(0, -dr), min(h, h - dr)
        c0, c1 = max(0, -dc), min(w, w - dc)
        zs[r0:r1, c0:c1] = z[r0 + dr:r1 + dr, c0 + dc:c1 + dc]
        vs[r0:r1, c0:c1] = valid[r0 + dr:r1 + dr, c0 + dc:c1 + dc]
        slope = np.where(vs, (z - zs) / dists[idx], -np.inf)
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
        "dem": z,                        # 高程（flow_accumulation 拓扑序用）
    }
    meta = _meta_base(
        "terrain.flow_d8", valid,
        cell_size=cy, cell_size_x=cx,
        encoding="ESRI powers-of-two: 1=E, 2=SE, 4=S, 8=SW, 16=W, 32=NW, 64=N, 128=NE; 0 = sink/outlet (no strictly lower in-grid neighbor)",
        tie_break="steepest-descent ties resolved to the lowest-index neighbor (E first)",
        flats="flats/pits are sinks (code 0); no epsilon-gradient flat routing (prevents cycles in topological accumulation)",
        boundary="grid boundary is the outlet: flow that would leave the grid terminates (cells only route to in-grid neighbors)",
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
