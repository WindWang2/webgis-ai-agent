"""Kernel density estimation algorithms - gaussian KDE surface grid and contours.

Deep math module for density estimation. Pure functions: take a GeoJSON dict,
return a :class:`GeoAnalysisResult` whose ``data`` is a FeatureCollection.

- :func:`kde_surface` - grid raster of density values, thresholded to drop
  near-zero cells. Output FC carries ``grid_size`` / ``stats`` / ``bandwidth_m``
  envelope keys.
- :func:`kde_contours` - vector isarithmic contours via matplotlib. Output FC
  carries ``levels_count`` and (when features exist) ``legend_spec`` envelope
  keys.

Extracted from ``app/tools/spatial_stats.py`` (architecture-review F2): these
were orphan algorithms doing deep math in the tool adapter layer while their
statistical siblings already lived in this package.
"""
import logging
from typing import Any, Optional

import numpy as np
import geopandas as gpd
from shapely.geometry import box, mapping

from app.lib.geo_processor.core import GeoAnalysisResult, to_utm_gdf
from app.lib.geo_analysis.statistics import _extract_numeric_values as extract_numeric_values

logger = logging.getLogger(__name__)

# OOM guard: cap the KDE grid to prevent unbounded memory allocation.
_MAX_GRID_CELLS = 100_000
# Weighted-point repeat cap: prevents large dynamic-range weights from
# exploding the kde_data array.
_MAX_REPEAT_FACTOR = 100


