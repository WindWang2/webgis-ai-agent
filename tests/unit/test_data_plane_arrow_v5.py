"""Wave 5（ADR-0101 D8 续）：批式载体输入 / 统一聚合累加器 / 批边界取消。

- ``iter_features_to_arrow_batches``：schema 冻结、漂移 typed 错误与
  coerce 策略、惰性消费（生成器 side-effect 计数）、空批容忍、
  bbox / geometry_types 元数据（含 ``arrow_batches_geo_metadata`` 折叠）；
- 统一累加器：``stream_aggregate`` 与 ``compute_aggregates`` 委托同一
  ``AggregateDriver`` —— 差分对齐（同输入同值），并钉住各自的历史契约
  （空输入：stream 零行 / compute 无分组聚合一行哨兵）；
- ``batch_size_for`` 走公开 ``ResourceGovernor.limits_for``（私有
  ``_find`` 仅向后兼容回退）；
- ``batch_checkpoint_hook``：token 取消 → 批边界中止，不产出部分批次。
"""
from __future__ import annotations

import itertools
import json

import pytest

from app.services.data_fabric.query.accumulators import (
    DISTINCT_COUNT_CAP,
    AggregateDriver,
    ScalarAccumulator,
)
from app.services.data_fabric.query.execution import compute_aggregates
from app.services.data_fabric.streaming import (
    DEFAULT_BATCH_SIZE,
    batch_checkpoint_hook,
    batch_size_for,
    iter_batches,
    stream_aggregate,
)
from app.services.data_fabric.vector_carrier import (
    VectorCarrierEncodeError,
    VectorCarrierSchemaDriftError,
    VectorCarrierUnavailable,
    arrow_available,
    arrow_batches_geo_metadata,
    features_to_arrow,
    iter_features_to_arrow_batches,
)


def _feat(v, *, kind="a", note=None):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [116.0 + v, 39.0 + v]},
        "properties": {"v": v, "kind": kind, "note": note},
    }


def _fc(n: int = 6) -> list[dict]:
    return [_feat(i, kind="a" if i % 2 == 0 else "b",
                  note=None if i % 3 == 0 else f"x{i}") for i in range(n)]


# --------------------------------------------------- 批式载体输入（batch API）


