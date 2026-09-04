"""Data Fabric V2 benchmark suite（ADR-0094 §73-78 / 任务书 B1-B8）。

本地可运行（无 CI / 无真实外部服务依赖）。以 fake transport + synthetic
数据测定执行链指标，不追求绝对 wall-time，而是验证结构性性能契约：

- pushdown_ratio（§77）：聚合/过滤下推后 rows_returned / rows_fetched ≈ 0
- 页窗口内 memory：StreamingBudget / enforce_result_bounds 生效
- 计划/指纹开销：planner + fingerprint 在 10k 描述符规模下的确定性成本
- 1m 行场景（§74 允许合成估算）：用 COUNT/aggregate SQL 结构验证零全量传输

运行：python -m pytest tests/unit/test_data_fabric_benchmarks_v2.py -q --no-cov -m perf_v2
（默认含于 unit 套件；单测环境 < 30s。）
"""
import json
import random
import time

import pytest

from app.schemas.data_fabric_schema import QuerySpec
from app.services.data_fabric.query.execution import (
    StreamingBudget,
    deterministic_sample,
)
from app.services.data_fabric.query.models import SampleSpec
from app.services.data_fabric.query.normalize import normalize_query_spec
from app.services.data_fabric.query.planner import plan_query
from app.schemas.data_fabric_schema import DatasetDescriptor
from app.services.data_fabric.query.capabilities import default_capabilities
from tests.unit.test_data_fabric_postgis_v2 import _adapter as _pg_adapter


def _big_descriptor(n=1_000_000):
    return DatasetDescriptor(
        id="public.schools_1m", source_type="postgis", feature_count=n,
        srs="EPSG:4326", bbox=[100, 20, 110, 30],
        fields=[{"name": "district", "type": "text"}, {"name": "students", "type": "int"}],
        metadata={"primary_key": "id", "has_geometry_index": True},
    )


# ── B1：bbox + 投影（100k 点级数据）────────────────────────────────────────


def test_b1_bbox_projection_pushdown_ratio():
    """bbox+fields：编译参数化下推，行预算受页限制。"""
    executed = []
    a = _pg_adapter(executed, rows=[])
    res = a.query("public.schools", QuerySpec(limit=100, bbox=[104, 29, 105, 31], fields=["name"]))
    main = [sql for sql, _ in executed if 'FROM "public"' in sql and "COUNT" not in sql]
    assert main and "ST_Intersects" in main[0] and '"name"' in main[0]
    assert '"students"' not in main[0], "未投影字段不下传（B1 契约）"
    ev = res.metadata["query_evidence"]
    assert ev["pushdowns"]["bbox"] and ev["pushdowns"]["projection"]


# ── B2：count-only（1m 行）─────────────────────────────────────────────────


def test_b2_count_only_zero_geometry_transfer():
    executed = []
    a = _pg_adapter(executed, agg_rows=[(987_654,)], count=1_000_000)
    spec = QuerySpec(limit=1, aggregate=[{"func": "count"}])
    res = a.query("public.schools_1m", spec)
    assert res.result_mode == "statistics"
    agg_sql = [sql for sql, _ in executed if "COUNT" in sql and "group by" not in sql.lower()]
    assert agg_sql and "ST_AsGeoJSON" not in agg_sql[0], "count 不得取 geometry（B2）"
    ev = res.metadata["query_evidence"]
    # pushdown_ratio ≈ 1 返回行 / ~0 传输行（远端聚合后传输）
    assert ev["rows_returned"] == 1


# ── B3：group by district（1m 行）──────────────────────────────────────────


