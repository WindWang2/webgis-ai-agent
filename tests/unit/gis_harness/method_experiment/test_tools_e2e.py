"""方法实验端到端回归锁（method-experiment WP3-WP5）—— 真实 dispatch 全链。

真实 ``init_tools`` 注册表 + 真实内存 ``session_data_manager``：

- interpolation 实验四候选全链：plan → run（真实工具分发，同一折分配）
  → compare（可比组排名 + 证据推荐）→ adopt（回执记录）；
- 跨候选公平性：全部试验的 basis.scheme/folds 一致（同折分配）；
- 幂等重跑：reused 不重复 dispatch（内容指纹复用）；
- density 实验链（kde_fixed 带宽网格 → 守恒/边界可比）；
- 网络族 plan 预检拒绝（缺路网 ref）。
"""
from __future__ import annotations

import numpy as np
import pytest

from app.services.gis_harness.method_experiment.evaluate import (
    build_adoption_receipt,
    evaluate_experiment,
)
from app.services.gis_harness.method_experiment.plan import (
    MEXP_EXTRA_INPUT_REQUIRED,
    PlanCompileError,
    compile_experiment_plan,
)
from app.services.gis_harness.method_experiment.runner import ExperimentRunner
from app.services.gis_harness.method_experiment.state import new_state_from_plan
from app.services.session_data import session_data_manager

SESSION = "mexp-e2e-test-session"


@pytest.fixture
def registry():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry
    r = ToolRegistry()
    init_tools(r)
    return r


def _value_fc(n=48, seed=9):
    rng = np.random.default_rng(seed)
    lons = 104.0 + rng.uniform(-0.03, 0.03, n)
    lats = 30.6 + rng.uniform(-0.03, 0.03, n)
    mx = (lons - 104.0) * 111_320 * np.cos(np.deg2rad(30.6))
    my = (lats - 30.6) * 110_570
    vals = 100.0 + 0.004 * mx + 0.003 * my + rng.normal(0, 1.0, n)
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [float(lo), float(la)]},
         "properties": {"price": float(v)}}
        for lo, la, v in zip(lons, lats, vals)]}


def _points_fc(n=60, seed=4):
    rng = np.random.default_rng(seed)
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "geometry": {"type": "Point",
                      "coordinates": [float(104.0 + rng.uniform(-0.04, 0.04)),
                                      float(30.6 + rng.uniform(-0.04, 0.04))]},
         "properties": {}}
        for _ in range(n)]}


async def _dispatch_factory(registry):
    """生产 dispatch 形状的测试注入：ref 解引用 + 真实注册表工具实现。

    与 ToolDispatchService 的「ref: 参数自动解引用」缝对齐——工具函数
    本身不解引用（生产由 dispatch 层完成）。
    """
    _REF_ARGS = ("geojson", "network_layer", "facilities")

    async def _dispatch(tool_name, args, session_id):
        _dispatch.calls.append({"tool": tool_name, "args": dict(args)})
        spec = registry.get(tool_name)
        assert spec is not None, f"工具未注册: {tool_name}"
        func = spec.func if hasattr(spec, "func") else spec
        resolved = dict(args)
        for key in _REF_ARGS:
            v = resolved.get(key)
            if isinstance(v, str) and v.startswith("ref:"):
                payload = await session_data_manager.get(session_id, v)
                assert payload is not None, f"ref 无法解引用: {v}"
                resolved[key] = payload
        raw = func(**resolved)
        # 最小 ToolDispatchResult 仿形（status/raw_result 契约）
        class _R:
            def __init__(self, raw):
                self.status = "ok"
                self.raw_result = raw
                self.error_msg = None
        return _R(raw)
    _dispatch.calls = []
    return _dispatch


