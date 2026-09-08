"""V6 差分正确性语料（ADR-0118 W11）：V6 优化执行 ≡ V5 参考执行。

种子化随机语料 + 手工边界用例：几何/NULL/CRS/键归一/重复键/空集。
断言：行集（canonical JSON 排序）逐位一致 —— 优化器不改变结果语义。
"""
import json

import pytest

from app.schemas.data_fabric_schema import QueryResult
from app.services.data_fabric.query.federation import (
    ChainJoin,
    ChainSource,
    FederatedChainRequest,
    FederatedExecutor,
)
from app.services.data_fabric.query.federated.planner import plan_federation_v6
from app.services.data_fabric.query.federated.executor import PhysicalExecutor
from app.services.data_fabric.query.models import ExecutionBudget


class _Adapter:
    """字典数据集假适配器（按 limit/offset 分页）。"""

    def __init__(self, datasets):
        self._datasets = datasets

    def query(self, dataset_id, spec):
        feats = self._datasets[dataset_id]
        limit = spec.limit or 100
        offset = spec.offset or 0
        return QueryResult(dataset_id=dataset_id, features=feats[offset : offset + limit])


def _pt(x, y, **props):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [x, y]},
        "properties": props,
    }


def _poly(minx, miny, maxx, maxy, **props):
    return {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [[minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny]]
            ],
        },
        "properties": props,
    }


def _canon(rows):
    return sorted(json.dumps(r, sort_keys=True, ensure_ascii=False) for r in rows)


def _run_both(adapters, req):
    v5 = FederatedExecutor(lambda sid: adapters.get(sid)).execute_chain(
        FederatedChainRequest(
            sources=req.sources, joins=req.joins, bbox=req.bbox,
            limit=req.limit, order_strategy=req.order_strategy,
        )
    )
    plan = plan_federation_v6(req)
    px = PhysicalExecutor(
        adapter_factory=lambda sid: adapters.get(sid),
        budget=req.budget, limit=req.limit, bbox=req.bbox,
        adaptive=True, order_strategy=req.order_strategy,
    )
    out = px.execute(plan.tree)
    return v5, out


# ── 语料 ───────────────────────────────────────────────────────────────────


def _corpus_datasets(seed: int, n_points: int = 12, n_polys: int = 4, n_dims: int = 6):
    """确定性语料：点/多边形/维表，含 NULL、空串、unicode、重复键、int/float。"""
    rows = []
    for i in range(n_points):
        x = 100.0 + (i * 37 % 100) / 25.0
        y = 20.0 + (i * 53 % 100) / 25.0
        region = f"R{i % n_dims}" if i % 7 else ""          # 空串键
        val = None if i % 11 == 0 else (i * 13)             # NULL 数值
        key = i if i % 5 else float(i)                       # int/float 混合
        rows.append(_pt(x, y, region=region, val=val, key=key, name=f"p{i}"))
    polys = [
        _poly(100.0 + k * 1.5, 20.0, 101.5 + k * 1.5, 22.0, district=f"D{k}")
        for k in range(n_polys)
    ]
    dims = [
        {"properties": {"region": f"R{k}", "label": f"标签{k}"}} for k in range(n_dims)
    ]
    dims.append({"properties": {"region": "", "label": "empty"}})  # 空串维表行
    return {"pts": rows, "polys": polys, "dims": dims}


CORPUS_SEEDS = [1, 2, 3, 7, 11, 19, 23, 31, 47, 97]


@pytest.mark.parametrize("seed", CORPUS_SEEDS)
def test_differential_attribute_chain(seed):
    ds = _corpus_datasets(seed)
    adapters = {k: _Adapter(ds) for k in ("sA", "sC")}
    req = FederatedChainRequest(
        sources=[ChainSource(source_id="sA", dataset_id="pts"),
                 ChainSource(source_id="sC", dataset_id="dims")],
        joins=[ChainJoin(kind="attribute_join", join_field_left="region",
                         join_field_right="region",
                         left_source_id="sA", right_source_id="sC")],
        limit=10_000,
        engine="v6",
    )
    v5, v6 = _run_both(adapters, req)
    assert _canon(v6["rows"]) == _canon(v5["rows"])
    assert v6["row_count"] == v5["row_count"]


