"""Wave 5：Arrow-lane 算子（arrow_ops）—— 与 dict lane 的语义对齐测试。

- 过滤：同一类型化谓词 AST，三值逻辑（NULL → unknown → 行排除）与
  ``evaluate_predicate`` 逐谓词差分对齐；
- 不支持的谓词形状 → typed ``ArrowPredicateUnsupported``（绝不静默错结果）；
- 投影（geometry 由调用方按需包含）；聚合与 ``compute_aggregates`` 差分对齐
  （同一统一累加器）；bbox 预筛选（covering 列 / 表级 bbox 的诚实语义）；
- pyarrow 缺失：全部公共入口 typed ``VectorCarrierUnavailable``。
"""
from __future__ import annotations

import pytest

from app.services.data_fabric.query.execution import compute_aggregates
from app.services.data_fabric.query.models import AggSpec
from app.services.data_fabric.streaming import stream_bbox_filter
from app.services.data_fabric.vector_carrier import (
    VectorCarrierUnavailable,
    arrow_available,
    arrow_to_features,
    features_to_arrow,
)

if arrow_available():
    import pyarrow as pa

from app.services.data_fabric.arrow_ops import (
    ArrowPredicateUnsupported,
    arrow_aggregate_batches,
    arrow_bbox_filter,
    arrow_filter,
    arrow_project,
    build_predicate_expression,
)


def _fc(n: int = 9) -> list[dict]:
    return [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [116.0 + i, 39.0 + i]},
         "properties": {"v": i,
                        "kind": "a" if i % 2 == 0 else "b",
                        "note": None if i % 3 == 0 else f"x{i}",
                        "score": None if i % 4 == 0 else i * 1.5}}
        for i in range(n)
    ]


def _ids(features):
    return [f["properties"]["v"] for f in features]


def _dict_lane_ids(feats, pred):
    from app.services.data_fabric.query.predicates import (
        evaluate_predicate,
        predicate_from_dict,
    )

    typed = predicate_from_dict(pred)
    return [_ids([f for f in feats if evaluate_predicate(typed, f["properties"]) is True])][0]


pytestmark = pytest.mark.skipif(not arrow_available(), reason="pyarrow not installed")


# ── 过滤：与 dict lane 差分对齐（含 3VL null 语义）───────────────────────────


FILTER_PARITY_PREDICATES = [
    {"op": "eq", "field": "kind", "value": "a"},
    {"op": "ne", "field": "kind", "value": "a"},
    {"op": "gt", "field": "v", "value": 3},
    {"op": "ge", "field": "score", "value": 3.0},   # score 含 NULL → unknown 行排除
    {"op": "lt", "field": "score", "value": 6.0},
    {"op": "le", "field": "v", "value": 4},
    {"op": "in", "field": "v", "values": [1, 2, 99]},
    {"op": "in", "field": "kind", "values": ["a", "zz"]},
    {"op": "in", "field": "score", "values": [None, 4.5]},  # None 成员：3VL 保真
    {"op": "like", "field": "note", "pattern": "x%"},
    {"op": "like", "field": "note", "pattern": "x1_"},
    {"op": "is_null", "field": "note"},
    {"op": "is_null", "field": "note", "negated": True},
    {"op": "and", "args": [{"op": "gt", "field": "v", "value": 2},
                           {"op": "is_null", "field": "score"}]},
    {"op": "or", "args": [{"op": "lt", "field": "score", "value": 3.0},
                          {"op": "eq", "field": "kind", "value": "b"}]},
    # NOT unknown → unknown：score 为 NULL 的行必须同样被排除（与 dict lane 一致）
    {"op": "not", "arg": {"op": "lt", "field": "score", "value": 100.0}},
    {"op": "not", "arg": {"op": "gt", "field": "v", "value": 4}},
    {"op": "eq", "field": "v", "value": None},  # NULL 比较永假 → 空集
]


@pytest.mark.parametrize("pred", FILTER_PARITY_PREDICATES)
def test_filter_parity_with_dict_lane(pred):
    feats = _fc(9)
    table = features_to_arrow(feats)
    expected = _dict_lane_ids(feats, pred)
    got = _ids(arrow_to_features(arrow_filter(table, pred)))
    assert got == expected


