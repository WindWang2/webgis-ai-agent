"""V6 物理执行器契约测试（ADR-0118 W6）。

覆盖：分页探针/取消/超时、Bloom 预滤假阳性语义、CRS 变换节点、
以及 **V6 vs V5 同数据差分**（行形状与行集逐位一致）。
"""

import threading

import pytest

from app.schemas.data_fabric_schema import QueryResult
from app.services.data_fabric.query.federated.executor import PhysicalExecutor
from app.services.data_fabric.query.federated.enumerator import enumerate_federation
from app.services.data_fabric.query.federated.physical import (
    CancelToken,
    CancelledError,
    bloom_prefilter,
    transform_rows_geometry,
)
from app.services.data_fabric.query.federated.planner import build_enumeration_context
from app.services.data_fabric.query.federation import (
    ChainJoin,
    ChainSource,
    FederatedChainRequest,
    FederatedExecutor,
)


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


class _PagedAdapter:
    """分页假适配器（记录调用以断言分页行为）。"""

    def __init__(self, data, page_break_at=None):
        self._data = data
        self.calls = []
        self._page_break_at = page_break_at

    def query(self, dataset_id, spec):
        self.calls.append((dataset_id, spec))
        feats = self._data[dataset_id]
        limit = spec.limit or 100
        offset = spec.offset or 0
        return QueryResult(
            dataset_id=dataset_id, features=feats[offset : offset + limit]
        )


def _v5_result(adapters, req):
    ex = FederatedExecutor(lambda sid: adapters.get(sid))
    return ex.execute_chain(req)


def _v6_result(adapters, req, ctx=None, **kw):
    enum_ctx = ctx or build_enumeration_context(req)
    plan = enumerate_federation(enum_ctx)
    px = PhysicalExecutor(
        adapter_factory=lambda sid: adapters.get(sid),
        budget=req.budget,
        limit=req.limit,
        bbox=req.bbox,
        **kw,
    )
    out = px.execute(plan.tree)
    out["order"] = plan.order
    return out


def _chain_req(sources, joins, limit=10_000):
    return FederatedChainRequest(sources=sources, joins=joins, limit=limit)


# ── 差分：V6 ≡ V5 ──────────────────────────────────────────────────────────


DIFF_POINTS = [
    _pt(104.0, 30.0, name="s1", region="A", students=500),
    _pt(104.1, 30.1, name="s2", region="B", students=300),
    _pt(106.5, 29.5, name="s3", region="A", students=800),
    _pt(0.0, 0.0, name="s4", region="none", students=1),
]
DIFF_POLYS = [
    _poly(103.9, 29.9, 104.2, 30.2, district="D1"),
    _poly(106.0, 29.0, 107.0, 30.0, district="D2"),
]
DIFF_DIMS = [
    {"properties": {"region": "A", "label": "alpha"}},
    {"properties": {"region": "B", "label": "beta"}},
    {"properties": {"region": "none", "label": "gamma"}},
]


def _diff_adapters():
    a = _PagedAdapter({"pts": DIFF_POINTS, "polys": DIFF_POLYS, "dims": DIFF_DIMS})
    return {"sA": a, "sB": a, "sC": a}


def _canon(rows):
    """行集规范化比较（去掉顺序敏感性；键集与值必须一致）。"""
    import json

    return sorted(json.dumps(r, sort_keys=True, ensure_ascii=False) for r in rows)


def test_differential_attribute_join_v6_equals_v5():
    adapters = _diff_adapters()
    req = _chain_req(
        [
            ChainSource(source_id="sA", dataset_id="pts"),
            ChainSource(source_id="sC", dataset_id="dims"),
        ],
        [
            ChainJoin(
                kind="attribute_join",
                join_field_left="region",
                join_field_right="region",
                left_source_id="sA",
                right_source_id="sC",
            )
        ],
    )
    v5 = _v5_result(adapters, req)
    v6 = _v6_result(adapters, req)
    assert _canon(v6["rows"]) == _canon(v5["rows"])
    assert v6["row_count"] == v5["row_count"]
    assert v6["joined_row_count"] == v5["joined_row_count"]


def test_differential_spatial_join_v6_equals_v5():
    adapters = _diff_adapters()
    req = _chain_req(
        [
            ChainSource(source_id="sA", dataset_id="pts"),
            ChainSource(source_id="sB", dataset_id="polys"),
        ],
        [
            ChainJoin(
                kind="spatial_join",
                spatial_op="within",
                left_source_id="sA",
                right_source_id="sB",
            )
        ],
    )
    v5 = _v5_result(adapters, req)
    v6 = _v6_result(adapters, req)
    assert _canon(v6["rows"]) == _canon(v5["rows"])
    assert v6["row_count"] == v5["row_count"] == 3  # s1,s2∈D1；s3∈D2；s4 无命中


def test_differential_three_hop_mixed_v6_equals_v5():
    adapters = _diff_adapters()
    req = _chain_req(
        [
            ChainSource(source_id="sA", dataset_id="pts"),
            ChainSource(source_id="sB", dataset_id="polys"),
            ChainSource(source_id="sC", dataset_id="dims"),
        ],
        [
            ChainJoin(
                kind="spatial_join",
                spatial_op="within",
                left_source_id="sA",
                right_source_id="sB",
            ),
            ChainJoin(
                kind="attribute_join",
                join_field_left="district",
                join_field_right="label",
                left_source_id="sB",
                right_source_id="sC",
            ),
        ],
    )
    v5 = _v5_result(adapters, req)
    v6 = _v6_result(adapters, req)
    assert _canon(v6["rows"]) == _canon(v5["rows"])


