"""链① 行政统计分母字段语义（ADR-0284 D4；断点 6 修复）。

- per_capita_rate 分母字段名来自 role:denominator 的 qualifier.field
  （默认 "population" 行为兼容）；
- qualifier.fields（可用列清单）声明而目标列不在其中 → **plan 期** typed
  skipped AWF_DENOMINATOR_MISSING（不再运行期 MissingRequiredField failed）；
- 绑定带 field 的端到端：真实 registry 跑通 per-capita 率（非 skipped）。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.analysis_workflows.contracts import (
    AWF_DENOMINATOR_MISSING,
    DataRoleBinding,
)
from app.services.gis_harness.analysis_workflows.executor import (
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

SESSION = "awf-denom-test-session"


def _poi_fc(n: int = 8) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"id": i},
             "geometry": {"type": "Point",
                          "coordinates": [104.006 + (i % 4) * 0.0055,
                                          30.605 + (i // 4) * 0.0042]}}
            for i in range(n)],
    }


def _districts_fc(pop_field: str = "population") -> dict:
    quads = [
        (104.00, 30.600, "nw"), (104.02, 30.600, "ne"),
    ]
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "properties": {"district": name, pop_field: 40_000 + i * 5_000},
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


async def test_per_capita_uses_qualifier_field_end_to_end(registry):
    poi_ref = await _store(_poi_fc())
    dist_ref = await _store(_districts_fc(pop_field="pop_2020"))
    plan = plan_workflow(
        "admin_facility_distribution",
        workflow_id="awf.denom.e2e",
        bindings={
            "subject": DataRoleBinding(role="subject", ref=poi_ref),
            "boundary": DataRoleBinding(role="boundary", ref=dist_ref),
            "denominator": DataRoleBinding(
                role="denominator", ref=dist_ref,
                qualifier={"field": "pop_2020",
                           "geometry_types": ["Polygon"]}),
        })
    per_capita = next(s for s in plan.contract.steps
                      if s.step_id == "per_capita_rate")
    assert per_capita.step_params["denominator"] == "pop_2020"
    state = new_state(plan.contract, contract_fingerprint=plan.fingerprint)
    apply_plan_verdicts(state, plan.step_verdicts)
    executor = WorkflowExecutor(registry=registry,
                                session_data=session_data_manager)
    state = await executor.execute(SESSION, plan.contract, state)
    assert state.steps["per_capita_rate"].state == "satisfied", (
        state.steps["per_capita_rate"].message)
    assert state.status == "satisfied"
    env = state.envelopes["per_capita_rate"]
    assert env.feature_count == 2


def test_per_capita_field_not_in_declared_fields_typed_skipped_at_plan():
    plan = plan_workflow(
        "admin_facility_distribution",
        workflow_id="awf.denom.skip",
        bindings={
            "subject": DataRoleBinding(role="subject", ref="ref:poi"),
            "boundary": DataRoleBinding(role="boundary", ref="ref:zones"),
            "denominator": DataRoleBinding(
                role="denominator", ref="ref:zones",
                qualifier={"fields": ["area_km2", "households"],
                           "geometry_types": ["Polygon"]}),
        })
    verdict = next(v for v in plan.step_verdicts
                   if v.step_id == "per_capita_rate")
    assert verdict.verdict == "skipped"
    assert verdict.reason_code == AWF_DENOMINATOR_MISSING
    assert "population" in verdict.message
    # 主体链不受影响
    assert plan.runnable
    assert next(v for v in plan.step_verdicts
                if v.step_id == "zones_count").verdict == "ready"


def test_per_capita_declared_fields_contain_target_ready():
    plan = plan_workflow(
        "admin_facility_distribution",
        workflow_id="awf.denom.ok",
        bindings={
            "subject": DataRoleBinding(role="subject", ref="ref:poi"),
            "boundary": DataRoleBinding(role="boundary", ref="ref:zones"),
            "denominator": DataRoleBinding(
                role="denominator", ref="ref:zones",
                qualifier={"field": "pop_2020",
                           "fields": ["pop_2020", "households"],
                           "geometry_types": ["Polygon"]}),
        })
    verdict = next(v for v in plan.step_verdicts
                   if v.step_id == "per_capita_rate")
    assert verdict.verdict == "ready"


def test_per_capita_default_field_backcompat_no_qualifier():
    """无 qualifier（旧绑定形态）→ 默认 population，行为与 master 一致
    （无 fields 断言=不拦截；运行期由工具校验字段存在性）。"""
    plan = plan_workflow(
        "admin_facility_distribution",
        workflow_id="awf.denom.compat",
        bindings={
            "subject": DataRoleBinding(role="subject", ref="ref:poi"),
            "boundary": DataRoleBinding(role="boundary", ref="ref:zones"),
            "denominator": DataRoleBinding(
                role="denominator", ref="ref:zones",
                qualifier={"geometry_types": ["Polygon"]}),
        })
    per_capita = next(s for s in plan.contract.steps
                      if s.step_id == "per_capita_rate")
    assert per_capita.step_params["denominator"] == "population"
    verdict = next(v for v in plan.step_verdicts
                   if v.step_id == "per_capita_rate")
    assert verdict.verdict == "ready"
