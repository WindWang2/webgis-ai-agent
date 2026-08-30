"""Cost-aware Algorithm Resolution（ADR-0083）单元测试。

阈值全部有代码库出处（见 app/lib/gis/cost_model.py docstring）：
HEATMAP_MIN_POINTS=10 / INTERACTIVE_FEATURE_CAP=5k /
FETCH_FEATURE_CAP=20k / DATA_FABRIC_MAX_FEATURES=50k。
"""
import pytest

from app.lib.gis.algorithm_registry import AlgorithmDescriptor, AlgorithmRegistry
from app.lib.gis.algorithm_resolver import AlgorithmResolver
from app.lib.gis.cost_model import (
    DATA_FABRIC_MAX_FEATURES,
    FETCH_FEATURE_CAP,
    HEATMAP_MIN_POINTS,
    INTERACTIVE_FEATURE_CAP,
    infer_execution_policy,
    scale_tier,
    score_algorithm,
)


# ── ExecutionPolicy 推断 ─────────────────────────────────────────────


def test_policy_inference_ladder():
    # 显式 hint 直通
    assert infer_execution_policy(policy_hint="export_quality") == "export_quality"
    # 导出语境
    assert infer_execution_policy(feature_count=100, export=True) == "export_quality"
    # 超过前端渲染上限 → large_data
    assert infer_execution_policy(feature_count=FETCH_FEATURE_CAP + 1) == "large_data"
    assert infer_execution_policy(feature_count=DATA_FABRIC_MAX_FEATURES * 10) == "large_data"
    # 定量输出能力 → analysis_quality
    assert infer_execution_policy(feature_count=100, deterministic_output=True) == "analysis_quality"
    # 小数据 → interactive_fast；中数据 → balanced
    assert infer_execution_policy(feature_count=INTERACTIVE_FEATURE_CAP) == "interactive_fast"
    assert infer_execution_policy(feature_count=INTERACTIVE_FEATURE_CAP + 1) == "balanced"
    # 无画像事实 → balanced（未知 ≠ 激进）
    assert infer_execution_policy() == "balanced"


def test_scale_tiers():
    assert scale_tier(None) == "unknown"
    assert scale_tier(HEATMAP_MIN_POINTS - 1) == "insufficient"
    assert scale_tier(HEATMAP_MIN_POINTS) == "interactive"
    assert scale_tier(INTERACTIVE_FEATURE_CAP) == "interactive"
    assert scale_tier(FETCH_FEATURE_CAP) == "renderable"
    assert scale_tier(FETCH_FEATURE_CAP + 1) == "aggregate"
    assert scale_tier(DATA_FABRIC_MAX_FEATURES + 1) == "server_side"


# ── 成本打分 ─────────────────────────────────────────────────────────


def _algo(**kw):
    base = dict(
        id="x.test", name="x", capabilities=["point_profile"],
        tool_candidates=["spatial_stats"],
    )
    base.update(kw)
    return AlgorithmDescriptor(**base)


def test_interactive_policy_penalizes_io():
    io_heavy = _algo(id="x.io", io_cost="high", cpu_cost="low")
    cpu_heavy = _algo(id="x.cpu", io_cost="low", cpu_cost="high")
    s_io, _ = score_algorithm(io_heavy, policy="interactive_fast")
    s_cpu, _ = score_algorithm(cpu_heavy, policy="interactive_fast")
    # interactive_fast：io 权重 4 > cpu 权重 3 → io 重者更贵
    assert s_io > s_cpu


def test_large_data_prefers_server_offload():
    local = _algo(id="x.local", memory_cost="medium", preferred_execution_policy="THREAD")
    server = _algo(id="x.server", memory_cost="medium", preferred_execution_policy="CELERY")
    s_local, _ = score_algorithm(local, policy="large_data")
    s_server, bd = score_algorithm(server, policy="large_data")
    assert s_server < s_local
    assert "server=-3" in bd


def test_analysis_quality_penalizes_approximate():
    exact = _algo(id="x.exact")
    approx = _algo(id="x.approx", approximate=True)
    s_exact, _ = score_algorithm(exact, policy="analysis_quality")
    s_approx, _ = score_algorithm(approx, policy="analysis_quality")
    assert s_approx > s_exact


