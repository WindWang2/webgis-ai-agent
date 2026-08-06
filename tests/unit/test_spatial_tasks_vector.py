"""Characterization tests for the vectorized heatmap grid builder.

_build_grid_features converts non-zero histogram cells into GeoJSON features
(up to 500k cells). The vectorized implementation (shapely 2.x array box) must
produce point-for-point identical output to the scalar per-cell loop: same cell
order (row-major), same geometry coordinates, same count/weight properties.
"""
import numpy as np

from app.services.spatial_tasks import _build_grid_features


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
