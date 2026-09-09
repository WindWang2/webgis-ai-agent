"""V6 typed logical plan IR 契约测试（ADR-0118 W1）。

覆盖：chain → 树构建、canonical hash 确定性、语义敏感性、序列化往返、
全部节点形状（scan/filter/project/join/aggregate/sort/limit/reproject）。
"""

from app.services.data_fabric.query.federated.logical import (
    LogicalAggregate,
    LogicalFilter,
    LogicalJoin,
    LogicalLimit,
    LogicalProject,
    LogicalReproject,
    LogicalScan,
    LogicalSort,
    chain_to_logical,
    logical_from_dict,
)
from app.services.data_fabric.query.federation import (
    ChainJoin,
    ChainSource,
    FederatedChainRequest,
)


def _chain_req() -> FederatedChainRequest:
    return FederatedChainRequest(
        sources=[
            ChainSource(
                source_id="a", dataset_id="ds_a", estimated_rows=100, srs="EPSG:4326"
            ),
            ChainSource(
                source_id="b",
                dataset_id="ds_b",
                estimated_rows=50,
                where={"op": "eq", "field": "kind", "value": "road"},
            ),
            ChainSource(source_id="c", dataset_id="ds_c"),
        ],
        joins=[
            ChainJoin(
                kind="attribute_join",
                join_field_left="region",
                join_field_right="region",
                left_source_id="a",
                right_source_id="b",
            ),
            ChainJoin(
                kind="spatial_join",
                spatial_op="within",
                left_source_id="b",
                right_source_id="c",
            ),
        ],
        bbox=[103.0, 29.0, 107.0, 31.0],
        limit=500,
    )


# ── 树构建 ──────────────────────────────────────────────────────────────────


def test_chain_builds_left_deep_tree():
    tree = chain_to_logical(_chain_req())
    # limit(((a ⋈ b) ⋈ c))
    assert isinstance(tree, LogicalLimit)
    assert tree.limit == 500
    top = tree.input
    assert isinstance(top, LogicalJoin)
    assert top.join_kind == "spatial_join"
    inner = top.left
    assert isinstance(inner, LogicalJoin)
    assert inner.join_kind == "attribute_join"
    assert isinstance(inner.left, LogicalScan) and inner.left.source_id == "a"
    assert isinstance(inner.right, LogicalScan) and inner.right.source_id == "b"
    assert isinstance(top.right, LogicalScan) and top.right.source_id == "c"


def test_scan_carries_where_bbox_fields():
    req = FederatedChainRequest(
        sources=[
            ChainSource(
                source_id="a",
                dataset_id="ds_a",
                where={"op": "eq", "field": "kind", "value": "road"},
                fields=["kind", "region"],
            ),
            ChainSource(source_id="b", dataset_id="ds_b"),
        ],
        joins=[
            ChainJoin(
                kind="attribute_join",
                join_field_left="region",
                join_field_right="region",
            )
        ],
        bbox=[0.0, 0.0, 1.0, 1.0],
        limit=10,
    )
    tree = chain_to_logical(req)
    assert isinstance(tree, LogicalLimit) and tree.limit == 10
    join = tree.input
    left = join.left
    assert left.where is not None
    assert left.where.op == "eq"
    assert left.bbox == [0.0, 0.0, 1.0, 1.0]
    assert left.fields == ["kind", "region"]
    assert left.crs is None and join.right.crs is None


def test_scan_parses_where_dict_to_predicate_ast():
    req = FederatedChainRequest(
        sources=[
            ChainSource(
                source_id="a",
                dataset_id="ds_a",
                where={
                    "op": "and",
                    "args": [
                        {"op": "eq", "field": "x", "value": 1},
                        {"op": "eq", "field": "y", "value": 2},
                    ],
                },
            ),
            ChainSource(source_id="b", dataset_id="ds_b"),
        ],
        joins=[
            ChainJoin(kind="attribute_join", join_field_left="k", join_field_right="k")
        ],
    )
    tree = chain_to_logical(req)
    left = tree.input.left
    assert left.where is not None and left.where.op == "and"


def test_aggregate_join_node_carries_group_and_aggs():
    req = FederatedChainRequest(
        sources=[
            ChainSource(source_id="a", dataset_id="ds_a"),
            ChainSource(source_id="b", dataset_id="ds_b"),
        ],
        joins=[
            ChainJoin(
                kind="aggregate_join",
                join_field_left="region",
                join_field_right="district",
                group_by_right=["district"],
                aggregates=[{"func": "count"}, {"func": "sum", "field": "pop"}],
            )
        ],
    )
    tree = chain_to_logical(req)
    join = tree.input
    assert join.join_kind == "aggregate_join"
    assert join.group_by_right == ["district"]
    assert join.aggregates == [{"func": "count"}, {"func": "sum", "field": "pop"}]


# ── canonical hash ──────────────────────────────────────────────────────────