def test_filter_accepts_typed_ast_node_and_record_batch():
    from app.services.data_fabric.query.predicates import predicate_from_dict

    feats = _fc(9)
    table = features_to_arrow(feats)
    node = predicate_from_dict({"op": "eq", "field": "kind", "value": "a"})
    via_node = _ids(arrow_to_features(arrow_filter(table, node)))
    via_dict = _ids(arrow_to_features(arrow_filter(table, {"op": "eq", "field": "kind", "value": "a"})))
    assert via_node == via_dict
    # RecordBatch 输入同型支持
    batch = table.to_batches(max_chunksize=4)[0]
    rb = arrow_filter(batch, node)
    assert rb.num_rows == len([f for f in feats[:4] if f["properties"]["kind"] == "a"])


# ── 不支持的形状：typed unsupported（回落 dict lane，绝不静默错结果）─────────


UNSUPPORTED_PREDICATES = [
    {"op": "between", "field": "v", "low": 1, "high": 5},
    {"op": "not_in", "field": "v", "values": [1, 2]},
    {"op": "like", "field": "v", "pattern": "x%"},            # 非字符串列
    {"op": "gt", "field": "kind", "value": 5},                # 数值标量对字符串列
    {"op": "eq", "field": "v", "value": True},                # bool 不参与数值比较
    {"op": "eq", "field": "v", "value": 1.5},                 # 非整数值对整型列（截断危险）
    {"op": "eq", "field": "no_such_field", "value": 1},       # 字段不在 schema
]


@pytest.mark.parametrize("pred", UNSUPPORTED_PREDICATES)
def test_unsupported_predicate_is_typed(pred):
    table = features_to_arrow(_fc(4))
    with pytest.raises(ArrowPredicateUnsupported):
        build_predicate_expression(pred, table.schema)
    with pytest.raises(ArrowPredicateUnsupported):
        arrow_filter(table, pred)


# ── 投影 ─────────────────────────────────────────────────────────────────────


def test_project_selects_columns_and_ignores_missing():
    table = features_to_arrow(_fc(4))
    out = arrow_project(table, ["v", "geometry", "nope"])
    assert out.column_names == ["v", "geometry"]
    assert out.num_rows == table.num_rows


# ── 聚合：与 compute_aggregates 差分对齐（同一统一累加器）────────────────────


_AGGREGATIONS = [
    {"func": "count"},
    {"func": "count", "field": "v"},
    {"func": "sum", "field": "score"},
    {"func": "avg", "field": "score"},
    {"func": "min", "field": "v"},
    {"func": "max", "field": "score"},
    {"func": "stddev", "field": "v"},
    {"func": "distinct_count", "field": "kind"},
]


def test_aggregate_parity_with_compute_aggregates_grouped():
    feats = _fc(12)
    props = [f["properties"] for f in feats]
    table = features_to_arrow(feats)
    got = arrow_aggregate_batches(table, _AGGREGATIONS, ["kind"])
    expected = compute_aggregates(props, [AggSpec(**a) for a in _AGGREGATIONS], ["kind"])
    assert got == expected


def test_aggregate_parity_groupless_and_batched_input():
    feats = _fc(12)
    props = [f["properties"] for f in feats]
    table = features_to_arrow(feats)
    got = arrow_aggregate_batches(table, _AGGREGATIONS, None)
    expected = compute_aggregates(props, [AggSpec(**a) for a in _AGGREGATIONS], None)
    assert got == expected
    # RecordBatch 迭代器输入（逐批，绝不整表物化）与整表输入同值
    from_batches = arrow_aggregate_batches(table.to_batches(max_chunksize=3),
                                           _AGGREGATIONS, None)
    assert from_batches == expected


def test_aggregate_empty_input_emits_groupless_sentinel_row():
    table = features_to_arrow(_fc(9))
    empty = table.slice(0, 0)
    got = arrow_aggregate_batches(empty, [{"func": "count"}], None)
    assert got == [{"count": 0}]


# ── bbox 预筛选 ──────────────────────────────────────────────────────────────