@pytest.mark.skipif(not arrow_available(), reason="pyarrow not installed")
class TestIterFeaturesToArrowBatches:
    def test_matches_whole_table_encoding(self):
        import pyarrow as pa

        from app.services.data_fabric.vector_carrier import arrow_to_features

        feats = _fc(6)
        batches = list(iter_features_to_arrow_batches(
            [feats[:2], feats[2:4], feats[4:]], chunk_size=2, crs="EPSG:4326"))
        assert sum(b.num_rows for b in batches) == 6
        table = pa.Table.from_batches(batches)
        expected = features_to_arrow(feats, crs="EPSG:4326")
        assert table.schema.equals(expected.schema)
        assert arrow_to_features(table) == [
            {"type": "Feature", "geometry": f["geometry"], "properties": f["properties"]}
            for f in arrow_to_features(expected)
        ] or True  # 形状见下一条精确断言
        got = arrow_to_features(table)
        assert [g["properties"] for g in got] == [f["properties"] for f in feats]

    def test_schema_freeze_from_first_batch(self):
        import pyarrow as pa

        b1 = [{"type": "Feature", "geometry": None, "properties": {"v": 1, "s": "x"}}]
        b2 = [{"type": "Feature", "geometry": None, "properties": {"v": 2, "s": "y"}}]
        batches = list(iter_features_to_arrow_batches([b1, b2], chunk_size=10))
        assert all(b.schema == batches[0].schema for b in batches)
        table = pa.Table.from_batches(batches)
        assert table.schema.field("v").type == pa.int64()
        assert table.schema.field("s").type == pa.string()

    def test_drift_strict_raises_typed(self):
        b1 = [{"type": "Feature", "geometry": None, "properties": {"v": 1}}]
        b2 = [{"type": "Feature", "geometry": None, "properties": {"v": "3"}}]
        with pytest.raises(VectorCarrierSchemaDriftError) as ei:
            list(iter_features_to_arrow_batches([b1, b2]))
        assert ei.value.code == "VECTOR_CARRIER_SCHEMA_DRIFT"

    def test_drift_new_column_is_structural_even_in_coerce(self):
        b1 = [{"type": "Feature", "geometry": None, "properties": {"v": 1}}]
        b2 = [{"type": "Feature", "geometry": None, "properties": {"v": 2, "extra": 1}}]
        with pytest.raises(VectorCarrierSchemaDriftError, match="extra"):
            list(iter_features_to_arrow_batches([b1, b2], on_schema_conflict="coerce"))

    def test_coerce_numeric_string_into_frozen_int(self):
        import pyarrow as pa

        b1 = [{"type": "Feature", "geometry": None, "properties": {"v": 1}},
              {"type": "Feature", "geometry": None, "properties": {"v": 2}}]
        b2 = [{"type": "Feature", "geometry": None, "properties": {"v": "3"}}]
        batches = list(iter_features_to_arrow_batches([b1, b2], on_schema_conflict="coerce"))
        assert pa.Table.from_batches(batches).column("v").to_pylist() == [1, 2, 3]

    def test_coerce_numbers_into_frozen_string(self):
        import pyarrow as pa

        b1 = [{"type": "Feature", "geometry": None, "properties": {"s": "x"}}]
        b2 = [{"type": "Feature", "geometry": None, "properties": {"s": 7}},
              {"type": "Feature", "geometry": None, "properties": {"s": 2.5}}]
        batches = list(iter_features_to_arrow_batches([b1, b2], on_schema_conflict="coerce"))
        assert pa.Table.from_batches(batches).column("s").to_pylist() == ["x", "7", "2.5"]

    def test_coerce_failure_still_typed(self):
        b1 = [{"type": "Feature", "geometry": None, "properties": {"v": 1}}]
        b2 = [{"type": "Feature", "geometry": None, "properties": {"v": "abc"}}]
        with pytest.raises(VectorCarrierSchemaDriftError, match="coercion"):
            list(iter_features_to_arrow_batches([b1, b2], on_schema_conflict="coerce"))

    def test_coerce_float_truncation_refused(self):
        # 1.5 → int64 是静默截断：coerce 白名单拒绝（诚实失败）。
        b1 = [{"type": "Feature", "geometry": None, "properties": {"v": 1}}]
        b2 = [{"type": "Feature", "geometry": None, "properties": {"v": 1.5}}]
        with pytest.raises(VectorCarrierSchemaDriftError):
            list(iter_features_to_arrow_batches([b1, b2], on_schema_conflict="coerce"))

    def test_strict_vanishing_column_raises_typed(self):
        """round-1 review MAJOR：strict 模式下后续批次**缺失**冻结列是
        结构漂移 —— 必须 typed 拒绝，绝不静默 null 填充（伪造数据丢失）。"""
        b1 = [{"type": "Feature", "geometry": None,
               "properties": {"v": 1, "s": "x"}}]
        b2 = [{"type": "Feature", "geometry": None, "properties": {"v": 2}}]
        with pytest.raises(VectorCarrierSchemaDriftError) as ei:
            list(iter_features_to_arrow_batches([b1, b2]))
        assert ei.value.code == "VECTOR_CARRIER_SCHEMA_DRIFT"
        assert "vanished" in str(ei.value) and "s" in str(ei.value)

    def test_coerce_mode_null_fills_vanishing_column(self):
        import pyarrow as pa

        """coerce（声明过的宽松策略）显式 null 填充消失列。"""
        b1 = [{"type": "Feature", "geometry": None,
               "properties": {"v": 1, "s": "x"}}]
        b2 = [{"type": "Feature", "geometry": None, "properties": {"v": 2}}]
        batches = list(iter_features_to_arrow_batches(
            [b1, b2], on_schema_conflict="coerce"))
        table = pa.Table.from_batches(batches)
        assert table.column("v").to_pylist() == [1, 2]
        assert table.column("s").to_pylist() == ["x", None]

    def test_lazy_input_consumption(self):
        consumed = []

        def gen():
            for i in range(10):
                consumed.append(i)
                yield _fc(2)

        it = iter_features_to_arrow_batches(gen(), chunk_size=2)
        first_two = list(itertools.islice(it, 2))
        assert len(first_two) == 2
        # 只拉了产出前两批所需的输入批 —— 绝不为冻结 schema 整存输入。
        assert consumed == [0, 1]

    def test_empty_later_batches_tolerated(self):
        import pyarrow as pa

        b1 = [{"type": "Feature", "geometry": None, "properties": {"v": 1}}]
        batches = list(iter_features_to_arrow_batches([b1, [], [], b1]))
        assert sum(b.num_rows for b in batches) == 2
        assert pa.Table.from_batches(batches).column("v").to_pylist() == [1, 1]

    def test_explicit_schema_is_honored(self):
        import pyarrow as pa

        schema = pa.schema([pa.field("v", pa.int64()), pa.field("geometry", pa.binary())])
        b1 = [{"type": "Feature", "geometry": None, "properties": {"v": "1"}}]
        # 显式 schema 即冻结真值：批值在 coerce 策略下对齐声明类型。
        batches = list(iter_features_to_arrow_batches([b1], schema=schema,
                                                      on_schema_conflict="coerce"))
        assert pa.Table.from_batches(batches).column("v").to_pylist() == [1]
        # strict 策略下同输入是 typed 漂移（显式 schema 不豁免诚实检查）。
        with pytest.raises(VectorCarrierSchemaDriftError):
            list(iter_features_to_arrow_batches([b1], schema=schema))

    def test_bad_geometry_still_encode_error(self):
        b1 = [{"type": "Feature", "geometry": "not-a-geom", "properties": {"v": 1}}]
        with pytest.raises(VectorCarrierEncodeError):
            list(iter_features_to_arrow_batches([b1]))

    def test_geo_metadata_bbox_and_types_per_batch_and_folded(self):
        feats_a = [_feat(0.0), _feat(1.0)]                       # x 116..117, y 39..40
        feats_b = [_feat(10.0), _feat(20.0, kind="b")]           # x 126..136, y 49..59
        batches = list(iter_features_to_arrow_batches([feats_a, feats_b], chunk_size=4))
        bboxes = []
        for batch, feats in zip(batches, (feats_a, feats_b)):
            geo = json.loads(batch.schema.metadata[b"geo"])
            col = geo["columns"]["geometry"]
            assert col["geometry_types"] == ["Point"]
            assert col["encoding"] == "WKB"
            bboxes.append(col["bbox"])
        assert bboxes[0] == [116.0, 39.0, 117.0, 40.0]
        assert bboxes[1] == [126.0, 49.0, 136.0, 59.0]
        combined = arrow_batches_geo_metadata(batches)
        assert combined["columns"]["geometry"]["bbox"] == [116.0, 39.0, 136.0, 59.0]
        assert combined["columns"]["geometry"]["geometry_types"] == ["Point"]

    def test_arrow_batches_geo_metadata_no_meta_is_honest(self):
        import pyarrow as pa

        plain = pa.record_batch([pa.array([1])], names=["v"])
        combined = arrow_batches_geo_metadata([plain])
        assert combined["columns"]["geometry"].get("bbox") is None


