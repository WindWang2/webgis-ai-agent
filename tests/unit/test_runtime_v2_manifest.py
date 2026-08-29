"""GIS Harness Runtime v2 — Compiled Runtime Manifest tests (Phase 3/4).

Covers:
- R1: compile-once cross-registry validation with severity classification and
  strict fail-fast; parity lookups are O(1) manifest views.
- R2: network tool ↔ capability parity (accessibility / od_matrix /
  location_allocation / optimize_route / service_area binding).
- R3/#1084: fingerprint determinism + content sensitivity; STALE_PLAN guard on
  persisted plans; MapProductPlan carries the fingerprint.
- R4: planner capability_tool_map reads the manifest view (no per-call rebuild).
"""
import pytest

from app.lib.gis.runtime_manifest import (
    compile_runtime_manifest,
    validate_runtime_manifest_strict,
)


@pytest.fixture(scope="module")
def compiled():
    return compile_runtime_manifest()


def test_manifest_compiles_clean(compiled):
    """当前 registry 源编译 0 fatal —— 启动 fail-fast 门可用。"""
    fatal = compiled.fatal_issues()
    assert fatal == [], f"fatal issues: {[(i.code, i.detail) for i in fatal]}"
    counts = compiled.summary()["counts"]
    assert counts["capabilities"] > 20
    assert counts["algorithms"] > 30
    assert counts["tools"] > 100


def test_network_parity_lookups(compiled):
    """R2：网络工具反查 capability 正确（无孤儿、无错绑）。"""
    assert compiled.capability_for_tool("network_shortest_path") == ["shortest_path"]
    assert compiled.capability_for_tool("network_closest_facility") == ["closest_facility"]
    assert compiled.capability_for_tool("network_accessibility") == ["accessibility"]
    assert compiled.capability_for_tool("network_od_matrix") == ["od_matrix"]
    assert compiled.capability_for_tool("location_allocation") == ["location_allocation"]
    assert compiled.capability_for_tool("optimize_route") == ["route_optimization"]
    assert compiled.capability_for_tool("network_service_area") == ["service_area"]
    # 真路网服务区工具必须在 service_area 候选集内（phase-2 兼容承诺：
    # 缺省解析仍为 isochrone_analysis，故只断言成员而非首位）
    assert "network_service_area" in compiled.tools_for_capability("service_area")
    # shortest_path 不再退化到 isochrone 工具族（R2 移除错误 fallback 残留；
    # phase-2 诚实契约进一步收敛：候选精确为真路网工具）
    assert compiled.tools_for_capability("shortest_path") == [
        "network_shortest_path",
    ]


def _unregister_algorithm(ar, probe_id: str, capability: str) -> None:
    """测试探针的完整清理：_by_id 与 _by_capability 索引都还原（悬空
    索引会让后续 capability_tool_map() KeyError —— 组合运行污染源）。"""
    ar._by_id.pop(probe_id, None)
    bucket = ar._by_capability.get(capability)
    if bucket and probe_id in bucket:
        bucket.remove(probe_id)
        if not bucket:
            ar._by_capability.pop(capability, None)


def test_fingerprint_deterministic_and_content_sensitive():
    """同内容两次编译指纹一致；registry 内容变化指纹变化。"""
    m1 = compile_runtime_manifest()
    m2 = compile_runtime_manifest()
    assert m1.fingerprint == m2.fingerprint, "同内容指纹必须确定"

    from app.lib.gis.algorithm_registry import get_algorithm_registry
    from app.lib.gis.algorithm_registry import AlgorithmDescriptor
    ar = get_algorithm_registry()
    probe = AlgorithmDescriptor(
        id="probe.fp-sensitivity", name="指纹探针", capabilities=["poi_query"],
        tool_candidates=["search_poi_around"],
    )
    ar.register(probe)
    try:
        m3 = compile_runtime_manifest()
        assert m3.fingerprint != m1.fingerprint, "registry 内容变化指纹必须变化"
    finally:
        _unregister_algorithm(ar, "probe.fp-sensitivity", "poi_query")
    m4 = compile_runtime_manifest()
    assert m4.fingerprint == m1.fingerprint, "撤销变更后指纹复原"


