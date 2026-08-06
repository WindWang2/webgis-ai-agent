"""Characterization tests for the vectorized classification loops in
``app/lib/geo_analysis/statistics.py`` (behavior-preserving sweep).

Each test pins the scalar spec with an *independent* reference implementation
(no cKDTree, no matrix algebra, no shared helpers for the part under test):

- ``hotspot_narrated``: per-point Gi* recomputed with explicit O(n²) weights and
  Python loops, then the exact scalar if/elif classification spec
  (p-value thresholds 0.05/0.01/0.1, gi_star sign, confidence tiers, output
  ordering, and the p<0.05-only hot/cold counts).
- ``h3_lisa``: the q → label mapping (1=HH, 2=LH, 3=LL, 4=HL, else NS) applied
  by a scalar loop over the identical seeded ``Moran_Local`` output, plus the
  counts dict.
- ``moran_i_narrated``: pins the *current* 99-permutation p-value computation
  against a seeded reference that replays the exact same RNG stream
  (``np.random.default_rng(42)``) — the permutation loop is intentionally NOT
  vectorized, so this test documents that the algorithm must not change.
"""
import numpy as np
import pandas as pd
import pytest
from scipy.stats import norm

from app.lib.geo_processor.core import to_utm_gdf
from app.lib.geo_analysis.statistics import h3_lisa, hotspot_narrated, moran_i_narrated


# ── shared input builders ──────────────────────────────────────────────────────

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


def _hotspot_dataset(seed):
    """Dense high cluster + mid ring + low surround (values 8..110)."""
    rng = np.random.default_rng(seed)
    pts = []
    for _ in range(14):
        ang = rng.uniform(0, 2 * np.pi)
        r = rng.uniform(0, 40)
        pts.append(((116.39 + r * np.cos(ang) * 8e-5, 39.90 + r * np.sin(ang) * 8e-5),
                    rng.uniform(90, 110)))
    for _ in range(18):
        ang = rng.uniform(0, 2 * np.pi)
        r = rng.uniform(120, 220)
        pts.append(((116.39 + r * np.cos(ang) * 8e-5, 39.90 + r * np.sin(ang) * 8e-5),
                    rng.uniform(8, 15)))
    for _ in range(12):
        ang = rng.uniform(0, 2 * np.pi)
        r = rng.uniform(60, 110)
        pts.append(((116.39 + r * np.cos(ang) * 8e-5, 39.90 + r * np.sin(ang) * 8e-5),
                    rng.uniform(40, 60)))
    return pts


def _hex_fc(val_fn):
    """H3 resolution-8 hexagon grid (radius-4 disk) with a ``value`` property."""
    import h3
    center = h3.latlng_to_cell(39.9, 116.39, 8)
    features = []
    for h in h3.grid_disk(center, 4):
        boundary = h3.cell_to_boundary(h)
        coords = [[[lng, lat] for lat, lng in boundary]]
        coords[0].append(coords[0][0])
        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": coords},
            "properties": {"h3_index": h, "value": val_fn(h)},
        })
    return {"type": "FeatureCollection", "features": features}


# ── hotspot_narrated: independent scalar reference ────────────────────────────

def _reference_hotspot(geojson, value_field, distance_band):
    """Scalar spec for the hotspot output: O(n²) weights + per-point Gi* + the
    exact if/elif classification. No cKDTree, no matrix products."""
    gdf, _ = to_utm_gdf(geojson)
    series = gdf[value_field]
    if not np.issubdtype(series.dtype, np.number):
        series = pd.to_numeric(series, errors="coerce")
    valid = series.notna().to_numpy()
    gdf = gdf[valid].reset_index(drop=True)
    values = series[valid].to_numpy(dtype=float)
    n = len(values)
    coords = np.column_stack((gdf.centroid.x.values, gdf.centroid.y.values))

    if distance_band <= 0:
        best = np.full(n, np.inf)
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                d = float(np.hypot(coords[i, 0] - coords[j, 0], coords[i, 1] - coords[j, 1]))
                if d < best[i]:
                    best[i] = d
        bw = float(best.mean())
        if bw <= 0:
            bw = 1.0
    else:
        bw = float(distance_band)

    w = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            if i != j and np.hypot(coords[i, 0] - coords[j, 0], coords[i, 1] - coords[j, 1]) <= bw:
                w[i, j] = 1.0

    x_bar = float(values.mean())
    s = float(values.std(ddof=0))
    gis, ps = [], []
    for i in range(n):
        sum_wi = float(np.sum(w[i]))
        num = float(np.dot(w[i], values))
        numerator = num - x_bar * sum_wi
        denom_inner = (n * sum_wi - sum_wi ** 2) / (n - 1)
        denom = s * np.sqrt(denom_inner) if denom_inner > 0 else 0.0
        gi = numerator / denom if denom != 0 else 0.0
        gis.append(gi)
        ps.append(2.0 * (1.0 - norm.cdf(abs(gi))))

    types, confs = [], []
    for i in range(n):
        h_type = "Not Significant"
        confidence = "Not Significant"
        if ps[i] < 0.05:
            h_type = "Hot Spot" if gis[i] > 0 else "Cold Spot"
            confidence = "99%" if ps[i] < 0.01 else "95%"
        elif ps[i] < 0.1:
            h_type = "Hot Spot" if gis[i] > 0 else "Cold Spot"
            confidence = "90%"
        types.append(h_type)
        confs.append(confidence)

    # Module counts only p < 0.05 (the 90% tier is excluded).
    hot_count = sum(1 for i in range(n) if ps[i] < 0.05 and gis[i] > 0)
    cold_count = sum(1 for i in range(n) if ps[i] < 0.05 and gis[i] < 0)
    return {"types": types, "confs": confs, "gis": gis, "ps": ps, "hot": hot_count, "cold": cold_count}


