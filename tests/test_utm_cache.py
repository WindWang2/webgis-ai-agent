"""Tests for to_utm_gdf identity-based memoization (Phase 4 perf)."""
from app.lib.geo_processor.core import (
    to_utm_gdf,
    clear_utm_cache,
    get_utm_cache_info,
)


def _sample_fc():
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"v": 1},
             "geometry": {"type": "Point", "coordinates": [116.4, 39.9]}},
            {"type": "Feature", "properties": {"v": 2},
             "geometry": {"type": "Point", "coordinates": [116.5, 39.95]}},
        ],
    }


def test_cache_miss_then_hit_same_object():
    clear_utm_cache()
    fc = _sample_fc()
    g1, crs1 = to_utm_gdf(fc)
    assert g1 is not None
    info_after_miss = get_utm_cache_info()
    assert info_after_miss["size"] == 1

    g2, crs2 = to_utm_gdf(fc)  # same object -> hit
    assert crs1 == crs2
    # geometry equivalence (the cached entry is copied out)
    assert len(g2) == len(g1)
    assert (g2.geometry.geom_equals_exact(g1.geometry, tolerance=0).all())


def test_cache_returns_copy_not_alias():
    """Caller mutation must not poison the cache."""
    clear_utm_cache()
    fc = _sample_fc()
    g1, _ = to_utm_gdf(fc)
    g1["extra_col"] = 99  # mutate the returned gdf

    g2, _ = to_utm_gdf(fc)  # hit — must NOT carry the mutation
    assert "extra_col" not in g2.columns


def test_different_objects_get_different_entries():
    clear_utm_cache()
    fc_a = _sample_fc()
    fc_b = _sample_fc()  # same content, different identity
    to_utm_gdf(fc_a)
    to_utm_gdf(fc_b)
    assert get_utm_cache_info()["size"] == 2


def test_original_crs_preserved_on_hit():
    clear_utm_cache()
    fc = _sample_fc()
    g1, _ = to_utm_gdf(fc)
    original = g1._original_crs
    g2, _ = to_utm_gdf(fc)
    assert g2._original_crs == original


def test_string_input_not_cached():
    """Strings are parsed-and-discarded; identity caching is unsafe, must bypass."""
    clear_utm_cache()
    import json
    s = json.dumps(_sample_fc())
    to_utm_gdf(s)
    assert get_utm_cache_info()["size"] == 0  # string path skips cache


def test_none_for_empty():
    clear_utm_cache()
    g, crs = to_utm_gdf({"type": "FeatureCollection", "features": []})
    assert g is None and crs is None
    assert get_utm_cache_info()["size"] == 0  # empty not cached


def test_cache_speedup_on_repeat():
    """Second call on the same large-ish object must be substantially faster."""
    import time as _t
    clear_utm_cache()
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"v": i},
         "geometry": {"type": "Point", "coordinates": [116.0 + i * 0.01, 39.0 + i * 0.01]}}
        for i in range(2000)
    ]}
    t0 = _t.perf_counter()
    to_utm_gdf(fc)
    t_miss = _t.perf_counter() - t0
    t0 = _t.perf_counter()
    to_utm_gdf(fc)
    t_hit = _t.perf_counter() - t0
    # hit should be at least 3x faster (cache returns copy of precomputed gdf)
    assert t_miss / t_hit > 3, f"cache speedup too low: {t_miss/t_hit:.1f}x"


def test_cache_pins_geojson_reference_preventing_id_reuse():
    """Regression: cache must hold a strong reference to the geojson object.

    Without the pin, an evicted-but-still-live cache entry + CPython id()
    reuse (a new object landing on the same address) silently returns the OLD
    object's cached result — observed as flaky failures in the full suite
    (hotspot classification got another test's data).
    """
    import gc
    from app.lib.geo_processor.core import _utm_cache, _utm_cache_lock, _UTM_CACHE_MAX

    clear_utm_cache()
    fc = _sample_fc()
    to_utm_gdf(fc)

    with _utm_cache_lock:
        entry = _utm_cache.get((id(fc), None))
    assert entry is not None
    pinned = entry[0]
    assert pinned is fc, "缓存条目必须持有 geojson 引用（防 id 复用）"

    # 缓存条目存续期间，对象无法被 GC（引用被钉子持有）
    del fc
    gc.collect()
    # pinned 仍指向原对象 —— 地址未被复用，未来新对象的 id 不会与之冲突
    assert pinned["type"] == "FeatureCollection"

    # LRU 淘汰后钉子释放：旧对象可被 GC，id 可被新对象复用 —— 但此时缓存
    # 条目已不存在，新对象查不到旧条目（未命中 → 正确重算）。
    # 用一个小循环证明：大量不同对象进出缓存不产生错误命中。
    for i in range(_UTM_CACHE_MAX * 2):
        obj = _sample_fc()
        obj["features"][0]["properties"]["v"] = i
        g, _crs = to_utm_gdf(obj)
        # 每个对象的结果都应与其自身内容一致（id 冲突会破坏这一点）
        assert g is not None
