"""R1 独立评审清偿测试（FIX-FIRST）—— F-1/F-2/F-3/F-4/F-7/F-8。

- F-2（P2 科学缺陷）：分母字段解析/校验只认 role:denominator——
  population/subject 的 qualifier 不再劫持分母列或造成假 skip；
- F-1（P2）：trim 桩几何（无 coordinates）不进 routes FC；
- F-3（P2）：service_accessibility 族默认 exclude_unreachable=True 被钉住；
- F-4（P2）：GIS_AWF_PRODUCT_GATE=0 回退契约（开关关=门零发现）；
- F-7（P2）：resample→zonal 直通链（data.output_path 检出/绝对化）；
- F-8（P2）：recompute 工具真调用落 state=stale 并重跑回 satisfied。
"""
from __future__ import annotations

import numpy as np
import pytest

from app.services.gis_harness.analysis_workflows.contracts import (
    AWF_DENOMINATOR_MISSING,
    DataRoleBinding,
)
from app.services.gis_harness.analysis_workflows.executor import (
    WorkflowExecutor,
    _extract_fc,
)
from app.services.gis_harness.analysis_workflows.families import build_contract
from app.services.gis_harness.analysis_workflows.orchestrator import (
    plan_workflow,
)
from app.services.gis_harness.analysis_workflows.state import (
    apply_plan_verdicts,
    new_state,
)
from app.services.session_data import session_data_manager

SESSION = "awf-r1fix-test-session"


@pytest.fixture
def registry():
    from app.agent_pi_bridge import set_tool_registry
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry
    r = ToolRegistry()
    init_tools(r)
    set_tool_registry(r)   # run/recompute 工具的 _production_dispatch 需全局注入
    return r


# ── F-2：分母字段解析只认 role:denominator ───────────────────────────────

def test_f2_population_qualifier_does_not_hijack_or_skip_grid_rate():
    """population 角色如实声明 fields=['pop'] → grid_rate 不再假 skip；
    population.field='pop' → grid_rate denominator 不被改写（count 保持）。"""
    plan = plan_workflow(
        "statistical_zones",
        workflow_id="awf.f2.grid",
        bindings={
            "subject": DataRoleBinding(role="subject", ref="ref:poi"),
            "population": DataRoleBinding(
                role="population", ref="ref:pop",
                qualifier={"field": "pop", "fields": ["pop"]}),
        })
    grid_rate = next(s for s in plan.contract.steps
                     if s.step_id == "grid_rate")
    verdict = next(v for v in plan.step_verdicts
                   if v.step_id == "grid_rate")
    assert grid_rate.step_params["denominator"] == "count"   # 未被劫持
    assert verdict.verdict != "skipped" or (
        verdict.reason_code != AWF_DENOMINATOR_MISSING)       # 无假 skip


def test_f2_subject_field_does_not_hijack_per_capita():
    plan = plan_workflow(
        "admin_facility_distribution",
        workflow_id="awf.f2.capita",
        bindings={
            "subject": DataRoleBinding(
                role="subject", ref="ref:poi",
                qualifier={"field": "facility_type"}),
            "boundary": DataRoleBinding(role="boundary", ref="ref:zones"),
        })
    per_capita = next(s for s in plan.contract.steps
                      if s.step_id == "per_capita_rate")
    assert per_capita.step_params["denominator"] == "population"


def test_f2_denominator_role_still_authoritative():
    """正路不变：denominator.field 注入 + fields 缺列 typed skipped。"""
    plan = plan_workflow(
        "admin_facility_distribution",
        workflow_id="awf.f2.positive",
        bindings={
            "subject": DataRoleBinding(role="subject", ref="ref:poi"),
            "boundary": DataRoleBinding(role="boundary", ref="ref:zones"),
            "denominator": DataRoleBinding(
                role="denominator", ref="ref:zones",
                qualifier={"field": "pop_2020",
                           "fields": ["pop_2020"]}),
        })
    per_capita = next(s for s in plan.contract.steps
                      if s.step_id == "per_capita_rate")
    assert per_capita.step_params["denominator"] == "pop_2020"
    verdict = next(v for v in plan.step_verdicts
                   if v.step_id == "per_capita_rate")
    assert verdict.verdict == "ready"


# ── F-1：trim 桩几何不进 routes FC ───────────────────────────────────────

def test_f1_trimmed_stub_geometry_not_collected_into_fc():
    result = {
        "status": "success",
        "routes": [
            {"route_id": "r1", "total_cost": 1.0,
             "geometry": {"type": "LineString",
                          "coordinates": [[104.0, 30.6], [104.01, 30.61]]}},
            # trim_network_result 超预算时的摘要桩（无 coordinates）
            {"route_id": "r2", "total_cost": 2.0,
             "geometry": {"type": "LineString", "coordinate_count": 120,
                          "note": "trimmed"}},
        ],
    }
    fc, trimmed = _extract_fc(result)
    assert fc is not None
    assert len(fc["features"]) == 1
    assert fc["features"][0]["properties"]["route_id"] == "r1"
    assert trimmed == 1            # 桩丢弃计数显性化


