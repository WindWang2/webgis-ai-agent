"""Deterministic scene quality gate + self-heal mapping tests (ADR-0199 M7).

Oracle anchors:
- 同一统计产品 2D↔3D 切换：check_legend_invariance 断言 legend/分级 digest 不漂移。
- 典型 synthetic scene 通过确定性质量门（健康矩阵全绿，病态矩阵逐项命中）。
- 自愈只产生既有 mutation intents（SetSceneIntent / SetViewIntent /
  PatchLayerPresentationIntent）。
"""
from __future__ import annotations

import copy

from app.lib.cartography.scene_quality import (
    SCENE_QUALITY_CODES,
    check_legend_invariance,
    evaluate_scene_quality,
)
from app.services.mapspec.lifecycle_engine import (
    PatchLayerPresentationIntent,
    SetSceneIntent,
    SetViewIntent,
)
from app.services.mapspec.coordinator import validate as validate_mapspec


def _healthy_3d_spec():
    return {
        "version": "1.4",
        "view": {"center": [116, 39], "zoom": 12},
        "scene": {
            "mode": "3d",
            "terrain": {"source": "dem", "exaggeration": 1.0, "vertical_unit": "m"},
            "camera": {"pitch": 50, "bearing": -15},
        },
        "sources": {
            "s1": {"type": "geojson", "inlineData": {"type": "FeatureCollection", "features": []}},
            "dem": {"type": "raster-dem", "url": "https://x.test/{z}/{x}/{y}.png"},
        },
        "layers": [
            {
                "id": "l1",
                "source": "s1",
                "type": "fill-extrusion",
                "extrusion": {"height_field": "gdp", "elevation_ref": "ref:geojson-aabbccdd00112233"},
                "legend_spec": {"kind": "graduated", "field": "gdp", "breaks": [1, 2, 3]},
                "paint": {"fill-extrusion-height": {"method": "field", "field": "gdp"}},
            }
        ],
    }


class TestHealthyScenes:
    def test_healthy_3d_passes(self):
        report = evaluate_scene_quality(_healthy_3d_spec())
        assert report["passed"] is True
        assert report["blocking_count"] == 0

    def test_healthy_2d_passes(self):
        spec = _healthy_3d_spec()
        spec["scene"] = {"mode": "2d"}
        report = evaluate_scene_quality(spec)
        assert report["passed"] is True

    def test_spec_without_scene_passes(self):
        spec = _healthy_3d_spec()
        spec.pop("scene")
        spec["layers"][0]["type"] = "fill"
        report = evaluate_scene_quality(spec)
        assert report["passed"] is True

    def test_2_5d_with_terrain_passes(self):
        spec = _healthy_3d_spec()
        spec["scene"]["mode"] = "2.5d"
        spec["layers"][0]["type"] = "fill"
        report = evaluate_scene_quality(spec)
        assert report["passed"] is True


class TestBlockingFindings:
    def test_dangling_terrain_source_blocks(self):
        spec = _healthy_3d_spec()
        spec["scene"]["terrain"]["source"] = "missing-dem"
        report = evaluate_scene_quality(spec)
        assert report["passed"] is False
        codes = [f["code"] for f in report["findings"]]
        assert "SCENE_TERRAIN_SOURCE_REF" in codes

    def test_non_dem_terrain_source_blocks(self):
        spec = _healthy_3d_spec()
        spec["sources"]["dem"] = {"type": "raster", "imageRef": "ref:raster-x"}
        report = evaluate_scene_quality(spec)
        assert report["passed"] is False

    def test_exaggeration_out_of_range_blocks(self):
        spec = _healthy_3d_spec()
        spec["scene"]["terrain"]["exaggeration"] = 99.0
        report = evaluate_scene_quality(spec)
        assert report["passed"] is False

    def test_extreme_pitch_blocks(self):
        spec = _healthy_3d_spec()
        spec["scene"]["camera"]["pitch"] = 89
        report = evaluate_scene_quality(spec)
        assert report["passed"] is False


