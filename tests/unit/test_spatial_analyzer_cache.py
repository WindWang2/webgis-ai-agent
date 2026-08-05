"""
Unit tests for SpatialAnalyzer ST-DBSCAN pairwise distance matrix LRU cache seam.
"""
from app.services.spatial_analyzer import SpatialAnalyzer
from tests.unit.test_st_dbscan import create_sample_st_geojson


def test_spatial_analyzer_lru_cache_hit_and_miss():
    """Verify that repeated st_dbscan calls hit LRU cache and different parameters miss."""
    SpatialAnalyzer.clear_st_dbscan_cache()
    geojson = create_sample_st_geojson()

    # Call 1: Cache miss (first time)
    res1 = SpatialAnalyzer.st_dbscan(
        geojson,
        eps1_spatial_meters=1000.0,
        eps2_temporal_seconds=3600.0,
        min_samples=5,
    )
    assert res1.success is True
    info1 = SpatialAnalyzer.get_st_dbscan_cache_info()
    assert info1["hits"] == 0
    assert info1["misses"] == 1
    assert info1["size"] == 1

    # Call 2: Identical inputs -> Cache HIT
    res2 = SpatialAnalyzer.st_dbscan(
        geojson,
        eps1_spatial_meters=1000.0,
        eps2_temporal_seconds=3600.0,
        min_samples=5,
    )
    assert res2.success is True
    info2 = SpatialAnalyzer.get_st_dbscan_cache_info()
    assert info2["hits"] == 1
    assert info2["misses"] == 1
    assert info2["size"] == 1

    # Call 3: Different min_samples (same eps1, eps2) -> Cache HIT (distance matrix is identical!)
    res3 = SpatialAnalyzer.st_dbscan(
        geojson,
        eps1_spatial_meters=1000.0,
        eps2_temporal_seconds=3600.0,
        min_samples=3,
    )
    assert res3.success is True
    info3 = SpatialAnalyzer.get_st_dbscan_cache_info()
    assert info3["hits"] == 2
    assert info3["misses"] == 1

    # Call 4: Different eps1 parameter -> Cache MISS
    res4 = SpatialAnalyzer.st_dbscan(
        geojson,
        eps1_spatial_meters=500.0,
        eps2_temporal_seconds=3600.0,
        min_samples=5,
    )
    assert res4.success is True
    info4 = SpatialAnalyzer.get_st_dbscan_cache_info()
    assert info4["hits"] == 2
    assert info4["misses"] == 2
    assert info4["size"] == 2


def test_spatial_analyzer_cache_clear():
    """Verify clearing the LRU distance matrix cache."""
    geojson = create_sample_st_geojson()
    SpatialAnalyzer.st_dbscan(geojson, eps1_spatial_meters=1000.0)

    assert SpatialAnalyzer.get_st_dbscan_cache_info()["size"] > 0
    SpatialAnalyzer.clear_st_dbscan_cache()

    info = SpatialAnalyzer.get_st_dbscan_cache_info()
    assert info["size"] == 0
    assert info["hits"] == 0
    assert info["misses"] == 0
