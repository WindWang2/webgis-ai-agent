"""指定区域的基准面土方量（cut/fill volume，G3D-V2-C / ADR-0276）。

#1773（ADR-0274）时代明确的未做量测：体积。本模块在**充分 DEM 数据**
（合格高程栅格 + 显式基准面）下开放：

- ``cut_fill_volume``：区域（bool 掩膜）内 DEM 相对基准面的挖方（cut，
  地表高于基准面须挖除）/ 填方（fill，低于基准面须填筑）/ 净方
  （net = cut − fill，符号约定 disclosed）。

数值方法（诚实披露，无角点发明）：

- **像元中心中点求积**：每像元贡献 prism = (z_center − base) × cell_area
  （截断正部为 cut、负部为 fill）。中点求积对 ≤1 次多项式表面**精确**
  （解析 oracle：平面/斜面闭式可验——tests/unit/lib/test_terrain_volumes.py）；
  曲面上为离散近似，误差随分辨率收敛（O(h²) 经验披露，不宣称解析界）。
  与 surface_area 的"像元中心是唯一观测"纪律一致——**不发明角点高程**。

垂直基准治理（复用 ADR-0274）：z_units/vertical_datum 经
``resolve_vertical_reference``；基准面绝对高程声明在不同 datum →
``VERTICAL_REFERENCE_MISMATCH`` 类型化拒绝（跨 datum 混算零容忍）。

边界与 nodata 诚实披露：

- 掩膜像元是 all-or-nothing（多边形栅格化在工具层完成）：部分覆盖像元
  全计入/全排除由掩膜决定，meta 披露 boundary_cells（掩膜边缘像元数）
  供误差评估；
- nodata 像元剔除并披露 invalid_fraction，超 ``max_invalid_fraction``
  类型化失败（宁拒不给出错的体积）；
- 基准面 "mean" 模式 = 有效掩膜像元高程的算术均值（基于同一中点求积
  口径，net = 0 是解析恒等式——测试对账）。

单位：高程经 z_to_m 换算为米后与米制像元面积相乘 → 体积恒为 m³；
ft DEM 精确 ×0.3048（transformations 披露）。
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np

from app.lib.geo_analysis.terrain import _prepare, _validate_cell_sizes, _guard_cells
from app.lib.geo_analysis.measurements import resolve_vertical_reference

__all__ = [
    "REFERENCE_MODES",
    "cut_fill_volume",
]

#: 基准面模式词表。
REFERENCE_MODES = ("constant", "mean")


def cut_fill_volume(
    dem: np.ndarray,
    cell_size: float,
    cell_size_x: Optional[float] = None,
    *,
    mask: Optional[np.ndarray] = None,
    reference: str = "constant",
    base_z: Optional[float] = None,
    nodata: Optional[float] = None,
    max_invalid_fraction: float = 0.5,
    z_units: str = "m",
    vertical_datum: str = "unknown",
    override_vertical_datum: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """基准面土方量：(stats, meta)。

    Args:
        dem: 高程栅格（z_units 单位）。
        cell_size / cell_size_x: y/x 向米制地面尺寸（地理 CRS 由调用方
            按 cos(lat) 政策换算——与 measurements 族同口径）。
        mask: bool (h, w)，True = 计入区域（多边形栅格化在工具层）；
            缺省全 True。
        reference: "constant"（``base_z`` 绝对基准面）| "mean"（区域有效
            像元均值基准面——平基检验/平衡挖填用）。
        base_z: constant 模式的基准面绝对高程（DEM z_units；垂直 datum
            声明须与 DEM 一致，否则类型化拒绝）。
        override_vertical_datum: 基准面高程的垂直基准声明（缺省继承
            DEM 声明）。

    Returns:
        stats: ``cut_volume_m3`` / ``fill_volume_m3`` / ``net_volume_m3``
            （net = cut − fill：正 = 挖除多于填筑）/ 基准面事实 / 深度
            极值 / 像元记账。
        meta: 采样口径 / nodata 政策 / 垂直基准 / 边界披露 / 方法。
    """
    cy, cx = _validate_cell_sizes(cell_size, cell_size_x)
    if reference not in REFERENCE_MODES:
        raise ValueError(
            f"reference must be one of {REFERENCE_MODES} (got {reference!r})"
        )
    vref = resolve_vertical_reference(
        vertical_datum, z_units, override_vertical_datum
    )
    if reference == "constant":
        if base_z is None or not math.isfinite(float(base_z)):
            raise ValueError(
                "reference='constant' requires a finite base_z "
                "(absolute elevation in the DEM's z_units)"
            )
        base_crs = float(base_z)
        base_mode = "constant"
    else:
        if base_z is not None:
            raise ValueError(
                "reference='mean' derives the base from the region itself — "
                "base_z must be omitted"
            )
        base_crs = None
        base_mode = "mean"

    z, finite = _prepare(dem, nodata)
    _guard_cells(z.shape, "terrain.cut_fill_volume")
    h, w = z.shape
    if mask is None:
        mask_b = np.ones((h, w), dtype=bool)
    else:
        mask_b = np.asarray(mask, dtype=bool)
        if mask_b.shape != (h, w):
            raise ValueError(f"mask shape {mask_b.shape} does not match DEM {(h, w)}")
    region = mask_b & finite
    n_region = int(np.count_nonzero(mask_b))
    usable = int(np.count_nonzero(region))
    if n_region == 0:
        from app.lib.gis.scientific_errors import NoValidObservations

        raise NoValidObservations(
            "region mask is empty — no cells to measure (polygon outside "
            "the DEM or fully excluded?)"
        )
    if usable == 0:
        from app.lib.gis.scientific_errors import NoValidObservations

        raise NoValidObservations(
            "no usable cells in the region (every cell is nodata) — volume "
            "is undefined without elevation observations"
        )
    invalid_fraction = 1.0 - usable / n_region

    quality_flags = list(vref["flags"])
    if invalid_fraction > 0:
        quality_flags.append(f"invalid_cell_fraction:{invalid_fraction:.4f}")
    if invalid_fraction > max_invalid_fraction:
        from app.lib.gis.scientific_errors import NoValidObservations

        raise NoValidObservations(
            f"invalid cell fraction {invalid_fraction:.3f} exceeds "
            f"max_invalid_fraction {max_invalid_fraction} — a volume over "
            f"the remaining cells would misrepresent the site",
            correction_hint="repair the DEM, shrink the polygon to valid "
            "coverage, or raise max_invalid_fraction explicitly",
        )

    # 米制换算后统一计算（dz 米 × 面积 m² → 体积 m³）。
    z_m = z * vref["z_to_m"]
    if reference == "mean":
        base_m = float(z_m[region].mean())
        base_crs = base_m / vref["z_to_m"]
    else:
        base_m = base_crs * vref["z_to_m"]

    dz = z_m[region] - base_m
    cell_area = cx * cy
    cut_cells = dz > 0
    fill_cells = dz < 0
    cut_volume = float(dz[cut_cells].sum() * cell_area)
    fill_volume = float(-dz[fill_cells].sum() * cell_area)
    net_volume = cut_volume - fill_volume

    # 边界披露：掩膜边缘像元（4-邻域内存在掩膜外像元）计数——多边形
    # 栅格化的半像元误差上界与该计数同阶（保守披露，不假称精确边界）。
    if n_region < h * w:
        padded = np.pad(mask_b, 1, constant_values=False)
        neighbors_all_in = (
            padded[:-2, 1:-1] & padded[2:, 1:-1] & padded[1:-1, :-2] & padded[1:-1, 2:]
        )
        boundary_cells = int(np.count_nonzero(mask_b & ~neighbors_all_in))
    else:
        boundary_cells = 0
    if boundary_cells:
        quality_flags.append(f"boundary_cells:{boundary_cells}")

    stats = {
        "reference_mode": base_mode,
        "base_z": round(base_crs, 6),
        "cut_volume_m3": round(cut_volume, 4),
        "fill_volume_m3": round(fill_volume, 4),
        "net_volume_m3": round(net_volume, 4),
        "cut_cells": int(np.count_nonzero(cut_cells)),
        "fill_cells": int(np.count_nonzero(fill_cells)),
        "grade_cells": usable - int(np.count_nonzero(cut_cells)) - int(np.count_nonzero(fill_cells)),
        "usable_cells": usable,
        "region_cells": n_region,
        "planar_region_area_m2": round(usable * cell_area, 4),
        "cut_depth_max_m": round(float(dz.max()), 4) if cut_cells.any() else 0.0,
        "fill_depth_max_m": round(float(-dz.min()), 4) if fill_cells.any() else 0.0,
        "cut_depth_mean_m": round(float(dz[cut_cells].mean()), 4) if cut_cells.any() else 0.0,
        "fill_depth_mean_m": round(float(-dz[fill_cells].mean()), 4) if fill_cells.any() else 0.0,
    }
    meta = {
        "vertical_datum": vref["vertical_datum"],
        "z_units": vref["z_units"],
        "z_unit_transformations": vref["transformations"],
        "max_invalid_fraction": max_invalid_fraction,
        "invalid_fraction": round(invalid_fraction, 6),
        "boundary_cells": boundary_cells,
        "cell_area_m2": round(cell_area, 6),
        "effective_resolution_m": [round(cx, 6), round(cy, 6)],
        "quality_flags": quality_flags,
        "volume_unit": "m³ (metres; z converted via z_to_m, cell area metric)",
        "sign_convention": "net = cut − fill; positive means excavate more "
        "than you fill",
        "method": "cell-center midpoint quadrature: each usable cell "
        "contributes (z_center − base) × cell_area, truncated positive part "
        "counted as cut, negative as fill; midpoint rule is exact for "
        "planar (≤degree-1) surfaces — see analytical oracles; curved "
        "surfaces converge with resolution (no analytic bound claimed); "
        "cell centers are the only observation — no synthetic corner "
        "elevations are invented",
        "boundary_policy": "mask cells are all-or-nothing (polygon "
        "rasterization upstream); boundary_cells counts mask-edge cells as "
        "the honest order of the half-cell boundary discretization error",
    }
    return stats, meta
