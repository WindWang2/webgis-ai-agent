"""Vectorize —— 类别栅格 → 地理多边形（V3 §D）。

流水线：class raster（像素）→ ``rasterio.features.shapes``（4 连通，
确定性遍历）→ affine → 地理坐标 → 拓扑修复（make_valid + 可选网格
snap）→ Douglas-Peucker 简化（preserve_topology）→ 面积过滤 →
GeoJSON FeatureCollection。

确定性：同输入两次运行逐位一致（无随机源；输出按 (-area, class,
ring) 稳定排序）。``confidence`` 可选——按多边形内部均值附加属性。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np


@dataclass(frozen=True)
class VectorizeParams:
    """矢量化参数（进 manifest.postprocess；全部确定性）。"""

    simplify_tolerance_px: float = 1.0     # Douglas-Peucker 容差（像素）
    min_area_px: float = 4.0               # 最小多边形面积（像素²）
    grid_snap_px: float = 0.0              # 拓扑 snap 网格（0 = 关闭）

    def fingerprint_payload(self) -> Dict[str, Any]:
        return {
            "simplify_tolerance_px": self.simplify_tolerance_px,
            "min_area_px": self.min_area_px,
            "grid_snap_px": self.grid_snap_px,
        }


def vectorize_class_raster(
    classes: np.ndarray,                    # (H,W) uint8/uint16；255/ignore 跳过
    *,
    transform: Any,                         # rasterio Affine（像素→地理）
    class_names: Optional[Sequence[str]] = None,
    confidence: Optional[np.ndarray] = None,
    params: Optional[VectorizeParams] = None,
    ignore_value: int = 255,
    crs: Optional[str] = None,
) -> Dict[str, Any]:
    """类别栅格 → GeoJSON FeatureCollection（地理坐标；拓扑修复 + 简化）。"""
    params = params or VectorizeParams()
    try:
        from rasterio import features
        from rasterio.transform import xy as transform_xy
    except Exception as exc:  # pragma: no cover — rasterio 缺失环境
        from app.lib.modelops.errors import ModelOpsError

        raise ModelOpsError(f"vectorize unavailable: {exc}") from exc

    if classes.ndim != 2:
        raise ValueError(f"classes must be (H,W); got {classes.shape}")
    features_out: List[Dict[str, Any]] = []
    values = [int(v) for v in np.unique(classes) if int(v) != ignore_value]
    for value in values:
        mask = classes == value
        for geom_pixels, _ in features.shapes(
            mask.astype(np.uint8), mask=mask, connectivity=4
        ):
            record = _build_feature(
                geom_pixels,
                value=value,
                transform=transform,
                transform_xy=transform_xy,
                class_names=class_names,
                confidence=confidence,
                shape_hw=classes.shape,
                params=params,
            )
            if record is not None:
                features_out.append(record)
    # 稳定输出序：类升序 → 面积降序 → 首坐标（确定性，跨运行一致）。
    features_out.sort(
        key=lambda f: (
            int(f["properties"]["class"]),
            -float(f["properties"]["area_px"]),
            tuple(f["geometry"]["coordinates"][0][0]),
        )
    )
    collection: Dict[str, Any] = {"type": "FeatureCollection", "features": features_out}
    if crs:
        collection["crs"] = {"type": "name", "properties": {"name": str(crs)}}
    return collection


def _build_feature(
    geom_pixels: Dict[str, Any],
    *,
    value: int,
    transform: Any,
    transform_xy: Any,
    class_names: Optional[Sequence[str]],
    confidence: Optional[np.ndarray],
    shape_hw: Tuple[int, int],
    params: VectorizeParams,
) -> Optional[Dict[str, Any]]:
    """单多边形：地理变换 → 拓扑修复 → 简化 → 面积过滤 → 属性。"""
    from shapely.geometry import shape, mapping
    from shapely.validation import make_valid

    geom = shape(geom_pixels)
    if params.grid_snap_px > 0:
        try:
            from shapely import set_precision

            geom = set_precision(geom, grid_size=float(params.grid_snap_px))
        except Exception:  # noqa: BLE001 — set_precision 不可用时跳过 snap
            pass
    topology_repaired = False
    if not geom.is_valid:
        geom = make_valid(geom)
        topology_repaired = True
    if geom.is_empty:
        return None
    if params.simplify_tolerance_px > 0:
        geom = geom.simplify(params.simplify_tolerance_px, preserve_topology=True)
        if geom.is_empty:
            return None
    if geom.area < params.min_area_px:
        return None
    # 像素坐标 → 地理坐标（角点变换；与 artifacts.py 的检测框同一口径）。
    geom_geo = _transform_geom(geom, transform, transform_xy)
    properties: Dict[str, Any] = {
        "class": value,
        "class_name": (
            class_names[value]
            if class_names and 0 <= value < len(class_names) else str(value)
        ),
        "area_px": round(float(geom.area), 4),
        "topology_repaired": topology_repaired,
    }
    if confidence is not None:
        # 按多边形内部均值（非全类均值）：几何栅格化取掩膜。
        from rasterio import features as _features

        geom_mask = _features.geometry_mask(
            [geom_pixels], out_shape=shape_hw,
            transform=__import__("affine").Affine.identity(),
            invert=True, all_touched=False,
        )
        region_conf = confidence[geom_mask]
        if region_conf.size:
            properties["mean_confidence"] = round(float(region_conf.mean()), 6)
    return {
        "type": "Feature",
        "properties": properties,
        "geometry": mapping(geom_geo),
    }


def _transform_geom(geom: Any, transform: Any, transform_xy: Any) -> Any:
    """shapely 几何的像素→地理仿射变换（多部分递归）。"""
    from shapely.geometry import MultiPolygon, Polygon

    if isinstance(geom, Polygon):

        def ring_to_geo(coords: Sequence[Tuple[float, float]]) -> List[List[float]]:
            return [
                list(
                    transform_xy(
                        transform,
                        cy,
                        cx,
                    )
                )
                for cx, cy in coords
            ]

        exterior = ring_to_geo(geom.exterior.coords)
        interiors = [ring_to_geo(r.coords) for r in geom.interiors]
        return Polygon(exterior, interiors)
    if isinstance(geom, MultiPolygon):
        parts = [
            _transform_geom(p, transform, transform_xy) for p in geom.geoms
        ]
        return MultiPolygon([p for p in parts if not p.is_empty])
    # 其余形态（GeometryCollection 等）：收集全部非空多边形部分
    # （make_valid 的修复碎片不静默丢弃）。
    polygons = []
    for part in getattr(geom, "geoms", []):
        transformed = _transform_geom(part, transform, transform_xy)
        if not transformed.is_empty:
            polygons.append(transformed)
    if len(polygons) == 1:
        return polygons[0]
    if polygons:
        return MultiPolygon(polygons)
    return geom


__all__ = ["VectorizeParams", "vectorize_class_raster"]
