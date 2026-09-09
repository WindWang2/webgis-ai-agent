"""
Performance Benchmarks & Memory Boundary Harness for Data Fabric V1
"""
from app.schemas.data_fabric_schema import QuerySpec, DatasetDescriptor
from app.services.data_fabric.spatial_catalog import SpatialCatalogService
from tests.fixtures.perf_budget import assert_within_budget


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

    # W11 迁移（原固定断言 ``elapsed < 0.1``，且用 time.time() 计时）：
    # 语义不变——10k 索引目录的单次检索必须有界（防退化为全量扫描）——
    # 单次采样 → median-of-5，time.time() → helper 内 perf_counter。
    holder: dict = {}

    def _search():
        holder["res"] = service.search(query="spatial_layer_500", limit=10)

    assert_within_budget(
        "fabric.catalog_search_10k", _search, iterations=5, floor_s=0.1)

    res = holder["res"]
    assert res["total"] >= 1


def test_pushdown_bounded_payload():
    """Pushdown 查询内存占用有界（V2 fake-connection 形式，不依赖本地 DB）。"""
    from tests.unit.test_data_fabric_postgis_v2 import _adapter

    executed: list = []
    rows = [(i, f"n{i}", '{"type":"Point","coordinates":[104,30]}') for i in range(200)]
    adapter = _adapter(executed, rows=rows)
    res = adapter.query(
        "public.large_table",
        QuerySpec(limit=50, bbox=[100.0, 20.0, 110.0, 30.0]),
    )
    assert len(res.features) <= 50
    assert res.metadata.get("pushdown_bbox") is True
    main = [sql for sql, _ in executed
            if 'FROM "public"."large_table"' in sql and "COUNT" not in sql]
    assert main and "ST_Intersects" in main[0]