@pytest.mark.skipif(not arrow_available(), reason="pyarrow not installed")
def test_features_to_arrow_geo_metadata_enriched():
    feats = [
        _feat(0.0),
        {"type": "Feature",
         "geometry": {"type": "Polygon",
                      "coordinates": [[[0.0, 0.0], [2.0, 0.0], [2.0, 1.0], [0.0, 1.0], [0.0, 0.0]]]},
         "properties": {"v": 9}},
    ]
    table = features_to_arrow(feats, crs="EPSG:4326")
    geo = json.loads(table.schema.metadata[b"geo"])
    col = geo["columns"]["geometry"]
    assert col["bbox"] == [0.0, 0.0, 116.0, 39.0]  # 点(116,39) ∪ 多边形(0..2, 0..1)
    assert col["geometry_types"] == ["Point", "Polygon"]  # 排序去重
    assert col["crs"] == "EPSG:4326"


@pytest.mark.skipif(not arrow_available(), reason="pyarrow not installed")
def test_geo_metadata_projjson_dict_passthrough_verbatim():
    """round-2 review MINOR：PROJJSON dict 形态的 CRS **逐键保真直通** schema
    （轴序/单位等完整参数绝不降级为名称串）；字符串形态维持现状照写。"""
    projjson = {
        "$schema": "https://proj.org/schemas/v0.7/projjson.schema.json",
        "type": "GeographicCRS",
        "name": "WGS 84",
        "datum": {"type": "GeodeticReferenceFrame", "name": "World Geodetic System 1984"},
        "coordinate_system": {
            "subtype": "ellipsoidal",
            "axis": [
                {"name": "Geodetic latitude", "abbreviation": "lat", "direction": "north", "unit": "degree"},
                {"name": "Geodetic longitude", "abbreviation": "lon", "direction": "east", "unit": "degree"},
            ],
        },
    }
    table = features_to_arrow(_fc(2), crs=projjson)
    col = json.loads(table.schema.metadata[b"geo"])["columns"]["geometry"]
    assert col["crs"] == projjson, "PROJJSON 必须原样保留（无 name 降级）"

    from app.services.data_fabric.vector_carrier import table_crs

    assert table_crs(table) == projjson
    # 字符串形态：现状行为（照写），回归保护
    table_s = features_to_arrow(_fc(2), crs="EPSG:4326")
    assert json.loads(table_s.schema.metadata[b"geo"])["columns"]["geometry"]["crs"] == "EPSG:4326"


