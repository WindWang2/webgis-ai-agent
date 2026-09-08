"""V6 Explain 契约测试（ADR-0118 W9）：确定性/树渲染/est vs actual。"""

from app.services.data_fabric.query.federated.enumerator import (
    EnumerationContext,
    JoinEdge,
    SourceFacts,
    enumerate_federation,
)
from app.services.data_fabric.query.federated.explain import (
    explain_v6_lines,
    render_tree,
)
from app.services.data_fabric.query.federated.planner import plan_federation_v6
from app.services.data_fabric.query.federation import (
    ChainJoin,
    ChainSource,
    FederatedChainRequest,
)
from app.services.data_fabric.query.models import ExecutionBudget


def _req():
    return FederatedChainRequest(
        sources=[
            ChainSource(source_id="sA", dataset_id="pts", estimated_rows=500),
            ChainSource(source_id="sC", dataset_id="dims", estimated_rows=100),
            ChainSource(source_id="sB", dataset_id="labels", estimated_rows=10),
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
        limit=1000,
        budget=ExecutionBudget(),
    )


def test_render_tree_contains_scans_and_joins():
    req = _req()
    plan = plan_federation_v6(req)
    lines = render_tree(plan.tree)
    text = "\n".join(lines)
    assert "join attribute_join" in text
    assert "scan" in text and "fetch_window" in text


def test_plan_hash_in_explain_and_deterministic():
    req = _req()
    plan = plan_federation_v6(req)
    l1 = explain_v6_lines(plan)
    l2 = explain_v6_lines(plan)
    assert l1 == l2
    assert any("plan_hash" in l for l in l1)


def test_explain_with_actual_side():
    req = _req()
    plan = plan_federation_v6(req)
    exec_result = {
        "row_count": 42,
        "joined_row_count": 42,
        "pages_fetched": 3,
        "per_source_rows": {"sA": 500, "sC": 100, "sB": 10},
        "hop_stats": [{"hop": 0, "kind": "attribute_join", "output_rows": 42}],
        "adaptive_observations": ["hop 0: cardinality deviated x4.20"],
        "replans_used": 0,
    }
    lines = explain_v6_lines(plan, exec_result=exec_result)
    text = "\n".join(lines)
    assert "actual:" in text
    assert "rows_returned: 42" in text
    assert "deviated" in text
    assert "replans_used: 0" in text


def test_explain_dry_run_has_no_actual():
    req = _req()
    plan = plan_federation_v6(req)
    lines = explain_v6_lines(plan)
    assert not any("actual:" in l for l in lines)
    assert any("estimated_cost" in l for l in lines)


def test_explain_includes_alternatives_and_warnings():
    ctx = EnumerationContext(
        sources=[
            SourceFacts(
                source_id="a",
                dataset_id="d1",
                estimated_rows=100,
                crs="EPSG:4326",
                extent=[0, 0, 1, 1],
            ),
            SourceFacts(
                source_id="b",
                dataset_id="d2",
                estimated_rows=100,
                crs="EPSG:3857",
                extent=[0, 0, 1, 1],
            ),
        ],
        joins=[
            JoinEdge(
                left_source_id="a",
                right_source_id="b",
                kind="spatial_join",
                spatial_op="within",
            )
        ],
        limit=100,
    )
    plan = enumerate_federation(ctx)
    lines = explain_v6_lines(plan, ctx)
    text = "\n".join(lines)
    assert "crs_transforms:" in text
    assert "EPSG:4326" in text and "EPSG:3857" in text
