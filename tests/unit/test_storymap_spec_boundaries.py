"""StoryMap spec/相机规划边界测试（#1552 测试债清偿，G08 后续）。

test_storymap_orchestrator.py 已覆盖主路径（8 步 trace 编译、轨迹连续性、
bundle 脱敏、API 契约）。本文件锁定其未触及的**边界与错误分支**：

- 载荷深度门卫：64/65 层精确边界、max_depth 参数、自引用环终止；
- 孤立代理字符剥除（合法代理对保留、嵌套结构递归）；
- 朗读时长估算 120s 上限 / 2s 下限；
- CameraKeyframe 全字段边界（t/zoom/center 长度/easing 词表/NaN 拒绝）；
- StoryMapSpec 章节下限、StoryChapter 时长默认值与负值拒绝；
- 相机跨度规则：宏观跨度 pitch 压平 ≤5°、超大跨度 zoom 钳到下限、
  微观跨度 +10° 抬升、未知弧角色基准俯仰 20°；
- 纯数学助手：贝塞尔缓动端点/单调性、bearing 最短弧 180° 平局；
- 轨迹奇点：空关键帧、samples_per_leg=0、空轨迹校验、跨 ±180° 中心跳变
  不误报。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.lib.storymap.camera_planner import (
    _shortest_bearing_delta,
    build_camera_track,
    cubic_bezier_ease,
    plan_camera_for_bbox,
    validate_track,
    CameraSample,
)
from app.lib.storymap.spec import (
    NARRATIVE_ARC,
    StoryChapter,
    StoryMapSpec,
    assert_json_depth,
    estimate_duration,
    strip_surrogates,
)


def _nested_dicts(depth: int) -> dict:
    """构造最大深度为 depth 的 dict 链（根容器算 1 层）。"""
    root: dict = {}
    cur = root
    for _ in range(depth - 1):
        cur["k"] = {}
        cur = cur["k"]
    return root


class TestPayloadDepthGuard:
    def test_depth_boundary_64_pass_65_fail(self):
        assert_json_depth(_nested_dicts(64))  # 恰在默认上限内
        with pytest.raises(ValueError, match="exceeds maximum nesting depth"):
            assert_json_depth(_nested_dicts(65))

    def test_depth_max_depth_kwarg_respected(self):
        assert_json_depth(_nested_dicts(4), max_depth=4)
        with pytest.raises(ValueError):
            assert_json_depth(_nested_dicts(5), max_depth=4)

    def test_depth_cycle_guard_terminates(self):
        # 自引用 dict 不能撞 RecursionError（迭代遍历 + seen 去重），
        # 且须正常终结（返回 None，debt-ratchet 要求显式断言）。
        cyclic: dict = {"a": 1}
        cyclic["self"] = cyclic
        assert assert_json_depth(cyclic) is None


class TestTextAndDuration:
    def test_strip_surrogates_lone_keeps_pairs_nested(self):
        # 孤立代理 \ud800/\udfff 剥除；合法代理对（emoji）原样保留。
        assert strip_surrogates("ok\ud800bad\udfff") == "okbad"
        assert strip_surrogates("x\U0001F600y") == "x\U0001F600y"
        nested = {"a": ["\ud800"], "b": {"c": "hi\udfff"}}
        assert strip_surrogates(nested) == {"a": [""], "b": {"c": "hi"}}

    def test_estimate_duration_clamps_floor_and_ceiling(self):
        assert estimate_duration("") == 2.0  # 下限
        assert estimate_duration("汉" * 2000) == 120.0  # 上限（500s 压回 120）


class TestCameraKeyframeBounds:
    @pytest.mark.parametrize(
        "override",
        [
            {"t": -0.1}, {"t": 1.1},
            {"zoom": 2.9}, {"zoom": 18.1},
            {"center": [116.4]}, {"center": [1.0, 2.0, 3.0]},
            {"easing": "bounce"},
            {"pitch": float("nan")}, {"bearing": float("nan")},
        ],
        ids=["t-lo", "t-hi", "zoom-lo", "zoom-hi",
             "center-1", "center-3", "easing", "nan-pitch", "nan-bearing"],
    )
    def test_camera_keyframe_bounds_rejected(self, override):
        from app.lib.storymap.spec import CameraKeyframe

        base = dict(chapter_id="c", t=0.5, center=[116.4, 39.9],
                    zoom=5.0, pitch=10.0, bearing=0.0)
        with pytest.raises(ValidationError):
            CameraKeyframe(**{**base, **override})


class TestSpecChapterGuards:
    def test_spec_requires_at_least_one_chapter(self):
        with pytest.raises(ValidationError):
            StoryMapSpec(chapters=[])

    def test_chapter_duration_hint_default_and_negative_rejected(self):
        ch = StoryChapter(id="c", title="t", narrative="x" * 40,
                          arc_role="introduction")
        # 缺省 → 由正文估算（40 字 / 4 字每秒 = 10s）
        assert ch.duration_hint_s == estimate_duration("x" * 40) == 10.0
        with pytest.raises(ValidationError):
            StoryChapter(id="c", title="t", narrative="n",
                         arc_role="introduction", duration_hint_s=-1.0)


class TestCameraPlannerSpanRules:
    def test_macro_span_pitch_capped_all_arcs(self):
        # 跨度 > 10° → pitch 压平至 ≤5°（宏观叙事平面化）。
        for role in NARRATIVE_ARC:
            view = plan_camera_for_bbox((0.0, 0.0, 40.0, 10.0), role)
            assert view["pitch"] <= 5.0, role

    def test_huge_span_zoom_clamped_to_min(self):
        # 300° 跨度：log2(360/300)+1 ≈ 1.27 → 钳到 ZOOM_MIN=3.0。
        view = plan_camera_for_bbox((0.0, 0.0, 300.0, 10.0), "macro_situation")
        assert view["zoom"] == 3.0

    def test_pitch_base_fallback_and_micro_boost(self):
        mid = (116.0, 39.0, 118.0, 41.0)  # 2° 跨度：无微调
        assert plan_camera_for_bbox(mid, "no_such_role")["pitch"] == 20.0
        # 点状 bbox（跨度 < 0.05°）→ 基准 +10°（解剖 45 → 55）
        point = (116.4, 39.9, 116.40001, 39.90001)
        assert plan_camera_for_bbox(point, "focus_dissection")["pitch"] == 55.0


class TestPlannerMathHelpers:
    def test_planner_math_helpers(self):
        # 贝塞尔缓动：端点精确、值域内、[0,1] 网格单调不减。
        assert cubic_bezier_ease(0.0) == 0.0
        assert cubic_bezier_ease(1.0) == 1.0
        ys = [cubic_bezier_ease(i / 100.0) for i in range(101)]
        assert all(a <= b + 1e-12 for a, b in zip(ys, ys[1:]))
        assert 0.0 <= cubic_bezier_ease(0.5) <= 1.0
        # bearing 最短弧：±180° 平局取 -180（确定性）；跨极取短程 +20。
        assert _shortest_bearing_delta(0.0, 180.0) == -180.0
        assert _shortest_bearing_delta(170.0, -170.0) == 20.0


class TestTrackEdgeCases:
    def test_build_camera_track_empty_and_min_samples(self):
        assert build_camera_track([]) == []
        # samples_per_leg=0 折叠为 1（max(1, int)），不崩不除零。
        from app.lib.storymap.spec import CameraKeyframe

        kfs = [
            CameraKeyframe(chapter_id="a", t=0.0, center=[0.0, 0.0], zoom=5.0),
            CameraKeyframe(chapter_id="b", t=1.0, center=[10.0, 10.0], zoom=6.0),
        ]
        samples = build_camera_track(kfs, samples_per_leg=0)
        assert len(samples) == 2  # 1 采样/leg + 终点
        assert samples[-1].center == [10.0, 10.0]

    def test_validate_track_empty_antimeridian_and_jump(self):
        assert validate_track([]) == []
        s0 = CameraSample(0.0, [179.9, 0.0], 5.0, 10.0, 0.0)
        # 跨 ±180°：真实位移仅 0.2°，min(dlng, 360-dlng) 分支不得误报。
        s_wrap = CameraSample(1.0, [-179.9, 0.0], 5.0, 10.0, 0.0)
        assert validate_track([s0, s_wrap]) == []
        # 真跳变 79.9° → 必须报。
        s_jump = CameraSample(1.0, [100.0, 0.0], 5.0, 10.0, 0.0)
        assert validate_track([s0, s_jump])
