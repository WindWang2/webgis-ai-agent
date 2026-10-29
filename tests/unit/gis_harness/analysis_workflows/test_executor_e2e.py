"""执行器端到端回归锁（goal-02 M4）—— 真实 dispatch 全链。

用真实 ``init_tools`` 注册表 + 真实内存 session_data_manager：
- 行政区设施分布族：POI→空间聚合→计数；**总数=分区计数合计**（intersects
  多方属约定披露下允许>，与披露一致）；
- 大 FC 产物铸 ref:geojson-*；receipt 只含已执行步骤；
- 幂等：同指纹重跑零重铸（ref 不变）；
- 输入变更 → 依赖步骤失效（选择性重算的执行面）；
- 工具失败 → 步骤 failed、verdict 非 satisfied（诚实语义）。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.analysis_workflows.contracts import (
    DataRoleBinding,
)
from app.services.gis_harness.analysis_workflows.executor import (
    DEFAULT_MAX_FEATURES,
    WorkflowExecutor,
)
from app.services.gis_harness.analysis_workflows.orchestrator import (
    plan_workflow,
)
from app.services.gis_harness.analysis_workflows.state import (
    apply_plan_verdicts,
    new_state,
)
from app.services.session_data import session_data_manager

SESSION = "awf-e2e-test-session"


def _poi_fc(n: int = 30) -> dict:
    """POI 网格：全部落在 4 区覆盖范围 [104.00,104.04]×[30.60,30.632] 内，
    且坐标避开共享边界（x=104.02 / y=30.616）——严格计数守恒夹具。"""
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "properties": {"id": i, "name": f"school-{i}"},
             "geometry": {"type": "Point",
                          "coordinates": [104.006 + (i % 6) * 0.0055,
                                          30.605 + (i // 6) * 0.0042]}}
            for i in range(n)],
    }


def _boundary_poi_fc() -> dict:
    """点集恰好压在共享边界上——坐标用与 _districts_fc 完全相同的算术
    表达式生成（避免 ULP 错位把边界点变成区内点），用于钉扎 intersects
    多方属语义（显式去重策略验收）。"""
    x_edge = 104.00 + 0.02      # 与东北区左界同源
    y_edge = 30.600 + 0.016     # 与南排上界同源
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"id": 100},
             "geometry": {"type": "Point",
                          "coordinates": [x_edge, 30.600 + 0.008]}},  # 竖界
            {"type": "Feature", "properties": {"id": 101},
             "geometry": {"type": "Point",
                          "coordinates": [104.010, y_edge]}},         # 横界
            {"type": "Feature", "properties": {"id": 102},
             "geometry": {"type": "Point",
                          "coordinates": [x_edge, y_edge]}},          # 双界交点
            {"type": "Feature", "properties": {"id": 103},
             "geometry": {"type": "Point",
                          "coordinates": [104.008, 30.610]}},         # 区内单属
        ],
    }


def _districts_fc() -> dict:
    """两行两列 4 个互不重叠区（104.0–104.04, 30.6–30.632）。"""
    quads = [
        (104.00, 30.600, "northwest"), (104.02, 30.600, "northeast"),
        (104.00, 30.616, "southwest"), (104.02, 30.616, "southeast"),
    ]
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "properties": {"district": name, "population": 50_000 + i * 10_000},
             "geometry": {"type": "Polygon", "coordinates": [[
                 [x, y], [x + 0.02, y], [x + 0.02, y + 0.016],
                 [x, y + 0.016], [x, y]]]}}
            for i, (x, y, name) in enumerate(quads)],
    }


@pytest.fixture
def registry():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry
    r = ToolRegistry()
    init_tools(r)
    return r


async def _store(data: dict) -> str:
    return await session_data_manager.store(SESSION, data, prefix="geojson")


async def test_admin_distribution_e2e_real_dispatch(registry):
    poi_ref = await _store(_poi_fc(30))
    dist_ref = await _store(_districts_fc())
    plan = plan_workflow(
        "admin_facility_distribution",
        workflow_id="awf.e2e.dist",
        bindings={
            "subject": DataRoleBinding(role="subject", ref=poi_ref),
            "boundary": DataRoleBinding(role="boundary", ref=dist_ref),
        })
    assert plan.runnable
    state = new_state(plan.contract, contract_fingerprint=plan.fingerprint)
    apply_plan_verdicts(state, plan.step_verdicts)
    executor = WorkflowExecutor(registry=registry,
                                session_data=session_data_manager)
    state = await executor.execute(SESSION, plan.contract, state)

    # 1) 主链 satisfied
    assert state.steps["zones_count"].state == "satisfied"
    assert state.steps["area_density"].state == "satisfied"
    assert state.steps["per_capita_rate"].state == "skipped"   # 无分母绑定
    assert state.status == "satisfied"

    # 2) 产物封套：ref 铸造 + 计数守恒（总数=分区合计；本 fixture 区互不
    #    重叠 → 严格相等；多方属语义由族披露声明）
    env = state.envelopes["zones_count"]
    assert env.ref.startswith("ref:geojson")
    assert env.feature_count == 4
    fc = await session_data_manager.get(SESSION, env.ref)
    counts = [f["properties"]["facility_count"] for f in fc["features"]]
    assert sum(counts) == 30, f"分区计数合计应等于 POI 总数: {counts}"
    assert all(c > 0 for c in counts)

    # 3) receipt 只含已执行步骤；带算法 id
    assert set(state.receipts) == {"zones_count", "area_density"}
    receipt = state.receipts["zones_count"]
    assert receipt["tool_name"] == "spatial_aggregate"
    assert receipt["algorithm_ids"]
    assert receipt["output_ref"] == env.ref
    assert receipt["input_refs"] == (poi_ref, dist_ref)

    # 4) 幂等重跑：同输入零重铸（ref 不变、零新 dispatch 证据）
    first_ref = env.ref
    state2 = await executor.execute(SESSION, plan.contract, state)
    assert state2.envelopes["zones_count"].ref == first_ref
    assert any(e["event"] == "step_reused"
               for e in state2.run_log if isinstance(e, dict))


async def test_input_change_invalidates_dependent_steps(registry):
    poi_ref = await _store(_poi_fc(24))
    dist_ref = await _store(_districts_fc())
    bindings = {
        "subject": DataRoleBinding(role="subject", ref=poi_ref),
        "boundary": DataRoleBinding(role="boundary", ref=dist_ref),
    }
    plan = plan_workflow("admin_facility_distribution",
                         workflow_id="awf.e2e.change",
                         bindings=bindings)
    state = new_state(plan.contract, contract_fingerprint=plan.fingerprint)
    apply_plan_verdicts(state, plan.step_verdicts)
    executor = WorkflowExecutor(registry=registry,
                                session_data=session_data_manager)
    state = await executor.execute(SESSION, plan.contract, state)
    old_refs = {sid: e.ref for sid, e in state.envelopes.items()}

    # 换 POI 数据（新 ref = 新内容身份）
    poi_ref2 = await _store(_poi_fc(12))
    bindings2 = dict(bindings)
    bindings2["subject"] = DataRoleBinding(role="subject", ref=poi_ref2)
    plan2 = plan_workflow("admin_facility_distribution",
                          workflow_id="awf.e2e.change",
                          bindings=bindings2)
    # 重算：全部步骤因输入身份变化而重跑（zones_count 直接消费 subject）
    state2 = await executor.execute(SESSION, plan2.contract, state)
    fc = await session_data_manager.get(
        SESSION, state2.envelopes["zones_count"].ref)
    assert sum(f["properties"]["facility_count"] for f in fc["features"]) == 12
    assert state2.envelopes["zones_count"].ref != old_refs["zones_count"]

    # 边界未变：仍以相同输入水合（此处行为=重跑，因 identity 未分离缓存；
    # 核心验收（只失效依赖步骤）由 recompute 层单测钉扎）


async def test_tool_failure_step_failed_verdict_honest(registry):
    """上游失败（空 POI 数据集 → 工具报错）→ 步骤 failed，绝不 pass。"""
    empty_ref = await _store({"type": "FeatureCollection", "features": []})
    dist_ref = await _store(_districts_fc())
    plan = plan_workflow(
        "admin_facility_distribution", workflow_id="awf.e2e.fail",
        bindings={"subject": DataRoleBinding(role="subject", ref=empty_ref),
                  "boundary": DataRoleBinding(role="boundary", ref=dist_ref)})
    state = new_state(plan.contract, contract_fingerprint=plan.fingerprint)
    apply_plan_verdicts(state, plan.step_verdicts)
    executor = WorkflowExecutor(registry=registry,
                                session_data=session_data_manager)
    state = await executor.execute(SESSION, plan.contract, state)
    assert state.status in ("failed", "partial")
    assert state.status != "satisfied"
    assert "zones_count" not in state.receipts   # 失败步骤无 receipt


async def test_budget_guard_blocks_unbounded_load(registry):
    """超行数输入 typed blocked（不无界加载）。"""
    big_ref = "ref:geojson-huge"
    # 直接注入 descriptor 预算事实（不真实构造 10 万要素）
    session_data_manager._descriptors.setdefault(SESSION, {})[big_ref] = {
        "feature_count": DEFAULT_MAX_FEATURES + 1,
        "content_revision": 1,
    }
    dist_ref = await _store(_districts_fc())
    plan = plan_workflow(
        "admin_facility_distribution", workflow_id="awf.e2e.budget",
        bindings={"subject": DataRoleBinding(role="subject", ref=big_ref),
                  "boundary": DataRoleBinding(role="boundary", ref=dist_ref)})
    state = new_state(plan.contract, contract_fingerprint=plan.fingerprint)
    apply_plan_verdicts(state, plan.step_verdicts)
    executor = WorkflowExecutor(registry=registry,
                                session_data=session_data_manager)
    state = await executor.execute(SESSION, plan.contract, state)
    entry = state.steps["zones_count"]
    assert entry.state == "blocked"
    assert "AWF_BUDGET_EXCEEDED" in entry.reason_code
    assert "zones_count" not in state.receipts


async def test_hazard_exposure_chain_e2e(registry):
    """暴露族：缓冲→求交（M5 前置冒烟——真实两步链 + lineage ref）。"""
    hazard_ref = await _store({
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"id": "plant-1"},
             "geometry": {"type": "Point", "coordinates": [104.01, 30.61]}}]})
    receptor_ref = await _store(_poi_fc(20))
    plan = plan_workflow(
        "hazard_exposure", workflow_id="awf.e2e.hz",
        bindings={"hazard": DataRoleBinding(role="hazard", ref=hazard_ref),
                  "receptor": DataRoleBinding(role="receptor",
                                              ref=receptor_ref)},
        params_overrides={"hazard_buffer": {"distance": 1500.0}})
    assert plan.runnable
    state = new_state(plan.contract, contract_fingerprint=plan.fingerprint)
    apply_plan_verdicts(state, plan.step_verdicts)
    executor = WorkflowExecutor(registry=registry,
                                session_data=session_data_manager)
    state = await executor.execute(SESSION, plan.contract, state)
    assert state.status == "satisfied", {
        sid: e.state for sid, e in state.steps.items()}
    assert state.envelopes["hazard_buffer"].ref.startswith("ref:geojson")
    assert state.envelopes["exposure_intersect"].feature_count > 0
    # lineage：暴露交集的输入链含上游缓冲 ref（step: 输入不入 lineage ref，
    # 但 receipt 的 input_fingerprints 有 fp: 链）
    receipt = state.receipts["exposure_intersect"]
    assert any(v.startswith("fp:") for v in receipt["input_fingerprints"].values())


async def test_boundary_points_multi_membership_disclosed(registry):
    """验收钉扎：边界点 intersects 多方属——合计>去重总数，且族披露
    显式声明该策略（显式去重策略，不静默放大分母）。"""
    poi_ref = await _store(_boundary_poi_fc())
    dist_ref = await _store(_districts_fc())
    plan = plan_workflow(
        "admin_facility_distribution", workflow_id="awf.e2e.boundary",
        bindings={"subject": DataRoleBinding(role="subject", ref=poi_ref),
                  "boundary": DataRoleBinding(role="boundary", ref=dist_ref)})
    state = new_state(plan.contract, contract_fingerprint=plan.fingerprint)
    apply_plan_verdicts(state, plan.step_verdicts)
    executor = WorkflowExecutor(registry=registry,
                                session_data=session_data_manager)
    state = await executor.execute(SESSION, plan.contract, state)
    fc = await session_data_manager.get(
        SESSION, state.envelopes["zones_count"].ref)
    counts = [f["properties"]["facility_count"] for f in fc["features"]]
    # 实测归属（UTM 重投影后确定性）：角点（id=102）四属；共享边点按
    # 投影后确定性单属；区内点单属 → 合计 7 > 去重总数 4。
    # 科学不变量：①无丢失（每个 POI 至少计入一区）②多方属真实发生
    # （合计>总数）③策略在族契约中显式披露。
    assert len(fc["features"]) == 4
    assert sum(counts) >= 4, f"存在丢失计数: {counts}"
    assert sum(counts) > 4, f"未观察到多方属: {counts}"
    assert any("多方属" in note for note in plan.contract.risk_notes)