def kde_surface(
    geojson: dict,
    bandwidth: float = 0,
    cell_size: float = 500,
    value_field: str = "",
    bounds: Optional[list] = None,
) -> GeoAnalysisResult:
    """Gaussian KDE surface grid over the input points.

    Args:
        geojson: input point FeatureCollection (WGS84).
        bandwidth: kernel bandwidth in meters; 0 = auto (Silverman/Scott).
        cell_size: grid cell size in meters (default 500).
        value_field: optional numeric field used as point weights.
        bounds: optional ``[xmin, ymin, xmax, ymax]`` (WGS84) analysis extent;
            defaults to data bounds + 10% buffer.

    Returns:
        ``GeoAnalysisResult`` whose ``data`` is a FeatureCollection of
        thresholded grid cells with a ``density`` property, plus ``grid_size``,
        ``stats``, and ``bandwidth_m`` envelope keys.
    """
    if not geojson:
        return GeoAnalysisResult(
            False, None, "无效的 GeoJSON 输入",
            error_type="ValueError", correction_hint="提供有效的点要素 GeoJSON",
        )

    result = to_utm_gdf(geojson)
    if result is None:
        return GeoAnalysisResult(
            False, None, "无法解析矢量数据",
            error_type="ValueError", correction_hint="检查 GeoJSON 是否为有效的点要素集合",
        )
    gdf, utm_crs = result

    if len(gdf) < 3:
        return GeoAnalysisResult(
            False, None, "至少需要3个有效点要素",
            error_type="ValueError", correction_hint="提供至少 3 个点要素",
        )

    coords = np.array([(g.centroid.x, g.centroid.y) for g in gdf.geometry])

    if value_field:
        weights = extract_numeric_values(gdf, value_field)
        if weights is None:
            numeric_cols = [c for c in gdf.columns if c != "geometry" and gdf[c].dtype in ("float64", "int64", "float32", "int32")]
            return GeoAnalysisResult(
                False, None, f"字段 '{value_field}' 不是数值类型。可用字段: {numeric_cols}",
                error_type="TypeError",
                correction_hint=f"使用以下数值字段之一: {numeric_cols}",
            )
        weights = np.abs(weights)
        # Weighted: repeat points by weight (clamped to avoid OOM)
        min_w = float(weights.min())
        if min_w == 0:
            min_w = 1e-10
        repeat_factors = np.clip(np.maximum((weights / min_w).astype(int), 1), 1, _MAX_REPEAT_FACTOR)
        weighted_coords = np.repeat(coords, repeat_factors, axis=0)
        kde_data = weighted_coords.T
    else:
        kde_data = coords.T

    # Bandwidth - always compute in CRS units (meters)
    from scipy.stats import gaussian_kde

    data_std = np.mean(np.std(kde_data, axis=1))
    if data_std == 0:
        data_std = 1.0

    if bandwidth <= 0:
        kde = gaussian_kde(kde_data, bw_method="scott")
        scott_factor = kde.factor
        bw = float(scott_factor * data_std)
    else:
        bw_factor = float(bandwidth / data_std)
        kde = gaussian_kde(kde_data, bw_method=bw_factor)
        bw = bandwidth

    # Bounds
    if bounds and len(bounds) == 4:
        bounds_gdf = gpd.GeoDataFrame(geometry=[box(bounds[0], bounds[1], bounds[2], bounds[3])],
                                      crs="EPSG:4326").to_crs(utm_crs)
        xmin, ymin, xmax, ymax = bounds_gdf.total_bounds
    else:
        xmin, ymin, xmax, ymax = gdf.total_bounds
        buffer_x = (xmax - xmin) * 0.1
        buffer_y = (ymax - ymin) * 0.1
        xmin -= buffer_x
        xmax += buffer_x
        ymin -= buffer_y
        ymax += buffer_y

    # Grid
    nx = max(int((xmax - xmin) / cell_size), 2)
    ny = max(int((ymax - ymin) / cell_size), 2)

    # Grid safety limit to prevent OOM
    if nx * ny > _MAX_GRID_CELLS:
        cell_size = max(cell_size, ((xmax - xmin) * (ymax - ymin)) ** 0.5 / (_MAX_GRID_CELLS ** 0.5))
        nx = max(int((xmax - xmin) / cell_size), 2)
        ny = max(int((ymax - ymin) / cell_size), 2)
        logger.warning(f"KDE grid auto-adjusted to {nx}x{ny}={nx*ny} cells (cell_size={cell_size:.0f}m)")

    grid_x = np.linspace(xmin, xmax, nx)
    grid_y = np.linspace(ymin, ymax, ny)
    gx, gy = np.meshgrid(grid_x, grid_y)
    grid_coords = np.vstack([gx.ravel(), gy.ravel()])
    density = kde(grid_coords).reshape(ny, nx)

    max_d = density.max()
    threshold = max_d * 0.1

    # 批量构建格网几何体并一次性 reproject 到 WGS84（审计：避免 O(n) 逐单元格 CRS 转换）
    cell_geoms = []
    cell_data = []  # (i, j, d_val) tuples
    for i in range(ny):
        for j in range(nx):
            d_val = float(density[i, j])
            if d_val < threshold:
                continue
            x0, x1 = grid_x[j] - cell_size / 2, grid_x[j] + cell_size / 2
            y0, y1 = grid_y[i] - cell_size / 2, grid_y[i] + cell_size / 2
            cell_geoms.append(box(x0, y0, x1, y1))
            cell_data.append((i, j, d_val))

    if cell_geoms:
        cells_gdf = gpd.GeoSeries(cell_geoms, crs=utm_crs).to_crs("EPSG:4326")
        out_features = [
            {
                "type": "Feature",
                "geometry": mapping(cells_gdf.iloc[k]),
                "properties": {"density": round(cell_data[k][2], 8)},
            }
            for k in range(len(cell_geoms))
        ]
    else:
        out_features = []

    fc = {
        "type": "FeatureCollection",
        "features": out_features,
        "count": len(out_features),
        "grid_size": [nx, ny],
        "stats": {
            "min_density": round(float(density.min()), 8),
            "max_density": round(float(density.max()), 8),
            "mean_density": round(float(density.mean()), 8),
        },
        "bandwidth_m": round(bw, 1),
    }
    return GeoAnalysisResult(True, fc, f"KDE surface: {len(out_features)} cells, grid {nx}x{ny}, bw={bw:.0f}m")


