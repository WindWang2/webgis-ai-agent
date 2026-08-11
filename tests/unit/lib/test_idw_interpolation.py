"""Reference + adversarial tests for idw_interpolation (metric rewrite).

Covers (task §§14-16):
- exact-value specs (single point → constant surface; cell centred on a
  sample → exact recovery),
- an independent *metric* reference IDW (UTM-projected Euclidean, computed
  scalarly from first principles) compared against the vectorized output,
- metric-vs-degree correctness: at high latitude, equal-degree E/W and N/S
  separations have different ground distances and MUST weight differently,
- duplicate-coordinate order invariance (deterministic mean aggregation),
- edge cases: empty / missing field / non-numeric / NaN,inf / power<=0 /
  invalid resolution,
- resource guard: explosive bbox+resolution raises with a suggested lower res.
"""
import numpy as np
import pytest
import h3
import geopandas as gpd

from app.lib.geo_analysis.interpolation import (
    InterpolationResourceExceededError,
    idw_interpolation,
)


def _point_feature(lon, lat, value, field="val"):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {field: value},
    }


def _fc(features):
    return {"type": "FeatureCollection", "features": features}


def _metric_crs_for(lonlat):
    """Pick the metric CRS idw_interpolation would pick for these coordinates."""
    lats = lonlat[:, 1]
    if max(abs(lats.min()), abs(lats.max())) > 84.0:
        return "EPSG:3413" if lats.mean() >= 0 else "EPSG:3031"
    return str(
        gpd.GeoDataFrame(
            geometry=gpd.points_from_xy(lonlat[:, 0], lonlat[:, 1]), crs="EPSG:4326"
        ).estimate_utm_crs()
    )


def _project_to_metric(lonlat, crs):
    gdf = gpd.GeoDataFrame(
        geometry=gpd.points_from_xy(lonlat[:, 0], lonlat[:, 1]), crs="EPSG:4326"
    ).to_crs(crs)
    return np.column_stack((gdf.geometry.x.values, gdf.geometry.y.values))


def _reference_idw_metric(cell_lonlat, pts_lonlat, values, power, k=5):
    """Scalar IDW spec computed in METRIC (UTM) space — used by the standalone
    property tests below. The vectorized-comparison test inlines the same
    maths after a single batched projection."""
    crs = _metric_crs_for(pts_lonlat)
    pts_m = _project_to_metric(pts_lonlat, crs)
    cell_m = _project_to_metric(np.asarray([cell_lonlat]), crs)[0]
    ds = [float(np.hypot(pts_m[m, 0] - cell_m[0], pts_m[m, 1] - cell_m[1]))
          for m in range(len(pts_m))]
    order = sorted(range(len(pts_m)), key=lambda m: ds[m])[:k]
    if ds[order[0]] < 1e-9:
        return float(values[order[0]])
    weights = [1.0 / (ds[m] ** power) for m in order]
    wsum = sum(w * float(values[m]) for w, m in zip(weights, order))
    return wsum / sum(weights)


# --------------------------------------------------------------------------- #
# Core behaviour
# --------------------------------------------------------------------------- #
def test_idw_single_point_constant_surface():
    geojson = _fc([_point_feature(120.0, 30.0, 42.5)])
    result = idw_interpolation(geojson, value_field="val", resolution=8)
    assert len(result) > 0
    assert all(r["value"] == 42.5 for r in result)
    assert all(isinstance(r["h3_index"], str) for r in result)


def test_idw_cell_centered_on_input_point_is_exact():
    cell_a = h3.latlng_to_cell(30.0, 120.0, 8)
    cell_b = h3.latlng_to_cell(30.05, 120.05, 8)
    lat_a, lng_a = h3.cell_to_latlng(cell_a)
    lat_b, lng_b = h3.cell_to_latlng(cell_b)
    geojson = _fc([
        _point_feature(lng_a, lat_a, 10.0),
        _point_feature(lng_b, lat_b, 20.0),
    ])
    result = idw_interpolation(geojson, value_field="val", resolution=8)
    by_cell = {r["h3_index"]: r["value"] for r in result}
    assert by_cell[cell_a] == 10.0
    assert by_cell[cell_b] == 20.0


def test_idw_equal_value_points_give_constant_surface():
    geojson = _fc([
        _point_feature(120.0, 30.0, 7.0),
        _point_feature(120.01, 30.01, 7.0),
    ])
    result = idw_interpolation(geojson, value_field="val", resolution=8)
    assert len(result) > 0
    assert all(r["value"] == pytest.approx(7.0) for r in result)


