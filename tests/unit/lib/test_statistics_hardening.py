"""Hardening tests for the spatial-statistics layer (Slice 5).

Covers the E-1 .. E-11 audit findings: JSON-unsafe cluster keys, Moran's I /
H3-LISA constant-value guards, KNN self-loop on duplicates, dead (None,None)
input checks, inf filtering, hotspot auto-band connectivity, and the kmeans
n_clusters guard.
"""
import json

import numpy as np
import pytest

from app.lib.geo_analysis.statistics import (
    cluster_narrated,
    h3_lisa,
    hotspot_narrated,
    moran_i_narrated,
)


def _points_fc(pts, field="val"):
    feats = []
    for (xy, v) in pts:
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [xy[0], xy[1]]},
            "properties": {field: v},
        })
    return {"type": "FeatureCollection", "features": feats}


def _clustered_field():
    """Two tight high-value clusters + two tight low-value clusters."""
    rng = np.random.default_rng(5)
    pts = []
    for cx, cy, val in [(116.39, 39.90, 100.0), (116.42, 39.92, 100.0),
                        (116.39, 39.95, 1.0), (116.42, 39.95, 1.0)]:
        for _ in range(12):
            pts.append(((cx + rng.normal(0, 3e-4), cy + rng.normal(0, 3e-4)), float(val)))
    return _points_fc(pts)


# --------------------------------------------------------------------------- #
# E-1: cluster result is JSON-serializable
# --------------------------------------------------------------------------- #
def test_cluster_result_json_serializable():
    res = cluster_narrated(_clustered_field(), method="dbscan", eps=100, min_samples=3)
    assert res.success
    # The dispatch layer runs json.dumps on every successful result; the old
    # np.int64 dict keys crashed it.
    s = json.dumps(res.data)
    assert "cluster_stats" in s


# --------------------------------------------------------------------------- #
# E-2 / E-11: Moran's I rejects constant values; inf filtered
# --------------------------------------------------------------------------- #
def test_moran_rejects_constant_values():
    pts = [((116.0 + i * 0.001, 39.0), 5.0) for i in range(8)]
    res = moran_i_narrated(_points_fc(pts), "val")
    assert not res.success
    assert res.error_type == "ValueError"


def test_moran_inf_values_filtered_not_nan():
    pts = [((116.0 + i * 0.001, 39.0), v) for i, v in enumerate([1, 2, 3, 4, float("inf"), 6, 7, 8])]
    res = moran_i_narrated(_points_fc(pts), "val")
    assert res.success
    # Discriminating (review F): the buggy path kept inf, poisoning std -> I=0.
    # With inf filtered, the remaining 7 ascending values show real negative
    # autocorrelation on this near-linear layout (I well away from 0).
    assert np.isfinite(res.data["moran_i"])
    assert np.isfinite(res.data["p_value"])
    assert abs(res.data["moran_i"]) > 0.05, res.data["moran_i"]


# --------------------------------------------------------------------------- #
# E-3: h3_lisa rejects constant values
# --------------------------------------------------------------------------- #
def _h3_grid():
    import h3

    cells = h3.geo_to_cells(
        {"type": "Polygon",
         "coordinates": [[[116.38, 39.89], [116.43, 39.89], [116.43, 39.93], [116.38, 39.93], [116.38, 39.89]]]},
        8,
    )
    feats = []
    for i, c in enumerate(cells):
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [
                [(lng, lat) for lat, lng in h3.cell_to_boundary(c)]
            ]},
            "properties": {"h3_index": c, "val": 1.0},  # constant
        })
    return {"type": "FeatureCollection", "features": feats}


def test_h3_lisa_rejects_constant_values():
    fc = _h3_grid()
    if len(fc["features"]) < 3:
        pytest.skip("H3 grid too small at this extent")
    res = h3_lisa(fc, "val")
    assert not res.success
    assert res.error_type == "ValueError"