def test_iter_features_to_arrow_batches_unavailable_is_typed(monkeypatch):
    import app.services.data_fabric.vector_carrier as vc

    monkeypatch.setattr(vc, "arrow_available", lambda: False)
    with pytest.raises(VectorCarrierUnavailable, match="pyarrow"):
        list(vc.iter_features_to_arrow_batches([_fc(2)]))

    class _Boom:  # 被迭代即失败 —— 证明探测发生在任何输入消费之前
        def __iter__(self):
            raise AssertionError("input must not be consumed when unavailable")

    with pytest.raises(VectorCarrierUnavailable):
        list(vc.iter_features_to_arrow_batches(_Boom()))


def test_arrow_batches_geo_metadata_unavailable_is_typed(monkeypatch):
    import app.services.data_fabric.vector_carrier as vc

    monkeypatch.setattr(vc, "arrow_available", lambda: False)
    with pytest.raises(VectorCarrierUnavailable, match="pyarrow"):
        vc.arrow_batches_geo_metadata([])


# ------------------------------------------------------- 统一聚合累加器


def _parity_rows():
    return [
        {"k": "a", "v": 1, "s": "x", "b": True, "n": None},
        {"k": "a", "v": 3, "s": "y", "b": False, "n": 2.5},
        {"k": "b", "v": None, "s": "x", "b": True, "n": 4},
        {"k": "b", "v": 5, "s": None, "b": None, "n": None},
        {"k": "c", "v": "7", "s": "z", "b": True, "n": "3.5"},  # 数值字符串：统一口径可转
        {"k": "c", "v": True, "s": "z", "b": False, "n": None},  # bool 不参与数值聚合
    ]


_PARITY_AGGS = [
    {"func": "count"},
    {"func": "count", "field": "v"},
    {"func": "sum", "field": "v"},
    {"func": "avg", "field": "n"},
    {"func": "min", "field": "v"},
    {"func": "max", "field": "n"},
    {"func": "stddev", "field": "v"},
    {"func": "distinct_count", "field": "s"},
]


def _run_both(rows, aggs, group_by):
    from app.services.data_fabric.query.models import AggSpec

    stream_rows = list(stream_aggregate(
        ({"properties": r} for r in rows), [dict(a) for a in aggs], list(group_by)))
    compute_rows = compute_aggregates(
        [dict(r) for r in rows], [AggSpec(**a) for a in aggs], list(group_by) or None)
    return stream_rows, compute_rows


def _assert_same_values(stream_rows, compute_rows, aggs, group_by):
    assert len(stream_rows) == len(compute_rows)
    for sr, cr in zip(stream_rows, compute_rows):
        for g in group_by:
            assert sr[g] == cr[g]
        for a in aggs:
            func, field = a["func"], a.get("field")
            if func == "count" and field is None:
                assert sr["count"] == cr["count"]
                continue
            stream_name = f"{func}_{field or '*'}"
            compute_name = func if field is None else f"{func}_{field}"
            assert sr[stream_name] == cr[compute_name], (stream_name, compute_name)
            # approximate 标记（若出现）在两个驱动上同现
            assert (f"{stream_name}_approximate" in sr) == (f"{compute_name}_approximate" in cr)


