import logging
from typing import Literal

import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import box, Polygon
from app.lib.geo_processor.core import GeoAnalysisResult
from app.lib.geo_processor.core import to_utm_gdf, gdf_from_features
from app.lib.geo_analysis.evidence import build_quality_evidence
# ADR-0052: 协作式取消检查点。cancellable() 在 chunk 边界读一次 contextvar，
# 未绑定 token 时开销为零；用户取消后长循环立即抛 OperationCancelled 退出，
# 真正释放 CPU 而不是只改 UI 状态。
from app.lib.cancellation import cancellable

logger = logging.getLogger(__name__)

def generate_fishnet(bounds: tuple[float, float, float, float], cell_size: float, type: str = 'square') -> GeoAnalysisResult:
    """Generate a square or hexagonal grid over specified bounds.

    GIS contract (V-F01): ``bounds`` are WGS84 degrees and ``cell_size`` is in
    **metres** (matching the ``fishnet_grid`` tool description). The grid is
    built in a projected metric CRS (UTM, or polar stereographic beyond 84°)
    and reprojected back to EPSG:4326, so a 500 m request yields ~500 m cells
    on the ground. Previously the metre value was applied directly in degree
    space, producing a single polygon spanning hundreds of degrees. Includes
    OOM protection (50 000-cell estimate).
    """
    if cell_size <= 0:
        return GeoAnalysisResult(
            False, None, f"cell_size must be positive (metres), got {cell_size}",
            error_type="ValueError",
        )

    xmin, ymin, xmax, ymax = bounds

    # Pick a metric CRS for the (WGS84) bounds: UTM, or polar stereographic.
    abs_max_lat = max(abs(float(ymin)), abs(float(ymax)))
    if abs_max_lat > 84.0:
        metric_crs = "EPSG:3413" if (float(ymin) + float(ymax)) / 2 >= 0 else "EPSG:3031"
    else:
        try:
            metric_crs = str(
                gpd.GeoDataFrame(geometry=[box(xmin, ymin, xmax, ymax)], crs="EPSG:4326")
                .estimate_utm_crs()
            )
        except Exception:
            clon = (float(xmin) + float(xmax)) / 2
            lon = (clon + 180.0) % 360.0 - 180.0
            zone = max(1, min(60, int((lon + 180) / 6) + 1))
            metric_crs = f"EPSG:{32600 if (float(ymin)+float(ymax))/2 >= 0 else 32700}{zone}"

    # Project the bounds to the metric CRS and build the grid there.
    bounds_m = (
        gpd.GeoDataFrame(geometry=[box(xmin, ymin, xmax, ymax)], crs="EPSG:4326")
        .to_crs(metric_crs)
    )
    mxmin, mymin, mxmax, mymax = (float(v) for v in bounds_m.total_bounds)
    m_width = mxmax - mxmin
    m_height = mymax - mymin

    estimated_cells = (m_width / cell_size) * (m_height / cell_size)
    warning = ""
    if estimated_cells > 50000:
        new_cell_size = float(np.sqrt((m_width * m_height) / 50000))
        warning = (
            f"Warning: Grid too dense ({int(estimated_cells)} cells). "
            f"Cell size adjusted from {cell_size}m to {new_cell_size:.4f}m."
        )
        cell_size = new_cell_size

    metric_polys = []
    if type == 'square':
        cols = np.arange(mxmin, mxmax, cell_size)
        rows = np.arange(mymin, mymax, cell_size)
        # Vectorized cell grid, x-major order preserved (for each x, all y).
        xs = np.repeat(cols, len(rows))
        ys = np.tile(rows, len(cols))
        metric_polys = [box(x, y, x + cell_size, y + cell_size) for x, y in zip(xs, ys)]

    elif type == 'hexagon':
        R = cell_size / np.sqrt(3)
        dx = cell_size
        dy = 1.5 * R

        cols = np.arange(mxmin - dx, mxmax + dx, dx)
        rows = np.arange(mymin - dy, mymax + dy, dy)
        nrows = len(rows)

        # GIS-P2-1: the lattice above is pointy-top (dx=√3·R flat-to-flat,
        # dy=1.5·R interleaved rows) — vertices must sit at 30°+k·60° so each
        # hexagon's width is √3·R and height 2R. The old 0°+k·60° (flat-top)
        # vertices made every cell overlap its neighbors on BOTH axes
        # (0.134·cell vertical, 0.155·cell horizontal) — not a tessellation.
        angles = np.radians([30, 90, 150, 210, 270, 330, 30])
        cos_a = np.cos(angles)
        sin_a = np.sin(angles)

        offsets = (np.arange(nrows) % 2) * (dx / 2)
        cx = cols[None, :] + offsets[:, None]
        cy = np.broadcast_to(rows[:, None], (nrows, len(cols)))

        vx = cx[:, :, None] + R * cos_a[None, None, :]
        vy = cy[:, :, None] + R * sin_a[None, None, :]
        vertices = np.stack([vx, vy], axis=-1).reshape(-1, 7, 2)
        metric_polys = [Polygon(v) for v in vertices]
    else:
        return GeoAnalysisResult(success=False, data=None, summary=f"Unsupported type: {type}")

    # Reproject the metric grid back to WGS84 for the response.
    grid = gpd.GeoDataFrame({'geometry': metric_polys}, crs=metric_crs).to_crs("EPSG:4326")

    return GeoAnalysisResult(
        success=True,
        data=grid.__geo_interface__,
        summary=f"Generated {len(metric_polys)} {type} cells. {warning}".strip()
    )