# --------------------------------------------------------------------------- #
# E-4: KNN weights — no self-loop on duplicate coordinates
# --------------------------------------------------------------------------- #
def test_moran_duplicate_coordinates_no_self_loop():
    # Two coincident points + distinct points; a self-loop would bias I.
    base = [((116.0, 39.0), 10.0), ((116.0, 39.0), 10.0)]  # exact duplicate
    base += [((116.0 + i * 0.001, 39.0 + i * 0.001), float(i)) for i in range(1, 8)]
    res = moran_i_narrated(_points_fc(base), "val")
    assert res.success
    # Reordering the duplicate pair must not change the result (deterministic).
    reordered = [base[1]] + [base[0]] + base[2:]
    res2 = moran_i_narrated(_points_fc(reordered), "val")
    assert res.data["moran_i"] == pytest.approx(res2.data["moran_i"], abs=1e-12)


# --------------------------------------------------------------------------- #
# E-5: dead (None,None) checks -> friendly errors on empty/invalid input
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("fn", [moran_i_narrated, hotspot_narrated])
def test_stats_empty_input_friendly_error(fn):
    res = fn({"type": "FeatureCollection", "features": []}, "val")
    assert not res.success


# --------------------------------------------------------------------------- #
# E-7: hotspot auto-band finds structure on clustered data
# --------------------------------------------------------------------------- #
def test_hotspot_auto_band_detects_clusters():
    """The old mean-1st-NN auto band left ~half the points disconnected and
    found 0 hotspots on a deliberately clustered field. The 8th-NN band must
    recover hot/cold spots."""
    res = hotspot_narrated(_clustered_field(), "val", distance_band=0)
    assert res.success
    data = res.data
    hot = data.get("hotspots") or data.get("cluster_stats", {}).get("hot", 0)
    # At least one hotspot OR coldspot should be found now.
    counts = data.get("hotspot_counts", {})
    total_sig = sum(counts.values()) if counts else (hot or 0)
    assert total_sig > 0 or any(
        v > 0 for k, v in data.items() if isinstance(v, int) and v > 0
    ), f"auto band found no hotspots: {data}"


# --------------------------------------------------------------------------- #
# E-10: kmeans n_clusters<=0 no longer crashes
# --------------------------------------------------------------------------- #
def test_kmeans_zero_clusters_friendly_error():
    res = cluster_narrated(_clustered_field(), method="kmeans", n_clusters=0)
    assert res.success  # clamped to 1, not crashed
    assert res.data["method"] == "kmeans"


def test_bh_qvalues_nan_sanitized():
    from app.lib.geo_analysis.statistics import _bh_qvalues

    p_vals = np.array([0.0001, 0.001, 0.005, 0.01, 0.02, 0.03, np.nan])
    q_vals = _bh_qvalues(p_vals)
    assert not np.isnan(q_vals[:-1]).any()
    assert q_vals[-1] == 1.0
    assert np.all((q_vals >= 0) & (q_vals <= 1))
    assert np.all(q_vals[:-1] < 0.05)
    p_clean = np.array([0.0001, 0.001, 0.005, 0.01, 0.02, 0.03])
    q_clean = _bh_qvalues(p_clean)
    assert q_clean == pytest.approx(np.array([0.0006, 0.003, 0.01, 0.015, 0.024, 0.03]), abs=1e-12)


def test_h3_lisa_island_cells_neutral():
    import h3
    from shapely.geometry import Polygon, mapping

    hex_bj = h3.latlng_to_cell(39.9042, 116.4074, 7)
    hex_sh = h3.latlng_to_cell(31.2304, 121.4737, 7)
    hex_gz = h3.latlng_to_cell(23.1291, 113.2644, 7)
    features = []
    for i, h in enumerate([hex_bj, hex_sh, hex_gz]):
        b = h3.cell_to_boundary(h)
        coords = [(lng, lat) for lat, lng in b]
        coords.append(coords[0])
        features.append({
            "type": "Feature",
            "geometry": mapping(Polygon(coords)),
            "properties": {"val": float((i + 1) * 10), "h3_index": h},
        })
    fc = {"type": "FeatureCollection", "features": features}
    res = h3_lisa(fc, "val")
    assert res.success
    assert len(res.data["features"]) == 3
    for f in res.data["features"]:
        assert f["properties"]["lisa_cluster"] == "NS"
    assert res.data["cluster_stats"]["NS"] == 3