# ── Resolver 集成（Scenario C：大规模自动切换通道）───────────────────


@pytest.fixture
def resolver():
    return AlgorithmResolver()


def test_large_point_dataset_switches_off_native_rendering(resolver):
    """Scenario C：超过 FETCH_FEATURE_CAP 的点数据 —— 原生视觉热力被
    over_render_cap 拒绝，能力级 fallback 指向聚合通道 grid_binning。"""
    res = resolver.resolve(
        "density_surface",
        profile={"geometryTypes": ["Point"], "featureCount": 150_000},
        available_tools={"heatmap_data", "h3_binning"},
    )
    # 主能力诚实 unavailable（不能渲染 15 万点的原生热力）
    assert res.status == "unavailable"
    assert any("over_render_cap:density.visual.heatmap:150000>20000" in x
               for x in res.rejected)
    # 确定性降级建议：聚合通道
    assert res.fallback_candidates == ["grid_binning"]
    assert "capability_fallback_available:grid_binning" in res.reason


def test_renderable_scale_keeps_native_heatmap(resolver):
    """≤ FETCH_FEATURE_CAP：原生热力仍是正确选择（低成本低扰动）。"""
    res = resolver.resolve(
        "density_surface",
        profile={"geometryTypes": ["Point"], "featureCount": FETCH_FEATURE_CAP},
        available_tools={"heatmap_data"},
    )
    assert res.status == "resolved"
    assert res.algorithm == "density.visual.heatmap"


def test_boundary_is_exclusive(resolver):
    ok = resolver.resolve(
        "density_surface",
        profile={"geometryTypes": ["Point"], "featureCount": 20_000},
        available_tools={"heatmap_data"},
    )
    over = resolver.resolve(
        "density_surface",
        profile={"geometryTypes": ["Point"], "featureCount": 20_001},
        available_tools={"heatmap_data"},
    )
    assert ok.status == "resolved"
    assert over.status == "unavailable"


def test_fallback_cycle_is_guarded(resolver):
    """density_surface ⇄ grid_binning 双向 fallback 边 —— 环路由
    _visited 截断（grid_binning 在超限场景仍可解析，不死循环）。"""
    res = resolver.resolve(
        "grid_binning",
        profile={"geometryTypes": ["Point"], "featureCount": 150_000},
        available_tools={"h3_binning"},
    )
    assert res.status == "resolved"
    assert res.algorithm == "spatial.grid.h3"


def test_cost_ordering_breaks_priority_ties():
    """同 priority 竞争者按策略加权成本裁决（registry priority 是主序，
    兼容承诺不变；成本模型负责平局裁决 + policy/scale evidence）。"""
    algos = AlgorithmRegistry()
    algos.register(AlgorithmDescriptor(
        id="x.expensive.first", name="first", capabilities=["point_profile"],
        tool_candidates=["spatial_stats"], priority=50, io_cost="high",
    ))
    algos.register(AlgorithmDescriptor(
        id="x.cheap.second", name="second", capabilities=["point_profile"],
        tool_candidates=["spatial_stats"], priority=50, io_cost="low",
    ))
    r = AlgorithmResolver(algorithms=algos)
    res = r.resolve(
        "point_profile",
        profile={"geometryTypes": ["Point"], "featureCount": 100},
    )
    assert res.status == "resolved"
    assert res.algorithm == "x.cheap.second"
    # point_profile 是 deterministic 输出能力 → analysis_quality（推断序：
    # 导出 > 规模 > 定量 > 小数据）
    assert res.execution_policy == "analysis_quality"
    assert res.cost_score is not None
    assert "io=" in res.cost_breakdown
    assert "policy=analysis_quality" in res.reason


