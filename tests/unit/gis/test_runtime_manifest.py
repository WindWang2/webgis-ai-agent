"""Compiled GIS Runtime Manifest（audit5 #1084）契约测试。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest

from app.lib.gis.runtime_manifest import (
    compile_runtime_manifest,
    get_runtime_manifest,
    reset_runtime_manifest,
    current_manifest_fingerprint,
)


@pytest.fixture(autouse=True)
def _fresh_manifest():
    reset_runtime_manifest()
    yield
    reset_runtime_manifest()


def _real_registry():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    init_tools(reg)
    return reg


def test_manifest_compiles_clean_at_head():
    """HEAD 的真实注册表编译后 validation_report 为空（audit5 各批次已修）。"""
    manifest = compile_runtime_manifest(_real_registry())
    assert manifest.is_valid, manifest.validation_issues
    assert len(manifest.tool_ids) >= 150
    assert manifest.fingerprint


def test_fingerprint_stable_and_content_derived():
    """同内容必同指纹；无时间/进程分量。"""
    m1 = compile_runtime_manifest(_real_registry())
    m2 = compile_runtime_manifest(_real_registry())
    assert m1.fingerprint == m2.fingerprint


def test_registry_change_changes_fingerprint():
    """动态注册后重编译 → 指纹变化（reset + get 单例路径同样生效）。"""
    from app.lib.gis.algorithm_registry import (
        AlgorithmDescriptor, get_algorithm_registry,
    )

    m1 = get_runtime_manifest(_real_registry())
    areg = get_algorithm_registry()
    try:
        areg.register(AlgorithmDescriptor(
            id="probe.fingerprint", name="probe", category="network_analysis",
            capabilities=["shortest_path"],
            output_artifact_type="line_feature_set",
            tool_candidates=["network_shortest_path"],
        ))
        reset_runtime_manifest()
        m2 = get_runtime_manifest()
        assert m2.fingerprint != m1.fingerprint
    finally:
        areg._by_id.pop("probe.fingerprint", None)
        areg._by_capability.get("shortest_path", []).remove("probe.fingerprint")
        areg._tool_to_capability_cache = None
        reset_runtime_manifest()


def test_singleton_reset_recompiles():
    reg = _real_registry()
    m1 = get_runtime_manifest(reg)
    m_same = get_runtime_manifest()
    assert m1 is m_same
    reset_runtime_manifest()
    m2 = get_runtime_manifest(reg)
    assert m2 is not m1
    assert m2.fingerprint == m1.fingerprint  # 内容未变 → 指纹相同


def test_stale_detection_semantics():
    m = compile_runtime_manifest(_real_registry())
    assert m.stale(None) is False
    assert m.stale("") is False
    assert m.stale(m.fingerprint) is False
    assert m.stale("deadbeefdeadbeef") is True


def test_validation_catches_dangling_references():
    """悬空引用（反查指向未注册工具）必须出现在编译期报告里。"""
    from app.lib.gis.algorithm_registry import (
        AlgorithmDescriptor, get_algorithm_registry,
    )

    areg = get_algorithm_registry()
    probe_id = "probe.dangling"
    try:
        areg.register(AlgorithmDescriptor(
            id=probe_id, name="probe", category="data_access",
            capabilities=["poi_query"],
            output_artifact_type="poi_feature_set",
            tool_candidates=["definitely_not_a_tool"],
        ))
        manifest = compile_runtime_manifest(_real_registry())
        assert not manifest.is_valid
        assert any("definitely_not_a_tool" in issue for issue in manifest.validation_issues)
    finally:
        areg._by_id.pop(probe_id, None)
        areg._by_capability.get("poi_query", []).remove(probe_id)
        areg._tool_to_capability_cache = None


def test_plan_carries_manifest_fingerprint():
    """#1084 验收：MapProductPlan.manifest_fingerprint 落盘 + evidence 披露。"""
    from app.services.gis_harness.planner import MapProductPlanner
    from app.services.gis_harness.intent import resolve_map_request_intent

    get_runtime_manifest(_real_registry())
    planner = MapProductPlanner()
    intent = resolve_map_request_intent("成都小学的分布情况")
    plan = planner.plan_from_intent(intent)
    assert plan.manifest_fingerprint == current_manifest_fingerprint()
    assert plan.manifest_fingerprint


def test_manifest_survives_registry_outage_gracefully():
    """工具面不可得时编译不崩（工具集为空、报告可非空）。"""
    class _Broken:
        def list_tools(self):
            raise RuntimeError("registry unavailable")

        def all_metadata(self):
            raise RuntimeError("registry unavailable")

    manifest = compile_runtime_manifest(_Broken())
    assert manifest.tool_ids == frozenset()