@pytest.mark.parametrize("n_points,resolution,power", [
    (2, 8, 2.0),
    (7, 8, 2.0),
    (20, 7, 3.0),
])
def test_idw_matches_independent_metric_reference(n_points, resolution, power):
    """Vectorized output matches a scalar, independently-computed METRIC IDW.

    All cell centres are projected once (vectorized) in the same CRS the
    implementation uses; the per-cell IDW math is then scalar and independent
    of the cKDTree/batched implementation.
    """
    rng = np.random.default_rng(7)
    lats = 29.9 + rng.uniform(0, 0.2, n_points)
    lons = 119.9 + rng.uniform(0, 0.2, n_points)
    vals = 10.0 + rng.uniform(0, 10.0, n_points)
    geojson = _fc([
        _point_feature(float(lo), float(la), float(v))
        for la, lo, v in zip(lats, lons, vals)
    ])
    result = idw_interpolation(geojson, value_field="val", resolution=resolution, power=power)

    pts_lonlat = np.column_stack([lons, lats])
    min_lon, max_lon = lons.min() - 0.009, lons.max() + 0.009
    min_lat, max_lat = lats.min() - 0.009, lats.max() + 0.009
    polygon = {
        "type": "Polygon",
        "coordinates": [[
            [min_lon, min_lat], [max_lon, min_lat], [max_lon, max_lat],
            [min_lon, max_lat], [min_lon, min_lat],
        ]]
    }
    target_cells = h3.geo_to_cells(polygon, resolution)
    assert len(result) == len(target_cells)

    # Project points + all cell centres ONCE in the implementation's CRS.
    crs = _metric_crs_for(pts_lonlat)
    pts_m = _project_to_metric(pts_lonlat, crs)
    cell_latlng = np.array([h3.cell_to_latlng(c) for c in target_cells])  # (n,2) lat,lng
    cells_lonlat = np.column_stack([cell_latlng[:, 1], cell_latlng[:, 0]])
    cells_m = _project_to_metric(cells_lonlat, crs)

    for r, cell, cell_m in zip(result, target_cells, cells_m):
        assert r["h3_index"] == cell
        ds = np.hypot(pts_m[:, 0] - cell_m[0], pts_m[:, 1] - cell_m[1])
        order = np.argsort(ds)[: min(5, len(ds))]
        if ds[order[0]] < 1e-9:
            expected = float(vals[order[0]])
        else:
            w = 1.0 / (ds[order] ** power)
            expected = float(np.sum(w * vals[order]) / np.sum(w))
        assert abs(r["value"] - expected) < 1e-6, (
            f"cell {cell}: got {r['value']}, expected {expected}"
        )


# --------------------------------------------------------------------------- #
# Metric-vs-degree correctness (the headline fix, I-F01)
# --------------------------------------------------------------------------- #
def test_idw_uses_metric_not_degree_distance():
    """At lat 60, equal-degree N/S and E/W separations have different ground
    distances (1° lon ≈ 55 km, 1° lat ≈ 111 km). A cross of samples with
    N/S=100 and E/W=0 must therefore weight the closer E/W (value 0) more
    heavily → the central cell value must be < 50. The old degree-space IDW
    returned exactly 50 (all four at equal degree distance)."""
    lat0, lon0, deg = 60.0, 120.0, 0.02
    center_cell = h3.latlng_to_cell(lat0, lon0, 9)
    c_lat, c_lng = h3.cell_to_latlng(center_cell)
    geojson = _fc([
        _point_feature(c_lng, c_lat + deg, 100.0),  # north, ~111m×deg/.. far
        _point_feature(c_lng, c_lat - deg, 100.0),  # south
        _point_feature(c_lng + deg, c_lat, 0.0),    # east, ~55m×deg  (closer)
        _point_feature(c_lng - deg, c_lat, 0.0),    # west, ~55m×deg  (closer)
    ])
    result = idw_interpolation(geojson, value_field="val", resolution=9)
    by_cell = {r["h3_index"]: r["value"] for r in result}
    assert center_cell in by_cell, "center cell should be in the surface"
    # Metric: E/W (0.0) are closer → pull the centre well below the 50
    # midpoint. The buggy degree-space IDW returned ~49.9999 (float noise
    # off 50.0); a < 40 threshold clearly discriminates the metric fix
    # (HEAD ≈ 20).
    assert by_cell[center_cell] < 40.0, (
        f"expected metric IDW < 40 at lat 60, got {by_cell[center_cell]}"
    )


