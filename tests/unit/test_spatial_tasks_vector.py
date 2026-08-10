"""Characterization tests for the vectorized heatmap grid builder.

_build_grid_features converts non-zero histogram cells into GeoJSON features
(up to 500k cells). The vectorized implementation (shapely 2.x array box) must
produce point-for-point identical output to the scalar per-cell loop: same cell
order (row-major), same geometry coordinates, same count/weight properties.
"""
import math

import numpy as np

from app.services.spatial_tasks import _build_grid_features, _build_heatmap_grid


def _reference_grid_features(H, xedges, yedges, max_val):
    """Independent scalar spec: explicit per-cell loop over argwhere order."""
    from shapely.geometry import box, mapping

    features = []
    for idx in np.argwhere(H > 0):
        i, j = int(idx[0]), int(idx[1])
        count = int(H[i, j])
        rect = box(xedges[i], yedges[j], xedges[i + 1], yedges[j + 1])
        features.append({
            "type": "Feature",
            "geometry": mapping(rect),
            "properties": {
                "count": count,
                "weight": round(float(count / max_val), 4),
            },
        })
    return features


def test_build_grid_features_matches_scalar_reference():
    H = np.array([
        [0, 2, 0],
        [1, 0, 3],
        [0, 4, 0],
    ], dtype=int)
    xedges = np.array([0.0, 1.0, 2.0, 3.0])
    yedges = np.array([10.0, 11.0, 12.0, 13.0])
    max_val = 4.0

    got = _build_grid_features(H, xedges, yedges, max_val)
    ref = _reference_grid_features(H, xedges, yedges, max_val)

    assert len(got) == len(ref) == 4
    for g, r in zip(got, ref):
        assert g["properties"] == r["properties"]
        assert g["geometry"]["type"] == r["geometry"]["type"] == "Polygon"
        np.testing.assert_allclose(
            g["geometry"]["coordinates"], r["geometry"]["coordinates"], atol=1e-12
        )


def test_build_grid_features_empty_grid():
    H = np.zeros((3, 3), dtype=int)
    got = _build_grid_features(H, np.arange(4.0), np.arange(4.0), 1.0)
    assert got == []


def test_build_grid_features_row_major_order():
    """Non-zero cells emit in row-major (i, j) order, matching argwhere."""
    H = np.zeros((4, 4), dtype=int)
    H[2, 0] = 5
    H[0, 3] = 7
    H[3, 2] = 9
    xedges = np.arange(5.0)
    yedges = np.arange(5.0)
    got = _build_grid_features(H, xedges, yedges, 9.0)
    counts = [f["properties"]["count"] for f in got]
    assert counts == [7, 5, 9]  # (0,3), (2,0), (3,2) row-major


def test_build_grid_features_too_dense_raises():
    H = np.ones((800, 700), dtype=int)  # 560k nonzero > 500k cap
    with np.testing.assert_raises(ValueError):
        _build_grid_features(H, np.arange(801.0), np.arange(701.0), 1.0)


# ─── GIS-25: latitude-aware cell sizing ─────────────────────────

def _cell_widths_deg(H, xedges, yedges):
    """Return (lng_deg, lat_deg) single-cell widths from the bin edges."""
    return xedges[1] - xedges[0], yedges[1] - yedges[0]


def test_heatmap_cell_deg_is_latitude_corrected():
    """cell_size is meters; the lng bin width must shrink by cos(lat).

    At 60°N, 1 deg lng ≈ 55.7 km vs 111.3 km for lat, so a 500 m cell must be
    ~0.00898 deg wide in lng but only ~0.00449 deg in lat.
    """
    lat = 60.0
    xs = [116.0, 116.01, 116.02]
    # Symmetric offsets around the target latitude so the mean is exactly `lat`.
    ys = [lat - 0.001, lat, lat + 0.001]
    cell_size = 500

    H, xedges, yedges, _ = _build_heatmap_grid(xs, ys, cell_size)
    lng_deg, lat_deg = _cell_widths_deg(H, xedges, yedges)

    expected_lng = cell_size / (111320.0 * math.cos(math.radians(lat)))
    expected_lat = cell_size / 111320.0
    # Bins are built with np.arange, so accept float-accumulation slop (~1e-6 rel).
    np.testing.assert_allclose(lng_deg, expected_lng, rtol=1e-5)
    np.testing.assert_allclose(lat_deg, expected_lat, rtol=1e-5)
    # The old code used cell_size/111000 for BOTH axes — at 60°N that made the
    # lng cell 500m*cos(60°)=250m while the lat cell stayed 500m (not square).
    # Square-in-meters means lng_deg = lat_deg / cos(60°) = 2x lat_deg:
    np.testing.assert_allclose(lng_deg / lat_deg, 1.0 / math.cos(math.radians(lat)), rtol=1e-5)


def test_heatmap_cell_meter_square_at_equator_and_poles():
    """Cells are square in meters at the equator; high-lat guard applies."""
    for lat in (0.0, 30.0):
        xs = [100.0, 100.01, 100.02]
        ys = [lat - 0.001, lat, lat + 0.001]
        _, xedges, yedges, _ = _build_heatmap_grid(xs, ys, 500)
        lng_deg, lat_deg = xedges[1] - xedges[0], yedges[1] - yedges[0]
        m_lng = lng_deg * 111320.0 * math.cos(math.radians(lat))
        m_lat = lat_deg * 111320.0
        assert abs(m_lng - 500) < 0.1, f"lng cell {m_lng:.1f}m != 500m at lat {lat}"
        assert abs(m_lat - 500) < 0.1, f"lat cell {m_lat:.1f}m != 500m at lat {lat}"

    # Guard: at 89.9°N the naive cos(lat) ratio would blow the lng width up;
    # the floor keeps it bounded (<= cell_size/1000 per degree).
    xs = [0.0, 0.01, 0.02]
    ys = [89.899, 89.9, 89.901]
    _, xedges, yedges, _ = _build_heatmap_grid(xs, ys, 500)
    lng_deg = xedges[1] - xedges[0]
    max_lng_deg = 500 / 1000.0
    assert lng_deg <= max_lng_deg + 1e-9, f"lng width {lng_deg} exceeded guard floor"
