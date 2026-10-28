"""G3D05 三维制图产品 pack 契约测试（ADR-0275）.

锁定：
- SCENE_3D_PACK 注册/确定性/可解析（模型、fallback、slot 类型）；
- 3D 模板输出目标诚实性（不声明矢量目标 —— 矢量由导出管线显式降级）；
- scene_disclosure / elevation_profile 组件 payload 契约（禁第二真相、
  单调里程、NaN/Inf、有界、ref 协议）；
- 矢量孪生渲染 + 结构化省略回执（profileRef 不可用不伪造）；
- live↔export 语义语料闭环（marker 契约）；
- conformance：3D 模板缺 scene_disclosure 必填槽 → error。
"""
from __future__ import annotations

import math

import pytest

from app.contracts.cartography_components import (
    MAX_PROFILE_POINTS,
    validate_elevation_profile_payload,
    validate_scene_disclosure_payload,
)
from app.lib.cartography.composition_conformance import conformance_report
from app.lib.cartography.composition_packs import COMPOSITION_PACK_TEMPLATES
from app.lib.cartography.composition_templates import (
    get_composition_template_registry,
)
from app.lib.cartography.component_renderers import (
    get_component_renderer_registry,
)
from app.services.gis_harness.components import (
    elevation_profile_component,
    scene_disclosure_component,
)

pytestmark = pytest.mark.cartography

_SCENE_3D_IDS = {
    "composition.terrain_3d_scene",
    "composition.urban_3d_skyline",
    "composition.viewshed_scene_3d",
}


def _profile_points(n: int = 8) -> list:
    return [
        {"distance_m": float(i) * 300.0,
         "elevation_m": 500.0 + 200.0 * math.sin(i / 3.0)}
        for i in range(n)
    ]


class TestScene3dPackRegistration:
    def test_pack_registered_and_deterministic(self):
        treg = get_composition_template_registry()
        ids = [t.id for t in treg.all_templates()]
        for tid in _SCENE_3D_IDS:
            assert tid in ids
        # 追加不破坏既有 pack（注册顺序稳定、无重复 id 由 registry 保证）
        pack_ids = [t.id for t in COMPOSITION_PACK_TEMPLATES]
        assert len(pack_ids) == len(set(pack_ids))

    def test_templates_validate(self):
        treg = get_composition_template_registry()
        for t in treg.all_templates():
            if t.id in _SCENE_3D_IDS:
                assert treg.validate_template(t) == [], t.id

    def test_scene_disclosure_slot_required(self):
        treg = get_composition_template_registry()
        for t in treg.all_templates():
            if t.id in _SCENE_3D_IDS:
                sd = next(
                    (s for s in t.component_slots if s.id == "scene_disclosure"),
                    None,
                )
                assert sd is not None, t.id
                assert sd.required is True and sd.cardinality == "required"
                assert sd.allowed_component_types == ["scene_disclosure"]

    def test_no_vector_output_claims(self):
        """3D 模板不得声明 pdf/svg/print —— 矢量表达必须走显式降级。"""
        treg = get_composition_template_registry()
        for t in treg.all_templates():
            if t.id in _SCENE_3D_IDS:
                assert set(t.output_targets) <= {"interactive", "png"}, t.id

    def test_models_resolve(self):
        from app.lib.cartography.model_library import get_map_model_registry

        mreg = get_map_model_registry()
        treg = get_composition_template_registry()
        for t in treg.all_templates():
            if t.id in _SCENE_3D_IDS:
                for mid in t.compatible_map_models:
                    assert mreg.get(mid) is not None, f"{t.id}: model {mid}"