def spatial_aggregate(
    points_geojson: dict | str,
    polygons_geojson: dict | str,
    stats: list[str] | None = None,
    value_field: str | None = None,
    output_crs: str | None = "EPSG:4326",
) -> GeoAnalysisResult:
    """
    Aggregate points to polygons using spatial join.
    Supports stats: count, sum, mean, max, min.
    
    Args:
        output_crs: Output CRS (default EPSG:4326). Set to None to keep input CRS.
    """
    if stats is None:
        stats = ['count', 'sum', 'mean']
    try:
        # Use geo_processor for pre-processing (alignment)
        res_points = to_utm_gdf(points_geojson)
        res_polys = to_utm_gdf(polygons_geojson)

        # GIS-13: to_utm_gdf returns (None, None) on failure but a non-empty
        # tuple is always truthy, so `if not res_*` never fired. See network.py.
        if res_points is None or res_points[0] is None or res_polys is None or res_polys[0] is None:
            return GeoAnalysisResult(False, None, "Invalid input GeoJSON")
            
        points, utm_crs = res_points
        polygons, poly_crs = res_polys
        
        # Ensure same CRS
        if utm_crs != poly_crs:
            polygons = polygons.to_crs(utm_crs)

        # Spatial Join (aggregate convention: intersects, not strict within)
        joined = gpd.sjoin(points, polygons, how='inner', predicate='intersects')
        
        results = []
        for stat in stats:
            if stat == 'count':
                res = joined.groupby('index_right').size().rename('count')
                results.append(res)
            elif value_field and value_field in points.columns:
                if stat in ['sum', 'mean', 'max', 'min']:
                    res = joined.groupby('index_right')[value_field].agg(stat).rename(stat)
                    results.append(res)
        
        if not results:
            res = joined.groupby('index_right').size().rename('count')
            results.append(res)
            
        combined_stats = pd.concat(results, axis=1)
        final_gdf = polygons.join(combined_stats)
        
        # Null vs zero: polygons with no points keep null/count=0;
        # true zero stays 0. LLM consumers see has_data to disambiguate.
        if 'count' in final_gdf.columns:
            final_gdf['count'] = final_gdf['count'].fillna(0).astype(int)
        else:
            # #693 评审修正：stats 不含 count 时不能标量广播 0——那会把
            # has_data 全置 False、有值多边形的 sum/mean 一并被 NaN 掉。
            # 从 join 本身派生每多边形的点数（无点=0）。
            counts = joined.groupby('index_right').size()
            final_gdf['count'] = final_gdf.index.map(counts).fillna(0).astype(int)
        # has_data distinguishes "no points" (null stats) from "points with zero aggregate"
        final_gdf['has_data'] = final_gdf['count'] > 0
        for s in ['sum', 'mean', 'max', 'min']:
            if s in final_gdf.columns:
                # keep NaN for empty polygons (no data), not 0
                final_gdf[s] = final_gdf[s].where(final_gdf['has_data'], other=np.nan)
                # For polygons with data but NaN stat (e.g. all-null values), leave as NaN too
        
        # Convert to output CRS (audit: warn when silently reprojecting)
        if output_crs and str(final_gdf.crs) != str(output_crs):
            logger.warning(
                "spatial_aggregate: input CRS %s differs from output_crs %s; reprojecting.",
                final_gdf.crs, output_crs,
            )
            final_gdf = final_gdf.to_crs(output_crs)
            
        summary = f"Successfully aggregated points to {len(polygons)} polygons."
        if value_field:
            summary += f" Used field '{value_field}' for {', '.join([s for s in stats if s != 'count'])}."

        return GeoAnalysisResult(
            success=True,
            data=final_gdf.__geo_interface__,
            summary=summary,
            evidence=build_quality_evidence(
                input_count=len(points),
                output_count=len(final_gdf),
                working_crs=str(utm_crs),
                empty_count=int((final_gdf['count'] == 0).sum()) if 'count' in final_gdf.columns else 0,
                extra={"stats": [str(s) for s in stats][:8]},
            ),
        )
    except Exception as e:
        return GeoAnalysisResult(
            success=False,
            data=None,
            summary=f"Aggregation failed: {str(e)}"
        )