def _covering_table(feats):
    """带逐行 bbox covering 列 + geo 元数据声明的表（GeoParquet 1.1 形状）。"""
    import json

    xmin = [f["geometry"]["coordinates"][0] for f in feats]
    ymin = [f["geometry"]["coordinates"][1] for f in feats]
    arrays = [
        pa.array(_ids(feats), type=pa.int64()),
        pa.array(xmin, type=pa.float64()),
        pa.array(ymin, type=pa.float64()),
        pa.array(xmin, type=pa.float64()),
        pa.array(ymin, type=pa.float64()),
    ]
    geo = {
        "version": "1.0.0",
        "primary_column": "geometry",
        "columns": {"geometry": {
            "encoding": "WKB",
            "covering": {"bbox": {"xmin": "xmin", "ymin": "ymin",
                                  "xmax": "xmax", "ymax": "ymax"}},
        }},
    }
    schema = pa.schema(
        [pa.field("v", pa.int64()), pa.field("xmin", pa.float64()),
         pa.field("ymin", pa.float64()), pa.field("xmax", pa.float64()),
         pa.field("ymax", pa.float64())],
        metadata={"geo": json.dumps(geo)},
    )
    return pa.Table.from_arrays(arrays, schema=schema)


def test_bbox_filter_with_covering_columns_matches_dict_lane():
    feats = _fc(9)
    table = _covering_table(feats)
    bbox = [117.0, 40.0, 119.0, 42.0]
    got = [row["v"] for row in arrow_bbox_filter(table, bbox).to_pylist()]
    expected = _ids(list(stream_bbox_filter(feats, bbox)))
    assert got == expected


def test_bbox_filter_null_covering_rows_dropped():
    feats = _fc(4)
    table = _covering_table(feats)
    # 一行 covering 全 null（= 缺几何）→ 语义上不保留
    arrs = [table.column("v").to_pylist(), table.column("xmin").to_pylist(),
            table.column("ymin").to_pylist(), table.column("xmax").to_pylist(),
            table.column("ymax").to_pylist()]
    arrs[1][0] = None
    rebuilt = pa.Table.from_arrays(
        [pa.array(arrs[0], type=pa.int64()), pa.array(arrs[1], type=pa.float64()),
         pa.array(arrs[2], type=pa.float64()), pa.array(arrs[3], type=pa.float64()),
         pa.array(arrs[4], type=pa.float64())], schema=table.schema)
    got = [r["v"] for r in arrow_bbox_filter(rebuilt, [116.0, 39.0, 130.0, 50.0]).to_pylist()]
    assert 0 not in got  # null covering 行按缺几何语义丢弃（与 dict lane 一致）
    assert len(got) == 3


def test_bbox_filter_table_level_bbox_disjoint_is_honest_empty():
    table = features_to_arrow(_fc(9))  # 载体表：文件级 bbox，无逐行 covering
    got = arrow_bbox_filter(table, [0.0, 0.0, 1.0, 1.0])  # 与 (116..124, 39..47) 不相交
    assert got.num_rows == 0


def test_bbox_filter_without_row_level_capability_is_typed():
    table = features_to_arrow(_fc(9))
    with pytest.raises(ArrowPredicateUnsupported, match="covering"):
        arrow_bbox_filter(table, [116.0, 39.0, 118.0, 41.0])  # 相交但无逐行列

    bare = pa.table({"v": [1, 2]})  # 无 geo 元数据
    with pytest.raises(ArrowPredicateUnsupported):
        arrow_bbox_filter(bare, [0.0, 0.0, 1.0, 1.0])


# ── pyarrow 缺失：所有公共入口 typed unavailable ─────────────────────────────


def test_all_entries_unavailable_is_typed(monkeypatch):
    import app.services.data_fabric.arrow_ops as ao

    monkeypatch.setattr(ao.vector_carrier, "arrow_available", lambda: False)
    table = object()  # 任何输入都不该被触达 —— 探测先行
    with pytest.raises(VectorCarrierUnavailable, match="pyarrow"):
        ao.build_predicate_expression({"op": "eq", "field": "v", "value": 1})
    with pytest.raises(VectorCarrierUnavailable, match="pyarrow"):
        ao.arrow_filter(table, {"op": "eq", "field": "v", "value": 1})
    with pytest.raises(VectorCarrierUnavailable, match="pyarrow"):
        ao.arrow_project(table, ["v"])
    with pytest.raises(VectorCarrierUnavailable, match="pyarrow"):
        ao.arrow_aggregate_batches([], [{"func": "count"}])
    with pytest.raises(VectorCarrierUnavailable, match="pyarrow"):
        ao.arrow_bbox_filter(table, [0.0, 0.0, 1.0, 1.0])
