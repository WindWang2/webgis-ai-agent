"""
Performance Benchmarks & Memory Boundary Harness for Data Fabric V1
"""
import time
import pytest
from app.schemas.data_fabric_schema import ConnectionProfile, QuerySpec, DatasetDescriptor
from app.services.data_fabric.spatial_catalog import SpatialCatalogService
from app.services.data_fabric.adapters.postgis_adapter import PostGISAdapter


def test_10k_catalog_items_search_benchmark():
    """Benchmark searching over 10,000 indexed catalog items."""
    service = SpatialCatalogService()
    service.clear()

    for i in range(10000):
        service.register_dataset(
            DatasetDescriptor(
                id=f"item_{i}",
                source_id="src_1",
                source_type="postgis",
                name=f"spatial_layer_{i}",
                title=f"Spatial Layer Title {i}",
                geometry_type="Polygon" if i % 2 == 0 else "Point",
                feature_type="vector",
                crs="EPSG:4326",
                bbox=[100.0, 20.0, 105.0, 25.0],
            ),
            tags=["gis", "china", f"tag_{i % 10}"],
        )

    start_t = time.time()
    res = service.search(query="spatial_layer_500", limit=10)
    elapsed = time.time() - start_t

    assert res["total"] >= 1
    assert elapsed < 0.1, f"10k search took {elapsed:.4f}s; expected < 0.1s"


def test_pushdown_bounded_payload():
    """Verify that pushdown query memory footprint remains bounded."""
    profile = ConnectionProfile(
        source_type="postgis",
        endpoint_url="postgresql://user:pass@localhost:5432/gisdb",
        name="test_postgis",
    )
    adapter = PostGISAdapter(profile)
    q_spec = QuerySpec(limit=50, bbox=[100.0, 20.0, 110.0, 30.0])

    res = adapter.query("public.large_table", q_spec)
    assert res.is_pushed_down is True
    assert len(res.features) <= 50
