"""Conformance tests for the general spatial-weights module (ADR-0099 VNext).

Trusted hand-computed anchors — these verify the *science* of the weights
builders (adjacency semantics, islands, self-exclusion, standardization),
not implementation internals:

- 3×3 grid rook: non-corner interior cell has 4 neighbours, edge centres 3,
  corners 2 (the classic textbook contiguity anchor);
- queen adds the diagonals: interior 8 / edges 5 / corners 3;
- islands (no neighbour at all) are detected, not silently dropped;
- KNN is symmetric after the union symmetrization and never self-loops,
  even with coincident points (E-4);
- distance-band binary weights support the Gi* include_self (w_ii=1) form;
- inverse-distance weights are 1/(d+eps)^power with zero diagonal.
"""
import numpy as np
import pytest
import geopandas as gpd
from shapely.geometry import Polygon

from app.lib.geo_analysis.spatial_weights import (
    auto_band_8nn,
    build_contiguity_weights,
    build_distance_band_weights,
    build_inverse_distance_weights,
    build_knn_weights,
)
from app.lib.gis.scientific_errors import ResourceScaleMismatch, UnsupportedMethod


def _grid_gdf(nrows, ncols, cell=10.0, x0=0.0, y0=0.0, val_fn=None):
    """Grid polygons whose adjacent cells share *identical* vertex floats.

    Naive ``x0 + (c+1)*cell`` vs ``(x0 + cell) + c*cell`` differ in the last
    ULP, which silently breaks exact vertex-matching contiguity — build all
    cells from one shared edge-coordinate array instead.
    """
    xs = [x0 + i * cell for i in range(ncols + 1)]
    ys = [y0 + j * cell for j in range(nrows + 1)]
    polys, vals = [], []
    for r in range(nrows):
        for c in range(ncols):
            polys.append(Polygon([
                (xs[c], ys[r]), (xs[c + 1], ys[r]),
                (xs[c + 1], ys[r + 1]), (xs[c], ys[r + 1]),
            ]))
            vals.append(float(val_fn(r, c)) if val_fn else 0.0)
    return gpd.GeoDataFrame({"val": vals}, geometry=polys, crs="EPSG:32650")


def _degrees(wm):
    """Binary neighbour degree per observation (row sums of the raw matrix)."""
    return np.asarray(wm.matrix.sum(axis=1)).ravel()


# ── rook: 3×3 hand-computed anchor ───────────────────────────────────

def test_rook_3x3_grid_hand_computed():
    wm = build_contiguity_weights(_grid_gdf(3, 3), scheme="rook",
                                  row_standardized=False)
    assert wm.scheme == "rook"
    assert wm.n == 9
    assert wm.islands == []
    deg = _degrees(wm)
    # cell index = r*3 + c: centre (1,1) → 4, edge centres → 3, corners → 2
    assert deg[4] == 4
    assert sorted(deg[[1, 3, 5, 7]]) == [3, 3, 3, 3]
    assert sorted(deg[[0, 2, 6, 8]]) == [2, 2, 2, 2]
    # symmetric: rook contiguity is a mutual relation
    dense = wm.matrix.toarray()
    assert np.allclose(dense, dense.T)
    assert np.trace(dense) == 0  # no self-neighbourhood
    # 12 undirected rook adjacencies → 24 directed entries
    assert dense.sum() == 24


def test_queen_adds_diagonals_3x3():
    wm = build_contiguity_weights(_grid_gdf(3, 3), scheme="queen",
                                  row_standardized=False)
    deg = _degrees(wm)
    assert deg[4] == 8          # interior: rook + 4 diagonals
    assert sorted(deg[[1, 3, 5, 7]]) == [5, 5, 5, 5]
    assert sorted(deg[[0, 2, 6, 8]]) == [3, 3, 3, 3]


def test_rook_row_standardized_rows_sum_to_one():
    wm = build_contiguity_weights(_grid_gdf(3, 3), scheme="rook",
                                  row_standardized=True)
    assert wm.row_standardized is True
    row_sums = np.asarray(wm.matrix.sum(axis=1)).ravel()
    assert np.allclose(row_sums, 1.0)
    # S0 of a row-standardized matrix = number of non-island observations
    assert wm.s0 == pytest.approx(9.0)


def test_islands_detected_two_separated_squares():
    from shapely.geometry import Polygon as Poly
    far = gpd.GeoDataFrame(
        geometry=[Poly([(0, 0), (1, 0), (1, 1), (0, 1)]),
                  Poly([(100, 100), (101, 100), (101, 101), (100, 101)])],
        crs="EPSG:32650",
    )
    wm = build_contiguity_weights(far, scheme="rook", row_standardized=False)
    assert sorted(wm.islands) == [0, 1]
    assert wm.s0 == 0.0
    # standardization must not fabricate neighbours for islands
    std = wm.row_standardize()
    assert sorted(std.islands) == [0, 1]
    assert np.asarray(std.matrix.sum(axis=1)).ravel().tolist() == [0.0, 0.0]


