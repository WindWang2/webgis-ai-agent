"""V6 下推决策契约测试（ADR-0118 W7）：fetch 窗口/聚合下推裁决/边界解释。"""

from app.services.data_fabric.query.federated.enumerator import (
    EnumerationContext,
    JoinEdge,
    SourceFacts,
)
from app.services.data_fabric.query.federated.planner import (
    aggregate_pushdown_verdict,
    apply_fetch_windows,
    derive_fetch_windows,
    pushdown_boundary_lines,
)
from app.services.data_fabric.query.federation import MAX_JOIN_CANDIDATES
from app.services.data_fabric.query.models import ExecutionBudget
from app.services.data_fabric.query.federated.logical import (
    LogicalJoin,
    LogicalLimit,
    LogicalScan,
)


def _ctx(rows_by_source=None, kinds=None):
    rows_by_source = rows_by_source or {"a": 100, "b": 200, "c": 50}
    kinds = kinds or ["attribute_join", "attribute_join"]
    ctx = EnumerationContext(
        sources=[
            SourceFacts(source_id=s, dataset_id=f"ds_{s}", estimated_rows=r)
            for s, r in rows_by_source.items()
        ],
        joins=[
            JoinEdge(
                left_source_id="a",
                right_source_id="b",
                kind=kinds[0],
                join_field_left="k",
                join_field_right="k",
            ),
            JoinEdge(
                left_source_id="b",
                right_source_id="c",
                kind=kinds[1],
                join_field_left="k",
                join_field_right="k",
            ),
        ],
        limit=1000,
    )
    return ctx


# ── fetch 窗口 ─────────────────────────────────────────────────────────────


def test_build_side_window_widens_to_join_cap():
    ctx = _ctx()
    budget = ExecutionBudget()
    windows = derive_fetch_windows(ctx, budget)
    # b、c 都是 build 侧 → 窗口放宽到 max(limit, join cap)
    assert windows["b"] == min(budget.max_rows, MAX_JOIN_CANDIDATES)
    assert windows["c"] == min(budget.max_rows, MAX_JOIN_CANDIDATES)
    assert "a" not in windows, "probe 链首保持 req.limit（V5 链语义）"


def test_apply_fetch_windows_rewrites_scans_only():
    ctx = _ctx()
    budget = ExecutionBudget()
    windows = derive_fetch_windows(ctx, budget)
    # 构造树：limit(scan_a ⋈ scan_b)（单跳形状）
    scan_a = LogicalScan(source_id="a", dataset_id="ds_a", fetch_limit=1000)
    scan_b = LogicalScan(source_id="b", dataset_id="ds_b", fetch_limit=1000)
    tree = LogicalLimit(
        limit=1000,
        input=LogicalJoin(
            join_kind="attribute_join",
            left=scan_a,
            right=scan_b,
            join_field_left="k",
            join_field_right="k",
        ),
    )
    out = apply_fetch_windows(tree, windows)
    new_a = out.input.left
    new_b = out.input.right
    assert new_a.fetch_limit == 1000, "a 是 probe 侧：窗口不变"
    assert new_b.fetch_limit == windows["b"], "b 是 build 侧：窗口放宽"
    h_before = tree.plan_hash()
    h_after = out.plan_hash()
    assert h_before != h_after, "窗口是执行决策，进计划哈希"


# ── 聚合下推裁决 ───────────────────────────────────────────────────────────


def test_aggregate_pushdown_rejected_with_reason():
    ctx = _ctx(kinds=["attribute_join", "aggregate_join"])
    verdict = aggregate_pushdown_verdict(ctx)
    assert verdict is not None
    assert verdict["feasible"] is False
    assert "semantically unsafe" in verdict["rejected_reason"]


def test_no_aggregate_no_verdict():
    ctx = _ctx()
    assert aggregate_pushdown_verdict(ctx) is None


# ── 下推边界解释 ───────────────────────────────────────────────────────────


def test_boundary_lines_without_caps_are_honest():
    ctx = _ctx()
    lines = pushdown_boundary_lines(ctx)
    assert len(lines) == 3
    assert all("not probed" in ln for ln in lines)


def test_boundary_lines_with_caps():
    from app.services.data_fabric.query.models import AdapterCapabilitiesV2

    ctx = _ctx()
    ctx.sources[0].caps = AdapterCapabilitiesV2(
        source_type="postgis",
        bbox_pushdown=True,
        filter_pushdown=True,
        projection_pushdown=True,
        temporal_filter=True,
        aggregation=True,
    )
    ctx.sources[1].caps = AdapterCapabilitiesV2(
        source_type="stac",
        bbox_pushdown=True,
        filter_pushdown=False,
        temporal_filter=True,
    )
    lines = pushdown_boundary_lines(ctx)
    assert "pushdown bbox, filter, projection, temporal, aggregation" in lines[0]
    assert "local filter" in lines[1], "stac 不支持属性下推 → 本地"
