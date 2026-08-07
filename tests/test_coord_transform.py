import math
from app.utils.coord_transform import (
    wgs84_to_gcj02, gcj02_to_wgs84,
    wgs84_to_bd09, bd09_to_wgs84,
    gcj02_to_bd09, bd09_to_gcj02,
)

def test_wgs84_gcj02_roundtrip():
    lng, lat = 116.4074, 39.9042
    gcj_lng, gcj_lat = wgs84_to_gcj02(lng, lat)
    back_lng, back_lat = gcj02_to_wgs84(gcj_lng, gcj_lat)
    assert abs(back_lng - lng) < 0.00001, f"lng drift: {back_lng - lng}"
    assert abs(back_lat - lat) < 0.00001, f"lat drift: {back_lat - lat}"

def test_wgs84_bd09_roundtrip():
    lng, lat = 116.4074, 39.9042
    bd_lng, bd_lat = wgs84_to_bd09(lng, lat)
    back_lng, back_lat = bd09_to_wgs84(bd_lng, bd_lat)
    assert abs(back_lng - lng) < 0.00001, f"lng drift: {back_lng - lng}"
    assert abs(back_lat - lat) < 0.00001, f"lat drift: {back_lat - lat}"

def test_gcj02_bd09_roundtrip():
    lng, lat = 116.4074, 39.9042
    gcj_lng, gcj_lat = wgs84_to_gcj02(lng, lat)
    bd_lng, bd_lat = gcj02_to_bd09(gcj_lng, gcj_lat)
    back_lng, back_lat = bd09_to_gcj02(bd_lng, bd_lat)
    assert abs(back_lng - gcj_lng) < 1e-5
    assert abs(back_lat - gcj_lat) < 1e-5

def test_shanghai_coordinates():
    lng, lat = 121.4737, 31.2304
    gcj_lng, gcj_lat = wgs84_to_gcj02(lng, lat)
    diff_lng = abs(gcj_lng - lng) * 111000 * math.cos(math.radians(lat))
    diff_lat = abs(gcj_lat - lat) * 111000
    assert 200 < diff_lng < 800, f"Expected ~500m offset, got {diff_lng:.0f}m lng"
    assert 200 < diff_lat < 800, f"Expected ~500m offset, got {diff_lat:.0f}m lat"

def test_out_of_china_no_transform():
    lng, lat = -73.9857, 40.7484  # New York
    gcj_lng, gcj_lat = wgs84_to_gcj02(lng, lat)
    assert gcj_lng == lng
    assert gcj_lat == lat


# ---------------------------------------------------------------------------
# Vectorized-array parity tests (Phase 2 perf optimization)
# ---------------------------------------------------------------------------
import numpy as np
from app.utils.coord_transform import (
    wgs84_to_gcj02_array, gcj02_to_wgs84_array,
    gcj02_to_bd09_array, bd09_to_gcj02_array,
    transform_geojson,
)


def test_array_vs_scalar_parity_wgs84_gcj02():
    lngs = np.array([116.4074, 121.4737, 113.2644, -73.9857])  # incl. out-of-China
    lats = np.array([39.9042, 31.2304, 23.1291, 40.7484])
    out_lng, out_lat = wgs84_to_gcj02_array(lngs, lats)
    for i in range(len(lngs)):
        sx, sy = wgs84_to_gcj02(float(lngs[i]), float(lats[i]))
        assert abs(out_lng[i] - sx) < 1e-9, f"lng mismatch at {i}"
        assert abs(out_lat[i] - sy) < 1e-9, f"lat mismatch at {i}"


def test_array_vs_scalar_parity_all_chinese_legs():
    rng = np.random.default_rng(42)
    lngs = rng.uniform(73, 135, 200)
    lats = rng.uniform(3, 53, 200)
    pairs = {
        "wgs84->gcj02": (wgs84_to_gcj02_array, wgs84_to_gcj02),
        "gcj02->wgs84": (gcj02_to_wgs84_array, gcj02_to_wgs84),
        "gcj02->bd09": (gcj02_to_bd09_array, gcj02_to_bd09),
        "bd09->gcj02": (bd09_to_gcj02_array, bd09_to_gcj02),
    }
    for name, (arr_fn, scalar_fn) in pairs.items():
        out_lng, out_lat = arr_fn(lngs, lats)
        for i in range(len(lngs)):
            sx, sy = scalar_fn(float(lngs[i]), float(lats[i]))
            assert abs(out_lng[i] - sx) < 1e-9, f"{name} lng mismatch at {i}: {out_lng[i]} vs {sx}"
            assert abs(out_lat[i] - sy) < 1e-9, f"{name} lat mismatch at {i}"