def test_differential_aggregate_join_v6_equals_v5():
    adapters = _diff_adapters()
    req = _chain_req(
        [
            ChainSource(source_id="sA", dataset_id="pts"),
            ChainSource(source_id="sC", dataset_id="dims"),
        ],
        [
            ChainJoin(
                kind="aggregate_join",
                join_field_left="region",
                join_field_right="region",
                group_by_right=["label"],
                aggregates=[{"func": "count"}, {"func": "sum", "field": "students"}],
                left_source_id="sA",
                right_source_id="sC",
            )
        ],
    )
    v5 = _v5_result(adapters, req)
    v6 = _v6_result(adapters, req)
    assert _canon(v6["rows"]) == _canon(v5["rows"])
    assert v6["row_count"] == v5["row_count"] == 3


# ── 分页 / 取消 / 超时 ─────────────────────────────────────────────────────


def test_paged_scan_consumes_pages_with_checks():
    adapters = _diff_adapters()
    req = _chain_req(
        [
            ChainSource(source_id="sA", dataset_id="pts"),
            ChainSource(source_id="sC", dataset_id="dims"),
        ],
        [
            ChainJoin(
                kind="attribute_join",
                join_field_left="region",
                join_field_right="region",
                left_source_id="sA",
                right_source_id="sC",
            )
        ],
        limit=10_000,
    )
    v6 = _v6_result(adapters, req, page_size=2)
    assert v6["row_count"] == 4  # A×3 + none×1
    assert v6["pages_fetched"] >= 2, "分页探针应产生多页拉取"


def test_cancel_event_stops_execution():
    adapters = _diff_adapters()
    req = _chain_req(
        [
            ChainSource(source_id="sA", dataset_id="pts"),
            ChainSource(source_id="sC", dataset_id="dims"),
        ],
        [
            ChainJoin(
                kind="attribute_join",
                join_field_left="region",
                join_field_right="region",
                left_source_id="sA",
                right_source_id="sC",
            )
        ],
    )
    ev = threading.Event()
    ev.set()
    plan = enumerate_federation(build_enumeration_context(req))
    px = PhysicalExecutor(
        adapter_factory=lambda sid: adapters.get(sid),
        budget=req.budget,
        limit=req.limit,
        cancel_event=ev,
    )
    with pytest.raises(CancelledError):
        px.execute(plan.tree)
    assert px.trace.cancelled


def test_deadline_exceeded_is_typed_budget_error():
    adapters = _diff_adapters()
    from app.services.data_fabric.errors import QueryBudgetExceededError
    from app.services.data_fabric.query.models import ExecutionBudget

    req = _chain_req(
        [
            ChainSource(source_id="sA", dataset_id="pts"),
            ChainSource(source_id="sC", dataset_id="dims"),
        ],
        [
            ChainJoin(
                kind="attribute_join",
                join_field_left="region",
                join_field_right="region",
                left_source_id="sA",
                right_source_id="sC",
            )
        ],
    )
    req.budget = ExecutionBudget(
        deadline_s=1.0,
        max_rows=50_000,
        max_bytes=256 * 1024 * 1024,
        max_vertices=50_000_000,
        max_pages=200,
    )
    plan = enumerate_federation(build_enumeration_context(req))
    px = PhysicalExecutor(
        adapter_factory=lambda sid: adapters.get(sid),
        budget=req.budget,
        limit=req.limit,
    )
    px.token = CancelToken(deadline_s=-1.0)  # 已过期
    with pytest.raises(QueryBudgetExceededError):
        px.execute(plan.tree)


# ── 物理原语 ───────────────────────────────────────────────────────────────


def test_bloom_prefilter_keeps_false_positives_never_true_negatives():
    bloom = None
    from app.services.data_fabric.query.federated.bloom import build_bloom_from_rows

    left = [{"region": "A"}, {"region": "B"}]
    bloom, stats = build_bloom_from_rows(left, "region")
    right = [
        {"properties": {"region": "A"}},  # 应保留（真命中）
        {"properties": {"region": "C"}},  # 应过滤（键不在左）
        {"properties": {"other": 1}},  # 键缺失 → 保留（保守）
    ]
    kept, pre = bloom_prefilter(right, bloom, "region")
    assert len(kept) == 2
    assert pre["filtered"] == 1


def test_transform_rows_geometry_epsg4326_to_3857():
    rows = [
        {"geometry": {"type": "Point", "coordinates": [104.0, 30.0]}},
        {"geometry": None},
    ]
    out = transform_rows_geometry(rows, 4326, 3857)
    x, y = out[0]["geometry"]["coordinates"]
    assert 11_500_000 < x < 11_600_000 and 3_500_000 < y < 3_600_000
    assert out[1]["geometry"] is None


def test_transform_rejects_bad_crs_pair():
    from app.services.data_fabric.query.federation import FederatedQueryError

    with pytest.raises(FederatedQueryError):
        transform_rows_geometry([{"geometry": None}], 0, 999_999)
