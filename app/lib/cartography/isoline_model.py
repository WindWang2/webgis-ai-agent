"""
Continuous Surface & Isoline Contour Cartographic Model for WebGIS AI Agent.
Supports both contour lines (LineString/MultiLineString) with index contour styling and labels,
and filled contour bands (Polygon/MultiPolygon) with topological hole repair,
from either 2D scalar grids or point density distributions.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Literal, Optional, Union

import numpy as np
from pydantic import BaseModel, Field
from shapely.geometry import LineString, Polygon, mapping
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

logger = logging.getLogger(__name__)

ContourGeometryMode = Literal["lines", "filled_bands", "both"]
ContourLevelStrategy = Literal["explicit", "equal_interval", "quantiles", "auto"]


class IsolineContourSpec(BaseModel):
    """Specification for isoline contour generation and cartographic rendering."""
    value_field: Optional[str] = Field(default=None, description="Scalar attribute field name")
    unit: str = Field(default="", description="Physical measurement unit (e.g., 'm', 'people/km²', '°C')")
    mode: ContourGeometryMode = Field(default="lines", description="Output geometry: 'lines', 'filled_bands', or 'both'")
    levels: Union[int, List[float]] = Field(default=8, description="Level count or explicit list of contour thresholds")
    level_strategy: ContourLevelStrategy = Field(default="auto", description="Level generation strategy")
    index_contour_interval: int = Field(default=5, description="Every Nth level is treated as an index contour (thicker line)")
    bandwidth_m: float = Field(default=0.0, description="Spatial bandwidth in meters for point KDE (0 = auto)")
    palette: str = Field(default="Viridis", description="Color palette name for contours")
    line_width_base: float = Field(default=1.5, description="Standard line width in pixels")
    line_width_index: float = Field(default=3.0, description="Index contour line width in pixels")
    show_labels: bool = Field(default=True, description="Attach formatted contour label properties")


def resolve_contour_levels(
    values: Any,
    levels_spec: Union[IsolineContourSpec, int, List[float]],
    strategy: Optional[ContourLevelStrategy] = None,
) -> List[float]:
    """
    Computes sorted, distinct, monotonic contour threshold levels.
    Preserves explicit user levels exactly.
    """
    if isinstance(values, IsolineContourSpec) and not isinstance(levels_spec, IsolineContourSpec):
        values, levels_spec = levels_spec, values

    if isinstance(levels_spec, IsolineContourSpec):
        spec_levels = levels_spec.levels
        strat = levels_spec.level_strategy if strategy is None else strategy
    else:
        spec_levels = levels_spec
        strat = "auto" if strategy is None else strategy

    # 1. Explicit levels take top precedence
    if isinstance(spec_levels, (list, tuple)) and len(spec_levels) > 0:
        cleaned = sorted(list({float(x) for x in spec_levels if not (math.isnan(x) or math.isinf(x))}))
        if len(cleaned) >= 2:
            return cleaned
        elif len(cleaned) == 1:
            return [cleaned[0], cleaned[0] + 1.0]

    val_arr = np.asarray(values, dtype=float)
    val_clean = val_arr[np.isfinite(val_arr)]
    if len(val_clean) == 0:
        return [0.0, 1.0]

    min_v = float(np.min(val_clean))
    max_v = float(np.max(val_clean))

    if math.isclose(min_v, max_v, abs_tol=1e-7):
        return [min_v, min_v + 1.0]

    k = int(spec_levels) if isinstance(spec_levels, (int, float)) and spec_levels > 0 else 8
    k = max(2, min(k, 32))

    if strat == "equal_interval":
        return [float(x) for x in np.linspace(min_v, max_v, k)]
    elif strat == "quantiles":
        quantiles = np.linspace(0, 100, k)
        return sorted(list({float(x) for x in np.percentile(val_clean, quantiles)}))
    else:
        # Default auto
        return [float(x) for x in np.linspace(min_v, max_v, k)]


def generate_contour_features_from_grid(
    X: np.ndarray,
    Y: np.ndarray,
    Z: np.ndarray,
    spec: IsolineContourSpec,
    utm_crs: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generates GeoJSON FeatureCollection of contours from 2D coordinate matrices.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import geopandas as gpd

    levels = resolve_contour_levels(Z, spec)
    fig, ax = plt.subplots()

    features: List[Dict[str, Any]] = []

    # 1. Line Contours
    if spec.mode in ("lines", "both"):
        cs_lines = ax.contour(X, Y, Z, levels=levels)
        raw_lines = []
        for lvl_idx, segs in enumerate(cs_lines.allsegs):
            lvl_val = float(cs_lines.levels[lvl_idx])
            is_index = bool((lvl_idx + 1) % spec.index_contour_interval == 0)
            for line_coords in segs:
                if len(line_coords) < 2:
                    continue
                ls = LineString(line_coords)
                if not ls.is_empty and ls.length > 0:
                    raw_lines.append((ls, lvl_val, is_index))

        if raw_lines and utm_crs:
            gdf_lines = gpd.GeoDataFrame(
                [{"level": r[1], "value": r[1], "is_index": r[2]} for r in raw_lines],
                geometry=[r[0] for r in raw_lines],
                crs=utm_crs,
            ).to_crs("EPSG:4326")
            for _, row in gdf_lines.iterrows():
                geom = row.geometry
                lvl = float(row["level"])
                is_idx = bool(row["is_index"])
                label_str = f"{lvl:g} {spec.unit}".strip()
                features.append({
                    "type": "Feature",
                    "geometry": mapping(geom),
                    "properties": {
                        "level": lvl,
                        "value": lvl,
                        "unit": spec.unit,
                        "label": label_str,
                        "is_index_contour": is_idx,
                        "line_width": spec.line_width_index if is_idx else spec.line_width_base,
                        "layer_kind": "contour_line",
                    },
                })
        elif raw_lines:
            for ls, lvl, is_idx in raw_lines:
                label_str = f"{lvl:g} {spec.unit}".strip()
                features.append({
                    "type": "Feature",
                    "geometry": mapping(ls),
                    "properties": {
                        "level": lvl,
                        "value": lvl,
                        "unit": spec.unit,
                        "label": label_str,
                        "is_index_contour": is_idx,
                        "line_width": spec.line_width_index if is_idx else spec.line_width_base,
                        "layer_kind": "contour_line",
                    },
                })

    # 2. Filled Contour Bands
    if spec.mode in ("filled_bands", "both"):
        cs_bands = ax.contourf(X, Y, Z, levels=levels)
        raw_bands = []

        for lvl_idx, segs in enumerate(cs_bands.allsegs):
            if lvl_idx >= len(cs_bands.levels) - 1:
                lvl_val = float(cs_bands.levels[lvl_idx])
                max_val = lvl_val
            else:
                lvl_val = float(cs_bands.levels[lvl_idx])
                max_val = float(cs_bands.levels[lvl_idx + 1])

            band_polys = []
            for poly_coords in segs:
                if len(poly_coords) < 3:
                    continue
                p = Polygon(poly_coords)
                if not p.is_valid:
                    p = p.buffer(0)
                if not p.is_empty and p.area > 0:
                    band_polys.append(p)

            if not band_polys:
                continue

            # Reconstruct topological even-odd containment for holes (ADR-0095 & bug #762)
            band_polys.sort(key=lambda p: p.area, reverse=True)
            outers: List[Polygon] = []
            inners: List[Polygon] = []
            inner_parent: Dict[int, Polygon] = {}

            for idx, p in enumerate(band_polys):
                containers = [prev for prev in band_polys[:idx] if prev.contains(p)]
                depth = len(containers)
                if depth % 2 == 0:
                    outers.append(p)
                else:
                    inners.append(p)
                    # Immediate enclosing parent outer polygon is the smallest container
                    inner_parent[id(p)] = min(containers, key=lambda cp: cp.area)

            if not outers:
                continue

            diff_parts: List[Any] = []
            for o in outers:
                assigned_inners = [inp for inp in inners if inner_parent.get(id(inp)) is o]
                if assigned_inners:
                    diff_geom = o.difference(unary_union(assigned_inners))
                else:
                    diff_geom = o
                if not diff_geom.is_empty:
                    diff_parts.append(diff_geom)

            if not diff_parts:
                continue

            region = unary_union(diff_parts)
            for geom in getattr(region, "geoms", [region]):
                if not geom.is_empty and getattr(geom, "area", 0) > 0:
                    if isinstance(geom, Polygon):
                        geom = orient(geom, sign=1.0)
                    raw_bands.append((geom, lvl_val, max_val))

        if raw_bands and utm_crs:
            gdf_bands = gpd.GeoDataFrame(
                [{"min_level": r[1], "max_level": r[2]} for r in raw_bands],
                geometry=[r[0] for r in raw_bands],
                crs=utm_crs,
            ).to_crs("EPSG:4326")
            for _, row in gdf_bands.iterrows():
                geom = row.geometry
                min_l = float(row["min_level"])
                max_l = float(row["max_level"])
                features.append({
                    "type": "Feature",
                    "geometry": mapping(geom),
                    "properties": {
                        "level": min_l,
                        "min_level": min_l,
                        "max_level": max_l,
                        "unit": spec.unit,
                        "label": f"{min_l:g} - {max_l:g} {spec.unit}".strip(),
                        "layer_kind": "filled_contour_band",
                    },
                })
        elif raw_bands:
            for p, min_l, max_l in raw_bands:
                features.append({
                    "type": "Feature",
                    "geometry": mapping(p),
                    "properties": {
                        "level": min_l,
                        "min_level": min_l,
                        "max_level": max_l,
                        "unit": spec.unit,
                        "label": f"{min_l:g} - {max_l:g} {spec.unit}".strip(),
                        "layer_kind": "filled_contour_band",
                    },
                })

    plt.close(fig)

    meta_isoline = {
        "model": "isoline_contour",
        "levels": levels,
        "levels_count": len(levels),
        "mode": spec.mode,
        "unit": spec.unit,
    }

    return {
        "type": "FeatureCollection",
        "features": features,
        "properties": meta_isoline,
        "metadata": {
            "isoline": meta_isoline,
        },
    }