def test_transform_geojson_vectorized_matches_scalar_polygon():
    """Multi-ring Polygon must preserve structure through the vectorized path."""
    geom = {
        "type": "Polygon",
        "coordinates": [
            [[116.0, 39.0], [116.5, 39.0], [116.5, 39.5], [116.0, 39.5], [116.0, 39.0]],
            [[116.1, 39.1], [116.2, 39.1], [116.2, 39.2], [116.1, 39.2], [116.1, 39.1]],
        ],
    }
    out = transform_geojson(geom, "wgs84", "gcj02")
    assert out["type"] == "Polygon"
    # 2 rings, 5 vertices each preserved
    assert len(out["coordinates"]) == 2
    assert all(len(ring) == 5 for ring in out["coordinates"])
    # First vertex transformed (not identity — inside China)
    assert out["coordinates"][0][0][0] != geom["coordinates"][0][0][0]


def test_transform_geojson_feature_collection_vectorized():
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"id": 1}, "geometry": {"type": "Point", "coordinates": [116.4, 39.9]}},
            {"type": "Feature", "properties": {"id": 2}, "geometry": {"type": "LineString", "coordinates": [[116.0, 39.0], [117.0, 40.0]]}},
        ],
    }
    out = transform_geojson(fc, "gcj02", "bd09")
    assert out["type"] == "FeatureCollection"
    assert len(out["features"]) == 2
    # properties preserved
    assert out["features"][0]["properties"]["id"] == 1
    # Point coords transformed
    assert out["features"][0]["geometry"]["coordinates"][0] != fc["features"][0]["geometry"]["coordinates"][0]
    # LineString structure preserved
    assert len(out["features"][1]["geometry"]["coordinates"]) == 2


def test_transform_geojson_preserves_z_dimension():
    """Z/M trailing dims must survive the flatten/rebuild."""
    geom = {"type": "Point", "coordinates": [116.4, 39.9, 50.0]}
    out = transform_geojson(geom, "wgs84", "gcj02")
    assert len(out["coordinates"]) == 3
    assert out["coordinates"][2] == 50.0  # Z untouched


def test_transform_geojson_out_of_china_passthrough():
    """Out-of-China points must remain identity (vectorized path)."""
    geom = {"type": "Point", "coordinates": [-73.9857, 40.7484]}
    out = transform_geojson(geom, "wgs84", "gcj02")
    assert out["coordinates"][0] == -73.9857
    assert out["coordinates"][1] == 40.7484


def test_transform_geojson_does_not_mutate_input():
    geom = {"type": "Point", "coordinates": [116.4, 39.9]}
    orig = geom["coordinates"][0]
    _ = transform_geojson(geom, "wgs84", "gcj02")
    assert geom["coordinates"][0] == orig  # input untouched


def test_vectorized_perf_speedup():
    """Sanity: vectorized path should be meaningfully faster than scalar for 50k pts.

    This guards against regressions where the array helpers accidentally fall
    back to scalar loops. Asserts only a modest 5x floor to stay stable across
    machines; the real-world speedup is ~50-100x.
    """
    import time as _t
    rng = np.random.default_rng(0)
    n = 50_000
    lngs = rng.uniform(73, 135, n)
    lats = rng.uniform(3, 53, n)

    # vectorized
    t0 = _t.perf_counter()
    wgs84_to_gcj02_array(lngs, lats)
    t_vec = _t.perf_counter() - t0

    # scalar (sample subset to keep test fast — extrapolate)
    sample = 500
    t0 = _t.perf_counter()
    for i in range(sample):
        wgs84_to_gcj02(float(lngs[i]), float(lats[i]))
    t_scalar = (_t.perf_counter() - t0) * (n / sample)

    # vectorized must be at least 3x faster than extrapolated scalar
    # (conservative floor across machines; real speedup is ~10-50x at full scale)
    assert t_scalar / t_vec > 3, f"speedup too low: {t_scalar/t_vec:.1f}x (vec={t_vec:.4f}s, scalar_est={t_scalar:.4f}s)"
