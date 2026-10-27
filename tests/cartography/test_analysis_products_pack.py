"""G3D-V2-C 三维分析产品 pack 契约测试（ADR-0276）.

锁定：
- ANALYSIS_PRODUCTS_PACK 注册/确定性/可解析（模型、fallback、slot 类型）；
- 输出目标诚实性：三维语义产品（LOS/土方）不声明矢量目标；纯 2D 的
  route_profile_joint 合法声明 pdf/svg；
- 必填槽纪律：mountain_los/earthwork 缺 scene_disclosure → conformance
  error；earthwork 缺 methodology_note → error；route_profile_joint 缺
  elevation_profile → error（剖面面板是产品主体）；
- 组件工厂（G3D-V2-C 补的 elevation_profile/scene_disclosure 工厂）可经
  upsert 创建——模板槽与运行时创建路径对得上。
"""
from __future__ import annotations

import pytest

from app.lib.cartography.composition_conformance import conformance_report
from app.lib.cartography.composition_packs import COMPOSITION_PACK_TEMPLATES
from app.lib.cartography.composition_packs.analysis_products import (
    ANALYSIS_PRODUCTS_PACK,
)
from app.lib.cartography.composition_templates import (
    get_composition_template_registry,
)

pytestmark = pytest.mark.cartography

_ANALYSIS_PRODUCT_IDS = {
    "composition.mountain_los_obstruction",
    "composition.route_profile_joint",
    "composition.earthwork_cut_fill",
}

#: 各模板的必填槽期望（conformance 反证用）。
_REQUIRED_SLOTS = {
    "composition.mountain_los_obstruction": {"scene_disclosure", "statistics_panel"},
    "composition.route_profile_joint": {"elevation_profile", "statistics_panel"},
    "composition.earthwork_cut_fill": {
        "scene_disclosure", "methodology_note", "statistics_panel",
    },
}


class TestAnalysisProductsPackRegistration:
    def test_pack_registered_and_deterministic(self):
        treg = get_composition_template_registry()
        ids = [t.id for t in treg.all_templates()]
        for tid in _ANALYSIS_PRODUCT_IDS:
            assert tid in ids
        pack_ids = [t.id for t in COMPOSITION_PACK_TEMPLATES]
        assert len(pack_ids) == len(set(pack_ids))
        assert _ANALYSIS_PRODUCT_IDS <= set(pack_ids)

    def test_templates_validate(self):
        treg = get_composition_template_registry()
        for t in treg.all_templates():
            if t.id in _ANALYSIS_PRODUCT_IDS:
                assert treg.validate_template(t) == [], t.id

    def test_models_resolve(self):
        from app.lib.cartography.model_library import get_map_model_registry

        mreg = get_map_model_registry()
        treg = get_composition_template_registry()
        for t in treg.all_templates():
            if t.id in _ANALYSIS_PRODUCT_IDS:
                assert t.compatible_map_models, t.id
                for mid in t.compatible_map_models:
                    assert mreg.get(mid) is not None, f"{t.id}: model {mid}"

    def test_fallback_resolves(self):
        treg = get_composition_template_registry()
        for t in treg.all_templates():
            if t.id in _ANALYSIS_PRODUCT_IDS:
                assert treg.get(t.fallback_template) is not None, (
                    f"{t.id}: fallback {t.fallback_template}"
                )

    def test_output_targets_honesty(self):
        """三维语义产品不声明矢量；2D 剖面联合制图合法声明矢量。"""
        treg = get_composition_template_registry()
        for t in treg.all_templates():
            if t.id in _ANALYSIS_PRODUCT_IDS:
                if t.id == "composition.route_profile_joint":
                    assert set(t.output_targets) <= {
                        "interactive", "png", "pdf", "svg"}, t.id
                else:
                    assert set(t.output_targets) <= {"interactive", "png"}, t.id

    def test_required_slots_declared(self):
        treg = get_composition_template_registry()
        for t in treg.all_templates():
            if t.id in _REQUIRED_SLOTS:
                required = {
                    s.id for s in t.component_slots
                    if s.required or s.cardinality == "required"
                }
                assert _REQUIRED_SLOTS[t.id] <= required, (
                    f"{t.id}: missing { _REQUIRED_SLOTS[t.id] - required}"
                )


class TestAnalysisProductsConformance:
    """conformance_report 反证：缺必填槽必须 error（不静默放行）。"""

    def _report_without(self, template_id: str, drop_slot: str):
        treg = get_composition_template_registry()
        template = next(
            t for t in treg.all_templates() if t.id == template_id
        )
        # 反证构造：最小组件表（按模板 required 槽）去掉被测槽 → error
        components = [
            {"id": f"c-{i}", "type": ctype, "enabled": True}
            for i, ctype in enumerate(_minimal_types(template))
            if ctype != _slot_type(template, drop_slot)
        ]
        spec = {"layout": {"components": components}}
        return conformance_report(spec, template=template)

    def test_missing_scene_disclosure_is_error(self):
        for tid in (
            "composition.mountain_los_obstruction",
            "composition.earthwork_cut_fill",
        ):
            report = self._report_without(tid, "scene_disclosure")
            assert any(
                issue.severity == "error"
                and "scene_disclosure" in (issue.code + issue.message)
                for issue in report
            ), (tid, [i.code for i in report])

    def test_missing_elevation_profile_is_error(self):
        report = self._report_without(
            "composition.route_profile_joint", "elevation_profile"
        )
        assert any(
            issue.severity == "error"
            and "elevation_profile" in (issue.code + issue.message)
            for issue in report
        ), [i.code for i in report]

    def test_missing_methodology_note_is_error(self):
        report = self._report_without(
            "composition.earthwork_cut_fill", "methodology_note"
        )
        assert any(
            issue.severity == "error"
            and "methodology" in (issue.code + issue.message)
            for issue in report
        ), [i.code for i in report]


def _minimal_types(template) -> list:
    """按模板 required 槽推导最小组件类型集（反证构造用）。"""
    out = []
    for s in template.component_slots:
        if s.required or s.cardinality == "required":
            out.extend(s.allowed_component_types)
    return out


def _slot_type(template, slot_id: str) -> str:
    for s in template.component_slots:
        if s.id == slot_id:
            return s.allowed_component_types[0]
    return ""


class TestComponentFactoriesForProductSlots:
    def test_elevation_profile_and_scene_disclosure_factories_exist(self):
        """G3D-V2-C 补的工厂：upsert 创建路径覆盖模板槽类型。"""
        from app.services.gis_harness.components import _FACTORY_BY_TYPE

        assert "elevation_profile" in _FACTORY_BY_TYPE
        assert "scene_disclosure" in _FACTORY_BY_TYPE
        ep = _FACTORY_BY_TYPE["elevation_profile"]("profile-1")
        assert ep.type == "elevation_profile"
        sd = _FACTORY_BY_TYPE["scene_disclosure"]("scene-1")
        assert sd.type == "scene_disclosure"