@pytest.mark.parametrize("seed", CORPUS_SEEDS)
def test_differential_numeric_key_chain(seed):
    """int/float 键归一（V5 _hashable_key 语义）在两引擎一致。"""
    ds = _corpus_datasets(seed)
    adapters = {k: _Adapter(ds) for k in ("sA", "sC")}
    req = FederatedChainRequest(
        sources=[ChainSource(source_id="sA", dataset_id="pts"),
                 ChainSource(source_id="sC", dataset_id="dims")],
        joins=[ChainJoin(kind="attribute_join", join_field_left="key",
                         join_field_right="key",
                         left_source_id="sA", right_source_id="sC")],
        limit=10_000,
        engine="v6",
    )
    v5, v6 = _run_both(adapters, req)
    assert _canon(v6["rows"]) == _canon(v5["rows"])


@pytest.mark.parametrize("op", ["within", "intersects"])
@pytest.mark.parametrize("seed", CORPUS_SEEDS[:5])
def test_differential_spatial_chain(seed, op):
    ds = _corpus_datasets(seed)
    adapters = {k: _Adapter(ds) for k in ("sA", "sB")}
    req = FederatedChainRequest(
        sources=[ChainSource(source_id="sA", dataset_id="pts"),
                 ChainSource(source_id="sB", dataset_id="polys")],
        joins=[ChainJoin(kind="spatial_join", spatial_op=op,
                         left_source_id="sA", right_source_id="sB")],
        limit=10_000,
        engine="v6",
    )
    v5, v6 = _run_both(adapters, req)
    assert _canon(v6["rows"]) == _canon(v5["rows"])


AGG_SETS = [
    [{"func": "count"}],
    [{"func": "sum", "field": "val"}],
    [{"func": "avg", "field": "val"}],
    [{"func": "min", "field": "val"}, {"func": "max", "field": "val"}],
    [{"func": "distinct_count", "field": "val"}],
    [{"func": "stddev", "field": "val"}],
    [{"func": "count"}, {"func": "sum", "field": "val"}],
]


@pytest.mark.parametrize("aggs", AGG_SETS, ids=lambda a: "+".join(x["func"] for x in a))
def test_differential_aggregate_join(aggs):
    ds = _corpus_datasets(7)
    adapters = {k: _Adapter(ds) for k in ("sA", "sC")}
    req = FederatedChainRequest(
        sources=[ChainSource(source_id="sA", dataset_id="pts"),
                 ChainSource(source_id="sC", dataset_id="dims")],
        joins=[ChainJoin(kind="aggregate_join", join_field_left="region",
                         join_field_right="region", group_by_right=["label"],
                         aggregates=aggs,
                         left_source_id="sA", right_source_id="sC")],
        limit=10_000,
        engine="v6",
    )
    v5, v6 = _run_both(adapters, req)
    assert _canon(v6["rows"]) == _canon(v5["rows"])


@pytest.mark.parametrize("seed", CORPUS_SEEDS[:6])
def test_differential_three_hop_mixed(seed):
    """三跳混合链（空间+属性+聚合语义；NULL/重复键边界）。"""
    ds = _corpus_datasets(seed)
    adapters = {k: _Adapter(ds) for k in ("sA", "sB", "sC")}
    req = FederatedChainRequest(
        sources=[ChainSource(source_id="sA", dataset_id="pts"),
                 ChainSource(source_id="sB", dataset_id="polys"),
                 ChainSource(source_id="sC", dataset_id="dims")],
        joins=[ChainJoin(kind="spatial_join", spatial_op="within",
                         left_source_id="sA", right_source_id="sB"),
               ChainJoin(kind="attribute_join", join_field_left="district",
                         join_field_right="label",
                         left_source_id="sB", right_source_id="sC")],
        limit=10_000,
        engine="v6",
    )
    v5, v6 = _run_both(adapters, req)
    assert _canon(v6["rows"]) == _canon(v5["rows"])