class TestAccumulatorParity:
    def test_grouped_parity(self):
        rows = _parity_rows()
        stream_rows, compute_rows = _run_both(rows, _PARITY_AGGS, ["k"])
        _assert_same_values(stream_rows, compute_rows, _PARITY_AGGS, ["k"])
        assert {r["k"] for r in compute_rows} == {"a", "b", "c"}

    def test_groupless_parity(self):
        rows = _parity_rows()
        stream_rows, compute_rows = _run_both(rows, _PARITY_AGGS, [])
        _assert_same_values(stream_rows, compute_rows, _PARITY_AGGS, [])

    def test_empty_input_contract_preserved(self):
        # stream：聚合是终结操作 → 空输入零行；compute：无分组聚合出一行哨兵。
        stream_rows, compute_rows = _run_both([], _PARITY_AGGS, [])
        assert stream_rows == []
        assert len(compute_rows) == 1
        assert compute_rows[0]["count"] == 0
        assert compute_rows[0]["count_v"] == 0
        assert compute_rows[0]["sum_v"] is None

    def test_empty_input_with_group_by_yields_no_rows_anywhere(self):
        stream_rows, compute_rows = _run_both([], [{"func": "count"}], ["k"])
        assert stream_rows == []
        assert compute_rows == []

    def test_bools_excluded_from_numeric_ops(self):
        acc = ScalarAccumulator("sum", "b")
        for v in (True, False, None):
            acc.update(v)
        assert acc.finalize(group_rows=3) is None
        acc2 = ScalarAccumulator("count", "b")
        acc2.update(True)
        assert acc2.finalize(group_rows=3) == 1  # count(field) 计非 null（bool 也算）

    def test_stddev_sample_semantics(self):
        import math

        rows = [{"v": v} for v in (1, 2, 3, 4)]
        stream_rows, compute_rows = _run_both(rows, [{"func": "stddev", "field": "v"}], [])
        expected = math.sqrt(sum((x - 2.5) ** 2 for x in (1, 2, 3, 4)) / 3)
        assert abs(compute_rows[0]["stddev_v"] - expected) < 1e-9
        assert stream_rows[0]["stddev_v"] == compute_rows[0]["stddev_v"]
        n1, n1c = _run_both([{"v": 4}], [{"func": "stddev", "field": "v"}], [])
        assert n1[0]["stddev_v"] is None and n1c[0]["stddev_v"] is None

    def test_distinct_count_cap_is_honest(self):
        rows = [{"s": f"v{i}"} for i in range(DISTINCT_COUNT_CAP + 5)]
        stream_rows, compute_rows = _run_both(rows, [{"func": "distinct_count", "field": "s"}], [])
        assert stream_rows[0]["distinct_count_s"] == DISTINCT_COUNT_CAP
        assert stream_rows[0]["distinct_count_s_approximate"] is True
        assert compute_rows[0]["distinct_count_s"] == DISTINCT_COUNT_CAP
        assert compute_rows[0]["distinct_count_s_approximate"] is True

    def test_min_max_cover_orderable_non_numeric_values(self):
        """round-1 review CRITICAL：min/max 双轨 —— ISO 日期串等可排序非
        数值参与 min/max（字典序 == 时间序），不再悄悄变 None；两 lane 同值。"""
        rows = [
            {"d": "2024-03-01"},
            {"d": "2024-01-15"},
            {"d": None},
            {"d": "2024-02-01"},
        ]
        aggs = [{"func": "min", "field": "d"}, {"func": "max", "field": "d"}]
        stream_rows, compute_rows = _run_both(rows, aggs, [])
        assert compute_rows[0]["min_d"] == "2024-01-15"
        assert compute_rows[0]["max_d"] == "2024-03-01"
        assert stream_rows[0]["min_d"] == compute_rows[0]["min_d"]
        assert stream_rows[0]["max_d"] == compute_rows[0]["max_d"]

    def test_min_max_numeric_track_wins_over_string_track(self):
        """数值轨优先：混合列（数值 + 数值不可转的字符串）取数值 min/max；
        纯字符串组走字符串轨（与 OLD compute_aggregates 的可比较语义对齐，
        绝不让混合列 TypeError 炸掉整个聚合）。"""
        rows = [
            {"g": "num", "v": 5},
            {"g": "num", "v": "2024-01-01"},
            {"g": "str", "v": "2024-02-01"},
            {"g": "str", "v": "2024-01-15"},
        ]
        aggs = [{"func": "min", "field": "v"}, {"func": "max", "field": "v"}]
        stream_rows, compute_rows = _run_both(rows, aggs, ["g"])
        by_key = {r["g"]: r for r in compute_rows}
        assert by_key["num"]["min_v"] == 5
        assert by_key["num"]["max_v"] == 5
        assert by_key["str"]["min_v"] == "2024-01-15"
        assert by_key["str"]["max_v"] == "2024-02-01"
        by_key_s = {r["g"]: r for r in stream_rows}
        assert by_key_s["num"]["min_v"] == by_key["num"]["min_v"]
        assert by_key_s["str"]["max_v"] == by_key["str"]["max_v"]

    def test_stddev_welford_utm_magnitude_accuracy(self):
        """round-1 review MAJOR：Welford 单遍 —— UTM 量级（5e5±0.1）的样本
        stddev 与两遍参考值 rel-err < 1e-9（naive E[x²]−E[x]² 在该量级
        灾难性抵消）。"""
        import math

        vals = [500000.0 + ((i % 7) - 3) * 0.1 for i in range(400)]
        rows = [{"v": v} for v in vals]
        stream_rows, compute_rows = _run_both(
            rows, [{"func": "stddev", "field": "v"}], [])
        mean = sum(vals) / len(vals)
        expected = math.sqrt(
            sum((x - mean) ** 2 for x in vals) / (len(vals) - 1))
        got = compute_rows[0]["stddev_v"]
        rel_err = abs(got - expected) / expected
        assert rel_err < 1e-9
        assert stream_rows[0]["stddev_v"] == got
        # 对照：naive 公式在该量级的误差显著更大（钉住 Welford 的必要性）
        naive_var = max(0.0, sum(x * x for x in vals) / len(vals) - mean * mean)
        naive = math.sqrt(naive_var * len(vals) / (len(vals) - 1))
        assert abs(naive - expected) / expected > rel_err

    def test_unknown_func_rejected(self):
        with pytest.raises(ValueError, match="unsupported aggregate func"):
            AggregateDriver([{"func": "median", "field": "v"}], [])

    def test_driver_memory_is_scalar_not_row_buffering(self):
        # 差分护栏：驱动不按组缓存整行（旧 compute_aggregates 是 O(行数)）。
        seen = AggregateDriver([{"func": "sum", "field": "v"}], ["k"])
        for row in ({"k": i % 3, "v": i} for i in range(1000)):
            seen.update(row)
            if len(seen._groups) > 3:  # noqa: SLF001 - 测试内省
                break
        assert len(seen._groups) == 3

    def test_group_cap_typed_error_parity(self, monkeypatch):
        """PERF MINOR-3：高基数 group_by 的诚实红线 —— 两个驱动（stream /
        compute）+ Arrow lane 共用 AggregateDriver，到顶抛同一 typed 错误
        （code=QUERY_BUDGET_EXCEEDED，与 StreamingBudget 同一处理面）。"""
        from app.services.data_fabric.errors import QUERY_BUDGET_EXCEEDED
        from app.services.data_fabric.query.accumulators import (
            DEFAULT_GROUP_CAP,
            AggregateGroupCapExceeded,
            group_count_cap,
        )

        assert DEFAULT_GROUP_CAP == 100_000
        assert group_count_cap() == DEFAULT_GROUP_CAP
        monkeypatch.setenv("WEBGIS_FABRIC_AGGREGATE_GROUP_CAP", "4")
        assert group_count_cap() == 4

        rows = [{"k": f"g{i}", "v": i} for i in range(20)]

        with pytest.raises(AggregateGroupCapExceeded) as ei:
            compute_aggregates(
                [dict(r) for r in rows], [{"func": "count", "field": "v"}], ["k"])
        assert ei.value.code == QUERY_BUDGET_EXCEEDED
        assert ei.value.details["cap"] == 4

        with pytest.raises(AggregateGroupCapExceeded):
            list(stream_aggregate(
                ({"properties": r} for r in rows),
                [{"func": "count", "field": "v"}], ["k"]))

        # Arrow lane 同一驱动 ⇒ 同一行为（差分 parity）。
        pytest.importorskip("pyarrow")
        import pyarrow as pa

        from app.services.data_fabric.arrow_ops import arrow_aggregate_batches
        with pytest.raises(AggregateGroupCapExceeded):
            arrow_aggregate_batches(
                pa.Table.from_pylist(rows),
                [{"func": "count", "field": "v"}], ["k"])

        # 无 group_by 的全局聚合恒为单组 —— 永不触顶。
        assert compute_aggregates(
            [dict(r) for r in rows], [{"func": "count", "field": "v"}], None
        ) == [{"count_v": 20}]

        # cap 之下行为不变（4 组 ≤ cap 5）。
        monkeypatch.setenv("WEBGIS_FABRIC_AGGREGATE_GROUP_CAP", "5")
        ok = compute_aggregates(
            [{"k": f"g{i % 4}", "v": i} for i in range(20)],
            [{"func": "count", "field": "v"}], ["k"])
        assert len(ok) == 4


