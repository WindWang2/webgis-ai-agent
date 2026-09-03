"""受控联邦查询测试（ADR-0094 Wave G）。

覆盖：计划校验、attribute join（哈希）、spatial join（STRtree 候选 + 精确
判定）、aggregate+join、预算超限 typed error、同源 server-side 优先回退。
"""
import pytest

from app.schemas.data_fabric_schema import QueryResult
from app.services.data_fabric.errors import QueryBudgetExceededError
from app.services.data_fabric.query.federation import (
    FEDERATION_BUDGET,
    FederatedExecutor,
    FederatedQueryRequest,
    aggregate_join_rows,
    attribute_join_local,
    plan_federated,
    spatial_join_local,
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
            "coordinates": [[[minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny]]],
        },
        "properties": props,
    }


SCHOOLS = [
    _pt(104.0, 30.0, name="s1", district="金牛", students=500),
    _pt(104.1, 30.1, name="s2", district="武侯", students=300),
    _pt(106.5, 29.5, name="s3", district="渝中", students=800),
    _pt(0.0, 0.0, name="far", district="none", students=1),
]

DISTRICTS = [
    _poly(103.9, 29.9, 104.2, 30.2, name="金牛"),
    _poly(104.0, 30.0, 104.3, 30.3, name="武侯"),
    _poly(106.0, 29.0, 107.0, 30.0, name="渝中"),
]


# ── 本地原语 ────────────────────────────────────────────────────────────────


def test_attribute_join_hash():
    left = [{"properties": {"district": "金牛", "v": 1}}, {"properties": {"district": "X"}}]
    right = [{"properties": {"name": "金牛", "pop": 100}}]
    rows = attribute_join_local(left, right, join_field_left="district", join_field_right="name")
    assert len(rows) == 1
    assert rows[0]["__right__"]["pop"] == 100


def test_attribute_join_numeric_normalization():
    left = [{"properties": {"id": 1}}]
    right = [{"properties": {"id": 1.0}}]
    rows = attribute_join_local(left, right, join_field_left="id", join_field_right="id")
    assert len(rows) == 1


def test_spatial_join_strtree_exact():
    rows = spatial_join_local(SCHOOLS, DISTRICTS, spatial_op="within")
    names = {r["name"] for r in rows}
    assert names == {"s1", "s2", "s3"}, "far 点（0,0）不得命中"
    # s2 在金牛/武侯交界 —— within 语义取第一个精确命中的多边形
    by_name = {r["name"]: r for r in rows}
    assert by_name["s3"]["__right__"]["name"] == "渝中"


def test_spatial_join_intersects():
    rows = spatial_join_local(SCHOOLS[:3], DISTRICTS[:1], spatial_op="intersects")
    assert {r["name"] for r in rows} == {"s1", "s2"}


def test_spatial_join_budget_guard():
    from app.services.data_fabric.query.execution import StreamingBudget

    budget = StreamingBudget(max_rows=2, max_bytes=10**9, max_vertices=10**9)
    with pytest.raises(QueryBudgetExceededError):
        spatial_join_local(SCHOOLS, DISTRICTS, spatial_op="within", budget=budget)


def test_aggregate_join_rows():
    joined = spatial_join_local(SCHOOLS, DISTRICTS, spatial_op="within")
    rows = aggregate_join_rows(
        joined,
        [{"func": "count"}, {"func": "sum", "field": "students"}, {"func": "avg", "field": "students"}],
        ["name"],
    )
    by_d = {r["name"]: r for r in rows}
    assert by_d["渝中"]["count"] == 1
    assert by_d["渝中"]["sum_students"] == 800
    # s1、s2 都落金牛框内（金牛先于武侯命中，首个精确命中即绑定）
    assert by_d["金牛"]["count"] == 2
    assert by_d["金牛"]["sum_students"] == 800


# ── 计划校验 ────────────────────────────────────────────────────────────────


def test_plan_requires_join_semantics():
    with pytest.raises(Exception):
        plan_federated(FederatedQueryRequest("a", "t1", "b", "t2"))
    with pytest.raises(Exception):
        plan_federated(FederatedQueryRequest("a", "t1", "b", "t2", spatial_op="touches"))
    with pytest.raises(Exception):
        plan_federated(FederatedQueryRequest(
            "a", "t1", "b", "t2", join_field_left="x", join_field_right="y",
            aggregates=[{"func": "count"}],  # aggregates 无 group_by
        ))


