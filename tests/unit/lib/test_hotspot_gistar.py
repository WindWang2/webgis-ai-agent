"""Issue #693 item 1: hotspot Gi* must include self (w_ii=1).

Published small example (hand-checked): 4 collinear points, first two
high (100), last two low (1), all within 1500 m of the immediate
neighbour but not the far pair. Gi* (with self) gives z ≈ ±1.73 for the
ends, Gi (w_ii=0) gives ±1.0. The new code implements Gi* — this test
is RED on the old sparse path that dropped (i,i) self pairs.
"""

import numpy as np
import pytest
from scipy import sparse
from scipy.spatial import cKDTree

from app.lib.geo_analysis.statistics import hotspot_narrated
from app.lib.geo_processor.core import to_utm_gdf


def _fc_4pt():
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.39, 39.9]}, "properties": {"val": 100}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.399, 39.9]}, "properties": {"val": 100}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.41, 39.9]}, "properties": {"val": 1}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.42, 39.9]}, "properties": {"val": 1}},
        ],
    }


def test_hotspot_gistar_includes_self():
    """Gi* with w_ii=1: end points have |z| ≈ 1.732, not 1.0 (Gi without self)."""
    fc = _fc_4pt()
    res = hotspot_narrated(fc, "val", distance_band=1500)
    assert res.success, res.summary
    gis = [f["properties"]["gi_star"] for f in res.data["features"]]
    # Hand-checked Gi* values (symmetric): [1.7321, 1.0, -1.0, -1.7321]
    assert gis[0] == pytest.approx(1.7321, abs=1e-3)
    assert gis[3] == pytest.approx(-1.7321, abs=1e-3)
    # Gi* strictly larger in magnitude than Gi (w_ii=0) for the hot ends.
    # Gi without self would be [1.0, 0, 0, -1.0].
    assert abs(gis[0]) > 1.4


def test_hotspot_gistar_recomputed_reference():
    """Independent recomputation with explicit sparse Gi* weights matches output."""
    fc = _fc_4pt()
    res = hotspot_narrated(fc, "val", distance_band=1500)
    assert res.success
    gdf, _ = to_utm_gdf(fc)
    vals = gdf["val"].to_numpy(dtype=float)
    coords = np.column_stack((gdf.centroid.x.values, gdf.centroid.y.values))
    n = len(vals)
    tree = cKDTree(coords)
    coo = tree.sparse_distance_matrix(tree, max_distance=1500, output_type="coo_matrix")
    w_star = sparse.csr_matrix((np.ones(len(coo.data)), (coo.row, coo.col)), shape=(n, n))
    x_bar = vals.mean()
    s = vals.std(ddof=0)
    sum_wi = np.asarray(w_star.sum(axis=1)).ravel()
    sum_wi2 = np.asarray(w_star.multiply(w_star).sum(axis=1)).ravel()
    num = np.asarray(w_star @ vals).ravel() - x_bar * sum_wi
    denom_inner = (n * sum_wi2 - sum_wi ** 2) / (n - 1)
    denom = np.where(denom_inner > 0, s * np.sqrt(denom_inner), 0)
    gi_star = np.where(denom != 0, num / denom, 0)
    got = np.array([f["properties"]["gi_star"] for f in res.data["features"]])
    assert np.allclose(got, gi_star, atol=5e-5)
