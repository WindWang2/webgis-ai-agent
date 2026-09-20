"""计划级统一估算聚合测试（ADR-0204 D2：critical path / peak / retry / cache）。

对应 direction 7 必测面：
- parallel peak vs cumulative（内存峰值 vs 累计维求和）
- retry multiplier（wall/累计维乘、内存不乘）
- cache hit（可缓存维折扣、内存不折扣）
- LLM token cost 聚合、export DPI cost、unknown 保守地板
- optional/fallback 披露池、bounded 截断、确定性
"""
import pytest

from app.services.governor.contract import (
    DegradationSemantics,
    Dimension,
    DimValue,
    ResourceClass,
    ResourceEstimate,
    Subsystem,
)
from app.services.governor.estimation import estimate_for_tool
from app.services.governor.plan_aggregation import (
    AGGREGATE_VERSION,
    MAX_PLAN_NODES,
    PlanNode,
    PlanNodeKind,
    aggregate_plan,
    budget_violations,
)
from app.services.governor.render_budget import (
    estimate_render,
    render_input_from_spec_summary,
)


def _est(**overrides) -> ResourceEstimate:
    """轻量构造：wall/memory 秒级/字节级可调的 estimate。"""
    wall = overrides.pop("wall", (1.0, 2.0, 4.0))
    mem = overrides.pop("mem", (10 * 1024**2, 20 * 1024**2, 40 * 1024**2))
    extra = overrides.pop("dims", {})
    return ResourceEstimate(
        subsystem=Subsystem.TOOL_DISPATCH,
        dims={
            Dimension.WALL_TIME_S: DimValue.estimated(*wall, confidence=0.6),
            Dimension.MEMORY_BYTES: DimValue.estimated(*mem, confidence=0.6),
            **extra,
        },
    )


def test_sequential_wall_sums_memory_peaks():
    """串行链：wall 求和（critical path）；内存取 max（前序已释放）。"""
    agg = aggregate_plan([
        PlanNode(key="a", estimate=_est(wall=(1.0, 2.0, 4.0),
                                       mem=(10 * 1024**2, 20 * 1024**2,
                                            40 * 1024**2))),
        PlanNode(key="b", estimate=_est(wall=(2.0, 3.0, 6.0),
                                        mem=(100 * 1024**2, 128 * 1024**2,
                                             256 * 1024**2))),
    ])
    wall = agg.estimate.dim(Dimension.WALL_TIME_S)
    mem = agg.estimate.dim(Dimension.MEMORY_BYTES)
    assert wall.expected == pytest.approx(5.0)
    assert wall.max == pytest.approx(10.0)
    assert mem.expected == pytest.approx(128 * 1024**2)   # max，不是 sum
    assert agg.critical_path == ["a", "b"]
    assert agg.estimate.source == AGGREGATE_VERSION


def test_parallel_wall_max_memory_peaks_sum():
    """并行组：wall 取 max；live 内存峰值成员求和（同时在驻）。"""
    agg = aggregate_plan([
        PlanNode(key="a", kind=PlanNodeKind.PARALLEL,
                 estimate=_est(wall=(1.0, 2.0, 4.0),
                               mem=(16 * 1024**2, 16 * 1024**2,
                                    16 * 1024**2))),
        PlanNode(key="b", kind=PlanNodeKind.PARALLEL,
                 estimate=_est(wall=(4.0, 8.0, 16.0),
                               mem=(32 * 1024**2, 32 * 1024**2,
                                    32 * 1024**2))),
        PlanNode(key="c", kind=PlanNodeKind.PARALLEL,
                 estimate=_est(wall=(3.0, 4.0, 5.0),
                               mem=(64 * 1024**2, 64 * 1024**2,
                                    64 * 1024**2))),
        PlanNode(key="d",
                 estimate=_est(wall=(2.0, 3.0, 6.0),
                               mem=(8 * 1024**2, 8 * 1024**2,
                                    8 * 1024**2))),
    ])
    wall = agg.estimate.dim(Dimension.WALL_TIME_S)
    mem = agg.estimate.dim(Dimension.MEMORY_BYTES)
    # run1 = [a,b,c] 并行：wall max=8；run2 = [d] 串行：+3 → 11
    assert wall.expected == pytest.approx(11.0)
    # 峰值 = max(并行组 sum=112MiB, 串行组 max=8MiB)
    assert mem.expected == pytest.approx(112 * 1024**2)
    assert agg.parallel_peak_memory_bytes == pytest.approx(112 * 1024**2)
    # 并行组 critical path 只留 argmax 成员
    assert agg.critical_path == ["b", "d"]


