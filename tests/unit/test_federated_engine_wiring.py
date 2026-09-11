"""V6 引擎接线契约测试（ADR-0118 W10）：engine 分派/回退/typed 错误/工具形状。"""

import json

import pytest

from app.schemas.data_fabric_schema import QueryResult
from app.services.data_fabric.query.federation import (
    ChainJoin,
    ChainSource,
    FederatedChainRequest,
    FederatedExecutor,
    FederatedQueryError,
)


@pytest.fixture(autouse=True)
def _clean_engine_breaker():
    """隔离进程级 V6 熔断：前序用例崩溃记账不得污染本文件 engine=v6 断言。"""
    from app.services.data_fabric.fabric.engine_breaker import reset_engine_breaker

    reset_engine_breaker()
    yield
    reset_engine_breaker()



def _pt(x, y, **props):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [x, y]},
        "properties": props,
    }


class _Fake:
    def __init__(self, data):
        self._data = data

    def query(self, dataset_id, spec):
        feats = self._data[dataset_id]
        limit = spec.limit or 100
        offset = spec.offset or 0
        return QueryResult(
            dataset_id=dataset_id, features=feats[offset : offset + limit]
        )


PTS = [
    _pt(104.0, 30.0, region="A", name="p1"),
    _pt(105.0, 31.0, region="B", name="p2"),
]
DIMS = [
    {"properties": {"region": "A", "label": "alpha"}},
    {"properties": {"region": "B", "label": "beta"}},
]


def _adapters():
    a = _Fake({"pts": PTS, "dims": DIMS})
    return {"sA": a, "sC": a}


def _req(engine=None, **kw):
    sources = [
        ChainSource(source_id="sA", dataset_id="pts"),
        ChainSource(source_id="sC", dataset_id="dims"),
    ]
    joins = [
        ChainJoin(
            kind="attribute_join",
            join_field_left="region",
            join_field_right="region",
            left_source_id="sA",
            right_source_id="sC",
        )
    ]
    if engine:
        return FederatedChainRequest(sources=sources, joins=joins, engine=engine, **kw)
    return FederatedChainRequest(sources=sources, joins=joins, **kw)


def _canon(rows):
    return sorted(json.dumps(r, sort_keys=True, ensure_ascii=False) for r in rows)


# ── 默认路径位级不变 ───────────────────────────────────────────────────────


def test_default_engine_is_v5():
    req = _req()
    assert req.engine == "v5"
    res = FederatedExecutor(lambda sid: _adapters().get(sid)).execute_chain(req)
    assert "engine" not in res  # V5 结果形状无 engine 字段（位级不变）
    assert res["row_count"] == 2


# ── V6 引擎 ────────────────────────────────────────────────────────────────


def test_v6_engine_result_shape_and_parity():
    adapters = _adapters()
    v5 = FederatedExecutor(lambda sid: adapters.get(sid)).execute_chain(_req())
    v6 = FederatedExecutor(lambda sid: adapters.get(sid)).execute_chain(_req("v6"))
    assert v6["engine"] == "v6"
    assert v6["status"] == "success"
    assert _canon(v6["rows"]) == _canon(v5["rows"])
    assert v6["row_count"] == v5["row_count"] == 2
    # additive 披露字段
    assert isinstance(v6.get("explain_v6"), list) and v6["explain_v6"]
    assert "plan_hash" in "\n".join(v6["explain_v6"])
    assert v6.get("plans"), "V6 结果携带逐跳计划 dict（工具契约）"


