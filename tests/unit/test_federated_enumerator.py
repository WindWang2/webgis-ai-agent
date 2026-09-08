"""V6 join 枚举契约测试（ADR-0118 W4）：DP 子集枚举/bushy/剪枝/确定性/CRS。"""

import pytest

from app.services.data_fabric.query.federated.enumerator import (
    EnumerationContext,
    JoinEdge,
    SourceFacts,
    enumerate_federation,
)
from app.services.data_fabric.query.federation import FederatedQueryError
from app.services.data_fabric.query.predicates import predicate_from_dict


def _src(sid, rows=None, ndv=None, crs=None, server_reproj=False, extent=None):
    return SourceFacts(
        source_id=sid,
        dataset_id=f"ds_{sid}",
        estimated_rows=rows,
        column_ndv=ndv or {},
        crs=crs,
        server_reprojection=server_reproj,
        extent=extent,
        row_count=rows,
    )


def _edge(a, b, kind="attribute_join", **kw):
    return JoinEdge(left_source_id=a, right_source_id=b, kind=kind, **kw)


# ── 基础排序 ────────────────────────────────────────────────────────────────


def test_build_aware_order_prefers_small_build_sides():
    """右侧是物化（build）侧：成本模型应让大表作探针（链首），小表后置。"""
    ctx = EnumerationContext(
        sources=[
            _src("big", rows=1_000_000, ndv={"k": 500}),
            _src("small", rows=100, ndv={"k": 50}),
            _src("mid", rows=10_000, ndv={"k": 100}),
        ],
        joins=[
            _edge("small", "mid", join_field_left="k", join_field_right="k"),
            _edge("mid", "big", join_field_left="k", join_field_right="k"),
        ],
        limit=10_000,
    )
    out = enumerate_federation(ctx)
    ids = out.order
    assert len(ids) == 3
    assert ids[-1] == "small", "最小的源应作为最后的 build 侧"
    assert out.cost > 0
    assert out.tree is not None


def test_bushy_considered_when_graph_allows():
    # a-b, c-d, b-c： bushy ((a⋈b)⋈(c⋈d)) 是合法树
    ctx = EnumerationContext(
        sources=[
            _src("a", rows=900_000, ndv={"k": 1000}),
            _src("b", rows=900_000, ndv={"k": 1000}),
            _src("c", rows=800_000, ndv={"k": 900}),
            _src("d", rows=10, ndv={"k": 10}),
        ],
        joins=[
            _edge("a", "b", join_field_left="k", join_field_right="k"),
            _edge("b", "c", join_field_left="k", join_field_right="k"),
            _edge("c", "d", join_field_left="k", join_field_right="k"),
        ],
        limit=10_000,
    )
    out = enumerate_federation(ctx)
    texts = " | ".join(a.get("description", "") for a in out.alternatives)
    assert out.tree is not None
    # 枚举器如实披露考虑过的替代（含 bushy 形状描述）
    assert ("bushy" in texts) or ("left-deep" in texts)


def test_disconnected_graph_typed_error():
    ctx = EnumerationContext(
        sources=[_src("a", rows=10), _src("b", rows=10), _src("c", rows=10)],
        joins=[_edge("a", "b")],
        limit=100,
    )
    with pytest.raises(FederatedQueryError):
        enumerate_federation(ctx)


def test_positional_joins_identity_order_with_warning():
    ctx = EnumerationContext(
        sources=[_src("s0", rows=1_000_000), _src("s1", rows=100)],
        joins=[
            JoinEdge(
                left_source_id=None,
                right_source_id=None,
                kind="attribute_join",
                positional_index=0,
            )
        ],
        limit=100,
    )
    out = enumerate_federation(ctx)
    assert out.order == ["s0", "s1"]
    assert any("positional" in w for w in out.warnings)


# ── 确定性与界限 ────────────────────────────────────────────────────────────