def test_manifest_flags_fatal_on_dangling_capability():
    """悬空 capability 引用 → fatal issue + strict 校验 raise。"""
    from app.lib.gis.algorithm_registry import get_algorithm_registry, AlgorithmDescriptor
    ar = get_algorithm_registry()
    probe = AlgorithmDescriptor(
        id="probe.dangling", name="悬空探针",
        capabilities=["capability_that_does_not_exist"],
        tool_candidates=["network_shortest_path"],
    )
    ar.register(probe)
    try:
        m = compile_runtime_manifest()
        codes = [i.code for i in m.fatal_issues()]
        assert "algorithm_dangling_capability" in codes
        with pytest.raises(RuntimeError, match="dangling"):
            validate_runtime_manifest_strict(m)
    finally:
        _unregister_algorithm(ar, "probe.dangling", "capability_that_does_not_exist")


def test_planner_capability_tool_map_matches_manifest():
    """R4：planner 视图 = manifest 预排序 O(1) 视图。"""
    from app.services.gis_harness.planner import capability_tool_map
    m = compile_runtime_manifest()
    view = capability_tool_map()
    assert view == dict(m.capability_to_tools)


def test_plan_stale_detection():
    """#1084：携带旧指纹的计划判 stale；无指纹/当前指纹不判。"""
    from app.services.session_plan import SessionPlan, session_plan_stale
    import time

    m = compile_runtime_manifest()
    plan_old = SessionPlan(
        envelope_id="e1", session_id="s1", updated_at=time.time(),
        gis_chapter={"manifest_fingerprint": "deadbeef" * 8, "recipe_id": "r"},  # 64-hex 形状（32 位旧值会被形状守卫拒绝）
    )
    assert session_plan_stale(plan_old) is True

    plan_current = SessionPlan(
        envelope_id="e2", session_id="s1", updated_at=time.time(),
        gis_chapter={"manifest_fingerprint": m.fingerprint, "recipe_id": "r"},
    )
    assert session_plan_stale(plan_current) is False

    # 形状守卫：损坏/截断（非 64-hex）的存储值不判 stale
    plan_corrupt = SessionPlan(
        envelope_id="e4", session_id="s1", updated_at=time.time(),
        gis_chapter={"manifest_fingerprint": "deadbeef", "recipe_id": "r"},
    )
    assert session_plan_stale(plan_corrupt) is False

    plan_legacy = SessionPlan(
        envelope_id="e3", session_id="s1", updated_at=time.time(),
        gis_chapter={"recipe_id": "r"},
    )
    assert session_plan_stale(plan_legacy) is False

    from app.services.session_plan import format_session_plan_projection
    text = format_session_plan_projection(plan_old)
    assert "STALE_PLAN=true" in text
    assert "STALE_PLAN" not in format_session_plan_projection(plan_legacy)


def test_map_product_plan_carries_fingerprint():
    """MapProductPlan 编制时携带当前 manifest 指纹。"""
    from app.services.gis_harness.planner import MapProductPlanner
    from app.services.gis_harness.intent import MapRequestIntent

    planner = MapProductPlanner()
    intent = MapRequestIntent(query="成都小学分布", task="distribution_overview")
    plan = planner.plan_from_intent(intent)
    m = compile_runtime_manifest()
    assert plan.manifest_fingerprint == m.fingerprint


def test_manifest_stale_disclosure_uses_real_api(compiled):
    """v3(audit A1)：``_manifest_stale`` 必须调用真实 manifest API。

    post-merge 回归锁定：此前调用不存在的 ``manifest.stale(...)``，
    AttributeError 被 broad except 洗成 ``False`` —— ``manifest_stale``
    证据恒假。修复后：不同指纹披露 True；当前指纹披露 False；manifest
    访问失败显式暴露（correctness signal 不允许 fail-open）。
    """
    from app.services.gis_harness import tools as harness_tools

    assert harness_tools._manifest_stale("deadbeef" * 8) is True
    assert harness_tools._manifest_stale(compiled.fingerprint) is False
    # 空指纹（历史计划）不判 stale
    assert harness_tools._manifest_stale("") is False

    # API 漂移守卫：manifest 访问异常必须传播，不得静默洗成 not-stale
    class _BrokenManifest:
        pass

    import app.lib.gis.runtime_manifest as rm
    original = rm.get_runtime_manifest
    rm.get_runtime_manifest = lambda *a, **kw: _BrokenManifest()
    try:
        with pytest.raises(AttributeError):
            harness_tools._manifest_stale("deadbeef" * 8)
    finally:
        rm.get_runtime_manifest = original
    # 复原后语义正常
    assert harness_tools._manifest_stale("deadbeef" * 8) is True