class TestWarningFindings:
    def test_evidenceless_extrusion_warns(self):
        spec = _healthy_3d_spec()
        spec["layers"][0]["extrusion"] = None
        spec["layers"][0]["paint"] = {}
        report = evaluate_scene_quality(spec)
        codes = [f["code"] for f in report["findings"]]
        assert "SCENE_EXTRUSION_NO_EVIDENCE" in codes
        # warning 不阻塞（层可保留，但披露无证据）
        assert report["passed"] is True

    def test_distorting_exaggeration_disclosed(self):
        spec = _healthy_3d_spec()
        spec["scene"]["terrain"]["exaggeration"] = 3.0
        report = evaluate_scene_quality(spec)
        codes = [f["code"] for f in report["findings"]]
        assert "SCENE_EXAGGERATION_DISTORTS_SCALE" in codes
        assert report["passed"] is True

    def test_3d_mode_without_vertical_content_warns(self):
        spec = _healthy_3d_spec()
        spec["scene"]["mode"] = "3d"
        spec["scene"]["terrain"] = None
        spec["layers"][0]["type"] = "fill"
        report = evaluate_scene_quality(spec)
        codes = [f["code"] for f in report["findings"]]
        assert "SCENE_MODE_EMPTY_3D" in codes

    def test_unknown_vertical_unit_warns(self):
        spec = _healthy_3d_spec()
        spec["scene"]["terrain"]["vertical_unit"] = "feet"
        report = evaluate_scene_quality(spec)
        codes = [f["code"] for f in report["findings"]]
        assert "SCENE_VERTICAL_UNIT_UNKNOWN" in codes


class TestLegendInvariance:
    def test_scene_switch_keeps_legend_digests(self):
        """Oracle G1：2D↔3D 切换（仅 scene 字段变化）→ legend digest 不变。"""
        before = _healthy_3d_spec()
        after = copy.deepcopy(before)
        after["scene"]["mode"] = "2d"
        after["scene"]["terrain"] = None
        finding = check_legend_invariance(before["layers"], after["layers"])
        assert finding is None

    def test_legend_change_detected(self):
        before = _healthy_3d_spec()["layers"]
        after = copy.deepcopy(before)
        after[0]["legend_spec"]["breaks"] = [1, 5, 9]
        finding = check_legend_invariance(before, after)
        assert finding is not None
        assert finding["code"] == "SCENE_LEGEND_DRIFT"
        assert finding["layer_id"] == "l1"

    def test_legend_removal_detected(self):
        before = _healthy_3d_spec()["layers"]
        after = copy.deepcopy(before)
        after[0].pop("legend_spec")
        finding = check_legend_invariance(before, after)
        assert finding is not None

    def test_layer_set_change_detected(self):
        before = _healthy_3d_spec()["layers"]
        after = copy.deepcopy(before)[:-1]
        finding = check_legend_invariance(before, after)
        assert finding is not None


class TestSelfHealMapping:
    def test_pitch_blocking_maps_to_set_view(self):
        from app.lib.cartography.scene_selfheal import plan_scene_repairs

        spec = _healthy_3d_spec()
        spec["scene"]["camera"]["pitch"] = 89
        report = evaluate_scene_quality(spec)
        intents = plan_scene_repairs(spec, report["findings"])
        assert intents, "expected at least one repair intent"
        for intent in intents:
            assert isinstance(intent, (SetSceneIntent, SetViewIntent, PatchLayerPresentationIntent))

    def test_exaggeration_blocks_map_to_set_scene(self):
        from app.lib.cartography.scene_selfheal import plan_scene_repairs

        spec = _healthy_3d_spec()
        spec["scene"]["terrain"]["exaggeration"] = 99.0
        report = evaluate_scene_quality(spec)
        intents = plan_scene_repairs(spec, report["findings"])
        assert any(isinstance(i, SetSceneIntent) for i in intents)
        fixed = next(i for i in intents if isinstance(i, SetSceneIntent))
        assert fixed.scene["terrain"]["exaggeration"] <= 10.0

    def test_healthy_spec_yields_no_repairs(self):
        from app.lib.cartography.scene_selfheal import plan_scene_repairs

        report = evaluate_scene_quality(_healthy_3d_spec())
        assert plan_scene_repairs(_healthy_3d_spec(), report["findings"]) == []