def test_declared_priority_still_dominates_cost():
    """不同 priority：主序不变（兼容承诺 —— 如 service_area 默认解析）。
    更贵但 priority 更优的候选仍胜出。"""
    algos = AlgorithmRegistry()
    algos.register(AlgorithmDescriptor(
        id="x.preferred.expensive", name="p", capabilities=["point_profile"],
        tool_candidates=["spatial_stats"], priority=10, io_cost="high",
    ))
    algos.register(AlgorithmDescriptor(
        id="x.cheap", name="c", capabilities=["point_profile"],
        tool_candidates=["spatial_stats"], priority=90, io_cost="low",
    ))
    r = AlgorithmResolver(algorithms=algos)
    res = r.resolve("point_profile")
    assert res.algorithm == "x.preferred.expensive"


def test_single_candidate_skips_cost_evidence(resolver):
    """单候选直通：cost evidence 留空（无竞争不表演）。"""
    res = resolver.resolve(
        "density_surface",
        profile={"geometryTypes": ["Point"], "featureCount": 100},
        available_tools={"heatmap_data"},
    )
    assert res.status == "resolved"
    assert res.cost_score is None
    assert res.cost_breakdown == ""


def test_resolution_is_deterministic(resolver):
    kwargs = dict(
        profile={"geometryTypes": ["Point"], "featureCount": 8_000},
        available_tools={"h3_binning", "fishnet_grid"},
    )
    a = resolver.resolve("grid_binning", **kwargs)
    b = resolver.resolve("grid_binning", **kwargs)
    assert a.algorithm == b.algorithm
    assert a.cost_score == b.cost_score
    assert a.reason == b.reason


# ── ADR-0088 P6：运行策略词表 + 跨前后端阈值 parity ───────────────────


def test_resolve_runtime_strategy_vocabulary():
    from app.lib.gis.cost_model import RUNTIME_STRATEGIES, resolve_runtime_strategy

    assert resolve_runtime_strategy(feature_count=None) == "frontend_native"
    assert resolve_runtime_strategy(feature_count=5_000) == "frontend_native"
    assert resolve_runtime_strategy(feature_count=20_000) == "frontend_native"
    assert resolve_runtime_strategy(feature_count=20_001) == "preaggregated"
    assert resolve_runtime_strategy(feature_count=50_000) == "preaggregated"
    assert resolve_runtime_strategy(feature_count=50_001) == "server_vector"
    # 栅格 artifact 走栅格通道（与 heatmap 通道现状一致）
    assert resolve_runtime_strategy(
        feature_count=100, artifact_type="raster_surface"
    ) == "server_raster"
    # 词表有限集合：每个返回值都在表内（防漂移）
    for fc in (None, 0, 4_999, 20_001, 150_000):
        assert resolve_runtime_strategy(feature_count=fc) in RUNTIME_STRATEGIES


def test_strategy_thresholds_share_cost_model_constants():
    """策略阈值与 ExecutionPolicy 推断同源（单一契约，不出现第二套数）。"""
    from app.lib.gis import cost_model as cm

    assert cm.resolve_runtime_strategy(feature_count=cm.FETCH_FEATURE_CAP) == "frontend_native"
    assert cm.resolve_runtime_strategy(feature_count=cm.FETCH_FEATURE_CAP + 1) == "preaggregated"
    # ExecutionPolicy 在同一断点切换 large_data
    assert cm.infer_execution_policy(feature_count=cm.FETCH_FEATURE_CAP) != "large_data"
    assert cm.infer_execution_policy(feature_count=cm.FETCH_FEATURE_CAP + 1) == "large_data"


def test_frontend_fetch_cap_parity_with_backend():
    """跨前后端 magic number parity：ref-source-resolver 的 FETCH_FEATURE_CAP
    必须与后端 cost_model.FETCH_FEATURE_CAP 相等（contract 漂移即测试红）。"""
    import re
    from pathlib import Path

    from app.lib.gis.cost_model import FETCH_FEATURE_CAP

    ts_path = (
        Path(__file__).resolve().parents[3]
        / "frontend" / "lib" / "mapspec" / "ref-source-resolver.ts"
    )
    assert ts_path.exists(), f"frontend resolver missing: {ts_path}"
    match = re.search(r"const FETCH_FEATURE_CAP = (\d+);", ts_path.read_text())
    assert match, "FETCH_FEATURE_CAP constant not found in ref-source-resolver.ts"
    assert int(match.group(1)) == FETCH_FEATURE_CAP