# Alias for backward compatibility with plan
aggregate_points_to_polygons = spatial_aggregate


# ── VNext（ADR-0099）：显式分母聚合 ─────────────────────────────────────
#
# 反目标（CONTRACT_BACKBONE §10）：count 永不冒充 rate/density —— 分母必须
# 显式；零分母策略必须披露（rate=None，从不编造 0/inf）。本函数是库层的
# 归一化通道：工具参数面（spatial_aggregate）尚未暴露这些参数（由编排方
# 中央接线），descriptor spatial.aggregate.rates 已声明该缺口。

DenominatorKind = Literal["field", "area", "count"]


def _metric_zone_area_m2(zones_gdf: gpd.GeoDataFrame) -> tuple[pd.Series, str, str]:
    """Zone areas in true m².

    Returns ``(areas, crs_used, area_crs_class)``. Geographic input → auto
    UTM (polar fallback), mirroring to_utm_gdf's zone selection. A projected
    LOCAL-METRIC CRS (UTM/polar stereographic) is used directly (with the
    axis factor for non-metre units); a world-scale projected CRS (Web
    Mercator) distorts areas at high latitudes, so it is NOT trusted — the
    zones are re-projected to UTM instead.
    """
    from app.lib.gis.crs_safety import classify_crs

    crs = zones_gdf.crs
    data_class = classify_crs(str(crs)) if crs is not None else "unknown"
    if data_class == "projected_local_metric":
        # Non-metre local CRS (state-plane feet etc.): convert to m² via the
        # axis factor (metres per CRS unit), same contract as #524/#588.
        factor = 1.0
        try:
            axis = crs.axis_info[0]
            factor = float(getattr(axis, "unit_conversion_factor", 1.0) or 1.0)
        except Exception:
            factor = 1.0
        areas = zones_gdf.geometry.area * factor * factor
        return areas, str(crs), data_class

    # Geographic / world-scale projected / unknown → UTM estimate (polar
    # fallback), the same metric-frame policy as to_utm_gdf.
    metric_crs = None
    try:
        metric_crs = zones_gdf.estimate_utm_crs()
    except Exception:
        metric_crs = None
    if metric_crs is None:
        try:
            centroid = zones_gdf.geometry.union_all().centroid
        except Exception:
            centroid = zones_gdf.geometry.iloc[0].centroid
        lon = (float(centroid.x) + 180.0) % 360.0 - 180.0
        zone = max(1, min(60, int((lon + 180) / 6) + 1))
        hemisphere = 32600 if float(centroid.y) >= 0 else 32700
        metric_crs = f"EPSG:{hemisphere + zone}"
    areas = zones_gdf.geometry.to_crs(metric_crs).area
    return areas, str(metric_crs), data_class