def test_v6_engine_three_source_parity():
    labels = [{"properties": {"label": "alpha", "tag": "t1"}}]
    a = _Fake({"pts": PTS, "dims": DIMS, "labels": labels})
    adapters = {"sA": a, "sC": a, "sB": a}
    req = FederatedChainRequest(
        # 提示升序 == given 序（V5 cost 排序沿 given 序保序，不做连通性过滤）
        sources=[
            ChainSource(source_id="sA", dataset_id="pts", estimated_rows=1),
            ChainSource(source_id="sC", dataset_id="dims", estimated_rows=2),
            ChainSource(source_id="sB", dataset_id="labels", estimated_rows=3),
        ],
        joins=[
            ChainJoin(
                kind="attribute_join",
                join_field_left="region",
                join_field_right="region",
                left_source_id="sA",
                right_source_id="sC",
            ),
            ChainJoin(
                kind="attribute_join",
                join_field_left="label",
                join_field_right="label",
                left_source_id="sC",
                right_source_id="sB",
            ),
        ],
        limit=10_000,
        engine="v6",
    )
    v5_req = FederatedChainRequest(sources=req.sources, joins=req.joins, limit=10_000)
    v5 = FederatedExecutor(lambda sid: adapters.get(sid)).execute_chain(v5_req)
    v6 = FederatedExecutor(lambda sid: adapters.get(sid)).execute_chain(req)
    assert _canon(v6["rows"]) == _canon(v5["rows"])
    assert v6["engine"] == "v6"


# ── typed 错误契约一致 ─────────────────────────────────────────────────────


def test_typed_error_parity_both_engines():
    req = FederatedChainRequest(
        sources=[
            ChainSource(source_id="sA", dataset_id="pts"),
            ChainSource(source_id="sC", dataset_id="dims"),
        ],
        joins=[],  # 形状错误
        engine="v6",
    )
    with pytest.raises(FederatedQueryError):
        FederatedExecutor(lambda sid: _adapters().get(sid)).execute_chain(req)
    req_v5 = FederatedChainRequest(
        sources=[
            ChainSource(source_id="sA", dataset_id="pts"),
            ChainSource(source_id="sC", dataset_id="dims"),
        ],
        joins=[],
    )
    with pytest.raises(FederatedQueryError):
        FederatedExecutor(lambda sid: _adapters().get(sid)).execute_chain(req_v5)


# ── 非 typed 异常回退 V5 ───────────────────────────────────────────────────


def test_v6_fallback_on_untyped_error(monkeypatch):
    def _boom(req):
        raise RuntimeError("simulated V6 internal bug")

    monkeypatch.setattr(
        "app.services.data_fabric.query.federated.planner.plan_federation_v6",
        _boom,
    )
    res = FederatedExecutor(lambda sid: _adapters().get(sid)).execute_chain(_req("v6"))
    assert res["engine"] == "v5_fallback"
    assert any("V5 engine" in w for w in res["warnings"])
    assert res["row_count"] == 2


# ── 同源 server-side 首跳委托 ──────────────────────────────────────────────


def test_same_source_first_hop_delegates_to_v5():
    class _ServerAdapter(_Fake):
        def server_spatial_join(self, *a, **kw):
            return [{"joined": True}]

    adapters = {"sA": _ServerAdapter({"pts": PTS, "dims": DIMS})}
    req = FederatedChainRequest(
        sources=[
            ChainSource(source_id="sA", dataset_id="pts"),
            ChainSource(source_id="sA", dataset_id="dims"),
        ],
        joins=[
            ChainJoin(
                kind="spatial_join",
                spatial_op="within",
                left_source_id="sA",
                right_source_id="sA",
            )
        ],
        engine="v6",
    )
    res = FederatedExecutor(lambda sid: adapters.get(sid)).execute_chain(req)
    assert res["engine"] == "v5_server_first_hop"
    assert any("delegated" in w for w in res["warnings"])


# ── M-2（评审 R2）：生产入口差分 —— 混 CRS × derive_projection ──────────────


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


