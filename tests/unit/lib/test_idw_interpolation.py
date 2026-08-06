"""Characterization tests for idw_interpolation (NumPy-vectorization refactor).

These pin the public behavior of `idw_interpolation` so the vectorized
implementation must stay point-for-point equivalent to the scalar spec:

- exact-value specs (single point → constant surface; cell centered on an
  input point → exact branch), and
- an independent reference computation (explicit Euclidean nearest-neighbor
  IDW, no cKDTree / no batched math) compared across randomized inputs.
"""
import numpy as np
import pytest
import h3

from app.lib.geo_analysis.interpolation import idw_interpolation


def _point_feature(lon, lat, value, field="val"):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {field: value},
    }


def _fc(features):
    return {"type": "FeatureCollection", "features": features}


def _reference_idw(cell_center, coords, values, power, k=5):
    """Scalar IDW spec: explicit Euclidean distances + manual nearest selection.

    Independent of the implementation under test (no cKDTree, no batching) —
    the same algorithm, computed from first principles.
    """
    ds = [float(np.hypot(coords[m, 0] - cell_center[0], coords[m, 1] - cell_center[1]))
          for m in range(len(coords))]
    order = sorted(range(len(coords)), key=lambda m: ds[m])[:k]
    if ds[order[0]] < 1e-10:
        return float(values[order[0]])
    weights = [1.0 / (ds[m] ** power) for m in order]
    wsum = sum(w * float(values[m]) for w, m in zip(weights, order))
    return wsum / sum(weights)


def test_idw_single_point_constant_surface():
    """One input point ⇒ every H3 cell gets exactly that point's value."""
    geojson = _fc([_point_feature(120.0, 30.0, 42.5)])
    result = idw_interpolation(geojson, value_field="val", resolution=8)
    assert len(result) > 0
    assert all(r["value"] == 42.5 for r in result)
    assert all(isinstance(r["h3_index"], str) for r in result)


def test_idw_cell_centered_on_input_point_is_exact():
    """A cell whose center coincides with an input point takes its exact value
    (the d < 1e-10 branch), even in a multi-point field."""
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
    """All neighbors equal ⇒ weighted mean equals that value everywhere."""
    geojson = _fc([
        _point_feature(120.0, 30.0, 7.0),
        _point_feature(120.01, 30.01, 7.0),
    ])
    result = idw_interpolation(geojson, value_field="val", resolution=8)
    assert len(result) > 0
    # Weighted-mean arithmetic carries ~1e-15 float noise around the exact
    # value (verified on the scalar implementation: 6.999999999999999..7.000000000000001).
    assert all(r["value"] == pytest.approx(7.0) for r in result)


@pytest.mark.parametrize("n_points,resolution,power", [
    (2, 8, 2.0),
    (7, 9, 2.0),
    (30, 8, 3.0),
])
def test_idw_matches_independent_reference(n_points, resolution, power):
    """Vectorized output matches a scalar, independently-computed IDW spec:
    same cells in the same order, values equal to 1e-9."""
    rng = np.random.default_rng(7)
    lats = 29.9 + rng.uniform(0, 0.2, n_points)
    lons = 119.9 + rng.uniform(0, 0.2, n_points)
    vals = 10.0 + rng.uniform(0, 10.0, n_points)
    geojson = _fc([
        _point_feature(float(lo), float(la), float(v))
        for la, lo, v in zip(lats, lons, vals)
    ])
    result = idw_interpolation(geojson, value_field="val", resolution=resolution, power=power)

    # Recompute the same target cells the implementation must cover.
    coords = np.column_stack([lats, lons])
    min_lat, min_lon = coords.min(axis=0)
    max_lat, max_lon = coords.max(axis=0)
    buf = 0.009
    polygon = {
        "type": "Polygon",
        "coordinates": [[
            [min_lon - buf, min_lat - buf], [max_lon + buf, min_lat - buf],
            [max_lon + buf, max_lat + buf], [min_lon - buf, max_lat + buf],
            [min_lon - buf, min_lat - buf],
        ]]
    }
    target_cells = h3.geo_to_cells(polygon, resolution)
    assert len(result) == len(target_cells)

    for r, cell in zip(result, target_cells):
        assert r["h3_index"] == cell
        center = h3.cell_to_latlng(cell)
        expected = _reference_idw(center, coords, vals, power)
        assert abs(r["value"] - expected) < 1e-9, (
            f"cell {cell}: got {r['value']}, expected {expected}"
        )
