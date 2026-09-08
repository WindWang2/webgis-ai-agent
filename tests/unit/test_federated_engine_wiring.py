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