def test_production_entry_mixed_crs_with_default_derive_projection():
    """C-1 回归（评审 R2）：混 CRS 空间跳 + 属性跳 + 默认 derive_projection
    经生产分派 execute_chain(engine="v6") 必须给出正确行 —— 派生投影不得
    丢弃计划中的 LogicalReproject 节点。"""
    pts = [
        _pt(1.001, 1.001, name="p1", kind="a"),   # 变换后落在 D1 内
        _pt(5.0, 5.0, name="p2", kind="a"),
    ]
    polys_3857 = [
        _poly(111319.0, 111325.0, 111600.0, 111600.0, district="D1"),
    ]
    dims = [{"properties": {"district": "D1", "tag": "t1", "kind": "a"}}]
    data = {"pts": pts, "polys": polys_3857, "dims": dims}

    class _A:
        def query(self, dataset_id, spec):
            feats = data[dataset_id]
            limit = spec.limit or 100
            offset = spec.offset or 0
            return QueryResult(dataset_id=dataset_id, features=feats[offset : offset + limit])

    adapters = {sid: _A() for sid in ("g", "m", "d")}
    req = FederatedChainRequest(
        sources=[
            ChainSource(source_id="g", dataset_id="pts", srs="EPSG:4326"),
            ChainSource(source_id="m", dataset_id="polys", srs="EPSG:3857"),
            ChainSource(source_id="d", dataset_id="dims"),
        ],
        joins=[
            ChainJoin(kind="spatial_join", spatial_op="within",
                      left_source_id="g", right_source_id="m"),
            ChainJoin(kind="attribute_join", join_field_left="district",
                      join_field_right="district",
                      left_source_id="m", right_source_id="d"),
        ],
        limit=10_000,
        engine="v6",
    )
    res = FederatedExecutor(lambda sid: adapters.get(sid)).execute_chain(req)
    assert res["engine"] == "v6"
    assert res["row_count"] == 1, "混 CRS + 默认派生投影不得静默空结果"
    row = res["rows"][0]
    assert row["name"] == "p1"
    assert row["__right__"]["tag"] == "t1"


def test_production_entry_corpus_parity_engine_dispatch():
    """M-2（评审 R2）：W11 语料子集经生产分派（execute_chain）双引擎对齐。"""
    import json

    pts = [_pt(104.0 + i * 0.1, 30.0, region=f"R{i}", k=i) for i in range(8)]
    dims = [{"properties": {"region": f"R{i}", "label": f"L{i}"}} for i in range(8)]
    polys = [_poly(104.0, 30.0, 104.5, 30.5, district="R0")]

    def _canon(rows):
        return sorted(json.dumps(r, sort_keys=True, ensure_ascii=False) for r in rows)

    class _A:
        def __init__(self, d):
            self._d = d

        def query(self, dataset_id, spec):
            feats = self._d[dataset_id]
            limit = spec.limit or 100
            offset = spec.offset or 0
            return QueryResult(dataset_id=dataset_id, features=feats[offset : offset + limit])

    data = {"pts": pts, "dims": dims, "polys": polys}
    adapters = {sid: _A(data) for sid in ("sA", "sC", "sB")}
    base = dict(limit=10_000)
    reqs = [
        FederatedChainRequest(
            sources=[ChainSource(source_id="sA", dataset_id="pts"),
                     ChainSource(source_id="sC", dataset_id="dims")],
            joins=[ChainJoin(kind="attribute_join", join_field_left="region",
                             join_field_right="region",
                             left_source_id="sA", right_source_id="sC")],
            engine="v6", **base),
        FederatedChainRequest(
            sources=[ChainSource(source_id="sA", dataset_id="pts"),
                     ChainSource(source_id="sB", dataset_id="polys")],
            joins=[ChainJoin(kind="spatial_join", spatial_op="within",
                             left_source_id="sA", right_source_id="sB")],
            engine="v6", **base),
        FederatedChainRequest(
            sources=[ChainSource(source_id="sA", dataset_id="pts"),
                     ChainSource(source_id="sB", dataset_id="polys"),
                     ChainSource(source_id="sC", dataset_id="dims")],
            joins=[ChainJoin(kind="attribute_join", join_field_left="region",
                             join_field_right="district",
                             left_source_id="sA", right_source_id="sB"),
                   ChainJoin(kind="attribute_join", join_field_left="district",
                             join_field_right="region",
                             left_source_id="sB", right_source_id="sC")],
            engine="v6", **base),
    ]
    for req in reqs:
        v5 = FederatedExecutor(lambda sid: adapters.get(sid)).execute_chain(
            FederatedChainRequest(
                sources=req.sources, joins=req.joins, limit=req.limit))
        v6 = FederatedExecutor(lambda sid: adapters.get(sid)).execute_chain(req)
        assert _canon(v6["rows"]) == _canon(v5["rows"]), f"生产入口差分失败: {req.joins}"
        assert v6["engine"] == "v6"