def kde_contours(
    geojson: dict,
    levels: int = 8,
    bandwidth: float = 0,
) -> GeoAnalysisResult:
    """Gaussian KDE vector isarithmic contours.

    Args:
        geojson: input point FeatureCollection (WGS84).
        levels: number of contour levels (default 8).
        bandwidth: search radius in meters; 0 = auto (Scott).

    Returns:
        ``GeoAnalysisResult`` whose ``data`` is a FeatureCollection of contour
        polygons with ``level`` / ``density_value`` properties, plus
        ``levels_count`` and (when features exist) ``legend_spec`` envelope keys.
        The ``legend_spec`` (continuous: min/max + palette) is read by the
        cartography converters as an analysis marker.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from scipy.stats import gaussian_kde
    except ImportError:
        return GeoAnalysisResult(
            False, None, "需要 matplotlib 和 scipy",
            error_type="ImportError", correction_hint="安装 matplotlib 和 scipy",
        )

    result = to_utm_gdf(geojson)
    if result is None:
        return GeoAnalysisResult(
            False, None, "无法解析矢量数据",
            error_type="ValueError", correction_hint="检查 GeoJSON 是否为有效的点要素集合",
        )
    gdf, utm_crs = result

    if len(gdf) < 5:
        return GeoAnalysisResult(
            False, None, "至少需要5个有效点要素进行等值面分析",
            error_type="ValueError", correction_hint="提供至少 5 个点要素",
        )

    coords = np.array([(g.centroid.x, g.centroid.y) for g in gdf.geometry])
    kde_data = coords.T

    kde = gaussian_kde(kde_data, bw_method="scott" if bandwidth <= 0 else bandwidth / np.std(kde_data))

    xmin, ymin, xmax, ymax = gdf.total_bounds
    buf_x, buf_y = (xmax - xmin) * 0.2, (ymax - ymin) * 0.2
    X, Y = np.mgrid[xmin - buf_x:xmax + buf_x:100j, ymin - buf_y:ymax + buf_y:100j]
    positions = np.vstack([X.ravel(), Y.ravel()])
    Z = np.reshape(kde(positions).T, X.shape)

    fig, ax = plt.subplots()
    cs = ax.contourf(X, Y, Z, levels=levels)
    plt.close(fig)

    out_features = []
    from shapely.geometry import Polygon
    raw_polys = []  # (poly, val) - batch CRS transform after loop
    for i, segs in enumerate(cs.allsegs):
        val = float(cs.levels[i])
        for poly_coords in segs:
            if len(poly_coords) < 3:
                continue
            poly = Polygon(poly_coords)
            if not poly.is_valid:
                poly = poly.buffer(0)
            raw_polys.append((poly, val))

    # Batch CRS transform: one GeoSeries instead of N per-polygon calls
    if raw_polys:
        gs = gpd.GeoSeries([p for p, _ in raw_polys], crs=utm_crs).to_crs("EPSG:4326")
        for (poly, val), poly_wgs84 in zip(raw_polys, gs):
            out_features.append({
                "type": "Feature",
                "geometry": mapping(poly_wgs84),
                "properties": {"level": i, "density_value": val},
            })

    # compute continuous legend_spec from contour level values
    legend_spec = None
    if out_features:
        level_vals = [
            float(f.get("properties", {}).get("density_value", 0.0))
            for f in out_features
        ]
        level_vals = [v for v in level_vals if v is not None]
        if level_vals:
            try:
                from app.services.cartography_service import COLOR_PALETTES
                palette = "Viridis"
                palette_colors = list(COLOR_PALETTES.get(palette, []))
                legend_spec = {
                    "type": "continuous",
                    "min": min(level_vals),
                    "max": max(level_vals),
                    "palette": palette,
                    "palette_colors": palette_colors[:5] if palette_colors else ["#440154", "#21908c", "#fde725"],
                }
            except Exception:  # noqa: BLE001
                legend_spec = None

    fc: dict[str, Any] = {
        "type": "FeatureCollection",
        "features": out_features,
        "count": len(out_features),
        "levels_count": len(cs.levels),
    }
    if legend_spec is not None:
        fc["legend_spec"] = legend_spec

    return GeoAnalysisResult(True, fc, f"KDE contours: {len(out_features)} features, {len(cs.levels)} levels")