@pytest.mark.parametrize("seed,band", [
    (3, 800),
    (3, 0),     # auto distance band
    (7, 800),
    (11, 500),
])
def test_hotspot_classification_matches_scalar_reference(seed, band):
    """Vectorized classification is point-for-point equal to the scalar spec:
    same per-feature type/confidence/rounded stats, same ordering, same counts."""
    geojson = _points_fc(_hotspot_dataset(seed))
    res = hotspot_narrated(geojson, "val", distance_band=band)
    assert res.success, res.summary
    ref = _reference_hotspot(geojson, "val", band)

    features = res.data["features"]
    assert len(features) == len(ref["types"])
    assert [f["properties"]["hotspot_type"] for f in features] == ref["types"]
    assert [f["properties"]["confidence"] for f in features] == ref["confs"]
    assert [f["properties"]["gi_star"] for f in features] == [round(g, 4) for g in ref["gis"]]
    assert [f["properties"]["p_value"] for f in features] == [round(p, 6) for p in ref["ps"]]
    assert res.data["hot_spots_count"] == ref["hot"]
    assert res.data["cold_spots_count"] == ref["cold"]


def test_hotspot_classification_covers_all_confidence_tiers():
    """A cluster dataset must exercise every branch: 99%/95%/90%/NS tiers and
    both Hot and Cold Spot signs."""
    geojson = _points_fc(_hotspot_dataset(3))
    res = hotspot_narrated(geojson, "val", distance_band=800)
    assert res.success
    types = [f["properties"]["hotspot_type"] for f in res.data["features"]]
    confs = [f["properties"]["confidence"] for f in res.data["features"]]
    assert set(confs) == {"99%", "95%", "90%", "Not Significant"}
    assert set(types) == {"Hot Spot", "Cold Spot", "Not Significant"}
    # counts are consistent with the per-feature types (p < 0.05 only: the 90%
    # tier is excluded from the counts)
    assert res.data["hot_spots_count"] == sum(
        1 for f, c in zip(types, confs) if f == "Hot Spot" and c != "90%"
    )
    assert res.data["cold_spots_count"] == sum(
        1 for f, c in zip(types, confs) if f == "Cold Spot" and c != "90%"
    )


# ── h3_lisa: independent scalar classification reference ──────────────────────

def _scalar_lisa_classify(p_sim, q):
    """Scalar spec for the LISA q → label mapping + counts dict."""
    clusters = []
    counts = {"HH": 0, "LL": 0, "HL": 0, "LH": 0, "NS": 0}
    for i, p in enumerate(p_sim):
        if p < 0.05:
            qv = q[i]
            if qv == 1:
                c = "HH"
            elif qv == 2:
                c = "LH"
            elif qv == 3:
                c = "LL"
            elif qv == 4:
                c = "HL"
            else:
                c = "NS"
        else:
            c = "NS"
        clusters.append(c)
        counts[c] += 1
    return clusters, counts


@pytest.fixture(scope="module")
def _lisa_center():
    import h3
    return h3.latlng_to_cell(39.9, 116.39, 8)


def _val_moderate(center, h):
    """High blob + isolated high cell (HL) + isolated low cell in the
    transition zone (LL/NS), moderate contrast."""
    import h3
    ring3 = h3.grid_ring(center, 3)
    ring4 = h3.grid_ring(center, 4)
    iso_high, iso_low = ring4[2], ring3[5]
    d = h3.grid_distance(center, h)
    if h == iso_high:
        return 100
    if h == iso_low:
        return 5
    if d <= 2:
        return 100 + (int(h, 16) % 3)
    return 10 + (int(h, 16) % 3)


