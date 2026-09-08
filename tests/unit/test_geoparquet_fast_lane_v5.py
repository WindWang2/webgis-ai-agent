"""Wave 5：GeoParquet Arrow fast lane（属性-only 下推）+ 批边界取消 +
GeoParquet 磁盘工件物化。

- fast lane 与 dict lane 差分对齐：同一查询两种 lane 的 features/data 完全
  一致；``metadata["execution_lane"]`` 诚实标注实际执行的 lane；
- 任何不支持的形状（时间谓词、bbox 无逐行 covering、不支持谓词）→ dict
  lane 原样回落；
- ``arrow_available`` 探测 False → dict lane（pyarrow 在场）；
- CancellationToken 在每个批次边界协作检查（批粒度中止，不产出部分批）；
- ``materialize_geoparquet``：磁盘工件 ref:fabric-parquet/<id>；pyarrow
  缺失 typed unavailable；缺省 materialize 行为不变。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.schemas.data_fabric_schema import ConnectionProfile, QueryResult, QuerySpec
from app.services.data_fabric.vector_carrier import (
    VectorCarrierUnavailable,
    arrow_available,
    features_to_arrow,
    geoparquet_to_features,
    table_to_geoparquet,
)

pytestmark = pytest.mark.skipif(not arrow_available(), reason="pyarrow not installed")


def _attr_features(n: int = 50) -> list[dict]:
    return [
        {"type": "Feature", "geometry": None,
         "properties": {"id": i, "name": f"row_{i % 5}", "val": i * 1.5}}
        for i in range(n)
    ]


def _point_features(n: int = 9) -> list[dict]:
    return [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [116.0 + i, 39.0 + i]},
         "properties": {"id": i}}
        for i in range(n)
    ]


def _write_parquet(tmp_path: Path, name: str, features: list[dict]) -> str:
    table = features_to_arrow(features, crs="EPSG:4326")
    path = str(tmp_path / name)
    table_to_geoparquet(table, path)
    return path


def _write_covering_parquet(tmp_path: Path, name: str, features: list[dict]) -> str:
    """带逐行 bbox covering 列的 GeoParquet（GeoParquet 1.1 covering 形状）。"""
    import pyarrow as pa
    import pyarrow.parquet as pq

    xs = [f["geometry"]["coordinates"][0] for f in features]
    ys = [f["geometry"]["coordinates"][1] for f in features]
    import shapely

    wkbs = [
        shapely.to_wkb(shapely.from_geojson(json.dumps(f["geometry"])))
        for f in features
    ]
    geo = {
        "version": "1.0.0",
        "primary_column": "geometry",
        "columns": {"geometry": {
            "encoding": "WKB",
            "crs": "EPSG:4326",
            "covering": {"bbox": {"xmin": "xmin", "ymin": "ymin",
                                  "xmax": "xmax", "ymax": "ymax"}},
        }},
    }
    schema = pa.schema(
        [pa.field("id", pa.int64()),
         pa.field("xmin", pa.float64()), pa.field("ymin", pa.float64()),
         pa.field("xmax", pa.float64()), pa.field("ymax", pa.float64()),
         pa.field("geometry", pa.binary())],
        metadata={"geo": json.dumps(geo)},
    )
    arrays = [pa.array([f["properties"]["id"] for f in features], type=pa.int64()),
              pa.array(xs, type=pa.float64()), pa.array(ys, type=pa.float64()),
              pa.array(xs, type=pa.float64()), pa.array(ys, type=pa.float64()),
              pa.array(wkbs, type=pa.binary())]
    path = str(tmp_path / name)
    pq.write_table(pa.Table.from_arrays(arrays, schema=schema), path, compression="zstd")
    return path


def _adapter(monkeypatch, tmp_path: Path, endpoint: str):
    from app.services.data_fabric.adapters import geoparquet_adapter as gp_mod
    from app.services.data_fabric.adapters.geoparquet_adapter import GeoParquetAdapter

    monkeypatch.setattr(gp_mod, "_local_file_roots_from_settings", lambda: [str(tmp_path)])
    monkeypatch.setattr(gp_mod, "_local_file_max_bytes_from_settings",
                        lambda: 64 * 1024 * 1024)
    return GeoParquetAdapter(ConnectionProfile(
        source_type="geoparquet", endpoint_url=endpoint, name="test_geoparquet"))


@pytest.fixture
def attrs_parquet(tmp_path):
    return _write_parquet(tmp_path, "attrs.parquet", _attr_features(50))


# ── fast lane：与 dict lane 差分对齐 ─────────────────────────────────────────


def test_fast_lane_filter_parity_and_lane_flag(monkeypatch, tmp_path, attrs_parquet):
    adapter = _adapter(monkeypatch, tmp_path, attrs_parquet)
    spec = QuerySpec(limit=100, filter_expr={"op": "gt", "field": "val", "value": 25.0})
    fast = adapter.query("attrs", spec)
    assert fast.metadata["execution_lane"] == "arrow"
    assert [f["properties"]["id"] for f in fast.features] == list(range(17, 50))

    # 同一查询强制 dict lane → features 完全一致
    from app.services.data_fabric.adapters import geoparquet_adapter as gp_mod

    def _boom(*_a, **_kw):
        raise gp_mod.ArrowPredicateUnsupported("forced")

    monkeypatch.setattr(gp_mod, "build_predicate_expression", _boom)
    slow = adapter.query("attrs", spec)
    assert slow.metadata["execution_lane"] == "dict"
    assert slow.features == fast.features
    assert slow.total_matching == fast.total_matching == 33


def test_fast_lane_projection_parity(monkeypatch, tmp_path, attrs_parquet):
    adapter = _adapter(monkeypatch, tmp_path, attrs_parquet)
    spec = QuerySpec(limit=10, fields=["name"],
                     filter_expr={"op": "eq", "field": "name", "value": "row_3"})
    fast = adapter.query("attrs", spec)
    assert fast.metadata["execution_lane"] == "arrow"
    assert all(set(f["properties"].keys()) == {"name"} for f in fast.features)
    assert fast.returned_count == 10

    from app.services.data_fabric.adapters import geoparquet_adapter as gp_mod

    def _boom(*_a, **_kw):
        raise gp_mod.ArrowPredicateUnsupported("forced")

    monkeypatch.setattr(gp_mod, "build_predicate_expression", _boom)
    slow = adapter.query("attrs", spec)
    assert slow.metadata["execution_lane"] == "dict"
    assert slow.features == fast.features


def test_fast_lane_statistics_parity(monkeypatch, tmp_path, attrs_parquet):
    adapter = _adapter(monkeypatch, tmp_path, attrs_parquet)
    spec = QuerySpec(
        limit=100,
        aggregate=[{"func": "count"}, {"func": "sum", "field": "val"},
                   {"func": "distinct_count", "field": "name"}],
        group_by=["name"],
    )
    fast = adapter.query("attrs", spec)
    assert fast.metadata["execution_lane"] == "arrow"

    import app.services.data_fabric.vector_carrier as vc

    monkeypatch.setattr(vc, "arrow_available", lambda: False)  # → dict lane
    slow = adapter.query("attrs", spec)
    assert slow.metadata["execution_lane"] == "dict"
    assert fast.data == slow.data  # 统一累加器：两个 lane 完全同值
    assert len(fast.data) == 5


def test_temporal_predicate_falls_back_to_dict_lane(monkeypatch, tmp_path, attrs_parquet):
    adapter = _adapter(monkeypatch, tmp_path, attrs_parquet)
    spec = QuerySpec(limit=10, datetime_range=["2020-01-01", "2020-12-31"])
    res = adapter.query("attrs", spec)
    assert res.metadata["execution_lane"] == "dict"  # 时间谓词不在 Arrow 等价集
    assert res.features == []  # 无 time 字段 → 全部行 unknown→排除（与 dict 一致）


def test_bbox_query_without_covering_falls_back(monkeypatch, tmp_path):
    path = _write_parquet(tmp_path, "points.parquet", _point_features(9))
    adapter = _adapter(monkeypatch, tmp_path, path)
    spec = QuerySpec(limit=100, bbox=[116.0, 39.0, 118.0, 41.0])
    res = adapter.query("points", spec)
    # 载体写的文件只有表级 bbox，无逐行 covering → fast lane 诚实回落 dict lane
    assert res.metadata["execution_lane"] == "dict"
    assert [f["properties"]["id"] for f in res.features] == [0, 1, 2]


def test_bbox_query_with_covering_uses_arrow_lane(monkeypatch, tmp_path):
    path = _write_covering_parquet(tmp_path, "covered.parquet", _point_features(9))
    adapter = _adapter(monkeypatch, tmp_path, path)
    spec = QuerySpec(limit=100, bbox=[117.0, 40.0, 119.0, 42.0])
    res = adapter.query("covered", spec)
    assert res.metadata["execution_lane"] == "arrow"
    assert [f["properties"]["id"] for f in res.features] == [1, 2, 3]


def test_arrow_available_false_uses_dict_lane(monkeypatch, tmp_path, attrs_parquet):
    import app.services.data_fabric.vector_carrier as vc

    monkeypatch.setattr(vc, "arrow_available", lambda: False)
    adapter = _adapter(monkeypatch, tmp_path, attrs_parquet)
    spec = QuerySpec(limit=100, filter_expr={"op": "gt", "field": "val", "value": 25.0})
    res = adapter.query("attrs", spec)
    assert res.metadata["execution_lane"] == "dict"
    assert [f["properties"]["id"] for f in res.features] == list(range(17, 50))


def test_fast_lane_sample_and_offset_parity(monkeypatch, tmp_path, attrs_parquet):
    adapter = _adapter(monkeypatch, tmp_path, attrs_parquet)
    page2 = adapter.query("attrs", QuerySpec(limit=10, offset=40))
    assert page2.metadata["execution_lane"] == "arrow"
    assert [f["properties"]["id"] for f in page2.features] == list(range(40, 50))
    assert page2.has_more is False and page2.total_matching == 50

    a = adapter.query("attrs", QuerySpec(limit=5, result_mode="sample", sample_size=7))
    b = adapter.query("attrs", QuerySpec(limit=5, result_mode="sample", sample_size=7))
    assert a.metadata["execution_lane"] == "arrow"
    assert [f["properties"]["id"] for f in a.features] == \
        [f["properties"]["id"] for f in b.features]


# ── 批边界取消 ───────────────────────────────────────────────────────────────


class _CancelAfterFirstBatch:
    """鸭子类型 token：第一次边界检查即取消（真实 CancellationToken 承载信号）。"""

    def __init__(self):
        from app.lib.cancellation import CancellationToken

        self.token = CancellationToken()
        self.calls = 0

    def raise_if_cancelled(self):
        self.calls += 1
        if self.calls == 1:
            self.token.cancel("mid-stream")
        self.token.raise_if_cancelled()


def test_cancelled_token_stops_stream_at_batch_boundary(monkeypatch, tmp_path):
    pytest.importorskip("pyarrow.parquet")
    path = _write_parquet(tmp_path, "big.parquet", _attr_features(2500))
    adapter = _adapter(monkeypatch, tmp_path, path)

    from app.lib.cancellation import CancellationToken, OperationCancelled

    pre = CancellationToken()
    pre.cancel("stop-before-start")
    with pytest.raises(OperationCancelled, match="stop-before-start"):
        adapter.query("big", QuerySpec(limit=2500), cancel_token=pre)

    hook = _CancelAfterFirstBatch()
    with pytest.raises(OperationCancelled, match="mid-stream"):
        adapter.query("big", QuerySpec(limit=2500), cancel_token=hook)
    assert hook.calls == 1  # 第一批边界即中止 —— 不产出部分批次
    assert hook.token.cancelled

    # 未取消 token → 全量通过（2500 行跨 3 个 1024 行批）
    res = adapter.query("big", QuerySpec(limit=2500), cancel_token=CancellationToken())
    assert res.returned_count == 2500
    assert res.metadata["execution_lane"] == "arrow"


# ── GeoParquet 磁盘工件物化 ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_materialize_geoparquet_writes_disk_artifact(monkeypatch, tmp_path):
    from app.core.config import settings
    from app.services.data_fabric.materialization_service import materialization_service

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    feats = _point_features(4)
    table = features_to_arrow(feats, crs="EPSG:4326")
    res = await materialization_service.materialize_geoparquet("sess-1", table, "T")
    assert res["success"] is True
    assert res["ref"].startswith("ref:fabric-parquet/")
    assert len(res["ref"].rsplit("/", 1)[1]) == 16  # 16hex id
    path = Path(res["path"])
    assert path.is_file()
    assert path.parent.name == "fabric-geoparquet"
    assert path.parent.parent.name == "sess-1"
    back = geoparquet_to_features(res["path"])
    assert [b["properties"] for b in back] == [f["properties"] for f in feats]
    assert [b["geometry"] for b in back] == [f["geometry"] for f in feats]


@pytest.mark.asyncio
async def test_materialize_opt_in_geoparquet_and_default_unchanged(monkeypatch, tmp_path):
    from app.core.config import settings
    from app.services.data_fabric import materialization_service as mat_mod

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    svc = mat_mod.materialization_service
    feats = _point_features(3)
    qr = QueryResult(dataset_id="d1", features=feats, result_mode="materialize",
                     total_count=3)

    res = await svc.materialize("d1", qr, session_id="sess-2", output_format="geoparquet")
    assert res["success"] is True and res["format"] == "geoparquet"
    assert res["artifact_ref"] == res["ref"]
    assert res["ref_id"] is None  # session-store ref 不产生（磁盘工件 lane）
    assert Path(res["path"]).is_file()

    # 缺省（不带 format）行为不变：GeoJSON → session ref（fake store 证明）
    stored = {}

    async def fake_store(session_id, data, prefix="data"):
        ref = f"ref:{prefix}-xyz"
        stored[ref] = data
        return ref

    async def fake_set_alias(session_id, ref_id, alias):
        return None

    monkeypatch.setattr(mat_mod.session_data_manager, "store", fake_store)
    monkeypatch.setattr(mat_mod.session_data_manager, "set_alias", fake_set_alias)
    res2 = await svc.materialize("d1", qr, session_id="sess-2")
    assert res2["success"] is True
    assert res2["ref_id"] == "ref:data-fabric-xyz"
    assert res2["ref_id"] in stored

    # 轻量模式不受 format 影响：零物化直返
    qrs = QueryResult(dataset_id="d1", features=[], data=[{"count": 1}],
                      result_mode="statistics", total_count=1)
    res3 = await svc.materialize("d1", qrs, session_id="sess-2",
                                 output_format="geoparquet")
    assert res3["result_mode"] == "statistics" and res3["ref_id"] is None


@pytest.mark.asyncio
async def test_materialize_geoparquet_unavailable_is_typed(monkeypatch):
    import app.services.data_fabric.vector_carrier as vc
    from app.services.data_fabric import materialization_service as mat_mod

    monkeypatch.setattr(vc, "arrow_available", lambda: False)
    svc = mat_mod.materialization_service
    with pytest.raises(VectorCarrierUnavailable, match="pyarrow"):
        await svc.materialize_geoparquet("s", object(), "T")
    qr = QueryResult(dataset_id="d", features=_point_features(2),
                     result_mode="materialize", total_count=2)
    with pytest.raises(VectorCarrierUnavailable, match="pyarrow"):
        await svc.materialize("d", qr, session_id="s", output_format="geoparquet")


@pytest.mark.asyncio
async def test_materialize_geoparquet_rejects_bad_session_id(monkeypatch, tmp_path):
    from app.core.config import settings
    from app.services.data_fabric.materialization_service import materialization_service

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    table = features_to_arrow(_point_features(2))
    with pytest.raises(ValueError, match="session id"):
        await materialization_service.materialize_geoparquet("../evil", table, "T")


# ── round-1 review MAJOR：CRS 经 schema_info 进结果面 ────────────────────────


def test_query_result_carries_crs_both_lanes(monkeypatch, tmp_path):
    """adapter 在 query() 结果面携带 geo 元数据声明的 CRS（dict/arrow 两
    lane 同源）—— materialize(geoparquet) 由此取源 CRS。"""
    feats = _point_features(4)
    path = str(tmp_path / "utm.parquet")
    table_to_geoparquet(features_to_arrow(feats, crs="EPSG:32633"), path)
    adapter = _adapter(monkeypatch, tmp_path, path)

    spec = QuerySpec(limit=100)
    fast = adapter.query("utm", spec)
    assert fast.schema_info.get("crs") == "EPSG:32633"

    import app.services.data_fabric.vector_carrier as vc

    monkeypatch.setattr(vc, "arrow_available", lambda: False)  # → dict lane
    slow = adapter.query("utm", spec)
    assert slow.metadata["execution_lane"] == "dict"
    assert slow.schema_info.get("crs") == "EPSG:32633"


@pytest.mark.asyncio
async def test_materialize_projected_crs_table_keeps_crs(monkeypatch, tmp_path):
    """materialize(geoparquet)：投影 CRS 表的 geo 元数据携带 crs ——
    源 CRS 不再在物化时静默丢失（round-1 review MAJOR）。"""
    import pyarrow.parquet as pq

    from app.core.config import settings
    from app.services.data_fabric import materialization_service as mat_mod

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    feats = _point_features(4)
    path = str(tmp_path / "utm.parquet")
    table_to_geoparquet(features_to_arrow(feats, crs="EPSG:32633"), path)
    adapter = _adapter(monkeypatch, tmp_path, path)
    qr = adapter.query("utm", QuerySpec(limit=100))

    res = await mat_mod.materialization_service.materialize(
        "utm", qr, session_id="sess-utm", output_format="geoparquet")
    assert res["success"] is True and res["format"] == "geoparquet"
    md = pq.read_metadata(res["path"]).metadata or {}
    geo = json.loads(md[b"geo"].decode("utf-8"))
    crs = (geo.get("columns") or {}).get("geometry", {}).get("crs")
    assert crs == "EPSG:32633"
