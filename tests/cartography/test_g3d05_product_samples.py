"""G3D05 M7：三套产品级样例的契约级 E2E（ADR-0275）.

样例全部使用**合成数据**（声明为合成，不冒充真实观测）：
(a) 城市三维建筑 + 行政区统计 —— urban_3d_skyline 模板；
(b) 山地 DEM + 路线剖面 + 视域 —— viewshed_scene_3d + elevation_profile；
(c) 三维地形专题 + StoryMap 场景叙事导出 —— terrain_3d_scene + spec v1.1。

每套走完整产品链：组合模板选择 → 组件装配 → conformance → 场景质量门 →
矢量孪生（降级收据）→ product_manifest 溯源 →（c 加 StoryMap 编译/资产
round-trip + 离线 bundle）。
"""
from __future__ import annotations

import json
import math

import pytest

from app.lib.cartography.composition_conformance import conformance_report
from app.lib.cartography.composition_templates import get_composition_template_registry
from app.lib.cartography.product_manifest import build_product_manifest
from app.lib.cartography.scene_quality import evaluate_scene_quality
from app.services.gis_harness.components import (
    elevation_profile_component,
    scene_disclosure_component,
)
from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed

pytestmark = pytest.mark.cartography

_SYNTH_NOTE = "合成样本数据（非实测）"


def _components_for(template_id: str) -> list:
    """按模板必填槽装配组件（3D 模板的 required 槽全集）。"""
    treg = get_composition_template_registry()
    template = treg.get(template_id)
    assert template is not None, template_id
    comps: list = [
        {"id": "title-1", "type": "title",
         "options": {"text": "G3D05 样例"}},
        {"id": "north-1", "type": "north_arrow"},
        {"id": "scale-1", "type": "scale_bar"},
        {"id": "attr-1", "type": "attribution",
         "options": {"text": "© 合成样本数据（演示用途）"}},
    ]
    # legend 类槽（conditional）给一个占位 legend
    if any(s.id == "legend" for s in template.component_slots):
        comps.append({"id": "legend-1", "type": "legend"})
    if any(s.id == "scene_disclosure" for s in template.component_slots):
        comps.append(scene_disclosure_component(
            extra_notes=[_SYNTH_NOTE]).to_mapspec())
    if any(s.id == "elevation_profile" for s in template.component_slots):
        comps.append(elevation_profile_component(
            points=[
                {"distance_m": float(i) * 400.0,
                 "elevation_m": 520.0 + 180.0 * math.sin(i / 2.5)}
                for i in range(12)
            ],
            vertical_datum="EGM96（合成）",
        ).to_mapspec())
    return comps


def _product_spec(template_id: str, *, scene_mode: str = "3d",
                  layers=None, view_pitch=None) -> dict:
    spec: dict = {
        "version": "1.6",
        "view": {"center": [104.06, 30.67], "zoom": 12},
        "sources": {
            "dem": {"type": "raster-dem"},
            "stats": {"type": "geojson", "data": "ref:ref:sample-synthetic"},
        },
        "layers": layers if layers is not None else [
            {"id": "layer-primary", "type": "fill",
             "source": "stats", "legend_spec": {"field": "value"}},
        ],
        "scene": {
            "mode": scene_mode,
            "terrain": {"source": "dem", "exaggeration": 1.5,
                        "vertical_unit": "m", "elevation_ref": "EGM96"},
            "camera": {"pitch": 50, "bearing": -15},
        },
        "layout": {
            "components": _components_for(template_id),
            "composition": {"template_id": template_id},
        },
    }
    if view_pitch is not None:
        spec["view"]["pitch"] = view_pitch
    return spec


class TestSampleAUrban3d:
    """(a) 城市三维建筑 + 行政区统计。"""

    def test_full_product_chain(self):
        spec = _product_spec(
            "composition.urban_3d_skyline",
            layers=[
                {"id": "layer-buildings", "type": "fill-extrusion",
                 "source": "stats",
                 "paint": {"fill-extrusion-height": ["get", "height_m"]},
                 "extrusion": {"height_field": "height_m", "height_unit": "m"},
                 "legend_spec": {"field": "height_m"}},
                {"id": "layer-districts", "type": "fill", "source": "stats",
                 "legend_spec": {"field": "pop_density"}},
            ],
        )
        # conformance：png 目标（模板声明支持）零 required 缺失
        report = conformance_report(spec, export_targets=("png",))
        assert "required_slot_missing" not in [i.code for i in report]
        # 质量门：挤出有高度证据 → 无 SCENE_EXTRUSION_NO_EVIDENCE
        quality = evaluate_scene_quality(spec, export_targets=("png",))
        codes = [f["code"] for f in quality["findings"]]
        assert "SCENE_EXTRUSION_NO_EVIDENCE" not in codes
        assert quality["passed"] is True
        # 矢量目标 → 降级披露（不出 silent）
        quality_vec = evaluate_scene_quality(spec, export_targets=("svg",))
        assert "SCENE_EXPORT_VECTOR_DEGRADE" in [
            f["code"] for f in quality_vec["findings"]]
        # manifest 溯源
        manifest = build_product_manifest(spec, export_format="png")
        assert manifest["scene"]["mode"] == "3d"
        assert manifest["scene"]["terrain"]["exaggeration"] == 1.5
        assert manifest["composition"]["template_id"] == "composition.urban_3d_skyline"