def test_f1_all_stub_routes_yield_no_fc():
    result = {"status": "success", "routes": [
        {"route_id": "r1", "geometry": {"type": "LineString",
                                        "coordinate_count": 99}}]}
    fc, trimmed = _extract_fc(result)
    assert fc is None and trimmed == 1


# ── F-3：族默认 exclude_unreachable=True 钉扎 ────────────────────────────

def test_f3_service_accessibility_family_pins_exclude_unreachable():
    contract = build_contract(
        "service_accessibility", workflow_id="awf.f3.pin")
    isochrone = next(s for s in contract.steps
                     if s.step_id == "isochrone")
    assert isochrone.step_params["exclude_unreachable"] is True


# ── F-4：GIS_AWF_PRODUCT_GATE=0 回退契约 ─────────────────────────────────

def test_f4_product_gate_kill_switch_contract(monkeypatch):
    """GIS_AWF_PRODUCT_GATE=0 回退契约（R1 评审 F-4）：
    (a) 开关默认 True（门 armed）；
    (b) 开关 False（门 disarmed）；
    (c) AST 契约——map_product 的门体（gate_findings 的全部赋值点）只存在
        于 settings 开关的真值守卫分支内（开关假 → 门体不可达 = master 行为）。
    """
    import ast
    from pathlib import Path

    from app.core.config import settings

    assert getattr(settings, "GIS_AWF_PRODUCT_GATE", None) is True
    monkeypatch.setattr(settings, "GIS_AWF_PRODUCT_GATE", False,
                        raising=False)
    assert getattr(settings, "GIS_AWF_PRODUCT_GATE", True) is False

    source = Path(
        "app/services/gis_harness/tools.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    map_product = next(
        fn for fn in ast.walk(tree)
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
        and fn.name == "webgis_map_product")
    guard = next(
        n for n in ast.walk(map_product)
        if isinstance(n, ast.If) and "GIS_AWF_PRODUCT_GATE" in ast.unparse(
            n.test))
    # gate_findings 的赋值点必须在守卫的真分支里（含 try 体）
    assigns = [
        n for n in ast.walk(map_product)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "gate_findings"
                for t in n.targets)]
    assert assigns, "map_product 必须有 gate_findings 赋值（门体存在）"
    guard_span = (guard.lineno, guard.end_lineno)
    for a in assigns:
        inside = guard_span[0] <= a.lineno <= guard_span[1]
        empty_literal = (
            isinstance(a.value, ast.List) and not a.value.elts)
        assert inside or empty_literal, (
            f"gate_findings 非空赋值 L{a.lineno} 逃逸出开关守卫分支"
            f" {guard_span}——关闭开关时门体仍会执行（守卫外只允许"
            "空列表初始化/异常复位=禁用等价语义）")


# ── F-7：resample→zonal 直通链（output_path 检出/绝对化）────────────────

async def test_f7_resample_to_zonal_passthrough_chain(registry):
    """完整链（含 optional resample）：resample 产物 data.output_path →
    executor 检出+绝对化 → zonal step: 直通（不再 data_dir 双拼接）。"""
    from pathlib import Path

    import rasterio
    from rasterio.transform import from_origin

    data = np.array([[1.0, 2.0], [1.0, 2.0]], dtype="float32")
    data[0, 0] = -9999.0
    root = Path.cwd() / "data" / "awf-test-rasters"   # 路径沙箱允许区
    root.mkdir(parents=True, exist_ok=True)
    path = str(root / "ndvi_f7.tif")
    with rasterio.open(
            path, "w", driver="GTiff", height=2, width=2, count=1,
            dtype="float32", crs="EPSG:4326",
            transform=from_origin(104.00, 30.632, 0.02, 0.016),
            nodata=-9999.0) as dst:
        dst.write(data, 1)
    zones = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"district": n},
         "geometry": {"type": "Polygon", "coordinates": [[
             [x, 30.600], [x + 0.02, 30.600], [x + 0.02, 30.632],
             [x, 30.632], [x, 30.600]]]}}
        for x, n in [(104.00, "nw"), (104.02, "ne")]]}
    zref = await session_data_manager.store(SESSION, zones, prefix="geojson")
    plan = plan_workflow(
        "raster_zonal_summary",
        workflow_id="awf.f7.chain",
        params_overrides={"resample": {"target_resolution": 0.002}},
        bindings={
            "subject": DataRoleBinding(
                role="subject", ref=path, role_kind="raster"),
            "boundary": DataRoleBinding(role="boundary", ref=zref)})
    assert [s.step_id for s in plan.contract.steps] == [
        "resample", "zonal_stats"]
    state = new_state(plan.contract, contract_fingerprint=plan.fingerprint)
    apply_plan_verdicts(state, plan.step_verdicts)
    executor = WorkflowExecutor(registry=registry,
                                session_data=session_data_manager)
    state = await executor.execute(SESSION, plan.contract, state)
    assert state.steps["resample"].state == "satisfied", (
        state.steps["resample"].message)
    assert state.steps["zonal_stats"].state == "satisfied", (
        state.steps["zonal_stats"].message)
    resample_env = state.envelopes["resample"]
    # output_path 已绝对化（无 data/ 相对二次拼接）且 raster 事实在
    assert resample_env.ref.startswith("/")
    assert resample_env.raster.get("output_kind") == "raster_file"
    env = state.envelopes["zonal_stats"]
    payload = await session_data_manager.get(SESSION, env.ref)
    means = sorted(f["properties"]["mean"] for f in payload["features"])
    # 重采样（bilinear 0.002°）改变统计口径（族 risk_notes 披露的语义）——
    # 链路机制的诚实断言：结果有限、东西序保持（ne>nw：原始 2.0>1.0）、
    # 逐 zone 覆盖率披露在场。
    assert all(m is not None and np.isfinite(m) for m in means)
    assert means[1] > means[0]
    covs = [f["properties"].get("valid_pixel_coverage")
            for f in payload["features"]]
    assert all(c is not None for c in covs)


