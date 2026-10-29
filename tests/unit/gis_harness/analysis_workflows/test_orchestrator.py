"""Orchestrator 规划裁决回归锁（goal-02 M3）。

不变式（科学口径负例全部落这里）：
- 缺行政边界 → zones_count blocked (AWF_DATA_ROLE_MISSING:BOUNDARY) + alternatives；
- 无路网 → isochrone blocked 专属码，绝不给等时数值结论；alternatives 含
  buffer proxy 且显式注明科学含义差异；
- 分母缺失 → per_capita_rate typed skipped（不是 blocked，也不是输出）；
- temporal 年份缺失/相同/颠倒 → AWF_TEMPORAL_MISMATCH；
- runnable 只在有 required block 时为 False。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.analysis_workflows.contracts import (
    AWF_DENOMINATOR_MISSING,
    AWF_NO_NETWORK_FOR_ACCESSIBILITY,
    AWF_TEMPORAL_MISMATCH,
    DataRoleBinding,
)
from app.services.gis_harness.analysis_workflows.orchestrator import plan_workflow


def _b(role: str, ref: str = "", **qualifier) -> DataRoleBinding:
    return DataRoleBinding(role=role, ref=ref, qualifier=dict(qualifier))


# ── 行政区设施分布族 ──────────────────────────────────────────────────────

def test_distribution_full_bindings_all_ready():
    result = plan_workflow(
        "admin_facility_distribution",
        workflow_id="awf.t.dist",
        bindings={"subject": _b("subject", "ref:geojson-pois"),
                  "boundary": _b("boundary", "ref:geojson-districts"),
                  "denominator": _b("denominator", "ref:geojson-pop")})
    by_id = {v.step_id: v for v in result.step_verdicts}
    assert all(v.verdict == "ready" for v in result.step_verdicts)
    assert result.runnable
    assert by_id["per_capita_rate"].verdict == "ready"


def test_distribution_missing_boundary_blocks_with_alternatives():
    result = plan_workflow(
        "admin_facility_distribution",
        workflow_id="awf.t.dist2",
        bindings={"subject": _b("subject", "ref:geojson-pois")})
    by_id = {v.step_id: v for v in result.step_verdicts}
    assert by_id["zones_count"].verdict == "blocked"
    assert "AWF_DATA_ROLE_MISSING:BOUNDARY" in by_id["zones_count"].reason_code
    assert result.blocked_reasons
    assert not result.runnable
    assert any("行政区" in a for a in result.alternatives)


def test_distribution_missing_denominator_skips_per_capita_only():
    """核心验收：分母不存在 → 只给原始计数，不伪装密度。"""
    result = plan_workflow(
        "admin_facility_distribution",
        workflow_id="awf.t.dist3",
        bindings={"subject": _b("subject", "ref:geojson-pois"),
                  "boundary": _b("boundary", "ref:geojson-districts")})
    by_id = {v.step_id: v for v in result.step_verdicts}
    assert by_id["zones_count"].verdict == "ready"
    assert by_id["area_density"].verdict == "ready"       # 面积分母几何自足
    assert by_id["per_capita_rate"].verdict == "skipped"
    assert by_id["per_capita_rate"].reason_code == AWF_DENOMINATOR_MISSING
    assert result.runnable                                 # 主链不受 optional 影响


# ── 可达性族 ─────────────────────────────────────────────────────────────

_FULL_ACCESS = {
    "subject": _b("subject", "ref:geojson-schools"),
    "network": _b("network", "ref:geojson-roads"),
    "boundary": _b("boundary", "ref:geojson-districts"),
    "population": _b("population", "ref:geojson-pop"),
}


def test_accessibility_full_bindings_ready():
    result = plan_workflow("service_accessibility",
                           workflow_id="awf.t.acc",
                           bindings=_FULL_ACCESS)
    assert all(v.verdict == "ready" for v in result.step_verdicts)
    assert result.runnable


def test_accessibility_no_network_typed_blocked_not_numeric():
    """核心验收：无路网不得『15 分钟可达』数值结论；buffer 替代须注明差异。"""
    bindings = {k: v for k, v in _FULL_ACCESS.items() if k != "network"}
    result = plan_workflow("service_accessibility",
                           workflow_id="awf.t.acc2",
                           bindings=bindings)
    by_id = {v.step_id: v for v in result.step_verdicts}
    iso = by_id["isochrone"]
    assert iso.verdict == "blocked"
    assert iso.reason_code == AWF_NO_NETWORK_FOR_ACCESSIBILITY
    assert "欧氏距离" in "".join(iso.alternatives) or \
        "科学含义" in "".join(iso.alternatives)
    # 下游覆盖步骤因依赖 isochrone 而不可 ready
    assert by_id["coverage_zones"].verdict == "blocked"
    assert not result.runnable


def test_accessibility_missing_population_only_skips_coverage_step():
    bindings = {k: v for k, v in _FULL_ACCESS.items() if k != "population"}
    result = plan_workflow("service_accessibility",
                           workflow_id="awf.t.acc3",
                           bindings=bindings)
    by_id = {v.step_id: v for v in result.step_verdicts}
    assert by_id["isochrone"].verdict == "ready"
    assert by_id["coverage_zones"].verdict == "ready"
    assert by_id["coverage_population"].verdict == "skipped"
    assert result.runnable


# ── 时态族 ───────────────────────────────────────────────────────────────

def test_temporal_aligned_years_ready():
    result = plan_workflow(
        "temporal_change", workflow_id="awf.t.tc",
        bindings={"baseline": _b("baseline", "ref:geojson-2018", year=2018),
                  "target_time": _b("target_time", "ref:geojson-2023", year=2023)})
    assert all(v.verdict == "ready" for v in result.step_verdicts)


@pytest.mark.parametrize("y0,y1,detail", [
    (None, 2023, "年份画像缺失"),
    (2023, None, "年份画像缺失"),
    (2020, 2020, "相同"),
    (2023, 2018, "颠倒"),
])
def test_temporal_mismatch_typed_rejected(y0, y1, detail):
    result = plan_workflow(
        "temporal_change", workflow_id="awf.t.tc2",
        bindings={"baseline": _b("baseline", "ref:geojson-a",
                                **({"year": y0} if y0 else {})),
                  "target_time": _b("target_time", "ref:geojson-b",
                                    **({"year": y1} if y1 else {}))})
    codes = {v.reason_code for v in result.step_verdicts}
    assert AWF_TEMPORAL_MISMATCH in codes
    assert not result.runnable


# ── 暴露族 / 统计分区族 ───────────────────────────────────────────────────

def test_hazard_exposure_requires_both_roles():
    result = plan_workflow("hazard_exposure",
                           workflow_id="awf.t.hz",
                           bindings={"hazard": _b("hazard", "ref:geojson-h")})
    by_id = {v.step_id: v for v in result.step_verdicts}
    assert by_id["hazard_buffer"].verdict == "ready"
    assert by_id["exposure_intersect"].verdict == "blocked"
    assert "RECEPTOR" in by_id["exposure_intersect"].reason_code


def test_statistical_zones_without_population_skips_rate():
    result = plan_workflow("statistical_zones",
                           workflow_id="awf.t.sz",
                           bindings={"subject": _b("subject", "ref:geojson-pts")})
    by_id = {v.step_id: v for v in result.step_verdicts}
    assert by_id["grid_stats"].verdict == "ready"
    assert by_id["grid_rate"].verdict == "skipped"
    assert by_id["grid_rate"].reason_code == AWF_DENOMINATOR_MISSING
    assert by_id["hotspot"].verdict == "ready"            # 只依赖 grid_stats


def test_unknown_family_raises_keyerror():
    with pytest.raises(KeyError):
        plan_workflow("no_such_family", workflow_id="awf.t.x")


def test_param_override_flows_into_contract():
    result = plan_workflow(
        "service_accessibility", workflow_id="awf.t.acc4",
        bindings=_FULL_ACCESS,
        params_overrides={"isochrone": {"travel_time": 30.0}})
    step = next(s for s in result.contract.steps
                if s.step_id == "isochrone")
    assert step.step_params["travel_time"] == 30.0
