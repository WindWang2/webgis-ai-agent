"""Federated Arrow lane 测试（ADR-0119 W6）。

- GeoParquet ``iter_query_arrow_batches``：真实小型 parquet（合成、语义
  可证明）逐批产出；pyarrow 缺失通道 typed 拒绝；
- ``iter_scan_pages_arrow``：行形状与 dict lane 同形、where/bbox 过滤、
  分页语义；
- ``physical.iter_scan_pages`` 委托与回落（不支持通道的 adapter 原路径）。
"""


import pytest

pa = pytest.importorskip("pyarrow")

from app.services.data_fabric.adapters.geoparquet_adapter import (  # noqa: E402
    GeoParquetAdapter,
)
from app.services.data_fabric.fabric.arrow_lane import (  # noqa: E402
    adapter_supports_arrow_lane,
    iter_scan_pages_arrow,
)
from app.services.data_fabric.query.federated.physical import iter_scan_pages  # noqa: E402
from app.services.data_fabric.query.predicates import predicate_from_dict  # noqa: E402


class _Budget:
    deadline_s = 30.0
    max_rows = 10_000
    max_bytes = 10**9
    max_vertices = 10**9


class _Token:
    def check(self):
        return None


@pytest.fixture()
def parquet_source(tmp_path):
    """合成 GeoParquet：4 个 Point，属性 zone ∈ {A,B}；语义可证明。"""
    import pyarrow.parquet as pq

    from app.services.data_fabric import vector_carrier

    feats = [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [100.0 + i, 30.0]},
         "properties": {"zone": "A" if i % 2 == 0 else "B", "idx": i}}
        for i in range(4)
    ]
    table = vector_carrier.features_to_arrow(feats, crs="EPSG:4326")
    path = tmp_path / "points.parquet"
    pq.write_table(table, str(path))
    return str(path)


def _adapter(path):
    from app.schemas.data_fabric_schema import ConnectionProfile

    profile = ConnectionProfile(
        id="ds_arrow", name="arrow", source_type="geoparquet", url=path,
    )
    adapter = GeoParquetAdapter(profile)
    adapter.endpoint = path
    return adapter


def _flat_rows(path):
    """parquet 的行组内为 WKB 几何列 —— 测试用 describe 校验可读。"""
    import pyarrow.parquet as pq

    return pq.read_table(path)


def test_arrow_lane_supported_only_when_declared(parquet_source):
    adapter = _adapter(parquet_source)
    assert adapter_supports_arrow_lane(adapter)

    class _Plain:
        def query(self, *a, **k):
            raise AssertionError("dict lane only")

    assert not adapter_supports_arrow_lane(_Plain())


def test_arrow_batches_decode_to_same_feature_shape(parquet_source):
    adapter = _adapter(parquet_source)
    pages = list(iter_scan_pages_arrow(
        adapter, "ds_arrow", where=None, fields=None, bbox=None,
        fetch_limit=100, budget=_Budget(), token=_Token(), page_size=2,
    ))
    rows = [r for page in pages for r in page]
    assert len(rows) == 4
    for r in rows:
        assert r["type"] == "Feature"
        assert r["geometry"]["type"] == "Point"
        assert set(r["properties"].keys()) >= {"zone", "idx"}


def test_arrow_lane_where_and_pagination(parquet_source):
    adapter = _adapter(parquet_source)
    where = predicate_from_dict({"op": "eq", "field": "zone", "value": "A"})
    pages = list(iter_scan_pages_arrow(
        adapter, "ds_arrow", where=where, fields=None, bbox=None,
        fetch_limit=100, budget=_Budget(), token=_Token(), page_size=1,
    ))
    rows = [r for page in pages for r in page]
    assert [r["properties"]["idx"] for r in rows] == [0, 2]
    assert all(len(p) == 1 for p in pages)  # page_size=1 页语义


def test_arrow_lane_budget_guard_via_iter_scan_pages(parquet_source):
    adapter = _adapter(parquet_source)
    from app.services.data_fabric.errors import QueryBudgetExceededError

    budget = _Budget()
    budget.max_rows = 2
    with pytest.raises(QueryBudgetExceededError):
        list(iter_scan_pages(
            adapter, "ds_arrow", where=None, fields=None, bbox=None,
            fetch_limit=100, budget=budget, token=_Token(),
        ))


def test_arrow_lane_falls_back_to_dict_lane(parquet_source):
    """无 arrow 通道的 adapter：iter_scan_pages 原样走 dict lane。"""
    calls = []

    class _DictOnly:
        def query(self, dataset_id, spec):
            calls.append(spec)
            from app.schemas.data_fabric_schema import QueryResult

            return QueryResult(
                dataset_id=dataset_id,
                features=[{"type": "Feature", "geometry": None, "properties": {"i": 1}}],
            )

    pages = list(iter_scan_pages(
        _DictOnly(), "x", where=None, fields=None, bbox=None,
        fetch_limit=10, budget=_Budget(), token=_Token(),
    ))
    assert len(pages) == 1 and calls  # dict lane 路径未变


def test_arrow_lane_bbox_row_level_filter(parquet_source):
    """R1-C1 回归：bbox 行级精确过滤（行组剪枝只是粗粒度优化）。"""
    adapter = _adapter(parquet_source)
    pages = list(iter_scan_pages_arrow(
        adapter, "ds_arrow", where=None, fields=None,
        bbox=[100.0, 29.0, 101.5, 31.0],  # 只含 x=100.0 与 101.0 两点
        fetch_limit=100, budget=_Budget(), token=_Token(), page_size=10,
    ))
    rows = [r for page in pages for r in page]
    xs = [r["geometry"]["coordinates"][0] for r in rows]
    assert all(100.0 <= x <= 101.5 for x in xs)
    assert len(rows) == 2


def test_arrow_lane_bbox_outside_no_false_inclusion(parquet_source):
    adapter = _adapter(parquet_source)
    pages = list(iter_scan_pages_arrow(
        adapter, "ds_arrow", where=None, fields=None,
        bbox=[200.0, 200.0, 201.0, 201.0],  # 完全在外
        fetch_limit=100, budget=_Budget(), token=_Token(), page_size=10,
    ))
    assert pages == []
