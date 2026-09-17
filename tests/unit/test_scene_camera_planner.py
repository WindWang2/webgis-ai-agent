"""Scene camera planner tests (ADR-0199 M5).

Oracle anchors:
- overview/detail/compare 三档相机规划，确定性、纯函数。
- antimeridian（±180 经线跨越）bbox → 最短弧 center，不产生跨球翻转。
- pitch 安全带（0..60）、zoom 钳制 3..18、reduced-motion → duration 0。
"""
from __future__ import annotations

import math


from app.lib.cartography.scene_camera import (
    CAMERA_ROLE,
    plan_scene_camera,
)


class TestOverviewDetailCompare:
    def test_roles_vocabulary(self):
        assert set(CAMERA_ROLE) == {"overview", "detail", "compare"}

    def test_overview_higher_altitude_than_detail(self):
        bbox = [116.0, 39.0, 117.0, 40.0]
        ov = plan_scene_camera(bbox, role="overview")
        de = plan_scene_camera(bbox, role="detail")
        assert ov["zoom"] < de["zoom"]

    def test_compare_offsets_secondary(self):
        bbox = [116.0, 39.0, 117.0, 40.0]
        cmp_primary = plan_scene_camera(bbox, role="compare", slot="primary")
        cmp_secondary = plan_scene_camera(bbox, role="compare", slot="secondary")
        # 主副相机同高但方位差（对比语义：同高异向，不引入高度差干扰判读）
        assert cmp_primary["zoom"] == cmp_secondary["zoom"]
        assert cmp_primary["bearing"] != cmp_secondary["bearing"]

    def test_center_is_bbox_midpoint(self):
        cam = plan_scene_camera([100.0, 20.0, 102.0, 24.0], role="overview")
        assert cam["center"] == [101.0, 22.0]

    def test_output_shape_and_bounds(self):
        cam = plan_scene_camera([100.0, 20.0, 102.0, 24.0], role="detail")
        assert 0 <= cam["pitch"] <= 60
        assert -180 <= cam["bearing"] <= 180
        assert 3 <= cam["zoom"] <= 18
        assert isinstance(cam["transition_ms"], int)


class TestAntimeridian:
    def test_bbox_crossing_dateline_gets_short_arc_center(self):
        # 170E → 170W 跨 ±180 的 bbox（west > east 表达法）
        cam = plan_scene_camera([170.0, 60.0, -170.0, 65.0], role="overview")
        lng = cam["center"][0]
        # 短弧中点应在 180 附近，而不是 0 附近
        assert min(abs(lng - 180.0), abs(lng + 180.0)) < 5.0

    def test_zoom_not_exploded_by_dateline_span(self):
        cam = plan_scene_camera([170.0, 60.0, -170.0, 65.0], role="overview")
        # 若按 east-west 直读（340°），zoom 会坠到下限；短弧（20°）应给出更高 zoom
        assert cam["zoom"] > 3.5


class TestPitchAndMotion:
    def test_overview_low_pitch(self):
        cam = plan_scene_camera([0.0, 0.0, 10.0, 10.0], role="overview")
        assert cam["pitch"] <= 25

    def test_detail_scene_pitch(self):
        cam = plan_scene_camera([0.0, 0.0, 0.1, 0.1], role="detail", scene_mode="3d")
        assert 30 <= cam["pitch"] <= 60

    def test_2d_scene_zero_pitch(self):
        cam = plan_scene_camera([0.0, 0.0, 10.0, 10.0], role="detail", scene_mode="2d")
        assert cam["pitch"] == 0

    def test_reduced_motion_zero_transition(self):
        cam = plan_scene_camera([0.0, 0.0, 10.0, 10.0], role="detail", motion="reduced")
        assert cam["transition_ms"] == 0

    def test_full_motion_positive_transition(self):
        cam = plan_scene_camera([0.0, 0.0, 10.0, 10.0], role="detail")
        assert cam["transition_ms"] > 0


class TestDeterminismAndDegenerate:
    def test_replay_determinism(self):
        a = plan_scene_camera([10.0, 10.0, 20.0, 20.0], role="overview")
        b = plan_scene_camera([10.0, 10.0, 20.0, 20.0], role="overview")
        assert a == b

    def test_zero_area_bbox_does_not_crash(self):
        cam = plan_scene_camera([5.0, 5.0, 5.0, 5.0], role="detail")
        assert 3 <= cam["zoom"] <= 18

    def test_inverted_latitude_handled_conservatively(self):
        # south > north 的病态输入：保守钳制（max/min 交换），不崩溃不 NaN
        cam = plan_scene_camera([0.0, 40.0, 10.0, 30.0], role="overview")
        assert math.isfinite(cam["center"][1])
        assert 3 <= cam["zoom"] <= 18

    def test_polar_latitudes_clamped(self):
        cam = plan_scene_camera([-179.0, 84.0, 179.0, 89.0], role="overview")
        assert -85 <= cam["center"][1] <= 85