async def test_interpolation_experiment_full_loop(registry):
    ref = await session_data_manager.store(
        SESSION, _value_fc(), prefix="geojson")
    plan = compile_experiment_plan(
        family_id="interpolation", input_ref=ref, value_field="price",
        candidate_ids=["idw", "ordinary_kriging", "nearest_neighbor"],
        max_trials=10)
    state = new_state_from_plan(plan)

    dispatch = await _dispatch_factory(registry)
    persisted = []

    async def _persist(st):
        persisted.append(st.status)

    runner = ExperimentRunner(dispatch=dispatch, persist=_persist)
    state = await runner.run(SESSION, plan, state, max_trials_per_call=6)
    assert len(persisted) > 0

    counts = state.counts()
    # 5 试验 ≤ 窗口 6 → 一次窗口全部完成
    assert counts.get("completed", 0) == 5
    assert state.status == "completed"
    done = [t for t in state.trials.values()
            if t.state in ("completed", "reused")]
    assert len(done) >= 4

    # 公平性：全部完成试验共享同一折分配口径（同 scheme/folds）
    schemes = {t.metric_basis.get("cv_rmse", {}).get("scheme") for t in done}
    assert None not in schemes and len(schemes) == 1

    # 评价：可比组 + 推荐（线性面上 rbf/trend 应优于 idw 的噪声敏感性，
    # 这里只断言推荐结构合法 + 指标真实存在）
    ev = evaluate_experiment(plan, state)
    assert ev.status == state.status
    g = next((g for g in ev.comparable_groups
              if g.metric_id == "cv_rmse"), None)
    assert g is not None and g.n_evaluated >= 4
    recommendation = ev.recommendation
    assert recommendation is not None and recommendation.status == "recommended"
    assert recommendation.metric_evidence.get("cv_rmse") is not None

    # 幂等重跑：零新 dispatch（已完成试验保持 completed——重跑是 no-op）
    before = len(dispatch.calls)
    state2 = await runner.run(SESSION, plan, state, max_trials_per_call=6)
    assert len(dispatch.calls) == before       # 无新调用
    still_done = [t for t in state2.trials.values()
                  if t.state in ("completed", "reused")]
    assert len(still_done) == len(done)
    assert state2.status == "completed"

    # 采纳回执（真实完成试验）
    receipt, decline = build_adoption_receipt(
        plan, state2, trial_id=recommendation.trial_id,
        reason="oracle 检查后选择")
    assert receipt is not None and decline == ""


async def test_density_experiment_loop(registry):
    ref = await session_data_manager.store(
        SESSION, _points_fc(50), prefix="geojson")
    plan = compile_experiment_plan(
        family_id="density", input_ref=ref, value_field="",
        candidate_ids=["kde_fixed"], max_trials=6)
    state = new_state_from_plan(plan)
    dispatch = await _dispatch_factory(registry)
    async def _persist(st):
        return None
    runner = ExperimentRunner(dispatch=dispatch, persist=_persist)
    state = await runner.run(SESSION, plan, state)
    counts = state.counts()
    assert counts.get("completed", 0) == 3     # 带宽网格 3 候选变体
    ev = evaluate_experiment(plan, state)
    g = next((g for g in ev.comparable_groups
              if g.metric_id == "density_mass_error"), None)
    assert g is not None and g.n_evaluated == 3
    # 守恒误差全部在合理数值范围（非 0、非 NaN——真实计算）
    for r in g.rows:
        assert 0.0 <= r.value < 0.5


def test_network_plan_requires_extra_inputs():
    with pytest.raises(PlanCompileError) as ei:
        compile_experiment_plan(
            family_id="network_accessibility", input_ref="ref:facs",
            value_field="")
    assert ei.value.code == f"{MEXP_EXTRA_INPUT_REQUIRED}:network"


async def test_network_experiment_loop(registry):
    """网络情景实验：合成路网 + 设施，比较 travel_time 情景。"""
    # 简单格网路网（4×4 节点，双向边）
    nodes = [(104.0 + i * 0.01, 30.6 + j * 0.01)
             for i in range(4) for j in range(4)]
    lines = []
    for i in range(4):
        for j in range(4):
            a = i * 4 + j
            if i < 3:
                b = (i + 1) * 4 + j
                lines.append([nodes[a], nodes[b]])
            if j < 3:
                b = i * 4 + (j + 1)
                lines.append([nodes[a], nodes[b]])
    network = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {},
         "geometry": {"type": "LineString", "coordinates": line}}
        for line in lines]}
    facilities = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": f"f{i}"},
         "geometry": {"type": "Point",
                      "coordinates": [104.005 + (i % 2) * 0.02,
                                      30.605 + (i // 2) * 0.02]}}
        for i in range(4)]}
    net_ref = await session_data_manager.store(SESSION, network, prefix="geojson")
    fac_ref = await session_data_manager.store(SESSION, facilities, prefix="geojson")

    plan = compile_experiment_plan(
        family_id="network_accessibility", input_ref=fac_ref, value_field="",
        extra_inputs={"network": net_ref, "facilities": fac_ref},
        candidate_ids=["isochrone_walk"], max_trials=6)
    assert plan.trial_count() == 3             # 10/15/30 分钟情景
    state = new_state_from_plan(plan)
    dispatch = await _dispatch_factory(registry)
    async def _persist(st):
        return None
    runner = ExperimentRunner(dispatch=dispatch, persist=_persist)
    state = await runner.run(SESSION, plan, state)

    ev = evaluate_experiment(plan, state)
    g = next((g for g in ev.comparable_groups
              if g.metric_id == "network_coverage"), None)
    assert g is not None
    if g.n_evaluated >= 2:
        # 可达性随阈值单调不降（同一路网/设施的物理常识）
        vals = [(dict(r.params).get("travel_time"), r.value) for r in g.rows]
        vals.sort()
        assert vals[0][1] <= vals[-1][1]