class TestSceneDisclosurePayload:
    def test_empty_options_ok(self):
        assert validate_scene_disclosure_payload({}) is None

    def test_banned_scene_fields_rejected(self):
        # 第二真相防线：options 不得复制 spec.scene 字段
        for banned in ("mode", "exaggeration", "pitch", "bearing", "verticalUnit"):
            err = validate_scene_disclosure_payload({banned: "3d"})
            assert err is not None and banned in err

    def test_extra_notes_bounded(self):
        ok = {"extraNotes": ["合成样本", "视域半径 2km"]}
        assert validate_scene_disclosure_payload(ok) is None
        assert validate_scene_disclosure_payload(
            {"extraNotes": ["x"] * 5}) is not None
        assert validate_scene_disclosure_payload(
            {"extraNotes": ["x" * 200]}) is not None
        assert validate_scene_disclosure_payload(
            {"extraNotes": "not-a-list"}) is not None

    def test_factory_roundtrip(self):
        comp = scene_disclosure_component(extra_notes=["DEM：合成样本"])
        assert comp.type == "scene_disclosure"
        assert comp.options == {"extraNotes": ["DEM：合成样本"]}


class TestElevationProfilePayload:
    def test_valid_points(self):
        opts = {"points": _profile_points(), "unit": "m", "verticalDatum": "EGM96"}
        assert validate_elevation_profile_payload(opts) is None

    def test_ref_only(self):
        assert validate_elevation_profile_payload(
            {"profileRef": "ref:profile-route-1"}) is None
        # 非 ref: 前缀拒绝
        assert validate_elevation_profile_payload(
            {"profileRef": "http://evil"}) is not None

    def test_neither_points_nor_ref_rejected(self):
        assert validate_elevation_profile_payload({"unit": "m"}) is not None

    def test_monotonic_distance_enforced(self):
        pts = _profile_points()
        pts[3]["distance_m"] = pts[1]["distance_m"] - 1.0
        assert validate_elevation_profile_payload({"points": pts}) is not None

    def test_nan_inf_rejected(self):
        pts = _profile_points()
        pts[2]["elevation_m"] = float("nan")
        assert validate_elevation_profile_payload({"points": pts}) is not None
        pts[2]["elevation_m"] = float("inf")
        assert validate_elevation_profile_payload({"points": pts}) is not None

    def test_min_two_points_and_cap(self):
        assert validate_elevation_profile_payload(
            {"points": _profile_points(1)}) is not None
        big = [
            {"distance_m": float(i), "elevation_m": 1.0}
            for i in range(MAX_PROFILE_POINTS + 1)
        ]
        assert validate_elevation_profile_payload({"points": big}) is not None

    def test_factory_rejects_bad_input(self):
        with pytest.raises(ValueError):
            elevation_profile_component(
                points=[{"distance_m": 0, "elevation_m": float("nan")},
                        {"distance_m": 1, "elevation_m": 2}])
        comp = elevation_profile_component(
            points=_profile_points(), vertical_datum="EGM96")
        assert comp.options["verticalDatum"] == "EGM96"
        assert len(comp.options["points"]) == 8


class TestRendererMatrixTruth:
    def test_new_types_publication_native(self):
        reg = get_component_renderer_registry()
        for ctype in ("scene_disclosure", "elevation_profile"):
            support = reg.support_for(ctype)
            assert support is not None
            assert "interactive" in support.renderers
            for target in ("png", "pdf", "svg"):
                assert target in support.exporters, (ctype, target)
            assert support.publication is True