class TestSampleBMountainProfile:
    """(b) 山地 DEM + 路线剖面 + 视域。"""

    def test_full_product_chain(self):
        spec = _product_spec("composition.viewshed_scene_3d")
        res = compile_mapspec_to_svg_detailed(spec, include_chrome=True)
        # 矢量孪生：降级收据 + 剖面入图 + 披露行（2D 投影如实）
        codes = [d["code"] for d in res.diagnostics]
        assert "svg_3d_perspective_degrade" in codes
        assert 'data-kind="profile"' in res.svg
        assert "2D 投影" in res.svg and "1.5×" in res.svg
        assert "EGM96" in res.svg
        # 质量门：矢量目标降级披露 + 透视比例尺（camera.pitch=50>10，
        # scale_bar 在场且 scene_disclosure 在场 → info 而非 warning）
        quality = evaluate_scene_quality(spec, export_targets=("svg", "png"))
        by_code = {f["code"]: f for f in quality["findings"]}
        assert by_code.get("SCENE_EXPORT_VECTOR_DEGRADE") is not None
        scale = by_code.get("SCENE_SCALE_UNTRUSTWORTHY_PERSPECTIVE")
        assert scale is not None and scale["severity"] == "info"

    def test_synthetic_declared_not_fabricated(self):
        """剖面数据必须显式合成声明 —— 不冒充实测。"""
        spec = _product_spec("composition.viewshed_scene_3d")
        res = compile_mapspec_to_svg_detailed(spec, include_chrome=True)
        assert _SYNTH_NOTE in res.svg


class TestSampleCTerrainStorymap:
    """(c) 三维地形专题 + StoryMap 场景叙事导出。"""

    def test_storymap_v11_roundtrip_and_bundle(self):
        from app.lib.storymap.spec import StoryMapSpec
        from app.lib.storymap.export_packager import (
            build_story_bundle, bundle_to_json,
        )
        spec = _product_spec("composition.terrain_3d_scene")
        # 矢量孪生（png 截图的矢量对照）
        res = compile_mapspec_to_svg_detailed(spec, include_chrome=True)
        assert "svg_3d_perspective_degrade" in [
            d["code"] for d in res.diagnostics]

        story = StoryMapSpec.model_validate({
            "schema_version": "1.1",
            "metadata": {"title": "三维地形叙事（合成样本）"},
            "chapters": [
                {"id": "ch-intro", "title": "引言", "narrative": "合成 DEM 场景",
                 "arc_role": "introduction", "visible_layer_ids": []},
                {"id": "ch-focus", "title": "重点解剖", "narrative": "东坡剖面",
                 "arc_role": "focus_dissection",
                 "visible_layer_ids": ["layer-primary"]},
            ],
            "camera_keyframes": [
                {"chapter_id": "ch-intro", "t": 0, "center": [104.06, 30.67],
                 "zoom": 11, "pitch": 10, "bearing": 0,
                 "scene": {"mode": "2d"}},
                {"chapter_id": "ch-focus", "t": 0, "center": [104.1, 30.7],
                 "zoom": 13, "pitch": 45, "bearing": 15,
                 "scene": {"mode": "2.5d", "exaggeration": 1.5}},
            ],
        })
        # 离线 bundle：场景章节字段随包走（重放语义保留），敏感键脱敏
        bundle = build_story_bundle(story, layers=[], mapspec=spec)
        payload = json.loads(bundle_to_json(bundle))
        kfs = payload["spec"]["camera_keyframes"]
        assert kfs[1]["scene"] == {"mode": "2.5d", "exaggeration": 1.5}
        assert payload["spec"]["chapters"][1]["visible_layer_ids"] == [
            "layer-primary"]

    def test_compiler_gate_no_elevation_no_scene_claims(self):
        """无 raster-dem 会话编译 → 场景档不写（不虚构立体叙事）。"""
        from app.lib.storymap.story_compiler import compile_story_map

        story = compile_story_map(messages=[
            {"role": "user", "content": "分析地形"},
            {"role": "assistant", "content": "完成"},
        ])
        assert all(kf.scene is None for kf in story.camera_keyframes)