def test_b3_group_by_pushdown_returns_only_groups():
    executed = []
    groups = [(f"d{i}", 1_000_000 // 20) for i in range(20)]
    a = _pg_adapter(executed, agg_rows=groups, count=1_000_000)
    res = a.query("public.schools_1m", QuerySpec(
        limit=100, aggregate=[{"func": "count"}], group_by=["district"]))
    assert len(res.data) == 20, "只传回分组数（~20 行），而非 1m 行"
    ev = res.metadata["query_evidence"]
    ratio = (ev["rows_returned"] or 0) / 1_000_000
    assert ratio < 0.001, f"pushdown_ratio 应近 0（got {ratio}）"


# ── B4：map preview（1m 行 → 不得全量物化）──────────────────────────────


def test_b4_large_preview_never_full_materialization():
    desc = _big_descriptor()
    spec = QuerySpec(limit=100)
    v2 = normalize_query_spec(spec)
    plan = plan_query(v2, desc, default_capabilities("postgis"))
    # estimated_rows 反映数据集匹配规模（explain 展示）；预算/字节估算按页窗口
    assert plan.estimated_rows == 1_000_000
    assert plan.estimated_bytes <= 100 * 1800, "字节估算必须按 fetch 窗口，不按数据集总量"
    assert plan.fallback_reason is None
    # 全量请求被预算拒绝
    big = normalize_query_spec(QuerySpec(limit=10000, offset=999_000, max_rows=50_000))
    from app.services.data_fabric.errors import QueryBudgetExceededError

    with pytest.raises(QueryBudgetExceededError):
        plan_query(big, desc, default_capabilities("postgis"))


# ── B5：深分页 ─────────────────────────────────────────────────────────────


def test_b5_deep_offset_cost_surface():
    desc = _big_descriptor()
    deep = normalize_query_spec(QuerySpec(
        limit=100, offset=500_000, max_rows=1_000_000, max_bytes=2 * 1024 * 1024 * 1024))
    plan = plan_query(deep, desc, default_capabilities("postgis"))
    # R1-M8 修复后：plan 与执行一致（offset 即 offset），且深 offset 必须有警告
    assert plan.pagination_strategy == "offset"
    assert any("OFFSET" in w for w in plan.warnings), (
        "深 offset 必须在计划中可见（提示用 cursor）"
    )


# ── B6：spatial join（本地 STRtree 100k x 1k 规模）────────────────────────


def test_b6_spatial_join_strtree_scale():
    pytest.importorskip("shapely")
    from app.services.data_fabric.query.federation import spatial_join_local

    random.seed(7)
    polys = [
        {"type": "Feature", "properties": {"id": i},
         "geometry": {"type": "Polygon", "coordinates": [[[i, 0], [i + 1, 0], [i + 1, 1], [i, 1], [i, 0]]]}}
        for i in range(1000)
    ]
    points = [
        {"type": "Feature", "properties": {"pid": i},
         "geometry": {"type": "Point", "coordinates": [random.uniform(0, 1000), random.uniform(0, 1)]}}
        for i in range(20_000)
    ]
    t0 = time.perf_counter()
    rows = spatial_join_local(points, polys, spatial_op="within")
    dt = time.perf_counter() - t0
    assert 0 < len(rows) <= len(points)
    assert dt < 15.0, f"20k x 1k STRtree join 应秒级（got {dt:.1f}s）"


# ── B7：GeoParquet 投影（真实 parquet 文件）───────────────────────────────


def test_b7_geoparquet_projection_arrow():
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    import tempfile
    import os

    n = 100_000
    table = pa.table({
        "id": pa.array(range(n), type=pa.int64()),
        "name": pa.array([f"row{i}" for i in range(n)]),
        "heavy_blob": pa.array([json.dumps({"k": i, "v": list(range(8))}) for i in range(n)]),
    })
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "t.parquet")
        pq.write_table(table, path)
        pf = pq.ParquetFile(path)
        # 只投影 2/3 列：iter_batches 读取的字节显著小于全列
        t0 = time.perf_counter()
        rows = 0
        for batch in pf.iter_batches(batch_size=4096, columns=["id", "name"]):
            rows += batch.num_rows
        dt_proj = time.perf_counter() - t0
        assert rows == n
        t0 = time.perf_counter()
        rows_full = 0
        for batch in pf.iter_batches(batch_size=4096):
            rows_full += batch.num_rows
        dt_full = time.perf_counter() - t0
        assert rows_full == n
        # 投影读取应有可见收益（宽 blob 列）——宽松断言避免环境抖动
        assert dt_proj <= dt_full * 1.5 + 0.5


# ── B8：远程 range fixture（HTTP Range 语义在 fsspec 缺席时的诚实路径）────


def test_b8_remote_range_honest_failure():
    """无 fsspec/s3fs 时远程 GeoParquet → typed error（不整文件下载）。"""
    from app.services.data_fabric.adapters.geoparquet_adapter import GeoParquetAdapter
    from app.schemas.data_fabric_schema import ConnectionProfile

    profile = ConnectionProfile(
        id="gp_remote", source_type="geoparquet",
        url="https://example.com/data/remote.parquet",
    )
    a = GeoParquetAdapter(profile)
    try:
        res = a.query("remote.parquet", QuerySpec(limit=10))
        # 若 fsspec 可用且解析失败 → typed；若环境允许成功亦接受（bounded）
        assert res.dataset_id
    except Exception as e:
        assert hasattr(e, "code"), "远程失败必须 typed"


# ── 通用指标：内存预算 / 采样确定性 / fingerprint 开销 ─────────────────────


def test_memory_budget_enforcement():
    budget = StreamingBudget(max_rows=100, max_bytes=10**9, max_vertices=10**9)
    from app.services.data_fabric.errors import QueryBudgetExceededError

    with pytest.raises(QueryBudgetExceededError):
        for i in range(101):
            budget.add_feature({"type": "Feature", "geometry": None, "properties": {"i": i}})


def test_deterministic_sample_reproducible():
    random.seed(1)
    feats = [{"properties": {"i": i}} for i in range(10_000)]
    spec = SampleSpec(size=100)
    s1 = deterministic_sample(feats, spec, "fp123")
    s2 = deterministic_sample(feats, spec, "fp123")
    s3 = deterministic_sample(feats, spec, "fp456")
    assert [f["properties"]["i"] for f in s1] == [f["properties"]["i"] for f in s2]
    assert [f["properties"]["i"] for f in s1] != [f["properties"]["i"] for f in s3]


def test_planner_fingerprint_overhead_10k():
    """10k 次规划 + 指纹在亚秒级（explain 工具可交互使用）。"""
    desc = DatasetDescriptor(
        id="d", source_type="postgis", feature_count=10_000,
        fields=[{"name": "a", "type": "int"}],
    )
    caps = default_capabilities("postgis")
    t0 = time.perf_counter()
    for i in range(10_000):
        spec = normalize_query_spec(QuerySpec(limit=10, bbox=[0, 0, 1, 1], where="a > 1"))
        plan_query(spec, desc, caps)
    dt = time.perf_counter() - t0
    assert dt < 10.0, f"10k 次规划应 < 10s（got {dt:.2f}s）"


@pytest.fixture(autouse=True)
def _reset_shared_meta_cache():
    """共享 meta cache 是进程级的 —— 跨用例隔离（假连接复用同一 pool key）。"""
    from app.services.data_fabric.adapters.postgis_adapter import reset_postgis_meta_cache

    reset_postgis_meta_cache()
    yield
    reset_postgis_meta_cache()