def test_plan_limit_over_budget():
    req = FederatedQueryRequest(
        "a", "t1", "b", "t2", join_field_left="x", join_field_right="y",
        limit=FEDERATION_BUDGET.max_rows + 1,
    )
    with pytest.raises(QueryBudgetExceededError):
        plan_federated(req)


# ── 执行器（fake adapters）────────────────────────────────────────────────


class _FakeAdapter:
    """按 dataset_id 返回固定 features 的假适配器（V2 契约）。"""

    def __init__(self, data):
        self._data = data
        self.calls = []

    def query(self, dataset_id, spec):
        self.calls.append((dataset_id, spec))
        feats = self._data[dataset_id]
        limit = spec.limit or 100
        offset = spec.offset or 0
        return QueryResult(dataset_id=dataset_id, features=feats[offset:offset + limit])


def test_executor_attribute_join():
    adapters = {
        "srcA": _FakeAdapter({"points": SCHOOLS}),
        "srcB": _FakeAdapter({"dims": [{"properties": {"name": "金牛", "pop": 100}}]}),
    }
    ex = FederatedExecutor(lambda sid: adapters.get(sid))
    req = FederatedQueryRequest(
        "srcA", "points", "srcB", "dims",
        join_field_left="district", join_field_right="name",
    )
    res = ex.execute(req)
    assert res["status"] == "success"
    assert res["row_count"] == 1
    assert res["rows"][0]["__right__"]["pop"] == 100
    assert res["plan"]["kind"] == "attribute_join"


def test_executor_spatial_join_and_budget():
    adapters = {
        "srcA": _FakeAdapter({"schools": SCHOOLS}),
        "srcB": _FakeAdapter({"districts": DISTRICTS}),
    }
    ex = FederatedExecutor(lambda sid: adapters.get(sid))
    req = FederatedQueryRequest(
        "srcA", "schools", "srcB", "districts",
        spatial_op="within",
        aggregates=[{"func": "count"}],
        group_by_right=["name"],
    )
    res = ex.execute(req)
    assert res["status"] == "success"
    assert res["strategy"] == "local_hash_or_strtree"
    got = {r["name"]: r["count"] for r in res["rows"]}
    assert got.get("渝中") == 1 and got.get("金牛") == 2 and "none" not in got


def test_executor_budget_exceeded_typed():
    # 左侧返回超预算行数 → typed QUERY_BUDGET_EXCEEDED
    many = [_pt(104.0, 30.0, name=f"s{i}") for i in range(50)]
    adapters = {
        "srcA": _FakeAdapter({"schools": many}),
        "srcB": _FakeAdapter({"districts": DISTRICTS}),
    }
    ex = FederatedExecutor(lambda sid: adapters.get(sid))
    from app.services.data_fabric.query.models import ExecutionBudget

    small = ExecutionBudget(max_rows=5, max_bytes=10**9, max_vertices=10**9, max_pages=2)
    req = FederatedQueryRequest(
        "srcA", "schools", "srcB", "districts", spatial_op="within", limit=5,
    )
    req.budget = small
    with pytest.raises(QueryBudgetExceededError):
        ex.execute(req)


def test_executor_missing_source_is_typed_error():
    ex = FederatedExecutor(lambda sid: None)
    req = FederatedQueryRequest(
        "nope", "t1", "srcB", "t2", join_field_left="a", join_field_right="b"
    )
    with pytest.raises(Exception, match="not connected"):
        ex.execute(req)


def test_executor_same_source_prefers_server_side():
    class _ServerJoinAdapter(_FakeAdapter):
        def server_spatial_join(self, points, polygons, *, join_op="within",
                                point_filter=None, polygon_filter=None,
                                group_by_polygon_field=None, limit=10000):
            self.server_joined = True
            return [{"group_key": "渝中", "count": 42}]

    adapter = _ServerJoinAdapter({"schools": SCHOOLS, "districts": DISTRICTS})
    ex = FederatedExecutor(lambda sid: adapter)
    req = FederatedQueryRequest(
        "pg", "schools", "pg", "districts", spatial_op="within",
    )
    req.aggregates = [{"func": "count"}]
    req.group_by_right = ["name"]
    res = ex.execute(req)
    assert res["strategy"] == "server_side"
    assert res["rows"] == [{"group_key": "渝中", "count": 42}]
    assert not adapter.calls, "本地 join 不得执行（server-side 已完成）"