def test_retry_multiplier_wall_and_cumulative_not_memory():
    """retry 乘数：wall/累计维 ×attempts；内存不乘（重试串行）。"""
    agg = aggregate_plan([
        PlanNode(key="a", estimate=_est(wall=(1.0, 10.0, 20.0)),
                 expected_attempts=3),
    ])
    wall = agg.estimate.dim(Dimension.WALL_TIME_S)
    mem = agg.estimate.dim(Dimension.MEMORY_BYTES)
    assert wall.expected == pytest.approx(30.0)
    assert wall.max == pytest.approx(60.0)
    assert mem.expected == pytest.approx(20 * 1024**2)   # 原值，不乘 3
    assert agg.retry_tail_s == pytest.approx(20.0)       # (3-1)×10


def test_cache_hit_discounts_cached_dims_only():
    """cache 命中：可缓存维 ×(1-p)；内存照常计（载入产物仍要驻留）。"""
    net = DimValue.estimated(1e6, 2e6, 4e6, confidence=0.6)
    agg = aggregate_plan([
        PlanNode(key="a", estimate=_est(wall=(1.0, 10.0, 20.0),
                                        dims={Dimension.NETWORK_BYTES: net}),
                 cache_read_probability=0.75),
    ])
    wall = agg.estimate.dim(Dimension.WALL_TIME_S)
    net_dim = agg.estimate.dim(Dimension.NETWORK_BYTES)
    mem = agg.estimate.dim(Dimension.MEMORY_BYTES)
    assert wall.expected == pytest.approx(2.5)
    assert net_dim.expected == pytest.approx(0.5e6)
    assert mem.expected == pytest.approx(20 * 1024**2)
    assert agg.max_node_cache_hit == pytest.approx(0.75)


def test_cumulative_llm_tokens_and_cost_sum():
    """LLM token / cost 维全路径累计（不参与 peak 语义）。"""
    toks = DimValue.estimated(100.0, 500.0, 900.0, confidence=0.7)
    cost = DimValue.estimated(0.001, 0.002, 0.004, confidence=0.4)
    agg = aggregate_plan([
        PlanNode(key="a", estimate=_est(dims={
            Dimension.CONTEXT_TOKENS: toks,
            Dimension.ESTIMATED_LLM_COST: cost})),
        PlanNode(key="b", estimate=_est(dims={
            Dimension.CONTEXT_TOKENS: toks,
            Dimension.ESTIMATED_LLM_COST: cost})),
    ])
    assert agg.estimate.dim(Dimension.CONTEXT_TOKENS).expected == \
        pytest.approx(1000.0)
    assert agg.estimate.dim(Dimension.ESTIMATED_LLM_COST).expected == \
        pytest.approx(0.004)


def test_unknown_dim_charges_conservative_floor_with_disclosure():
    """unknown 维按保守地板计费（unknown≠0）+ floor_charged_dims 披露。"""
    est = _est()
    est = est.with_dim(Dimension.GPU_MEMORY_BYTES,
                       DimValue.unknown("vram not declared"))
    agg = aggregate_plan([PlanNode(key="a", estimate=est)])
    gpu = agg.estimate.dim(Dimension.GPU_MEMORY_BYTES)
    # 地板 1GiB 进入聚合数值（source 披露）
    assert gpu.expected == pytest.approx(1 * 1024**3)
    assert "gpu_memory_bytes" in agg.floor_charged_dims