def test_enumeration_deterministic():
    def build():
        return EnumerationContext(
            sources=[
                _src("a", rows=500, ndv={"k": 10}),
                _src("b", rows=300, ndv={"k": 8}),
                _src("c", rows=100, ndv={"k": 5}),
            ],
            joins=[
                _edge("a", "b", join_field_left="k", join_field_right="k"),
                _edge("b", "c", join_field_left="k", join_field_right="k"),
            ],
            limit=1000,
        )

    o1 = enumerate_federation(build())
    o2 = enumerate_federation(build())
    assert o1.cost == o2.cost
    assert o1.order == o2.order
    assert [a["name"] for a in o1.alternatives] == [a["name"] for a in o2.alternatives]


def test_alternatives_bounded():
    ctx = EnumerationContext(
        sources=[_src(s, rows=100) for s in ("a", "b", "c", "d")],
        joins=[_edge("a", "b"), _edge("b", "c"), _edge("c", "d")],
        limit=100,
    )
    out = enumerate_federation(ctx)
    assert len(out.alternatives) <= 8
    assert out.order, "4 源链必有展示序"


# ── CRS / 过滤感知 ─────────────────────────────────────────────────────────


def test_mixed_crs_spatial_join_records_transform():
    ctx = EnumerationContext(
        sources=[
            _src("a", rows=10, crs="EPSG:4326", extent=[0, 0, 1, 1]),
            _src(
                "b",
                rows=100,
                crs="EPSG:3857",
                server_reproj=True,
                extent=[0, 0, 100000, 100000],
            ),
        ],
        joins=[_edge("a", "b", kind="spatial_join", spatial_op="intersects")],
        limit=100,
    )
    out = enumerate_federation(ctx)
    assert out.crs_transforms, "混 CRS 空间跳必须产出变换决策"
    dec = out.crs_transforms[0]
    assert dec["placement"] == "server"
    assert "3857" in dec["reason"] or "4326" in dec["reason"]


def test_scan_filter_selectivity_shrinks_estimate():
    ctx_no_filter = EnumerationContext(
        sources=[_src("a", rows=10_000), _src("b", rows=10_000, ndv={"k": 100})],
        joins=[_edge("a", "b", join_field_left="k", join_field_right="k")],
        limit=100,
    )
    ctx_filter = EnumerationContext(
        sources=[
            SourceFacts(
                source_id="a",
                dataset_id="ds_a",
                estimated_rows=10_000,
                column_ndv={},
                where=predicate_from_dict({"op": "eq", "field": "x", "value": 1}),
                server_reprojection=False,
                extent=None,
                row_count=10_000,
            ),
            _src("b", rows=10_000, ndv={"k": 100}),
        ],
        joins=[_edge("a", "b", join_field_left="k", join_field_right="k")],
        limit=100,
    )
    out_no = enumerate_federation(ctx_no_filter)
    out_yes = enumerate_federation(ctx_filter)
    assert out_yes.cost < out_no.cost


def test_spatial_hop_uses_extent_overlap():
    disjoint = EnumerationContext(
        sources=[
            _src("a", rows=1000, crs="EPSG:4326", extent=[0, 0, 1, 1]),
            _src("b", rows=1000, crs="EPSG:4326", extent=[100, 100, 101, 101]),
        ],
        joins=[_edge("a", "b", kind="spatial_join", spatial_op="within")],
        limit=100,
    )
    overlapping = EnumerationContext(
        sources=[
            _src("a", rows=1000, crs="EPSG:4326", extent=[0, 0, 1, 1]),
            _src("b", rows=1000, crs="EPSG:4326", extent=[0, 0, 1, 1]),
        ],
        joins=[_edge("a", "b", kind="spatial_join", spatial_op="within")],
        limit=100,
    )
    out, out2 = enumerate_federation(disjoint), enumerate_federation(overlapping)
    assert out.cost < out2.cost
