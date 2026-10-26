"""G3D04 M3/M6 · 证据/能力/决策/相机/失效/verdict 单元测试。

含反证测试（撤掉防护应失败）：每个 fail-closed/user-wins 行为都有
"如果实现缺失，本测试会红"的语义锚。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.scene3d.camera import plan_camera
from app.services.gis_harness.scene3d.capabilities import (
    probe_scene3d_capabilities,
)
from app.services.gis_harness.scene3d.decision import decide_representation
from app.services.gis_harness.scene3d.evidence import verify_vertical_evidence
from app.services.gis_harness.scene3d.staleness import (
    AnalysisBinding,
    evaluate_staleness,
)
from app.services.gis_harness.scene3d.verdict import build_scene3d_verdict

pytestmark = pytest.mark.unit


def _spec_with_dem(extrusion_valid: int = 0) -> dict:
    spec: dict = {
        "sources": {
            "dem1": {"type": "raster-dem", "url": "https://x/t/{z}/{x}/{y}.png",
                     "encoding": "terrarium"},
        },
        "layers": [],
    }
    if extrusion_valid > 0:
        spec["layers"].append({
            "id": "bldg", "type": "fill-extrusion",
            "extrusion": {"height_field": "height",
                          "stats": {"valid": extrusion_valid}},
        })
    return spec


# ── 证据核实（fail-closed） ──────────────────────────────────────────


def test_evidence_none_mapspec_is_empty():
    bundle = verify_vertical_evidence(None)
    assert bundle.has_elevation is False and bundle.has_height is False
    assert bundle.dem_source_ids() == []


def test_evidence_requires_url_for_dem():
    """无 url 的 raster-dem 条目不算核实证据（半成品源不得骗过决策核）。"""
    bundle = verify_vertical_evidence(
        {"sources": {"bad": {"type": "raster-dem"}}})
    assert bundle.has_elevation is False


def test_evidence_height_requires_valid_samples():
    bundle = verify_vertical_evidence(_spec_with_dem(extrusion_valid=0))
    assert bundle.has_height is False
    bundle2 = verify_vertical_evidence(_spec_with_dem(extrusion_valid=42))
    assert bundle2.has_height is True
    assert bundle2.height_attribute[0].ref == "bldg"


def test_evidence_extra_height_evidence_channel():
    bundle = verify_vertical_evidence(
        None, extra_height_evidence=[{"ref": "upload-1", "valid": 7}])
    assert bundle.has_height is True
    bad = verify_vertical_evidence(
        None, extra_height_evidence=[{"ref": "upload-1", "valid": 0}])
    assert bad.has_height is False


# ── 能力快照（unavailable + alternatives） ───────────────────────────


def test_capabilities_empty_session_reports_unavailable_with_alternatives():
    caps = probe_scene3d_capabilities(verify_vertical_evidence(None),
                                      geometry_kinds=["point"])
    terrain = caps.entry("terrain")
    assert terrain.unavailable and terrain.reason_code == "SCENE_CAP_NO_DEM_SOURCE"
    assert terrain.alternatives, "unavailable 必须带 alternatives"
    extrusion = caps.entry("extrusion")
    assert extrusion.unavailable
    assert any("2d" in alt or "分级" in alt for alt in extrusion.alternatives)


def test_capabilities_full_stack_available():
    caps = probe_scene3d_capabilities(
        verify_vertical_evidence(_spec_with_dem(extrusion_valid=9)),
        geometry_kinds=["polygon"])
    for key in ("scene_mode", "terrain", "extrusion", "camera"):
        assert caps.entry(key).status == "available", key


def test_capabilities_viewshed_requires_registry_injection():
    """裸进程（registry 未注入）→ viewshed unavailable（fail-closed 反证）。"""
    caps = probe_scene3d_capabilities(
        verify_vertical_evidence(_spec_with_dem()), geometry_kinds=["raster"])
    entry = caps.entry("viewshed_analysis")
    assert entry.unavailable


def test_capabilities_pending_directions_typed_unavailable():
    caps = probe_scene3d_capabilities(verify_vertical_evidence(None))
    for key, code in (("line_of_sight", "SCENE_CAP_ALGORITHM_PENDING_G3D03"),
                      ("skyline", "SCENE_CAP_ALGORITHM_PENDING_G3D03"),
                      ("scene_asset", "SCENE_CAP_PENDING_G3D02")):
        entry = caps.entry(key)
        assert entry.unavailable and entry.reason_code == code, key


# ── 决策（用户偏好层 × 决策核） ──────────────────────────────────────


def test_decision_no_user_dimension_no_inference():
    """零推断红线：无用户偏好时不因证据存在而升级 3D。"""
    decision = decide_representation(
        evidence=verify_vertical_evidence(_spec_with_dem(extrusion_valid=10)),
        geometry_kinds=["polygon"])
    assert decision.mode == "2d" and decision.user_dimension == ""
    assert decision.user_dimension_respected is True


def test_decision_user_3d_with_evidence_applies_3d_and_binds_terrain():
    decision = decide_representation(
        user_dimension="3d", terrain_requested=True,
        evidence=verify_vertical_evidence(_spec_with_dem(extrusion_valid=10)),
        geometry_kinds=["polygon"])
    assert decision.mode == "3d" and decision.terrain
    assert not decision.degraded
    assert decision.scene_config["terrain"]["source"] == "dem1"
    assert decision.scene_config["terrain"]["elevation_ref"] == "dem1"


def test_decision_user_3d_without_evidence_degrades_but_keeps_preference():
    """诚实降级反证：用户偏好保留 + degrade 注记（若实现丢失则红）。"""
    decision = decide_representation(
        user_dimension="3d", terrain_requested=True,
        evidence=verify_vertical_evidence(None), geometry_kinds=["polygon"])
    assert decision.mode != "3d"
    assert decision.user_dimension == "3d"
    assert decision.user_dimension_respected is False
    codes = {d.code for d in decision.degradations}
    assert "SCENE_USER_3D_NO_HEIGHT_EVIDENCE" in codes
    assert "SCENE_USER_TERRAIN_NO_DEM" in codes


def test_decision_static_medium_degrades_3d_with_note():
    decision = decide_representation(
        user_dimension="3d", terrain_requested=True, medium="print",
        evidence=verify_vertical_evidence(_spec_with_dem(extrusion_valid=5)),
        geometry_kinds=["polygon"])
    assert decision.mode != "3d"
    assert any(d.code == "SCENE_USER_3D_MEDIUM_STATIC" for d in decision.degradations)


def test_decision_terrain_without_bindable_source_omits_terrain():
    """terrain 无处绑定时 scene_config 必须省略 terrain（SetScene 会拒绝悬空
    source——产半成品是缺陷，本测试是反证锚）。"""
    evidence = verify_vertical_evidence(None)
    # 人为给证据但不给源（capability 面不可用场景）
    decision = decide_representation(
        user_dimension="2.5d", terrain_requested=True,
        evidence=evidence, geometry_kinds=["raster"])
    assert "terrain" not in decision.scene_config
    assert decision.terrain is False
    assert any(d.code == "SCENE_USER_TERRAIN_NO_DEM" for d in decision.degradations)


def test_decision_exaggeration_clamped_by_core():
    decision = decide_representation(
        user_dimension="2.5d", terrain_requested=True, exaggeration=25.0,
        evidence=verify_vertical_evidence(_spec_with_dem()),
        geometry_kinds=["raster"])
    assert decision.scene_config.get("terrain", {}).get("exaggeration", 1.0) <= 10.0


# ── 相机 user-wins ───────────────────────────────────────────────────


def test_camera_applies_when_no_conflict():
    plan = plan_camera({"pitch": 50.0, "bearing": -15.0})
    assert plan.status == "apply" and plan.view["pitch"] == 50.0


def test_camera_suppressed_by_user_viewport_divergence():
    """user-wins 反证：用户镜头偏离 spec 时 agent 动作必须被抑制。"""
    snapshot = {"viewport": {"center": [104.0, 30.6], "zoom": 11,
                             "bearing": 42.0, "pitch": 60.0}}
    spec_view = {"center": [104.0, 30.66], "zoom": 11, "bearing": 0.0,
                 "pitch": 0.0}
    plan = plan_camera({"pitch": 50.0}, interaction_snapshot=snapshot,
                       spec_view=spec_view)
    assert plan.status == "user_wins_suppressed"
    assert plan.reason_code == "SCENE_CAMERA_USER_VIEWPORT_ACTIVE"
    assert plan.view == {}  # 不偷改


def test_camera_axis_locks_partial_apply():
    plan = plan_camera({"pitch": 50.0, "bearing": 90.0},
                       camera_locks=["pitch"])
    assert plan.status == "apply"
    assert plan.suppressed_axes == ["pitch"]
    assert "pitch" not in plan.view and plan.view["bearing"] == 90.0


def test_camera_all_axes_locked_suppresses():
    plan = plan_camera({"pitch": 50.0, "bearing": 90.0},
                       camera_locks=["pitch", "bearing"])
    assert plan.status == "user_wins_suppressed"
    assert plan.reason_code == "SCENE_CAMERA_AXES_LOCKED"


def test_camera_locked_axis_allows_user_preference_value():
    """锁上等值下发 = 执行用户自己的选择（user-wins 一致），不是偷改。"""
    plan = plan_camera({"pitch": 30.0, "bearing": 90.0},
                       camera_locks=["pitch"],
                       user_camera={"pitch": 30.0})
    assert plan.status == "apply"
    assert plan.view["pitch"] == 30.0
    # 偏离用户偏好的值仍被锁
    plan2 = plan_camera({"pitch": 60.0}, camera_locks=["pitch"],
                        user_camera={"pitch": 30.0})
    assert plan2.status == "user_wins_suppressed"
    assert plan2.suppressed_axes == ["pitch"]


def test_camera_empty_request_typed():
    plan = plan_camera({})
    assert plan.status == "user_wins_suppressed"
    assert plan.reason_code == "SCENE_CAMERA_EMPTY_REQUEST"


# ── 失效裁决 ─────────────────────────────────────────────────────────


def _binding(**kw) -> AnalysisBinding:
    defaults = dict(analysis_goal="viewshed", observer_feature_id="peak-a",
                    dem_source_id="dem1", result_layer_id="viewshed-1")
    defaults.update(kw)
    return AnalysisBinding(**defaults)


def test_staleness_fresh():
    verdict = evaluate_staleness(_binding(),
                                 interaction_snapshot={},
                                 current_dem_source_ids=["dem1"])
    assert verdict.state == "fresh" and not verdict.needs_recompute


def test_staleness_observer_moved_requires_recompute():
    """观察点失效反证：用户点选新要素后旧结果必须判 stale。"""
    verdict = evaluate_staleness(
        _binding(), interaction_snapshot={"selection": {"feature_id": "peak-b"}},
        current_dem_source_ids=["dem1"])
    assert verdict.state == "stale"
    assert verdict.reason_code == "SCENE_STALE_OBSERVER_MOVED"
    assert verdict.recommended_action == "recompute"


def test_staleness_dem_source_gone():
    verdict = evaluate_staleness(_binding(), interaction_snapshot={},
                                 current_dem_source_ids=[])
    assert verdict.state == "stale"
    assert verdict.reason_code == "SCENE_STALE_DEM_SOURCE_GONE"


def test_staleness_result_hidden_is_display_stale_not_stale():
    """隐藏结果层 ≠ 分析失效（分析正确性与可见性是两个门）。"""
    verdict = evaluate_staleness(
        _binding(),
        interaction_snapshot={"user_overrides": {
            "viewshed-1": {"layer_id": "viewshed-1", "visible": False}}},
        current_dem_source_ids=["dem1"])
    assert verdict.state == "display_stale"
    assert not verdict.needs_recompute
    assert verdict.recommended_action == "disclose_hidden"


def test_staleness_camera_changes_never_invalidate():
    """相机不是分析输入（范畴错误防线）：viewport 大幅变化仍 fresh。"""
    verdict = evaluate_staleness(
        _binding(),
        interaction_snapshot={"viewport": {"pitch": 70, "bearing": 120,
                                           "zoom": 9}},
        current_dem_source_ids=["dem1"])
    assert verdict.state == "fresh"


# ── 双门 verdict ─────────────────────────────────────────────────────


def test_verdict_completed_with_full_evidence():
    spec = _spec_with_dem()
    spec["scene"] = {"mode": "3d",
                     "terrain": {"source": "dem1", "exaggeration": 1.0}}
    verdict = build_scene3d_verdict(
        mapspec=spec,
        scene_apply_receipt={"is_compiled": True, "mutation_revision": 7},
        analysis_execution={"success": True},
        method_contract={"findings": []},
        analysis_goal="viewshed")
    assert verdict.verdict == "completed"
    assert verdict.render_evidence == "confirmed"
    assert "mapspec:rev:7" in verdict.evidence_refs


def test_verdict_blocked_without_render_receipt_or_execution_evidence():
    """反证：无渲染回执/无执行证据时绝不 completed。"""
    spec = _spec_with_dem()
    spec["scene"] = {"mode": "3d",
                     "terrain": {"source": "dem1", "exaggeration": 1.0}}
    verdict = build_scene3d_verdict(mapspec=spec, analysis_goal="viewshed")
    assert verdict.verdict == "blocked"
    blocking = {f.code for f in verdict.findings if f.blocking}
    assert "RENDER_EVIDENCE_MISSING" in blocking
    assert "SCIENCE_NO_EXECUTION_EVIDENCE" in blocking


def test_verdict_user_degradation_visible_even_when_display_na():
    """降级可见性反证：display 门 not_applicable 时降级注记仍是 degraded。"""
    verdict = build_scene3d_verdict(
        mapspec={"scene": {"mode": "2d"}},
        scene_apply_receipt={"is_compiled": True},
        degradations=[{"code": "SCENE_USER_3D_NO_HEIGHT_EVIDENCE",
                       "detail": "无高度证据"}])
    assert verdict.verdict == "degraded"


def test_verdict_display_gate_blocking_on_dangling_terrain():
    spec = {"scene": {"mode": "2.5d", "terrain": {"source": "ghost"}},
            "sources": {}}
    verdict = build_scene3d_verdict(
        mapspec=spec, scene_apply_receipt={"is_compiled": True})
    assert verdict.verdict == "blocked"
    assert verdict.display_gate == "blocked"


def test_verdict_pure_2d_without_scene_is_not_gated():
    verdict = build_scene3d_verdict(mapspec={"layers": []},
                                    scene_apply_receipt={"is_compiled": True})
    assert verdict.verdict == "completed"
    assert verdict.display_gate == "not_applicable"
