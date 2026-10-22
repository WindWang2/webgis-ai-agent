"""G03 活 registry 审计基线（W4 棘轮）。

对完整离线引导的 registry（init_tools 同款口径）跑 8 面对账，钉住：

- **fail = 0**：结构性破损（悬空引用/不可达/投影逐出）不允许存在；
- **contract_divergence = 0**：schema × parameter_contract 分叉在本 PR
  已清偿（ripley/cloud_qc/kriging 三处修复 + 家族并集豁免）；
- **ref_offload_without_ref_contract = 4**：剩余 4 个非 geojson 输出族的
  ref_offload 声明是真实含糊项（PR 披露为后续项），钉住集合防新增；
- 长尾完整度 warning 只设**上限棘轮**（只许降不许升）。
"""
import pytest

from app.lib.gis.tool_capability_conformance import (
    FACE_REGISTRATION,
    FACE_RESULT,
    FACE_SCIENCE,
    STATUS_FAIL,
    audit_registry_conformance,
    collect_live_facts,
)

#: 已披露的 ref_offload 含糊项（text/map_product/stats 输出族；PR 风险节）。
DISCLOSED_REF_OFFLOAD = {
    "apply_template", "create_3d_extrusion_map",
    "create_thematic_map", "hydrology_v4_analysis",
}

#: 完整度债务上限（W4 基线：只许下降，回升即红）。
_CEILINGS = {
    "metadata_incomplete": 158,
    "result_size_unknown": 76,
    "binding_unbacked": 61,
}


@pytest.fixture(scope="module")
def summary():
    facts = collect_live_facts(bootstrap=True)
    return audit_registry_conformance(facts)


def test_live_registry_has_no_structural_fail(summary):
    assert summary.by_overall.get(STATUS_FAIL, 0) == 0, (
        f"structural fails introduced: "
        f"{[r.tool for r in summary.reports if r.overall == STATUS_FAIL][:8]}")
    assert summary.tool_count >= 300  # 完整引导面（357 @ G03 基线）


def test_live_registry_zero_contract_divergence(summary):
    diverged = [
        r.tool for r in summary.reports
        if r.face(FACE_SCIENCE).reason == "contract_divergence"]
    assert diverged == [], f"schema×contract divergence regressed: {diverged[:8]}"


def test_live_registry_ref_offload_disclosed_set(summary):
    flagged = {
        r.tool for r in summary.reports
        if r.face(FACE_RESULT).reason == "ref_offload_without_ref_contract"}
    assert flagged == DISCLOSED_REF_OFFLOAD, (
        f"ref_offload ambiguity set changed: +{sorted(flagged - DISCLOSED_REF_OFFLOAD)} "
        f"-{sorted(DISCLOSED_REF_OFFLOAD - flagged)}")


def test_live_registry_completeness_ratchet(summary):
    reasons = dict(summary.top_reasons)
    for reason, ceiling in _CEILINGS.items():
        actual = reasons.get(reason, 0)
        assert actual <= ceiling, (
            f"{reason} regressed: {actual} > ceiling {ceiling}")


def test_live_registry_native_frontdoors_compile(summary):
    """7 个 native 前门全部 spec 可编译（pi_native 面 pass 或披露性
    not_evaluated，不允许 fail）。"""
    native_fails = [
        r.tool for r in summary.reports
        if r.face("pi_native").status == STATUS_FAIL]
    assert native_fails == []


def test_live_registry_zero_dangling_fallback(summary):
    """descriptor fallback_tool 悬空 = fail 面（registration）——单独点名
    防回归（与 catalog 条目级 fallback 检查互补）。"""
    dangling = [
        r.tool for r in summary.reports
        if r.face(FACE_REGISTRATION).reason == "fallback_tool_dangling"]
    assert dangling == []
