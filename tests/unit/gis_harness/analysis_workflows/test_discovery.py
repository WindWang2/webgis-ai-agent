"""Discovery 投影（ADR-0282 B4）—— 机读能力目录 + 结构化 next_step。"""
from __future__ import annotations

from app.services.gis_harness.analysis_workflows.discovery import (
    NEXT_STEP_ACTIONS,
    family_catalog,
    next_step_for_blocked,
)


def test_family_catalog_covers_seven_families_bounded():
    catalog = family_catalog()
    assert catalog["schema"] == "awf.discovery.v1"
    fams = {f["family"] for f in catalog["families"]}
    assert fams == {
        "admin_facility_distribution", "service_accessibility",
        "hazard_exposure", "temporal_change", "statistical_zones",
        "raster_zonal_summary", "network_proximity"}
    for fam in catalog["families"]:
        assert fam["roles"], fam["family"]
        assert all("tool" in s and "preconditions" in s and "mappable_output"
                   in s and "resource" in s for s in fam["steps"])


def test_family_catalog_single_family_with_tool_meta():
    catalog = family_catalog(
        "raster_zonal_summary",
        tool_meta=lambda t: {"latency_class": "slow",
                             "memory_class": "heavy"})
    fam = catalog["families"][0]
    zonal = next(s for s in fam["steps"] if s["step_id"] == "zonal_stats")
    assert zonal["resource"] == {"latency_class": "slow",
                                 "memory_class": "heavy"}
    assert zonal["capability"] == "zonal_statistics"
    # 未知族=空 families（不抛）
    assert family_catalog("nope")["families"] == []


def test_next_step_closed_vocabulary_typed_mapping():
    assert next_step_for_blocked(
        "AWF_BUDGET_EXCEEDED")["action"] == "reduce_input"
    assert next_step_for_blocked(
        "AWF_ROLE_KIND_MISMATCH:SUBJECT")["action"] == "fix_role_kind"
    assert next_step_for_blocked(
        "AWF_NO_NETWORK_FOR_ACCESSIBILITY")["action"] == "bind_role"
    assert next_step_for_blocked(
        "AWF_DATA_ROLE_MISSING:BOUNDARY", role="boundary")["action"] == "bind_role"
    # 未知码兜底仍在词表内
    fallback = next_step_for_blocked("SOMETHING_ELSE")
    assert fallback["action"] in NEXT_STEP_ACTIONS