def aggregate_with_denominator(
    features_gdf: gpd.GeoDataFrame,
    zones_gdf: gpd.GeoDataFrame,
    numerator_field: str | None = None,
    denominator: str | None = None,
    denominator_kind: DenominatorKind = "count",
) -> tuple[gpd.GeoDataFrame, dict]:
    """Per-zone explicit-denominator aggregation: numerator / denominator → rate.

    Count aggregation (``spatial_aggregate``) is NOT a rate or density — this
    function is the library-level normalization channel where the denominator
    is explicit and auditable.

    Args:
        features_gdf: numerator features (points/lines/polygons). CRS must be
            interpretable; reprojected onto the zones' CRS when they differ.
        zones_gdf: zone polygons defining the aggregation units.
        numerator_field: numeric field on ``features_gdf`` to SUM per zone.
            ``None`` → the numerator is the per-zone FEATURE COUNT. NaN values
            in the field are excluded from the sum and counted in the returned
            evidence (``nan_numerator_excluded``).
        denominator: with ``denominator_kind="field"`` — name of a numeric
            zone-level column in ``zones_gdf`` (population, exposed area...).
            Ignored (may be None) for the other kinds.
        denominator_kind: ``"field"`` (zone column), ``"area"`` (zone area in
            true m² via a metric CRS), ``"count"`` (per-zone feature count).

    Returns:
        ``(zones_gdf_like, evidence)`` — a GeoDataFrame carrying the original
        zone geometry/properties plus columns:

        - ``numerator``: sum of ``numerator_field`` (or feature count), 0 for
          zones with no intersecting features;
        - ``denominator``: the explicit denominator value;
        - ``rate``: numerator / denominator — **None** where the denominator
          is missing/NaN/≤0 (zero-denominator policy: never fabricate 0 or
          inf; consumers see JSON null);
        - ``has_support``: True when at least one feature intersected the zone
          (distinguishes a true zero rate from a no-data zone, #693 contract).

        ``evidence`` is the normalization evidence dict (denominator kind and
        unit, zero-denominator policy + zone count, excluded-NaN count, area
        CRS). ``rate_unit`` labels a count-denominator output as a RATIO, not
        a rate/density — the library never lets a count masquerade as one.

    Raises:
        MissingRequiredField: numerator_field / denominator column absent.
        InvalidCRS: features CRS cannot be aligned onto the zones' CRS.
        DegenerateData: empty features or zones.
        ValueError: unknown denominator_kind.
    """
    from app.lib.gis.scientific_errors import (
        DegenerateData,
        InvalidCRS,
        MissingRequiredField,
    )

    if denominator_kind not in ("field", "area", "count"):
        raise ValueError(
            f"unknown denominator_kind {denominator_kind!r} (field/area/count)")
    if features_gdf is None or len(features_gdf) == 0:
        raise DegenerateData(
            "aggregate_with_denominator: features_gdf is empty",
            correction_hint="提供非空的分子要素集（点/线/面）")
    if zones_gdf is None or len(zones_gdf) == 0:
        raise DegenerateData(
            "aggregate_with_denominator: zones_gdf is empty",
            correction_hint="提供非空的分区面要素集")
    if numerator_field is not None and numerator_field not in features_gdf.columns:
        raise MissingRequiredField(
            f"numerator field {numerator_field!r} not in features_gdf columns",
            correction_hint=f"可用字段: {[c for c in features_gdf.columns if c != 'geometry'][:8]}")
    if denominator_kind == "field":
        if not denominator:
            raise MissingRequiredField(
                "denominator_kind='field' requires a denominator column name",
                correction_hint="传入 zones 的数值分母列名（如人口/暴露面积）")
        if denominator not in zones_gdf.columns:
            raise MissingRequiredField(
                f"denominator column {denominator!r} not in zones_gdf columns",
                correction_hint=f"可用字段: {[c for c in zones_gdf.columns if c != 'geometry'][:8]}")

    # Align the numerator features onto the zones' frame (spatial join needs
    # one frame; alignment is disclosed in evidence).
    feats = features_gdf
    crs_aligned = False
    try:
        if zones_gdf.crs is not None and feats.crs is not None \
                and str(feats.crs) != str(zones_gdf.crs):
            feats = feats.to_crs(zones_gdf.crs)
            crs_aligned = True
    except Exception as exc:  # noqa: BLE001 — alignment failure is honest
        raise InvalidCRS(
            f"features_gdf CRS {feats.crs} could not be aligned to zones CRS "
            f"{zones_gdf.crs}: {exc}") from exc

    zones = zones_gdf.copy()
    zones["geometry"] = zones.geometry.make_valid()

    # Aggregate convention (same as spatial_aggregate): intersects, so a
    # boundary point is counted for the zone it lies on the edge of.
    joined = gpd.sjoin(feats, zones, how="inner", predicate="intersects")

    nan_numerator_excluded = 0
    if numerator_field is None:
        counts = joined.groupby("index_right").size()
        numerator = zones.index.map(counts).fillna(0).astype("int64")
    support_counts = joined.groupby("index_right").size()
    has_support = zones.index.map(support_counts).fillna(0) > 0
    if numerator_field is None:
        numerator = zones.index.map(counts).fillna(0).astype("int64")
    else:
        values = pd.to_numeric(joined[numerator_field], errors="coerce")
        nan_numerator_excluded = int(values.isna().sum())
        sums = values.groupby(joined["index_right"]).sum(min_count=1)
        numerator = zones.index.map(sums).astype("float64").fillna(0.0)
        # M2（科学评审修复）：有相交要素但数值**全部** NaN 的区 —— 分子
        # 无任何有效观测，绝不能伪装成真零（rate=0 + has_support=True 会
        # 把它与真实零混淆）→ NaN（rate=None）。无要素区的 0 是真实计数
        # 零，保留（has_support=False 已经区分了支撑语义）。
        valid_counts = values.dropna().groupby(joined["index_right"]).size()
        fabricated = has_support & (zones.index.map(valid_counts).fillna(0) == 0)
        numerator = pd.Series(
            np.asarray(numerator, dtype="float64"), index=zones.index
        ).mask(fabricated, np.nan)

    # Explicit denominator (never implicit, never invented).
    area_crs = ""
    area_crs_class = ""
    if denominator_kind == "field":
        denom_values = pd.to_numeric(zones[denominator], errors="coerce")
        denom_unit = f"zone_field:{denominator}"
    elif denominator_kind == "area":
        denom_values, area_crs, area_crs_class = _metric_zone_area_m2(zones)
        denom_unit = "m2"
    else:  # count
        denom_values = pd.Series(np.asarray(support_counts.reindex(zones.index).fillna(0)),
                                 index=zones.index, dtype="float64")
        denom_unit = "count"

    denom = pd.to_numeric(denom_values, errors="coerce")

    # Zero-denominator policy: rate is None (JSON null) where the denominator
    # is missing/NaN/≤0 — NEVER 0, NEVER inf.
    valid = denom.notna() & (denom > 0)
    rates: list = []
    num_list = numerator.tolist()
    den_list = [float(v) if pd.notna(v) else None for v in denom]
    for i in range(len(zones)):
        if bool(valid.iloc[i]):
            rates.append(float(num_list[i]) / float(denom.iloc[i]))
        else:
            rates.append(None)

    zero_denominator_zones = int((~valid).sum())

    out = zones.copy()
    out["numerator"] = num_list
    out["denominator"] = den_list
    # object dtype is load-bearing: a plain list assignment makes pandas
    # infer float64 and coerce None → NaN, hiding the explicit zero-denominator
    # contract. Consumers must see None (JSON null), never a fabricated value.
    out["rate"] = pd.Series(rates, index=zones.index, dtype=object)
    out["has_support"] = [bool(v) for v in has_support.tolist()]

    if denominator_kind == "count":
        # Anti-goal guard: a count denominator is a RATIO (numerator per
        # feature), not a rate/density — label it honestly.
        rate_unit = "count_ratio_not_rate"
    elif denominator_kind == "area":
        rate_unit = "per_m2"
    else:
        rate_unit = f"numerator_per_{denominator}"

    evidence = {
        "denominator_kind": denominator_kind,
        "denominator_unit": denom_unit,
        "rate_unit": rate_unit,
        "zero_denominator_policy": "rate=None（分母缺失/≤0 的区不产率值——从不编造 0 或 inf）",
        "zero_denominator_zones": zero_denominator_zones,
        "nan_numerator_excluded": nan_numerator_excluded,
        "numerator_aggregation": "sum" if numerator_field else "feature_count",
        "join_predicate": "intersects",
        "crs_aligned": crs_aligned,
        "area_crs": area_crs,
        "area_crs_class": area_crs_class,
        "zones_total": int(len(out)),
    }
    return out, evidence

