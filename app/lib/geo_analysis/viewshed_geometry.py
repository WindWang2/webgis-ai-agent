"""视域布尔栅格 → 可视几何产物（G3D-V2-C / ADR-0276）。

#1829 时代 ``viewshed_analysis`` 只有 stats——布尔栅格在工具层被丢弃，
"视域面"在制图侧完全缺席（组合模板 ``viewshed_scene_3d`` 的视域面无生产
供给）。本模块补**几何可视化产物层**，与算法计算分离（ADR-0274 数值
内核零改动——``terrain.viewshed`` 的可见性判定不因本模块存在而变化）。

职责（CONTRACT_BACKBONE §1 与 measurements.py 同）：

- 纯内存计算：bool 掩膜 → GeoJSON Polygon（含洞），不读文件、不写
  artifact、不挂证据块；
- 诚实边界：多边形按**整像元**归并（cell-edge 对齐）——像元是视域判定的
  原子单位，多边形覆盖恰为"被声称为可见的像元"全体，不外插半像元；
  nodata 像元不进入任何多边形（无声明即无覆盖）；
- 有界输出：多边形数/顶点数双预算；超预算时按面积阈值升档淘汰小多边形
  （确定性二进位阶梯），被淘汰面积在 meta 披露为
  ``unattributed_visible_area_m2`` —— 保留 stats 的诚实性，不静默缩水。

通用性：``raster_mask_polygons`` 是通用 bool 栅格 → GeoJSON 面提取器
（viewshed 可视面 / cut/fill 分区共用），不绑定视域语义。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.lib.cancellation import checkpoint
from app.lib.gis.scientific_errors import DegenerateData

__all__ = [
    "MAX_VISIBILITY_POLYGONS",
    "MAX_VISIBILITY_VERTICES",
    "raster_mask_polygons",
    "visibility_geometry",
]

#: 输出多边形数上限（超过按面积淘汰最小的，meta 披露）。
MAX_VISIBILITY_POLYGONS = 48
#: 输出总顶点预算（超预算触发面积阈值升档）。
MAX_VISIBILITY_VERTICES = 20_000
#: 面积淘汰阈值阶梯（像元数，确定性二进位）：1 → 2 → 4 → …
_MAX_AREA_STEP_CELLS = 64


def _edge_to_world(
    transform: Sequence[float], coords: Sequence[Sequence[float]]
) -> List[List[float]]:
    """rasterio.features 像元**边**坐标 → 世界坐标。

    shapes() 输出的顶点在像元角点空间（整数 = 像元边界）；GDAL 仿射
    原点即 UL 角点，故直接过仿射（与 ``_cell_to_world`` 的中心索引
    语义差 +0.5，见其注释）。
    """
    a, b, c, d, e, f = (float(v) for v in transform[:6])
    out: List[List[float]] = []
    for pt in coords:
        ex, ey = float(pt[0]), float(pt[1])
        out.append([a * ex + b * ey + c, d * ex + e * ey + f])
    return out


def _ring_area(ring: List[List[float]]) -> float:
    """世界坐标环的有符号面积（shoelace；外环 CCW 为正——与 GeoJSON 规范一致）。"""
    s = 0.0
    n = len(ring)
    for i in range(n - 1):
        x1, y1 = ring[i]
        x2, y2 = ring[i + 1]
        s += x1 * y2 - x2 * y1
    return 0.5 * s


def raster_mask_polygons(
    mask: np.ndarray,
    transform: Sequence[float],
    *,
    valid: Optional[np.ndarray] = None,
    max_polygons: int = MAX_VISIBILITY_POLYGONS,
    max_total_vertices: int = MAX_VISIBILITY_VERTICES,
    kind: str = "raster_mask",
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """bool 栅格掩膜 → GeoJSON FeatureCollection（有界、确定性）。

    - 掩膜为 True 的像元（``valid`` 给出时还须有效）归并为 cell-edge
      对齐的多边形（4-连通），含洞；世界坐标由 ``transform`` 仿射给出；
    - 输出有界：多边形按面积降序保留至多 ``max_polygons`` 个；顶点总数
      超 ``max_total_vertices`` 时按面积阈值（像元数二进位阶梯）升档
      淘汰直至入预算或达 ``_MAX_AREA_STEP_CELLS``；
    - 被淘汰的像元不消失：``unattributed_cells`` /
      ``unattributed_area_m2`` 在 meta 中披露（stats 与几何可对账）。

    返回 (feature_collection, meta)。空掩膜 → 空 FeatureCollection +
    meta（不抛错——"无可见区"是合法结果，由调用方语义决定是否降级）。
    """
    mask_b = np.asarray(mask, dtype=bool)
    if mask_b.ndim != 2:
        raise ValueError(f"mask must be 2-D (got shape {mask_b.shape})")
    if valid is not None:
        valid_b = np.asarray(valid, dtype=bool)
        if valid_b.shape != mask_b.shape:
            raise ValueError(
                f"valid shape {valid_b.shape} does not match mask {mask_b.shape}"
            )
        mask_b = mask_b & valid_b
    n_cells = int(np.count_nonzero(mask_b))

    fc: Dict[str, Any] = {"type": "FeatureCollection", "features": []}
    meta: Dict[str, Any] = {
        "kind": kind,
        "mask_cells": n_cells,
        "polygonization": "cell-edge aligned whole-cell merge (4-connectivity); "
        "polygons cover exactly the claimed cells — no sub-cell interpolation",
        "output_bounds": {
            "max_polygons": int(max_polygons),
            "max_total_vertices": int(max_total_vertices),
        },
    }
    if n_cells == 0:
        meta["polygon_count"] = 0
        meta["attributed_cells"] = 0
        meta["unattributed_cells"] = 0
        return fc, meta

    # ── 像元 → 多边形（rasterio.features：纯内存数组运算，无 IO）──────
    from rasterio.features import shapes as rio_shapes

    checkpoint()  # 多边形化前的协作取消点（大掩膜单次 shapes 可能秒级）
    src = mask_b.astype(np.uint8)
    polys: List[Tuple[List[List[List[float]]], float]] = []  # (rings, net |area|)
    for geom, value in rio_shapes(src, mask=mask_b, connectivity=4):
        if value != 1:
            continue
        rings: List[List[List[float]]] = []
        ext_abs = 0.0
        holes_abs = 0.0
        for ri, ring in enumerate(geom.get("coordinates", [])):
            world = _edge_to_world(transform, ring)
            if len(world) < 4 or world[0] != world[-1]:
                world.append(list(world[0]))
            signed = _ring_area(world)
            # GeoJSON RFC 7946：外环 CCW（有向面积 > 0）、洞 CW（< 0）。
            # 北向上图仿射 y 翻转（det<0）会把像元空间的环取向镜像——
            # 统一在世界坐标侧归一，消费方（MapLibre/GeoJSON 解析器）
            # 无需容错猜测。
            want_positive = ri == 0
            if (signed > 0) != want_positive and signed != 0:
                world.reverse()
                signed = -signed
            rings.append(world)
            if ri == 0:
                ext_abs = abs(signed)
            else:
                holes_abs += abs(signed)
        polys.append((rings, ext_abs - holes_abs))

    if not polys:
        # 掩膜非空但未产出 value==1 的形状：视为退化（防御性——理论上
        # 不可达，rasterio 对非空 bool 掩膜必产出形状）。
        meta["polygon_count"] = 0
        meta["attributed_cells"] = 0
        meta["unattributed_cells"] = n_cells
        return fc, meta

    # 面积换算像元数（仿射面积缩放恒等：世界面积 = 像元数 × |det|——
    # 矩形格上精确，用于淘汰排序与对账披露）。
    a, b, _c, d, e, _f = (float(v) for v in transform[:6])
    cell_area = abs(a * e - b * d)
    if not (cell_area > 0 and math.isfinite(cell_area)):
        raise ValueError(
            f"degenerate raster transform (cell area {cell_area!r})"
        )

    # ── 有界化：先按面积排序截断多边形数，再按顶点预算升档面积阈值 ──
    polys.sort(key=lambda item: item[1], reverse=True)

    def _vertex_count(items: List[Tuple[List[List[List[float]]], float]]) -> int:
        return sum(len(ring) for rings, _ in items for ring in rings)

    kept = polys[: max(1, int(max_polygons))]
    min_cells = 1
    while (
        _vertex_count(kept) > max_total_vertices
        and min_cells < _MAX_AREA_STEP_CELLS
    ):
        min_cells *= 2
        kept = [p for p in polys if p[1] >= min_cells * cell_area][: max_polygons]
        if not kept:
            kept = polys[:1]  # 至少保留最大多边形（几何通道不空转）
            break
    checkpoint()

    kept_ids = {id(p) for p in kept}
    dropped = [p for p in polys if id(p) not in kept_ids]
    kept_area = sum(p[1] for p in kept)
    dropped_area = sum(p[1] for p in dropped)

    features: List[Dict[str, Any]] = []
    for rings, _area_crs in kept:
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "kind": kind,
                    "n_holes": max(0, len(rings) - 1),
                },
                "geometry": {"type": "Polygon", "coordinates": rings},
            }
        )
    fc["features"] = features
    # 像元原子记账（CRS 无关）：多边形面积（shoelace，CRS 单位²）/
    # |det|（单像元面积，CRS 单位²）= 像元数。整數化前先做解析一致性
    # 校验——cell-edge 对齐多边形面积必为 |det| 整数倍，偏差 > 1e-6
    # 相对值即实现错误（带病上图不如立即失败）。
    kept_area_crs = sum(p[1] for p in kept)
    dropped_area_crs = sum(p[1] for p in dropped)
    total_area_crs = kept_area_crs + dropped_area_crs
    expected_crs = n_cells * cell_area
    if abs(total_area_crs - expected_crs) > 1e-6 * max(1.0, expected_crs):
        raise DegenerateData(
            f"mask polygon area {total_area_crs:.9g} does not reconstruct "
            f"the cell claim {expected_crs:.9g} (CRS units²) — polygonizer "
            "is inconsistent with the cell-atomic claim"
        )
    attributed_cells = int(round(kept_area_crs / cell_area))
    meta["polygon_count"] = len(kept)
    meta["attributed_cells"] = attributed_cells
    meta["dropped_polygon_count"] = len(dropped)
    meta["unattributed_cells"] = n_cells - attributed_cells
    meta["area_threshold_cells"] = min_cells
    meta["vertex_count"] = _vertex_count(kept)
    meta["polygon_area_crs_units2"] = round(kept_area_crs, 9)
    return fc, meta


def visibility_geometry(
    visible: np.ndarray,
    valid: np.ndarray,
    transform: Sequence[float],
    *,
    cell_size: float,
    cell_size_x: Optional[float] = None,
    max_polygons: int = MAX_VISIBILITY_POLYGONS,
    max_total_vertices: int = MAX_VISIBILITY_VERTICES,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """viewshed 布尔栅格 → 可视区 GeoJSON + 覆盖/误差/来源证据 meta。

    输入即 ``terrain.viewshed`` 的 ``result["visible"]`` 与 DEM 有效性掩膜
    （``_prepare`` 的 valid）——本函数不重算可见性，只做几何归并与披露：

    - 多边形覆盖 = ``visible & valid`` 像元全体（cell-edge 对齐，nodata
      像元不进任何多边形）；
    - 覆盖对账不变量：``kept_polygon_area_m2 + unattributed_area_m2 ==
      visible_cells × cell_area``（像元是原子单位——多边形不重不漏）；
    - meta 携带有效覆盖比例（valid 像元 / 全像元）、几何化率
      （归入多边形的可见像元 / 可见像元）与有效分辨率（cell 尺寸）。

    stats 零漂移声明：本函数不触碰 ``terrain.viewshed`` 的数值路径；
    ``visible_fraction`` 等统计仍由内核产出，调用方原样透传。
    """
    visible_b = np.asarray(visible, dtype=bool)
    valid_b = np.asarray(valid, dtype=bool)
    if visible_b.shape != valid_b.shape:
        raise ValueError(
            f"visible shape {visible_b.shape} does not match valid {valid_b.shape}"
        )
    cy = float(cell_size)
    cx = float(cell_size_x if cell_size_x is not None else cell_size)
    if not (cy > 0 and cx > 0):
        raise ValueError("cell sizes must be positive")
    h, w = visible_b.shape
    total_cells = h * w
    valid_cells = int(np.count_nonzero(valid_b))
    visible_cells = int(np.count_nonzero(visible_b & valid_b))

    fc, gmeta = raster_mask_polygons(
        visible_b,
        transform,
        valid=valid_b,
        max_polygons=max_polygons,
        max_total_vertices=max_total_vertices,
        kind="visibility",
    )
    # 米制核算（cx/cy 由调用方按 cos-lat 政策换算）：像元数 × 米制像元
    # 面积；几何模块内部的 CRS 单位面积不与米制混算（地理 CRS 的
    # shoelace 面积是度²，直接冒充 m² 会引入 ~1e10 倍错误）。
    cell_area = cx * cy
    attributed_cells = int(gmeta.get("attributed_cells", 0))
    unattributed_cells = int(gmeta.get("unattributed_cells", 0))

    meta: Dict[str, Any] = {
        "source": "terrain.viewshed boolean raster (kernel untouched)",
        "visible_cells": visible_cells,
        "valid_cells": valid_cells,
        "total_cells": total_cells,
        "valid_coverage_fraction": round(valid_cells / total_cells, 6)
        if total_cells
        else 0.0,
        "attributed_visible_area_m2": round(attributed_cells * cell_area, 3),
        "unattributed_visible_area_m2": round(unattributed_cells * cell_area, 3),
        "geometry_attribution_fraction": round(
            attributed_cells / visible_cells, 6
        )
        if visible_cells
        else 1.0,
        "effective_resolution_m": [round(cx, 6), round(cy, 6)],
        "geometry_meta": gmeta,
        "method": "whole-cell polygon merge of the viewshed boolean raster "
        "(4-connectivity, cell-edge aligned); visibility itself is the "
        "kernel's cell-level verdict — no sub-cell claims are made; metric "
        "areas are cell-count × metric cell area (never CRS-unit shoelace)",
    }
    return fc, meta