def test_plan_hash_deterministic():
    h1 = chain_to_logical(_chain_req()).plan_hash()
    h2 = chain_to_logical(_chain_req()).plan_hash()
    assert h1 == h2
    assert len(h1) == 16


def test_plan_hash_semantic_sensitive():
    req = _chain_req()
    h_base = chain_to_logical(req).plan_hash()
    req.limit = 100
    assert chain_to_logical(req).plan_hash() != h_base


def test_plan_hash_field_order_insensitive():
    req1 = FederatedChainRequest(
        sources=[
            ChainSource(source_id="a", dataset_id="ds_a", fields=["b", "a"]),
            ChainSource(source_id="b", dataset_id="ds_b"),
        ],
        joins=[
            ChainJoin(kind="attribute_join", join_field_left="k", join_field_right="k")
        ],
    )
    req2 = FederatedChainRequest(
        sources=[
            ChainSource(source_id="a", dataset_id="ds_a", fields=["a", "b"]),
            ChainSource(source_id="b", dataset_id="ds_b"),
        ],
        joins=[
            ChainJoin(kind="attribute_join", join_field_left="k", join_field_right="k")
        ],
    )
    assert chain_to_logical(req1).plan_hash() == chain_to_logical(req2).plan_hash()


def test_plan_hash_where_conjunct_order_insensitive():
    where1 = {
        "op": "and",
        "args": [
            {"op": "eq", "field": "x", "value": 1},
            {"op": "eq", "field": "y", "value": 2},
        ],
    }
    where2 = {
        "op": "and",
        "args": [
            {"op": "eq", "field": "y", "value": 2},
            {"op": "eq", "field": "x", "value": 1},
        ],
    }
    req1 = FederatedChainRequest(
        sources=[
            ChainSource(source_id="a", dataset_id="ds_a", where=where1),
            ChainSource(source_id="b", dataset_id="ds_b"),
        ],
        joins=[
            ChainJoin(kind="attribute_join", join_field_left="k", join_field_right="k")
        ],
    )
    req2 = FederatedChainRequest(
        sources=[
            ChainSource(source_id="a", dataset_id="ds_a", where=where2),
            ChainSource(source_id="b", dataset_id="ds_b"),
        ],
        joins=[
            ChainJoin(kind="attribute_join", join_field_left="k", join_field_right="k")
        ],
    )
    assert chain_to_logical(req1).plan_hash() == chain_to_logical(req2).plan_hash()


def test_plan_hash_different_join_kind():
    req = FederatedChainRequest(
        sources=[
            ChainSource(source_id="a", dataset_id="ds_a"),
            ChainSource(source_id="b", dataset_id="ds_b"),
        ],
        joins=[
            ChainJoin(kind="attribute_join", join_field_left="k", join_field_right="k")
        ],
    )
    h_attr = chain_to_logical(req).plan_hash()
    req.joins[0].kind = "spatial_join"
    req.joins[0].spatial_op = "intersects"
    assert chain_to_logical(req).plan_hash() != h_attr


# ── 序列化往返 ──────────────────────────────────────────────────────────────


def test_serialization_roundtrip_preserves_hash():
    tree = chain_to_logical(_chain_req())
    d = tree.model_dump(mode="json")
    tree2 = logical_from_dict(d)
    assert tree2 is not None
    assert tree2.plan_hash() == tree.plan_hash()


# ── 包装节点形状 ────────────────────────────────────────────────────────────


def test_wrapper_nodes_hash_shape():
    scan = LogicalScan(source_id="a", dataset_id="ds_a", fetch_limit=100)
    wrapped = LogicalLimit(
        limit=10,
        input=LogicalSort(
            order_by=[{"field": "x", "direction": "asc"}],
            input=LogicalAggregate(
                group_by=["g"],
                aggregates=[{"func": "count"}],
                input=LogicalFilter(
                    predicate={"op": "eq", "field": "x", "value": 1},
                    input=LogicalReproject(
                        from_crs="EPSG:4326",
                        to_crs="EPSG:3857",
                        placement="local",
                        input=LogicalProject(fields=["x", "g"], input=scan),
                    ),
                ),
            ),
        ),
    )
    assert wrapped.plan_hash()
    assert wrapped.canonical_dict()["input"]["input"]["kind"] == "aggregate"


def test_reproject_placement_in_hash():
    scan = LogicalScan(source_id="a", dataset_id="ds_a", fetch_limit=10)
    h_server = LogicalReproject(
        from_crs="EPSG:4326", to_crs="EPSG:3857", placement="server", input=scan
    ).plan_hash()
    scan2 = LogicalScan(source_id="a", dataset_id="ds_a", fetch_limit=10)
    h_local = LogicalReproject(
        from_crs="EPSG:4326", to_crs="EPSG:3857", placement="local", input=scan2
    ).plan_hash()
    assert h_server != h_local
