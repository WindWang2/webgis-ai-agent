"""G3D04 · 3D cue 语料回归（M1/M2）。

钉住 ：mod:`scene3d.corpus` 全部 32 场景的 cue 投影 + 确定性 + 零推断
红线。这是"意图理解不再停留在自然语言建议"的机器化断言面：每条典型
话语 → typed 属性的映射一旦回归立即红。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.scene3d.corpus import gap_summary, intent_scenarios
from app.services.gis_harness.scene3d.cues import extract_scene3d_cues

pytestmark = pytest.mark.unit

SCENARIOS = intent_scenarios()


def test_corpus_shape_and_bounds():
    assert len(SCENARIOS) >= 30
    ids = [s["id"] for s in SCENARIOS]
    assert len(set(ids)) == len(ids)
    families = {s["family"] for s in SCENARIOS}
    assert len(families) >= 8
    # 负例（零推断）族必须在场且 ≥3 条
    assert sum(1 for s in SCENARIOS if s["family"] == "no_inference") >= 3
    # 缺口表非空（真实反映相邻方向依赖；全空说明缺口表退化为形式主义）
    gaps = gap_summary()
    assert gaps, "gap_summary 不应为空——链路缺口是本方向的边界事实"


@pytest.mark.parametrize("entry", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_cue_projection_per_scenario(entry):
    cues = extract_scene3d_cues(entry["utterance"])
    for key, expected in entry["expected_cues"].items():
        got = getattr(cues, key)
        if isinstance(expected, list):
            got = list(got)
        assert got == expected, (
            f"{entry['id']} ({entry['utterance']!r}) cue {key}: "
            f"expect {expected!r}, got {got!r}")


def test_cues_deterministic():
    for entry in SCENARIOS:
        a = extract_scene3d_cues(entry["utterance"])
        b = extract_scene3d_cues(entry["utterance"])
        assert a.model_dump() == b.model_dump()


def test_zero_inference_red_line():
    """'分布情况'类话语不得推断任何 3D 表达（M2 红线）。"""
    for plain in ("显示成都小学分布情况", "做一个人口密度热力图", "统计各区的 GDP 并制图",
                  "对比两条道路的服务范围", "查询附近的咖啡店"):
        cues = extract_scene3d_cues(plain)
        assert cues.dimension == "", plain
        assert cues.terrain_requested is None, plain
        assert cues.extrusion_requested is None, plain
        assert not cues.analysis_goals, plain


def test_empty_and_none_inputs():
    assert extract_scene3d_cues("").has_any() is False
    assert extract_scene3d_cues("   ").has_any() is False


def test_bearing_normalization_and_direction_semantics():
    # "从西南方向看"：相机位于西南（225°）、看向东北 → bearing=45°
    cues = extract_scene3d_cues("从西南方向看")
    assert cues.camera_bearing == 45.0 and cues.bearing_source == "from_direction"
    # "朝东南方向看"：看向东南 → bearing=135°
    cues = extract_scene3d_cues("朝东南方向看")
    assert cues.camera_bearing == 135.0 and cues.bearing_source == "to_direction"
    # 方位词最长优先："东南"不得被"东"截断
    cues = extract_scene3d_cues("从东北方向看")
    # 东北（相机位 45°）+180 → 225 → 归一化到 [-180,180] = -135
    assert cues.camera_bearing == -135.0


def test_pitch_without_number_is_not_fabricated():
    """无数字的俯仰词面不得造角度（决策核推荐档职责，cue 层零编造）。"""
    cues = extract_scene3d_cues("鸟瞰整个城区")
    assert cues.camera_pitch is None
    cues = extract_scene3d_cues("压低视角俯仰角 30 度")
    assert cues.camera_pitch == 30.0


def test_exaggeration_qualifier_implies_terrain():
    cues = extract_scene3d_cues("垂直夸张 3 倍")
    assert cues.exaggeration == 3.0 and cues.terrain_requested is True


def test_medium_specificity_beats_static():
    cues = extract_scene3d_cues("出一张 PDF 静态图")
    assert cues.medium == "export_pdf"
