"""Regression tests for #385: hotspot Gi* keeps spatial weights sparse.

The old implementation densified the cKDTree COO weights into an n×n float64
array — ~8·n² bytes (800 MB at 10k features, 7.2 GB at 30k), which OOM'd
workers. Getis-Ord only needs ``w @ values``, row sums, and squared row
sums; all three are natively supported by CSR.
"""
import numpy as np
from scipy.stats import norm

from app.lib.geo_analysis.statistics import hotspot_narrated
from app.lib.geo_processor.core import to_utm_gdf


def _points_fc(points_vals):
    """FeatureCollection of Points with a numeric ``val`` property."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {"val": v},
            }
            for (lon, lat), v in points_vals
        ],
    }


def _random_pts(n, seed=3):
    rng = np.random.default_rng(seed)
    pts = []
    for _ in range(n):
        ang = rng.uniform(0, 2 * np.pi)
        r = rng.uniform(0, 150)
        pts.append(((116.39 + r * np.cos(ang) * 8e-5, 39.90 + r * np.sin(ang) * 8e-5),
                    float(rng.uniform(5, 100))))
    return pts


# ── numeric equivalence with the old dense implementation ───────────────────


def test_hotspot_matches_old_dense_implementation():
    """Small-n cross-check: the sparse reductions reproduce the old dense
    path (sparse_distance_matrix -> dense -> fill_diagonal 0) within the
    output rounding (gi_star 4 dp, p_value 6 dp)."""
    band = 500.0
    fc = _points_fc(_random_pts(60, seed=3))

    res = hotspot_narrated(fc, "val", distance_band=band)
    assert res.success
    got_gi = np.array([f["properties"]["gi_star"] for f in res.data["features"]])
    got_p = np.array([f["properties"]["p_value"] for f in res.data["features"]])

    # Replay the exact old dense implementation.
    from scipy.spatial import cKDTree
    gdf, _ = to_utm_gdf(fc)
    values = gdf["val"].to_numpy(dtype=float)
    coords = np.column_stack((gdf.centroid.x.values, gdf.centroid.y.values))
    n_pts = len(values)
    tree = cKDTree(coords)
    coo = tree.sparse_distance_matrix(tree, max_distance=band, output_type="coo_matrix")
    w = np.zeros((n_pts, n_pts))
    w[coo.row, coo.col] = 1.0
    np.fill_diagonal(w, 0)

    x_bar = values.mean()
    s = values.std(ddof=0)
    sum_wi = w.sum(axis=1)
    sum_wi2 = (w ** 2).sum(axis=1)
    numerators = w @ values - x_bar * sum_wi
    denom_inners = (n_pts * sum_wi2 - sum_wi ** 2) / (n_pts - 1)
    denominators = np.where(denom_inners > 0, s * np.sqrt(denom_inners), 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        gi_old = np.where(denominators != 0, numerators / denominators, 0)
    p_old = 2 * (1 - norm.cdf(np.abs(gi_old)))

    # Sparse and dense arithmetic agree far inside the output rounding.
    assert np.allclose(got_gi, gi_old, atol=5e-5)
    assert np.allclose(got_p, p_old, atol=5e-7)
    assert res.data["hot_spots_count"] == int(np.sum((p_old < 0.05) & (gi_old > 0)))
    assert res.data["cold_spots_count"] == int(np.sum((p_old < 0.05) & (gi_old < 0)))


# ── large n: no dense n×n allocation, bounded runtime ───────────────────────


def test_hotspot_large_n_stays_sparse(monkeypatch):
    """~20k features complete without ever allocating an n×n dense array
    (the old path would have requested ~3.2 GB here — the worker OOM from
    #385)."""
    import app.lib.geo_analysis.statistics as stats_mod

    side = 142  # 142² = 20164 ≈ 20k
    n = side * side
    pts = []
    # Regular grid, ~30 m spacing; band 100 m covers ~9 neighbours, so the
    # sparse weights stay tiny (O(n·k), not O(n²)).
    for i in range(side):
        for j in range(side):
            lon = 116.39 + i * 3.2e-4
            lat = 39.90 + j * 2.7e-4
            pts.append(((lon, lat), float((i * side + j) % 97)))
    fc = _points_fc(pts)

    real_zeros = np.zeros

    def guarded(shape, *a, **k):
        if isinstance(shape, tuple) and len(shape) == 2 and shape[0] == shape[1] == n:
            raise AssertionError(
                f"hotspot densified weights into an n×n ({shape}) array"
            )
        return real_zeros(shape, *a, **k)

    monkeypatch.setattr(stats_mod.np, "zeros", guarded)

    res = hotspot_narrated(fc, "val", distance_band=100)
    assert res.success
    gis = np.array([f["properties"]["gi_star"] for f in res.data["features"]])
    assert len(gis) == n
    assert np.all(np.isfinite(gis))
    assert isinstance(res.data["hot_spots_count"], int)
    assert isinstance(res.data["cold_spots_count"], int)
    assert res.data["hot_spots_count"] + res.data["cold_spots_count"] > 0