# ── F-8：recompute 工具真调用落 stale ────────────────────────────────────

async def test_f8_recompute_tool_marks_stale_then_reruns(registry):
    """经 registry 真调用 plan/run/recompute：原地 overwrite 同 ref →
    recompute 内部先落 state=stale（持久面可观察）再重跑回 satisfied。"""
    from app.services.gis_harness.analysis_workflows.state import (
        state_alias,
    )

    poi = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"id": i},
         "geometry": {"type": "Point",
                      "coordinates": [104.006 + i * 0.001, 30.61]}}
        for i in range(3)]}
    zones = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"district": "d"},
         "geometry": {"type": "Polygon", "coordinates": [[
             [104.0, 30.6], [104.02, 30.6], [104.02, 30.62],
             [104.0, 30.62], [104.0, 30.6]]]}}]}
    poi_ref = await session_data_manager.store(SESSION, poi, prefix="geojson")
    zone_ref = await session_data_manager.store(
        SESSION, zones, prefix="geojson")
    plan = await registry.dispatch(
        "webgis_analysis_workflow_plan",
        {"family": "admin_facility_distribution", "session_id": SESSION,
         "role_refs": '{"subject": "%s", "boundary": "%s"}'
                      % (poi_ref, zone_ref),
         "params": '{"per_capita_rate": {"denominator": "population"}}'},
        session_id=SESSION)
    assert plan.get("runnable") or plan.get("declined"), plan.get(
        "blocked_reasons")
    wf = plan["workflow_id"]
    run = await registry.dispatch(
        "webgis_analysis_workflow_run",
        {"workflow_id": wf, "session_id": SESSION}, session_id=SESSION)
    assert run["status"] == "satisfied", run["steps"]

    # 原地 overwrite（content_revision bump → 指纹漂移）→ recompute
    poi2 = {**poi, "features": poi["features"] + [
        {"type": "Feature", "properties": {"id": 99},
         "geometry": {"type": "Point", "coordinates": [104.009, 30.615]}}]}
    await session_data_manager.overwrite(SESSION, poi_ref, poi2)

    # 在 recompute 前观察不到 stale；recompute 返回后状态回 satisfied
    # 且 zones_count 真被重跑（attempts 增长）——stale 是 recompute 内部
    # 持久过渡态（对账函数与 run_log 证明其发生）。
    from app.services.gis_harness.analysis_workflows.state import (
        state_from_payload,
    )
    raw = await session_data_manager.get(
        SESSION, state_alias(wf))
    before = state_from_payload(raw)
    assert before.steps["zones_count"].state == "satisfied"
    assert not any(
        e.get("event") == "step_stale" for e in before.run_log)

    rec = await registry.dispatch(
        "webgis_analysis_workflow_recompute",
        {"workflow_id": wf, "session_id": SESSION,
         "role_refs": '{"subject": "%s"}' % poi_ref},
        session_id=SESSION)
    assert rec.get("status") in ("satisfied",), rec
    assert "zones_count" in (rec.get("recomputed_steps") or [])
    # stale 赋值路径发生过（run_log 记录 step_stale）
    raw2 = await session_data_manager.get(SESSION, state_alias(wf))
    after = state_from_payload(raw2)
    assert any(e.get("event") == "step_stale"
               for e in after.run_log), after.run_log
    assert after.steps["zones_count"].state == "satisfied"