# --------------------------------------------------------------------------- #
# Duplicate-coordinate determinism (I-F03)
# --------------------------------------------------------------------------- #
def test_idw_duplicate_coordinates_order_invariant():
    base = [_point_feature(120.0, 30.0, 10.0),
            _point_feature(120.0, 30.0, 20.0),  # exact duplicate coord
            _point_feature(120.05, 30.05, 30.0)]
    reordered = [base[1], base[2], base[0]]
    r1 = {r["h3_index"]: r["value"] for r in idw_interpolation(_fc(base), "val", 8)}
    r2 = {r["h3_index"]: r["value"] for r in idw_interpolation(_fc(reordered), "val", 8)}
    assert set(r1) == set(r2)
    for k in r1:
        assert r1[k] == pytest.approx(r2[k], abs=1e-12)


def test_idw_duplicate_coordinate_aggregates_by_mean():
    # Two coincident samples (10, 30) → exact-hit recovers their MEAN (20).
    cell = h3.latlng_to_cell(30.0, 120.0, 8)
    lat, lng = h3.cell_to_latlng(cell)
    geojson = _fc([
        _point_feature(lng, lat, 10.0),
        _point_feature(lng, lat, 30.0),
    ])
    result = idw_interpolation(geojson, value_field="val", resolution=8)
    by_cell = {r["h3_index"]: r["value"] for r in result}
    assert by_cell[cell] == pytest.approx(20.0)


# --------------------------------------------------------------------------- #
# Edge cases (I-F04)
# --------------------------------------------------------------------------- #
def test_idw_empty_input_raises():
    with pytest.raises(ValueError):
        idw_interpolation(_fc([]), "val", 8)


def test_idw_missing_value_field_raises():
    geojson = _fc([_point_feature(120.0, 30.0, 1.0)])  # field is 'val', ask for 'nope'
    with pytest.raises(ValueError):
        idw_interpolation(geojson, "nope", 8)


def test_idw_non_numeric_value_raises():
    geojson = _fc([_point_feature(120.0, 30.0, "x")])
    with pytest.raises(ValueError):
        idw_interpolation(geojson, "val", 8)


def test_idw_power_le_zero_raises():
    geojson = _fc([_point_feature(120.0, 30.0, 1.0)])
    with pytest.raises(ValueError):
        idw_interpolation(geojson, "val", 8, power=0)
    with pytest.raises(ValueError):
        idw_interpolation(geojson, "val", 8, power=-1)


def test_idw_invalid_resolution_raises():
    geojson = _fc([_point_feature(120.0, 30.0, 1.0)])
    with pytest.raises(ValueError):
        idw_interpolation(geojson, "val", 16)
    with pytest.raises(ValueError):
        idw_interpolation(geojson, "val", -1)


def test_idw_nan_inf_values_dropped_not_propagated():
    cell = h3.latlng_to_cell(30.0, 120.0, 8)
    lat, lng = h3.cell_to_latlng(cell)
    geojson = _fc([
        _point_feature(lng, lat, float("nan")),
        _point_feature(lng + 0.01, lat, 5.0),  # finite, keeps the surface alive
    ])
    result = idw_interpolation(geojson, value_field="val", resolution=8)
    assert len(result) > 0
    assert all(np.isfinite(r["value"]) for r in result)


def test_idw_all_nan_values_raises():
    geojson = _fc([_point_feature(120.0, 30.0, float("nan"))])
    with pytest.raises(ValueError):
        idw_interpolation(geojson, "val", 8)


# --------------------------------------------------------------------------- #
# Resource guard (I-F02)
# --------------------------------------------------------------------------- #
def test_idw_resource_guard_rejects_explosive_request():
    # Whole-world bbox at a high resolution → billions of cells.
    geojson = _fc([
        _point_feature(-179.0, -80.0, 1.0),
        _point_feature(179.0, 80.0, 2.0),
    ])
    with pytest.raises(InterpolationResourceExceededError) as ei:
        idw_interpolation(geojson, "val", resolution=10)
    err = ei.value
    assert err.estimated_cells > 1_500_000
    assert len(err.suggested_resolutions) >= 1
    assert all(s < 10 for s in err.suggested_resolutions)
