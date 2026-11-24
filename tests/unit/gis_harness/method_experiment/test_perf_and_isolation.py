"""性能有界验证 + 会话隔离（method-experiment WP6）。

- 候选数/输入规模缩放：真实 dispatch 计数（不是墙钟神话——CI 环境抖动
  大，断言计数与数据体积这些确定性量，耗时只做宽松上限防倒退）；
- 缓存冷/热：同内容重跑零 dispatch（复用语义的性能兑现）；
- 载荷有界：评价投影/状态投影体积不随输入规模增长（LLM/SSE 预算）；
- 会话隔离：实验状态按 session 作用域，跨 session 不可读写。
"""
from __future__ import annotations

import time

import numpy as np
import pytest

from app.services.gis_harness.method_experiment.plan import (
    compile_experiment_plan,
)
from app.services.gis_harness.method_experiment.runner import ExperimentRunner
from app.services.gis_harness.method_experiment.state import new_state
from app.services.session_data import session_data_manager

SESSION_A = "mexp-perf-session-a"
SESSION_B = "mexp-perf-session-b"


@pytest.fixture
def registry():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry
    r = ToolRegistry()
    init_tools(r)
    return r


def _fc(n, seed=6):
    rng = np.random.default_rng(seed)
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "geometry": {"type": "Point",
                      "coordinates": [float(104.0 + rng.uniform(-0.03, 0.03)),
                                      float(30.6 + rng.uniform(-0.03, 0.03))]},
         "properties": {"v": float(rng.normal(0, 1))}}
        for _ in range(n)]}


async def _dispatch_factory(registry):
    _REF_ARGS = ("geojson", "network_layer", "facilities")

    async def _dispatch(tool_name, args, session_id):
        _dispatch.calls.append({"tool": tool_name})
        spec = registry.get(tool_name)
        assert spec is not None
        func = spec.func if hasattr(spec, "func") else spec
        resolved = dict(args)
        for key in _REF_ARGS:
            v = resolved.get(key)
            if isinstance(v, str) and v.startswith("ref:"):
                resolved[key] = await session_data_manager.get(session_id, v)
        class _R:
            status = "ok"
            error_msg = None
            def __init__(self, raw):
                self.raw_result = raw
        return _R(func(**resolved))
    _dispatch.calls = []
    return _dispatch


async def test_candidate_scaling_linear_dispatch_count(registry):
    """候选数翻倍 → dispatch 计数线性翻倍（无隐藏重算）。"""
    ref = await session_data_manager.store(SESSION_A, _fc(200), prefix="geojson")
    dispatch = await _dispatch_factory(registry)

    async def _persist(st):
        return None
    runner = ExperimentRunner(dispatch=dispatch, persist=_persist)

    plan_small = compile_experiment_plan(
        family_id="interpolation", input_ref=ref, value_field="v",
        candidate_ids=["idw"], max_trials=6)
    state_s = new_state(plan_small.to_bounded_summary())
    t0 = time.monotonic()
    state_s = await runner.run(SESSION_A, plan_small, state_s)
    dt_small = time.monotonic() - t0
    n_small = len(dispatch.calls)

    plan_big = compile_experiment_plan(
        family_id="interpolation", input_ref=ref, value_field="v",
        candidate_ids=["idw", "nearest_neighbor"], max_trials=6)
    state_b = new_state(plan_big.to_bounded_summary())
    t0 = time.monotonic()
    state_b = await runner.run(SESSION_A, plan_big, state_b)
    dt_big = time.monotonic() - t0
    n_big = len(dispatch.calls)

    assert n_small == 3 and n_big - n_small == 4   # 网格展开计数（3 idw + 1 nn）
    # 单试验耗时稳定：大实验不应出现超线性惩罚（宽松 6x 上限防环境抖动）
    assert dt_big < max(dt_small * 6, 15.0)
    assert state_s.status == "completed" and state_b.status == "completed"


async def test_warm_cache_zero_recompute(registry):
    """缓存热路径：同输入+同参数+同协议重跑零 dispatch（复用兑现）。"""
    ref = await session_data_manager.store(SESSION_A, _fc(150), prefix="geojson")
    dispatch = await _dispatch_factory(registry)

    async def _persist(st):
        return None
    runner = ExperimentRunner(dispatch=dispatch, persist=_persist)
    plan = compile_experiment_plan(
        family_id="interpolation", input_ref=ref, value_field="v",
        candidate_ids=["idw"], max_trials=6)
    state = new_state(plan.to_bounded_summary())
    state = await runner.run(SESSION_A, plan, state)
    cold_calls = len(dispatch.calls)
    assert cold_calls == 3

    t0 = time.monotonic()
    state = await runner.run(SESSION_A, plan, state)
    warm_dt = time.monotonic() - t0
    assert len(dispatch.calls) == cold_calls   # 零重算
    assert warm_dt < 1.0                       # 热路径近零成本


async def test_projection_payload_bounded_under_scale(registry):
    """载荷有界：输入规模 ×10，评价/状态投影体积不随之增长。"""
    from app.services.gis_harness.method_experiment.evaluate import (
        evaluate_experiment,
    )
    ref_small = await session_data_manager.store(SESSION_A, _fc(60), prefix="geojson")
    ref_big = await session_data_manager.store(SESSION_A, _fc(600), prefix="geojson")
    dispatch = await _dispatch_factory(registry)

    async def _persist(st):
        return None
    runner = ExperimentRunner(dispatch=dispatch, persist=_persist)

    sizes = {}
    for tag, ref in (("small", ref_small), ("big", ref_big)):
        plan = compile_experiment_plan(
            family_id="interpolation", input_ref=ref, value_field="v",
            candidate_ids=["idw"], max_trials=6)
        state = new_state(plan.to_bounded_summary())
        state = await runner.run(SESSION_A, plan, state)
        ev = evaluate_experiment(plan, state).to_bounded_dict()
        sizes[tag] = len(str(ev))
    # 投影体积与输入规模解耦（行数由候选数决定，不由数据量决定）
    assert sizes["big"] < sizes["small"] * 1.5


async def test_session_isolation(registry):
    """会话隔离：session B 看不到 session A 的实验状态与产物。"""
    ref_a = await session_data_manager.store(SESSION_A, _fc(30), prefix="geojson")
    from app.services.gis_harness.method_experiment.state import (
        state_alias,
    )
    # A 的 ref 在 B 的作用域不可读
    leaked = await session_data_manager.get(SESSION_B, ref_a)
    assert leaked is None
    # A 实验别名在 B 作用域不可解析
    alias = state_alias("mexp-perf-iso")
    ref_alias = await session_data_manager.store(SESSION_A, {"x": 1}, prefix="mexp-state")
    await session_data_manager.set_alias(SESSION_A, ref_alias, alias)
    resolved_b = await session_data_manager.resolve_alias(SESSION_B, alias)
    assert not resolved_b or resolved_b == alias   # B 作用域无该别名