def test_optional_and_fallback_pools_excluded():
    """optional/fallback 不进主聚合（optional 增量、fallback 触发才计费）。"""
    agg = aggregate_plan([
        PlanNode(key="a", estimate=_est()),
        PlanNode(key="opt", kind=PlanNodeKind.OPTIONAL,
                 estimate=_est(wall=(100.0, 200.0, 400.0))),
        PlanNode(key="fb", kind=PlanNodeKind.FALLBACK,
                 estimate=_est(wall=(50.0, 60.0, 70.0))),
    ])
    wall = agg.estimate.dim(Dimension.WALL_TIME_S)
    assert wall.expected == pytest.approx(2.0)   # 只有 a
    assert [e["key"] for e in agg.optional_pool] == ["opt"]
    assert [e["key"] for e in agg.fallback_pool] == ["fb"]


def test_worst_semantics_disclosed():
    """降级语义候选 → 全计划最差语义披露（不偷偷改语义）。"""
    agg = aggregate_plan([
        PlanNode(key="a", estimate=_est()),
        PlanNode(key="b", estimate=_est(),
                 semantics=DegradationSemantics.APPROXIMATE),
        PlanNode(key="c", estimate=_est(),
                 semantics=DegradationSemantics.COMPARABLE),
    ])
    assert agg.worst_semantics == "approximate"


def test_bounded_truncation_disclosed():
    nodes = [PlanNode(key=f"n{i}", estimate=_est())
             for i in range(MAX_PLAN_NODES + 5)]
    agg = aggregate_plan(nodes)
    assert agg.truncated is True
    assert len(agg.critical_path) == MAX_PLAN_NODES


def test_export_dpi_cost_in_aggregation():
    """export DPI 平方律进入计划成本（300dpi ≫ 屏幕 96dpi）。"""
    screen = estimate_render(render_input_from_spec_summary({
        "layers": 3, "features": 2000, "width": 2000, "height": 1500}))
    publication = estimate_render(render_input_from_spec_summary({
        "layers": 3, "features": 2000, "width": 2000, "height": 1500,
        "dpi": 300}))
    assert (publication.dim(Dimension.RENDER_WORK_UNITS).expected
            > screen.dim(Dimension.RENDER_WORK_UNITS).expected * 8)
    agg = aggregate_plan([PlanNode(key="export", estimate=publication)])
    assert (agg.estimate.dim(Dimension.RENDER_WORK_UNITS).expected
            == pytest.approx(
                publication.dim(Dimension.RENDER_WORK_UNITS).expected))


def test_budget_violations_reports_overages():
    agg_est = _est(wall=(1.0, 120.0, 200.0))
    viols = budget_violations(agg_est, {Dimension.WALL_TIME_S: 60.0,
                                        Dimension.MEMORY_BYTES: 1e12})
    assert len(viols) == 1
    assert viols[0].startswith("wall_time_s:")


def test_aggregate_is_deterministic():
    nodes = [PlanNode(key="a", estimate=_est()),
             PlanNode(key="b", kind=PlanNodeKind.PARALLEL, estimate=_est())]
    a = aggregate_plan(nodes)
    aggregate_plan(list(reversed(nodes)))  # 反向声明序是另一个计划，仅验证不炸
    # 同输入必同输出
    assert a.estimate.dim(Dimension.WALL_TIME_S).expected == \
        aggregate_plan(nodes).estimate.dim(Dimension.WALL_TIME_S).expected
    assert a.estimate.model_dump() == aggregate_plan(nodes).estimate.model_dump()


def test_resource_class_severity_not_string_order():
    """P1-1 回归：ResourceClass 聚合按档位序，不按字符串字典序。"""
    est = ResourceEstimate(
        subsystem=Subsystem.TOOL_DISPATCH,
        resource_class=ResourceClass.HEAVY,
        dims={Dimension.WALL_TIME_S: DimValue.estimated(1.0, 2.0, 4.0,
                                                        confidence=0.6)},
    )
    agg = aggregate_plan([PlanNode(key="h", estimate=est)])
    assert agg.estimate.resource_class.value == "heavy"


def test_tool_prior_tool_node_aggregation_end_to_end():
    """真实先验（estimate_for_tool）喂聚合 —— 桥与聚合的端到端一致性。"""
    e = estimate_for_tool("query_osm_boundary", tool_class="light")
    agg = aggregate_plan([PlanNode(key="q", estimate=e, expected_attempts=2)])
    wall = agg.estimate.dim(Dimension.WALL_TIME_S)
    assert wall.expected == pytest.approx(2 * e.dim(Dimension.WALL_TIME_S).expected)