def h3_binning(geojson: dict | str, resolution: int | None = None, stat_field: str | None = None, stat_method: str = 'count') -> GeoAnalysisResult:
    """
    Bin points into H3 hexagons.
    Supports stats: count, sum, mean.
    If resolution is None, it is automatically selected based on the extent.
    """
    try:
        import h3
        import math
        if isinstance(geojson, dict) and 'features' in geojson:
            # GIS-599: honor a declared `crs` member instead of hardcoding
            # EPSG:4326 — a declared projected input (e.g. EPSG:3857 metres)
            # would otherwise be fed to latitude/longitude H3 lookups.
            gdf = gdf_from_features(geojson, "h3_binning")
        else:
            return GeoAnalysisResult(success=False, data=None, summary="Invalid geojson input.")
            
        if gdf.empty:
            return GeoAnalysisResult(success=False, data=None, summary="Empty geojson input.")
            
        # Automatic resolution selection (审计：从度数阈值改为米制阈值)
        if resolution is None:
            xmin, ymin, xmax, ymax = gdf.total_bounds
            # 用 centroid 纬度近似转换度数为米（仍受纬度偏差影响，但比纯度数更直观）
            lat = (ymin + ymax) / 2
            m_per_deg_lat = 111_320
            m_per_deg_lon = 111_320 * math.cos(math.radians(lat))
            max_dim_m = max(
                (xmax - xmin) * m_per_deg_lon,
                (ymax - ymin) * m_per_deg_lat,
            )
            # 阈值对应原度数阈值的等价米数（赤道近似）
            if max_dim_m > 5_566_000:  # > ~50°
                resolution = 1
            elif max_dim_m > 1_113_000:  # > ~10°
                resolution = 3
            elif max_dim_m > 111_300:  # > ~1°
                resolution = 5
            elif max_dim_m > 11_130:  # > ~0.1°
                resolution = 7
            elif max_dim_m > 1_113:  # > ~0.01°
                resolution = 9
            else:
                resolution = 11
            
        # Ensure point geometry
        if not all(geom.geom_type == 'Point' for geom in gdf.geometry):
            gdf['geometry'] = gdf.geometry.centroid
            
        stat_method_requested = stat_method
        _degraded_stat = False
        # Assign H3 index (向量化：避免 O(n) apply lambda)
        lats = gdf.geometry.y.values
        lngs = gdf.geometry.x.values
        gdf['h3_index'] = [h3.latlng_to_cell(lat, lng, resolution) for lat, lng in zip(lats, lngs)]
        
        # Group by H3 index
        if stat_method == 'count':
            grouped = gdf.groupby('h3_index').size().rename('count').reset_index()
        elif stat_field and stat_field in gdf.columns:
            if stat_method in ['sum', 'mean']:
                grouped = gdf.groupby('h3_index')[stat_field].agg(stat_method).rename(stat_method).reset_index()
            else:
                grouped = gdf.groupby('h3_index').size().rename('count').reset_index()
                stat_method = 'count'
                _degraded_stat = True
        else:
            grouped = gdf.groupby('h3_index').size().rename('count').reset_index()
            stat_method = 'count'
            # G-9（#873）：sum/mean 无有效 stat_field 时静默降级 count ——
            # 在结果信封显式披露（tool 层据此给 correction_hint），否则
            # LLM 以为拿到均值专题图，下游 legend_spec 还会因列名失配而
            # 静默缺失。
            _degraded_stat = stat_method_requested in ('sum', 'mean')
            
        # Create Polygons from H3 indices
        from app.lib.geo_analysis.interpolation import h3_cell_ring
        polygons = []
        for h3_id in cancellable(grouped['h3_index'], every=512):
            # #763: h3_cell_ring unwraps lngs for cells crossing the
            # antimeridian — a naive ring joins ±179.99 vertices through
            # lng 0, producing a world-spanning polygon (~160,000x area
            # blow-up at res 9).
            polygons.append(Polygon(h3_cell_ring(h3_id)))
            
        hex_gdf = gpd.GeoDataFrame(grouped, geometry=polygons, crs="EPSG:4326")

        summary = f"Binned {len(gdf)} points into {len(hex_gdf)} hexagons at resolution {resolution}."
        data_out = hex_gdf.__geo_interface__
        if _degraded_stat:
            # G-9（#873）：降级披露 —— 请求了 sum/mean 但缺有效 stat_field。
            data_out["stat_method_effective"] = "count"
            data_out["warning"] = (
                f"stat_method={stat_method_requested} 需要 stat_field 指向数值列，"
                f"已降级为 count 统计。"
            )
            summary += f" ⚠ stat_method={stat_method_requested} 缺 stat_field，已降级为 count。"

        return GeoAnalysisResult(
            success=True,
            data=data_out,
            summary=summary,
            evidence=build_quality_evidence(
                input_count=len(gdf),
                output_count=len(hex_gdf),
                working_crs="EPSG:4326",
                extra={
                    "resolution": int(resolution),
                    "stat_method": str(stat_method),
                    "stat_degraded": _degraded_stat,
                },
            )
        )
    except Exception as e:
        return GeoAnalysisResult(
            success=False,
            data=None,
            summary=f"H3 binning failed: {str(e)}"
        )