def test_contiguity_rejects_non_polygon_input():
    pts = gpd.GeoDataFrame(
        geometry=gpd.points_from_xy([0.0, 1.0], [0.0, 1.0]), crs="EPSG:32650")
    with pytest.raises(UnsupportedMethod) as excinfo:
        build_contiguity_weights(pts, scheme="queen")
    assert "polygonal" in str(excinfo.value)
    assert excinfo.value.correction_hint  # typed error carries a hint


# ── knn: symmetrized union, self-exclusion ───────────────────────────

def test_knn_symmetrized_union_no_self_loop():
    # five points on a line, k=2
    coords = np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0], [3.0, 0.0], [4.0, 0.0]])
    wm = build_knn_weights(coords, k=2, row_standardized=False)
    dense = wm.matrix.toarray()
    assert np.trace(dense) == 0                       # no self-loops
    assert np.allclose(dense, dense.T)                # union symmetrization
    # end points reach inward only; every row has >= k neighbours after union
    deg = dense.sum(axis=1)
    assert (deg >= 2).all()
    assert wm.k == 2


def test_knn_duplicate_coordinates_no_self_loop():
    # E-4 anchor: with coincident points the self column must still be
    # dropped per row — a coincident neighbour is a neighbour, not self.
    coords = np.array([[0.0, 0.0], [0.0, 0.0], [1.0, 0.0], [2.0, 0.0], [5.0, 5.0]])
    wm = build_knn_weights(coords, k=2, row_standardized=False)
    dense = wm.matrix.toarray()
    assert np.trace(dense) == 0
    assert dense[0, 1] == 1 and dense[1, 0] == 1       # duplicates are neighbours


def test_knn_row_standardized_s0_equals_n():
    rng = np.random.default_rng(0)
    coords = rng.uniform(0, 100, (25, 2))
    wm = build_knn_weights(coords, k=4, row_standardized=True)
    assert wm.s0 == pytest.approx(25.0)
    assert wm.islands == []


# ── distance band: binary + Gi* include_self ─────────────────────────

def test_distance_band_binary_and_include_self():
    coords = np.array([[0.0, 0.0], [10.0, 0.0], [20.0, 0.0], [100.0, 0.0]])
    wm = build_distance_band_weights(coords, threshold=10.0, include_self=False)
    dense = wm.matrix.toarray()
    # chain 0-1-2 linked (d == 10 <= threshold); isolated point 3 is an island
    assert dense[0, 1] == 1 and dense[1, 2] == 1 and dense[0, 2] == 0
    assert np.trace(dense) == 0
    assert wm.islands == [3]
    # Gi* form keeps w_ii = 1
    wm_star = build_distance_band_weights(coords, threshold=10.0, include_self=True)
    dense_star = wm_star.matrix.toarray()
    assert np.allclose(np.diag(dense_star), 1.0)
    assert wm_star.include_self is True
    assert wm_star.threshold == 10.0


# ── inverse distance ─────────────────────────────────────────────────

def test_inverse_distance_values_and_standardization():
    coords = np.array([[0.0, 0.0], [3.0, 0.0], [0.0, 4.0]])
    eps = 1e-9
    wm = build_inverse_distance_weights(coords, power=1.0, epsilon=eps,
                                        row_standardized=False)
    dense = wm.matrix.toarray()
    assert np.trace(dense) == 0
    assert dense[0, 1] == pytest.approx(1.0 / (3.0 + eps))
    assert dense[0, 2] == pytest.approx(1.0 / (4.0 + eps))
    assert dense[1, 2] == pytest.approx(1.0 / (5.0 + eps))
    std = build_inverse_distance_weights(coords, power=1.0, epsilon=eps,
                                         row_standardized=True)
    row_sums = np.asarray(std.matrix.sum(axis=1)).ravel()
    assert np.allclose(row_sums, 1.0)


def test_inverse_distance_scale_guard(monkeypatch):
    import app.lib.geo_analysis.spatial_weights as sw
    monkeypatch.setattr(sw, "_MAX_IDW_OBSERVATIONS", 4)
    rng = np.random.default_rng(1)
    coords = rng.uniform(0, 10, (5, 2))
    with pytest.raises(ResourceScaleMismatch) as excinfo:
        build_inverse_distance_weights(coords)
    assert excinfo.value.limit is not None


# ── auto band (E-7 rule) ─────────────────────────────────────────────

def test_auto_band_8nn_two_points_is_their_distance():
    coords = np.array([[0.0, 0.0], [30.0, 40.0]])
    assert auto_band_8nn(coords) == pytest.approx(50.0)


def test_auto_band_8nn_positive_and_deterministic():
    rng = np.random.default_rng(5)
    coords = rng.uniform(0, 1000, (60, 2))
    assert auto_band_8nn(coords) == auto_band_8nn(coords)
    assert auto_band_8nn(coords) > 0