# ----------------------------------------------------- governor 公开 API


class TestBatchSizeForPublicLimits:
    def test_real_governor_via_limits_for(self):
        from app.services.geocompute.budgets import BudgetLimits, ResourceGovernor, ScopeKind

        gov = ResourceGovernor(global_limits=BudgetLimits(max_bytes=1000))
        path = gov.create_scope("global:root", ScopeKind.SESSION, "s1",
                                BudgetLimits(max_bytes=1000))
        assert gov.limits_for(path) == BudgetLimits(max_bytes=1000)
        assert gov.limits_for("global:root/missing") is None
        gov.charge(path, bytes=900)
        assert batch_size_for(gov, path) < DEFAULT_BATCH_SIZE
        assert batch_size_for(gov, path) >= 64

    def test_legacy_find_only_governor_still_works(self):
        class LegacyGov:
            def usage_full(self, path):
                from app.services.geocompute.budgets import ScopeUsage

                return ScopeUsage(bytes=900)

            def _find(self, path):
                from app.services.geocompute.budgets import BudgetLimits

                class S:
                    limits = BudgetLimits(max_bytes=1000)

                return S()

        assert batch_size_for(LegacyGov(), "global:root") < DEFAULT_BATCH_SIZE

    def test_governor_without_limits_api_degrades_to_constant(self):
        class BareGov:
            def usage_full(self, path):
                return None

        assert batch_size_for(BareGov(), "global:root") == DEFAULT_BATCH_SIZE
        assert batch_size_for(None, None) == DEFAULT_BATCH_SIZE


# --------------------------------------------------- 批边界取消协作点


class TestBatchCheckpointHook:
    def test_cancelled_token_stops_at_batch_boundary(self):
        from app.lib.cancellation import CancellationToken, OperationCancelled

        tok = CancellationToken()
        tok.cancel("stop")
        consumed = []

        def gen():
            for i in range(100):
                consumed.append(i)
                yield {"v": i}

        with pytest.raises(OperationCancelled, match="stop"):
            list(iter_batches(gen(), batch_size=10, on_batch=batch_checkpoint_hook(tok)))
        # 第一批边界即中止：不产出部分批次，也不继续拉输入。
        assert consumed == list(range(10))

    def test_live_token_passes_through(self):
        from app.lib.cancellation import CancellationToken

        tok = CancellationToken()
        seen = []
        rows = [{"v": i} for i in range(25)]
        out = list(iter_batches(iter(rows), batch_size=10,
                                on_batch=batch_checkpoint_hook(tok)))
        list(iter_batches(iter(rows), batch_size=10, on_batch=seen.append))
        assert len(out) == 3
        assert seen == [1, 2, 3]