class TestGateWiring:
    def test_vocabulary_is_closed(self):
        assert set(SCENE_QUALITY_CODES) == {
            "SCENE_TERRAIN_SOURCE_REF",
            "SCENE_TERRAIN_SOURCE_TYPE",
            "SCENE_EXAGGERATION_RANGE",
            "SCENE_EXAGGERATION_DISTORTS_SCALE",
            "SCENE_EXTRUSION_NO_EVIDENCE",
            "SCENE_MODE_EMPTY_3D",
            "SCENE_PITCH_EXTREME",
            "SCENE_VERTICAL_UNIT_UNKNOWN",
            "SCENE_LEGEND_DRIFT",
            # v1.7（G3D02）：3D 场景资产门（源引用/放置域/重复 id/预算/证据）。
            "SCENE_ASSET_SOURCE_REF",
            "SCENE_ASSET_SOURCE_TYPE",
            "SCENE_ASSET_PLACEMENT_RANGE",
            "SCENE_ASSET_DUPLICATE_ID",
            "SCENE_ASSET_EXCEEDS_BUDGET",
            "SCENE_ASSET_NO_EVIDENCE",
            # G3D05（ADR-0275）：三维制图产品向扩展
            "SCENE_SCALE_UNTRUSTWORTHY_PERSPECTIVE",
            "SCENE_LABEL_OCCLUSION_RISK",
            "SCENE_EXPORT_VECTOR_DEGRADE",
        }

    def test_blocking_codes_match_coordinator_validate(self):
        """与 pre-compile 校验同口径：dangling terrain 源在两道闸都阻塞。"""
        spec = _healthy_3d_spec()
        spec["scene"]["terrain"]["source"] = "missing-dem"
        assert evaluate_scene_quality(spec)["passed"] is False
        assert validate_mapspec(spec)["success"] is False


# ── G3D05（ADR-0275）：三维制图产品向质量检查 ────────────────────────────


def _spec_3d(**overrides):
    spec = {
        "version": "1.6",
        "view": {"center": [104.0, 30.6], "zoom": 11, "pitch": 0, "bearing": 0},
        "sources": {"dem": {"type": "raster-dem"}},
        "layers": [],
        "scene": {
            "mode": "3d",
            "terrain": {"source": "dem", "exaggeration": 1.0, "vertical_unit": "m"},
            "camera": {"pitch": 50, "bearing": -15},
        },
        "layout": {"components": []},
    }
    spec.update(overrides)
    return spec