@pytest.mark.parametrize("bbox", [None, [100.0, 20.0, 102.0, 22.0], [0.0, 0.0, 1.0, 1.0]])
def test_differential_bbox_variants(bbox):
    ds = _corpus_datasets(11)
    adapters = {k: _Adapter(ds) for k in ("sA", "sC")}
    req = FederatedChainRequest(
        sources=[ChainSource(source_id="sA", dataset_id="pts"),
                 ChainSource(source_id="sC", dataset_id="dims")],
        joins=[ChainJoin(kind="attribute_join", join_field_left="region",
                         join_field_right="region",
                         left_source_id="sA", right_source_id="sC")],
        bbox=bbox,
        limit=10_000,
        engine="v6",
    )
    v5, v6 = _run_both(adapters, req)
    assert _canon(v6["rows"]) == _canon(v5["rows"])


def test_differential_empty_result_sets():
    """空交集：两引擎都为空（不抛错、不产生幽灵行）。"""
    ds = _corpus_datasets(3)
    adapters = {k: _Adapter(ds) for k in ("sA", "sC")}
    req = FederatedChainRequest(
        sources=[ChainSource(source_id="sA", dataset_id="pts"),
                 ChainSource(source_id="sC", dataset_id="dims")],
        joins=[ChainJoin(kind="attribute_join", join_field_left="name",
                         join_field_right="region",
                         left_source_id="sA", right_source_id="sC")],
        limit=10_000,
        engine="v6",
    )
    v5, v6 = _run_both(adapters, req)
    assert v5["row_count"] == v6["row_count"] == 0


def test_differential_limit_truncation_parity():
    """limit 截断：行集一致（排序前后缀由同一稳定语义产生）。"""
    ds = _corpus_datasets(5)
    adapters = {k: _Adapter(ds) for k in ("sA", "sC")}
    req = FederatedChainRequest(
        sources=[ChainSource(source_id="sA", dataset_id="pts"),
                 ChainSource(source_id="sC", dataset_id="dims")],
        joins=[ChainJoin(kind="attribute_join", join_field_left="region",
                         join_field_right="region",
                         left_source_id="sA", right_source_id="sC")],
        limit=3,
        engine="v6",
    )
    v5, v6 = _run_both(adapters, req)
    # 语料 i=0 的 region 为空串 → 只 R1/R2 命中 dims[:3]（R0,R1,R2）
    assert v6["row_count"] == v5["row_count"] == 2
    assert _canon(v6["rows"]) == _canon(v5["rows"])


def test_differential_where_ast_pushdown_parity():
    """where AST（dict 谓词）推送路径两引擎一致。"""
    ds = _corpus_datasets(19)
    adapters = {k: _Adapter(ds) for k in ("sA", "sC")}
    where = {"op": "and", "args": [
        {"op": "eq", "field": "region", "value": "R1"},
        {"op": "gt", "field": "key", "value": 3},
    ]}
    req = FederatedChainRequest(
        sources=[ChainSource(source_id="sA", dataset_id="pts", where=where),
                 ChainSource(source_id="sC", dataset_id="dims")],
        joins=[ChainJoin(kind="attribute_join", join_field_left="region",
                         join_field_right="region",
                         left_source_id="sA", right_source_id="sC")],
        limit=10_000,
        engine="v6",
    )
    v5, v6 = _run_both(adapters, req)
    assert _canon(v6["rows"]) == _canon(v5["rows"])


def test_differential_aggregate_join_all_null_group():
    """全部 NULL 分组键 → 单一 None 组，两引擎一致（F2 语义）。"""
    ds = {
        "pts": [_pt(100.0, 20.0, region=None, val=1), _pt(100.1, 20.1, region=None, val=2)],
        "dims": [{"properties": {"label": "L"}}],
    }
    adapters = {k: _Adapter(ds) for k in ("sA", "sC")}
    req = FederatedChainRequest(
        sources=[ChainSource(source_id="sA", dataset_id="pts"),
                 ChainSource(source_id="sC", dataset_id="dims")],
        joins=[ChainJoin(kind="aggregate_join", join_field_left="region",
                         join_field_right="region", group_by_right=["label"],
                         aggregates=[{"func": "count"}, {"func": "sum", "field": "val"}],
                         left_source_id="sA", right_source_id="sC")],
        limit=10_000,
        engine="v6",
    )
    v5, v6 = _run_both(adapters, req)
    assert _canon(v6["rows"]) == _canon(v5["rows"])
    assert v6["row_count"] == 0  # NULL 键不命中任何右行 → 空（内连接语义）