class TestVectorTwinScene3d:
    def _spec(self, *, with_scene=True, profile_ref_only=False,
              include_disclosure=True):
        components: list = []
        if include_disclosure:
            components.append({
                "id": "sd", "type": "scene_disclosure",
                "position": "bottom-right",
                "options": {"extraNotes": ["DEM：合成样本"]},
            })
        if profile_ref_only:
            components.append({
                "id": "ep", "type": "elevation_profile",
                "position": "bottom-left",
                "options": {"profileRef": "ref:profile-missing"},
            })
        else:
            components.append({
                "id": "ep", "type": "elevation_profile",
                "position": "bottom-left",
                "options": {"points": _profile_points(),
                            "verticalDatum": "EGM96"},
            })
        spec = {
            "version": "1.6",
            "view": {"center": [104.0, 30.6], "zoom": 11},
            "sources": {}, "layers": [],
            "layout": {"components": components},
        }
        if with_scene:
            spec["scene"] = {
                "mode": "3d",
                "terrain": {"source": "dem", "exaggeration": 1.5,
                            "vertical_unit": "m", "elevation_ref": "EGM96"},
                "camera": {"pitch": 50, "bearing": -15},
            }
        return spec

    def test_disclosure_states_vector_degradation(self):
        from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed
        res = compile_mapspec_to_svg_detailed(self._spec(), include_chrome=True)
        assert "data-kind=\"scene\"" in res.svg
        assert "2D 投影" in res.svg and "三维透视不可矢量化" in res.svg
        assert "1.5×" in res.svg and "垂直比例夸张" in res.svg
        assert "EGM96" in res.svg

    def test_no_scene_block_disclosure_absent(self):
        from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed
        res = compile_mapspec_to_svg_detailed(
            self._spec(with_scene=False), include_chrome=True)
        # 无场景状态不虚构披露内容（面板缺席，但 profile 正常画）
        assert "三维透视不可矢量化" not in res.svg

    def test_profile_renders_as_line_chart(self):
        from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed
        res = compile_mapspec_to_svg_detailed(self._spec(), include_chrome=True)
        assert "data-kind=\"profile\"" in res.svg
        assert "高程剖面" in res.svg

    def test_profile_ref_only_omitted_receipt(self):
        from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed
        res = compile_mapspec_to_svg_detailed(
            self._spec(profile_ref_only=True), include_chrome=True)
        # ref 不可用 → 结构化省略，不伪造数据
        assert "data-kind=\"profile\"" not in res.svg

    def test_semantic_corpus_roundtrip(self):
        from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed
        from app.lib.cartography.export_semantic_corpus import (
            compare_semantics,
            expected_semantics,
            extract_semantics,
        )
        spec = self._spec()
        res = compile_mapspec_to_svg_detailed(spec, include_chrome=True)
        report = compare_semantics(
            expected_semantics(spec), extract_semantics(res.svg))
        assert report["ok"] is True
        families = {f["family"]: f["verdict"] for f in report["families"]}
        assert families.get("scene_disclosure") == "match"
        assert families.get("elevation_profile") == "match"


class TestConformanceRequiredDisclosure:
    @staticmethod
    def _base_components(with_disclosure: bool) -> list:
        """除 scene_disclosure 外的必填槽位全部在场（孤立断言目标）。"""
        comps = [
            {"id": "title-1", "type": "title"},
            {"id": "north-1", "type": "north_arrow"},
            {"id": "scale-1", "type": "scale_bar"},
            {"id": "attr-1", "type": "attribution"},
        ]
        if with_disclosure:
            comps.append({"id": "sd-1", "type": "scene_disclosure"})
        return comps

    def test_missing_scene_disclosure_is_error(self):
        treg = get_composition_template_registry()
        template = treg.get("composition.terrain_3d_scene")
        assert template is not None
        spec = {
            "layers": [{"id": "layer-primary"}],
            "layout": {"components": self._base_components(False)},
        }
        report = conformance_report(spec, template=template,
                                    export_targets=("png",))
        codes = [i.code for i in report]
        assert "required_slot_missing" in codes
        issue = next(i for i in report if i.code == "required_slot_missing")
        assert "scene_disclosure" in issue.message

    def test_with_scene_disclosure_passes_required(self):
        treg = get_composition_template_registry()
        template = treg.get("composition.terrain_3d_scene")
        spec = {
            "layers": [{"id": "layer-primary"}],
            "layout": {"components": self._base_components(True)},
        }
        report = conformance_report(spec, template=template,
                                    export_targets=("png",))
        assert "required_slot_missing" not in [i.code for i in report]
