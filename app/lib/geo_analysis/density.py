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
from shapely import box as sbox
from shapely.geometry import box, mapping

from app.lib.geo_processor.core import GeoAnalysisResult, to_utm_gdf
from app.lib.geo_analysis.statistics import _filter_numeric_gdf
from app.lib.geo_analysis._vector import extract_centroids

logger = logging.getLogger(__name__)


def _extract_numeric_values(gdf, value_field):
    """Extract a numeric values array from a GeoDataFrame column.

    Thin adapter over :func:`_filter_numeric_gdf`: returns just the values
    array (or None when the field is missing), matching the contract
    ``density.py`` historically expected from the now-removed
    ``_extract_numeric_values`` in ``statistics.py``.
    """
    out = _filter_numeric_gdf(gdf, value_field)
    return None if out is None else out[1]

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

    coords = extract_centroids(gdf)

    kde_weights = None
    if value_field:
        # Align coords with the (NaN/inf-filtered) weights: previously coords
        # came from the full gdf while weights came from the filtered gdf, so
        # a single bad value crashed the repeat/broadcast (C-6).
        aligned = _filter_numeric_gdf(gdf, value_field)
        if aligned is None:
            numeric_cols = [c for c in gdf.columns if c != "geometry" and gdf[c].dtype in ("float64", "int64", "float32", "int32")]
            return GeoAnalysisResult(
                False, None, f"字段 '{value_field}' 不是数值类型。可用字段: {numeric_cols}",
                error_type="TypeError",
                correction_hint=f"使用以下数值字段之一: {numeric_cols}",
            )
        gdf_w, weights = aligned
        coords = extract_centroids(gdf_w)
        # gaussian_kde supports weights natively. Use them directly instead
        # of repeating points: repetition forced integer ratios and clamped
        # large dynamic ranges (1e6 -> 100x), silently distorting the density
        # (C-7), and built a bloated array.
        kde_weights = np.abs(weights.astype(float))

    kde_data = coords.T

    # Bandwidth - always compute in CRS units (meters)
    from scipy.stats import gaussian_kde

    data_std = float(np.mean(np.std(kde_data, axis=1)))
    if data_std == 0:
        data_std = 1.0

    # All-zero weights make gaussian_kde's weighted covariance singular (sum
    # of weights = 0 -> division by zero). Fall back to unweighted rather
    # than crash (review finding).
    if kde_weights is not None and float(kde_weights.sum()) == 0.0:
        logger.warning("kde_surface: all weights are zero; falling back to unweighted KDE.")
        kde_weights = None

    try:
        if bandwidth <= 0:
            kde = gaussian_kde(kde_data, bw_method="scott", weights=kde_weights)
            bw = float(kde.factor * data_std)
        else:
            kde = gaussian_kde(
                kde_data, bw_method=float(bandwidth / data_std), weights=kde_weights
            )
            bw = bandwidth
    except np.linalg.LinAlgError as e:
        # Degenerate input (all-coincident / collinear points) yields a
        # singular covariance matrix (C-5). Surface it as a structured error
        # instead of crashing.
        return GeoAnalysisResult(
            False, None,
            f"KDE 失败：数据退化（点重合或共线），无法估计核密度。{e}",
            error_type="NumericalError",
            correction_hint="提供空间上更分散的点，或增大 bandwidth。",
        )

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
    # 阈值选择 + box 构造均向量化：np.nonzero 按 (i, j) 行主序返回（与原始双层循环顺序一致），
    # shapely 2.x 的 sbox() 接受数组，一次 C 级调用构建全部几何。
    ys, xs = np.nonzero(density >= threshold)
    if len(ys):
        half = cell_size / 2
        cell_geoms = sbox(grid_x[xs] - half, grid_y[ys] - half,
                          grid_x[xs] + half, grid_y[ys] + half)
        cell_data = list(zip(ys.tolist(), xs.tolist(), density[ys, xs].tolist()))
    else:
        cell_geoms = []
        cell_data = []

    if len(cell_geoms):
        cells_gdf = gpd.GeoSeries(cell_geoms, crs=utm_crs).to_crs("EPSG:4326")
        out_features = [
            {
                "type": "Feature",
                "geometry": mapping(geom),
                "properties": {"density": round(d_val, 8)},
            }
            for geom, (_, _, d_val) in zip(cells_gdf.geometry, cell_data)
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

    coords = extract_centroids(gdf)
    kde_data = coords.T

    data_std = float(np.std(kde_data))
    if data_std == 0:
        data_std = 1.0
    try:
        kde = gaussian_kde(
            kde_data,
            bw_method="scott" if bandwidth <= 0 else float(bandwidth / data_std),
        )
    except np.linalg.LinAlgError as e:
        # Degenerate input (coincident/collinear points) -> singular
        # covariance (C-5). Surface a structured error.
        return GeoAnalysisResult(
            False, None,
            f"等值面 KDE 失败：数据退化（点重合或共线）。{e}",
            error_type="NumericalError",
            correction_hint="提供空间上更分散的点，或增大 bandwidth。",
        )

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
    raw_polys = []  # (poly, val, level_idx) - batch CRS transform after loop
    for i, segs in enumerate(cs.allsegs):
        val = float(cs.levels[i])
        for poly_coords in segs:
            if len(poly_coords) < 3:
                continue
            poly = Polygon(poly_coords)
            if not poly.is_valid:
                poly = poly.buffer(0)
            raw_polys.append((poly, val, i))

    # Batch CRS transform: one GeoSeries instead of N per-polygon calls
    if raw_polys:
        gs = gpd.GeoSeries([p for p, _, _ in raw_polys], crs=utm_crs).to_crs("EPSG:4326")
        for (poly, val, level_idx), poly_wgs84 in zip(raw_polys, gs):
            out_features.append({
                "type": "Feature",
                "geometry": mapping(poly_wgs84),
                # BUGFIX: previously `level: i` leaked the final loop value of
                # the enumerate(cs.allsegs) loop, assigning the same (last)
                # level index to every feature. Carry the per-polygon level_idx.
                "properties": {"level": level_idx, "density_value": val},
            })

    # compute continuous legend_spec from contour level values
    legend_spec = None
    if out_features:
        level_vals = [
            float(f.get("properties", {}).get("density_value", 0.0))
            for f in out_features
            if isinstance(f.get("properties", {}).get("density_value"), (int, float))
        ]
        if level_vals:
            try:
                # ADR-0052: route through the canonical continuous builder so the
                # legend carries `field` (the live map's interpolate needs it) and
                # the ramp resolves through one path — replacing the hand-built
                # palette_colors[:5] truncation + missing-field drift. The builder
                # accepts a flat domain (min==max) so a degenerate KDE still gets
                # a legend overlay (paint falls back to constant).
                from app.lib.cartography.thematic_spec import build_continuous_spec
                legend_spec = build_continuous_spec(
                    min(level_vals), max(level_vals), "Viridis", field="density_value",
                )
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


# ─── Heatmap raster/grid generation ─────────────────────────────
# Relocated verbatim from app/tools/spatial.py (_generate_heatmap) per
# ADR-0037 Win 3. This is deep matplotlib/scipy density-rendering logic that
# lived at the tool-adapter layer; density.py is the canonical home (same
# precedent as the kde_surface/kde_contours extraction noted in the module
# header — architecture-review F2). Renamed generate_heatmap_raster to drop
# the private '_' prefix (it's now a package public export) and to clarify it
# produces raster+grid output distinct from kde_surface.


# Matplotlib RGBA stop tuples keyed by frontend palette name. Used only by
# generate_heatmap_raster's raster mode (grid mode returns features, not PNGs).
_HEATMAP_PALETTES = {
    "classic": [
        (0.00, (0.0, 0.0, 0.0, 0.0)),
        (0.15, (0.0, 1.0, 1.0, 0.4)),
        (0.40, (0.0, 1.0, 0.0, 0.6)),
        (0.70, (1.0, 1.0, 0.0, 0.8)),
        (0.90, (1.0, 0.5, 0.0, 0.9)),
        (1.00, (1.0, 0.0, 0.0, 1.0)),
    ],
    "magma": [
        (0.00, (0.0, 0.0, 0.0, 0.0)),
        (0.20, (0.2, 0.04, 0.48, 0.5)),
        (0.50, (0.7, 0.13, 0.45, 0.7)),
        (0.80, (0.99, 0.55, 0.35, 0.85)),
        (1.00, (0.98, 0.94, 0.60, 1.0)),
    ],
    "viridis": [
        (0.00, (0.0, 0.0, 0.0, 0.0)),
        (0.25, (0.27, 0.0, 0.33, 0.5)),
        (0.50, (0.13, 0.57, 0.55, 0.7)),
        (0.75, (0.37, 0.79, 0.36, 0.85)),
        (1.00, (0.99, 0.9, 0.14, 1.0)),
    ],
    "thermal": [
        (0.00, (0.0, 0.0, 0.0, 0.0)),
        (0.33, (0.0, 0.0, 1.0, 0.5)),
        (0.66, (1.0, 1.0, 0.0, 0.8)),
        (1.00, (1.0, 0.0, 0.0, 1.0)),
    ],
}


def generate_heatmap_raster(features: list, cell_size: int = 500, radius: int = 1000,
                            render_type: str = "raster", palette: str = "classic") -> dict:
    """Generate heatmap data without Celery. Supports raster and grid render types.

    Relocated verbatim from ``app/tools/spatial.py:_generate_heatmap`` (ADR-0037
    Win 3). All heavy imports (matplotlib, scipy) are kept lazy inside the body
    to avoid making them hard package-level dependencies and to avoid import
    cycles with ``app.services.spatial_tasks`` (whose helpers this calls).
    """
    import base64
    import io

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap
    from scipy.ndimage import gaussian_filter

    # Lazy import: the spatial_tasks helpers are shared with the Celery task
    # path (run_heatmap_generation); keep them lazy to avoid a cycle.
    from app.services.spatial_tasks import (
        _extract_heatmap_points,
        _build_heatmap_grid,
        _build_grid_features,
    )

    fig = None
    try:
        xs, ys = _extract_heatmap_points(features)
        if not xs:
            return {"error": "No valid point features found"}

        H, xedges, yedges, _ = _build_heatmap_grid(xs, ys, cell_size)

        if render_type == "grid":
            max_val = float(H.max()) if H.max() > 0 else 1.0
            grid_features = _build_grid_features(H, xedges, yedges, max_val)
            return {
                "success": True,
                "data": {
                    "type": "FeatureCollection",
                    "features": grid_features,
                    "metadata": {
                        "render_type": "grid",
                        "field": "weight",
                        "cell_size": cell_size,
                        "point_count": len(xs),
                        "palette": palette
                    }
                },
                "status_desc": f"Vector grid heatmap generated with {len(grid_features)} cells."
            }

        else:
            # Raster mode
            sigma = max(1.0, radius / cell_size)
            H_smooth = gaussian_filter(H.T, sigma=sigma)

            v_max_actual = H_smooth.max()
            if v_max_actual > 0:
                H_smooth[H_smooth < v_max_actual * 0.01] = 0

            colors = _HEATMAP_PALETTES.get(palette, _HEATMAP_PALETTES["classic"])
            cmap = LinearSegmentedColormap.from_list("dynamic_heat", colors, N=256)

            fig, ax = plt.subplots(figsize=(10, 10), dpi=100)
            v_max = np.percentile(H_smooth, 98) if H_smooth.max() > 0 else 1.0
            if v_max <= 0:
                v_max = H_smooth.max() or 1.0

            ax.imshow(
                H_smooth,
                cmap=cmap,
                origin="lower",
                aspect="auto",
                vmin=0,
                vmax=v_max,
                interpolation="bilinear",
            )
            ax.axis("off")
            plt.subplots_adjust(left=0, right=1, top=1, bottom=0)

            buf = io.BytesIO()
            fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0, transparent=True)
            buf.seek(0)
            img_b64 = "data:image/png;base64," + base64.b64encode(buf.read()).decode()

            return {
                "success": True,
                "data": {
                    "type": "heatmap_raster",
                    "image": img_b64,
                    "bbox": [float(xedges[0]), float(yedges[0]), float(xedges[-1]), float(yedges[-1])],
                    "total_points": len(xs),
                    "metadata": {
                        "render_type": "raster",
                        "point_count": len(xs),
                        "palette": palette
                    }
                },
                "status_desc": f"Raster heatmap generated (palette: {palette}) covering {len(xs)} points."
            }
    except (ValueError, TypeError, OSError, RuntimeError) as e:
        logger.error(f"Heatmap generation failed: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__,
            "correction_hint": f"热力图生成失败 ({type(e).__name__}): {e}。请检查输入点集的坐标有效性及参数设置。"
        }
    finally:
        if fig:
            plt.close(fig)