class TestScaleTrustPerspective:
    def test_pitch_with_scale_bar_no_disclosure_warns(self):
        spec = _spec_3d(view={"center": [104, 30.6], "zoom": 11, "pitch": 45})
        spec["layout"]["components"] = [{"id": "s", "type": "scale_bar"}]
        report = evaluate_scene_quality(spec)
        warns = [f for f in report["findings"]
                 if f["code"] == "SCENE_SCALE_UNTRUSTWORTHY_PERSPECTIVE"]
        assert warns and warns[0]["severity"] == "warning"
        assert "scene_disclosure" in warns[0]["detail"]

    def test_pitch_with_scale_bar_and_disclosure_downgrades_to_info(self):
        spec = _spec_3d(view={"center": [104, 30.6], "zoom": 11, "pitch": 45})
        spec["layout"]["components"] = [
            {"id": "s", "type": "scale_bar"},
            {"id": "sd", "type": "scene_disclosure"},
        ]
        report = evaluate_scene_quality(spec)
        findings = [f for f in report["findings"]
                    if f["code"] == "SCENE_SCALE_UNTRUSTWORTHY_PERSPECTIVE"]
        assert findings and findings[0]["severity"] == "info"

    def test_flat_view_scale_trusted_no_finding(self):
        spec = _spec_3d(view={"center": [104, 30.6], "zoom": 11, "pitch": 0})
        spec["layout"]["components"] = [{"id": "s", "type": "scale_bar"}]
        report = evaluate_scene_quality(spec)
        assert not [f for f in report["findings"]
                    if f["code"] == "SCENE_SCALE_UNTRUSTWORTHY_PERSPECTIVE"]

    def test_camera_pitch_fallback_used_when_view_pitch_absent(self):
        # view 无 pitch 键 → 回退 scene.camera.pitch（推荐档）；注意
        # view.pitch=0 是合法的当前平面相机，必须优先于推荐档（下一个
        # 用例锁该语义）。
        spec = _spec_3d()
        spec["view"] = {"center": [104, 30.6], "zoom": 11}
        spec["layout"]["components"] = [{"id": "s", "type": "scale_bar"}]
        report = evaluate_scene_quality(spec)
        assert [f for f in report["findings"]
                if f["code"] == "SCENE_SCALE_UNTRUSTWORTHY_PERSPECTIVE"]

    def test_view_pitch_zero_wins_over_camera_recommendation(self):
        spec = _spec_3d()  # view.pitch=0（用户在平面视角） vs camera.pitch=50
        spec["layout"]["components"] = [{"id": "s", "type": "scale_bar"}]
        report = evaluate_scene_quality(spec)
        assert not [f for f in report["findings"]
                    if f["code"] == "SCENE_SCALE_UNTRUSTWORTHY_PERSPECTIVE"]

    def test_disabled_scale_bar_not_flagged(self):
        spec = _spec_3d(view={"center": [104, 30.6], "zoom": 11, "pitch": 45})
        spec["layout"]["components"] = [
            {"id": "s", "type": "scale_bar", "enabled": False}]
        report = evaluate_scene_quality(spec)
        assert not [f for f in report["findings"]
                    if f["code"] == "SCENE_SCALE_UNTRUSTWORTHY_PERSPECTIVE"]


class TestLabelOcclusion:
    def test_high_pitch_with_labels_info(self):
        spec = _spec_3d(view={"center": [104, 30.6], "zoom": 11, "pitch": 55})
        spec["layers"] = [{"id": "l1", "type": "symbol", "label": {"mode": "top_n"}}]
        report = evaluate_scene_quality(spec)
        occl = [f for f in report["findings"]
                if f["code"] == "SCENE_LABEL_OCCLUSION_RISK"]
        assert occl and occl[0]["severity"] == "info"

    def test_low_pitch_no_flag(self):
        spec = _spec_3d(view={"center": [104, 30.6], "zoom": 11, "pitch": 30})
        spec["layers"] = [{"id": "l1", "type": "symbol", "label": {"mode": "top_n"}}]
        report = evaluate_scene_quality(spec)
        assert not [f for f in report["findings"]
                    if f["code"] == "SCENE_LABEL_OCCLUSION_RISK"]


class TestVectorExportDegrade:
    def test_3d_vector_targets_info(self):
        report = evaluate_scene_quality(_spec_3d(), export_targets=["svg", "png"])
        deg = [f for f in report["findings"]
               if f["code"] == "SCENE_EXPORT_VECTOR_DEGRADE"]
        assert deg and deg[0]["severity"] == "info"
        assert "svg" in deg[0]["detail"]

    def test_2d_mode_no_degrade_finding(self):
        spec = _spec_3d()
        spec["scene"] = {"mode": "2d"}
        report = evaluate_scene_quality(spec, export_targets=["svg"])
        assert not [f for f in report["findings"]
                    if f["code"] == "SCENE_EXPORT_VECTOR_DEGRADE"]

    def test_no_targets_skips_check(self):
        report = evaluate_scene_quality(_spec_3d())
        assert not [f for f in report["findings"]
                    if f["code"] == "SCENE_EXPORT_VECTOR_DEGRADE"]


def test_scene_quality_codes_vocabulary_extended():
    for code in ("SCENE_SCALE_UNTRUSTWORTHY_PERSPECTIVE",
                 "SCENE_LABEL_OCCLUSION_RISK",
                 "SCENE_EXPORT_VECTOR_DEGRADE"):
        assert code in SCENE_QUALITY_CODES