def _val_lh(center, h):
    """Isolated low cell inside the high blob with extreme contrast (LH)."""
    import h3
    ring1 = h3.grid_ring(center, 1)
    if h == ring1[3]:
        return 1
    if h3.grid_distance(center, h) <= 2:
        return 1000 + (int(h, 16) % 3)
    return 10 + (int(h, 16) % 3)


@pytest.mark.parametrize("pattern,expected_labels", [
    ("moderate", {"HH", "LL", "HL", "NS"}),
    ("lh_extreme", {"HH", "LL", "LH", "NS"}),
])
def test_h3_lisa_classification_matches_scalar_reference(pattern, expected_labels, _lisa_center):
    """Vectorized q → label mapping + counts equal the scalar spec applied to
    the identical seeded Moran_Local output; all four q codes get exercised."""
    pytest.importorskip("esda", exc_type=ImportError)
    pytest.importorskip("libpysal", exc_type=ImportError)
    from libpysal.weights import Queen
    from esda.moran import Moran_Local

    if pattern == "moderate":
        def val_fn(h):
            return _val_moderate(_lisa_center, h)
    else:
        def val_fn(h):
            return _val_lh(_lisa_center, h)
    fc = _hex_fc(val_fn)

    res = h3_lisa(fc, "value")
    assert res.success

    gdf, _ = to_utm_gdf(fc)
    gdf = gdf[gdf["value"].notna()].reset_index(drop=True)
    w = Queen.from_dataframe(gdf)
    w.transform = "r"
    lisa = Moran_Local(gdf["value"].to_numpy(dtype=float), w, seed=42)
    ref_clusters, ref_counts = _scalar_lisa_classify(lisa.p_sim, lisa.q)

    got = [f["properties"]["lisa_cluster"] for f in res.data["features"]]
    assert got == ref_clusters
    assert res.data["cluster_stats"] == ref_counts
    assert set(got) == expected_labels


# ── moran_i_narrated: pin the seeded permutation p-value ──────────────────────

def test_moran_i_pvalue_matches_seeded_scalar_reference():
    """The 99-permutation p-value must stay tied to the exact RNG stream
    (np.random.default_rng(42)) — a seeded reference that replays the stream
    point-for-point must reproduce the reported moran_i and p_value."""
    rng = np.random.default_rng(21)
    n = 14
    pts = []
    for _ in range(n):
        ang = rng.uniform(0, 2 * np.pi)
        r = rng.uniform(0, 150)
        pts.append(((116.39 + r * np.cos(ang) * 8e-5, 39.90 + r * np.sin(ang) * 8e-5),
                    float(rng.uniform(5, 100))))
    geojson = _points_fc(pts)

    res = moran_i_narrated(geojson, "val")
    assert res.success, res.summary

    # Independent scalar reference: O(n²) kNN weights, then the permutation
    # test with the exact same RNG stream as the implementation.
    from scipy import sparse
    gdf, _ = to_utm_gdf(geojson)
    values = gdf["val"].to_numpy(dtype=float)
    coords = np.column_stack((gdf.centroid.x.values, gdf.centroid.y.values))
    n_pts = len(values)
    k = min(8, n_pts - 1)
    rows, cols = [], []
    for i in range(n_pts):
        order = sorted(range(n_pts), key=lambda j: (float(np.hypot(coords[i, 0] - coords[j, 0],
                                                                  coords[i, 1] - coords[j, 1])), j))
        nbrs = [j for j in order if j != i][:k]
        rows.extend([i] * k)
        cols.extend(nbrs)
    w = sparse.coo_matrix((np.ones(len(rows)), (np.array(rows), np.array(cols))),
                          shape=(n_pts, n_pts))

    z = values - values.mean()
    s0 = float(w.sum())
    numerator = float(np.sum(w.data * z[w.row] * z[w.col]))
    denominator = np.sum(z ** 2)
    moran_i = (n_pts / s0) * (numerator / denominator) if denominator > 0 else 0
    expected_i = -1.0 / (n_pts - 1)

    perm_rng = np.random.default_rng(42)
    perm_is = []
    for _ in range(99):
        pv = perm_rng.permutation(values)
        pz = pv - pv.mean()
        p_num = np.sum(w.data * pz[w.row] * pz[w.col])
        p_den = np.sum(pz ** 2)
        perm_is.append((n_pts / s0) * (p_num / p_den) if p_den > 0 else 0)
    p_value = float(np.mean(np.abs(np.array(perm_is) - expected_i) >= np.abs(moran_i - expected_i)))

    assert res.data["moran_i"] == pytest.approx(moran_i, abs=1e-12)
    assert res.data["p_value"] == pytest.approx(p_value, abs=1e-12)
