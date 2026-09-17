"""Scene planning (2D/2.5D/3D) — deterministic decision table tests (ADR-0199 M1).

Oracle anchors:
- 无 elevation 证据时不会伪造高度：无证据 → 绝不产出挤出/terrain 的 3d 决策。
- 决策确定性：同输入必同输出（replay 一致）。
"""
from __future__ import annotations

import pytest

from app.lib.cartography.scene_planning import (
    SCENE_MODES,
    SceneDecision,
    SceneIntent,
    plan_scene,
)


def _base_intent(**overrides) -> SceneIntent:
    payload = dict(
        purpose="analysis",
        medium="interactive",
        motion="full",
        geometry_kinds=["polygon"],
        feature_count=120,
    )
    payload.update(overrides)
    return SceneIntent(**payload)


class TestModeVocabulary:
    def test_modes_are_exactly_three(self):
        assert SCENE_MODES == ("2d", "2.5d", "3d")

    def test_decision_model_shape(self):
        d = plan_scene(_base_intent())
        assert isinstance(d, SceneDecision)
        assert d.mode in SCENE_MODES
        assert isinstance(d.reasons, list) and d.reasons
        assert d.extrusion is False and d.terrain is False


class TestFailClosedNoEvidence:
    """Oracle: 无 elevation 证据时不会伪造高度。"""

    def test_no_evidence_no_extrusion_even_with_intent(self):
        d = plan_scene(_base_intent(vertical_extrusion_intent=True))
        assert d.mode != "3d"
        assert d.extrusion is False
        assert any(r.code == "SCENE_EXTRUSION_NO_HEIGHT_EVIDENCE" for r in d.reasons)

    def test_no_evidence_no_terrain_even_with_intent(self):
        d = plan_scene(_base_intent(terrain_intent=True))
        assert d.terrain is False
        assert any(r.code == "SCENE_TERRAIN_NO_ELEVATION_EVIDENCE" for r in d.reasons)

    def test_height_evidence_without_intent_stays_flat(self):
        d = plan_scene(_base_intent(has_height_attribute_evidence=True))
        assert d.mode == "2d" and d.extrusion is False

    def test_full_evidence_and_intent_gives_3d(self):
        d = plan_scene(
            _base_intent(
                vertical_extrusion_intent=True,
                has_height_attribute_evidence=True,
                terrain_intent=True,
                has_elevation_evidence=True,
            )
        )
        assert d.mode == "3d"
        assert d.extrusion is True and d.terrain is True

    def test_extrusion_only_is_3d_terrain_only_is_2_5d(self):
        ext = plan_scene(
            _base_intent(vertical_extrusion_intent=True, has_height_attribute_evidence=True)
        )
        assert ext.mode == "3d" and ext.terrain is False
        ter = plan_scene(_base_intent(terrain_intent=True, has_elevation_evidence=True))
        assert ter.mode == "2.5d" and ter.extrusion is False and ter.terrain is True


class TestStaticMedia:
    @pytest.mark.parametrize("medium", ["print", "export_pdf", "export_svg"])
    def test_static_media_never_3d(self, medium):
        d = plan_scene(
            _base_intent(
                medium=medium,
                vertical_extrusion_intent=True,
                has_height_attribute_evidence=True,
            )
        )
        assert d.mode != "3d"
        assert any(r.code == "SCENE_MEDIUM_STATIC" for r in d.reasons)

    def test_static_media_allows_hillshade_2_5d(self):
        d = plan_scene(
            _base_intent(medium="print", terrain_intent=True, has_elevation_evidence=True)
        )
        assert d.mode == "2.5d"
        assert d.recommended_pitch == 0  # 静态媒介零透视

    def test_screen_media_allows_3d(self):
        d = plan_scene(
            _base_intent(
                medium="screen",
                vertical_extrusion_intent=True,
                has_height_attribute_evidence=True,
            )
        )
        assert d.mode == "3d"


class TestCameraRecommendation:
    def test_3d_pitch_in_safe_band(self):
        d = plan_scene(
            _base_intent(vertical_extrusion_intent=True, has_height_attribute_evidence=True)
        )
        assert 30 <= d.recommended_pitch <= 60
        assert -180 <= d.recommended_bearing <= 180

    def test_2d_pitch_zero(self):
        assert plan_scene(_base_intent()).recommended_pitch == 0

    def test_reduced_motion_zero_transition(self):
        d = plan_scene(_base_intent(motion="reduced"))
        assert d.camera_transition_ms == 0

    def test_full_motion_positive_transition(self):
        d = plan_scene(_base_intent())
        assert d.camera_transition_ms > 0


class TestExaggeration:
    def test_default_exaggeration_is_honest_one(self):
        d = plan_scene(_base_intent(terrain_intent=True, has_elevation_evidence=True))
        assert d.recommended_exaggeration == 1.0

    def test_exaggeration_disclosed_when_distorting(self):
        d = plan_scene(
            _base_intent(
                terrain_intent=True,
                has_elevation_evidence=True,
                exaggeration_request=3.0,
            )
        )
        assert d.recommended_exaggeration == 3.0
        assert any(r.code == "SCENE_EXAGGERATION_DISTORTS_SCALE" for r in d.reasons)

    def test_exaggeration_clamped_to_bound(self):
        d = plan_scene(
            _base_intent(
                terrain_intent=True,
                has_elevation_evidence=True,
                exaggeration_request=50.0,
            )
        )
        assert d.recommended_exaggeration <= 10.0


class TestDeterminismAndHonesty:
    def test_replay_determinism(self):
        i = _base_intent(vertical_extrusion_intent=True, has_height_attribute_evidence=True)
        assert plan_scene(i).model_dump() == plan_scene(i).model_dump()

    def test_reasons_carry_evidence_trace(self):
        d = plan_scene(_base_intent(terrain_intent=True))
        for r in d.reasons:
            assert r.code.startswith("SCENE_")
            assert isinstance(r.detail, str) and r.detail

    def test_geometry_kinds_empty_is_conservative(self):
        d = plan_scene(_base_intent(geometry_kinds=[]))
        assert d.mode == "2d"

    def test_point_geometry_extrusion_denied(self):
        """挤出只对 polygon 语义成立 —— 点线数据即使有 height 证据也不 3d 挤出。"""
        d = plan_scene(
            _base_intent(
                geometry_kinds=["point"],
                vertical_extrusion_intent=True,
                has_height_attribute_evidence=True,
            )
        )
        assert d.extrusion is False
        assert any(r.code == "SCENE_EXTRUSION_REQUIRES_POLYGON" for r in d.reasons)
