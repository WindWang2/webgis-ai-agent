"""Map Operation 契约测试（M1）：词表闭合/越权字段/有界/paint·symbol 白名单。"""
from __future__ import annotations

import pytest

from app.services.gis_harness.map_operations.contracts import (
    EFFECT_KINDS,
    MAX_EFFECTS,
    SCHEMA_VERSION,
    MapOperationRequest,
    sensitive_request,
)


def _req(**kwargs) -> MapOperationRequest:
    base = {
        "operation_id": "mop-test0001",
        "effects": [{"kind": "set_layer_visibility", "target": "heat", "visible": False}],
    }
    base.update(kwargs)
    return MapOperationRequest(**base)


class TestEffectVocabulary:
    def test_closed_kind_vocabulary(self):
        with pytest.raises(ValueError):
            _req(effects=[{"kind": "exec_shell"}])

    def test_extra_fields_rejected(self):
        with pytest.raises(ValueError):
            _req(effects=[{"kind": "set_layer_visibility", "target": "a",
                           "visible": True, "evil": "payload"}])

    def test_request_extra_fields_rejected(self):
        with pytest.raises(ValueError):
            _req(session_id="client-supplied")

    def test_schema_version_pinned(self):
        with pytest.raises(ValueError):
            _req(schema_version="map_operation.v0")

    def test_mode_vocabulary(self):
        with pytest.raises(ValueError):
            _req(mode="execute")

    def test_effects_bounded(self):
        effects = [
            {"kind": "set_layer_opacity", "target": f"l{i}", "opacity": 0.5}
            for i in range(MAX_EFFECTS + 1)
        ]
        with pytest.raises(ValueError):
            _req(effects=effects)

    def test_operation_id_bounds(self):
        with pytest.raises(ValueError):
            _req(operation_id="short")
        with pytest.raises(ValueError):
            _req(operation_id="x" * 65)


class TestViewportBounds:
    """反证（review P2）：center/bbox 只校验长度不够 —— NaN/越界/倒置会经
    _bbox_view / _viewport_after 静默写进 MapSpec view（zoom 夹到 18）。"""

    def test_center_rejects_non_finite(self):
        for bad in ([float("nan"), 30.6], [104.0, float("inf")]):
            with pytest.raises(ValueError):
                _req(effects=[{"kind": "set_viewport", "center": bad}])

    def test_center_rejects_out_of_wgs84_range(self):
        for bad in ([200.0, 30.6], [104.0, 99.0], [-181.0, 0.0]):
            with pytest.raises(ValueError):
                _req(effects=[{"kind": "set_viewport", "center": bad}])

    def test_center_accepts_valid(self):
        req = _req(effects=[{"kind": "set_viewport",
                             "center": [104.05, 30.65], "zoom": 12}])
        assert req.effects[0].center == [104.05, 30.65]

    def test_bbox_rejects_inverted(self):
        # west>east / south>north：span=1e-9 → zoom 夹到 18、中心取中点
        for bad in ([104.2, 30.8, 103.9, 30.6], [103.9, 30.9, 104.2, 30.6]):
            with pytest.raises(ValueError):
                _req(effects=[{"kind": "set_viewport", "bbox": bad}])

    def test_bbox_rejects_non_finite_and_out_of_range(self):
        for bad in ([float("nan"), 30.6, 104.2, 30.8],
                    [103.9, 30.6, 200.0, 30.8],
                    [103.9, 30.6, 104.2, 91.0]):
            with pytest.raises(ValueError):
                _req(effects=[{"kind": "set_viewport", "bbox": bad}])

    def test_bbox_accepts_valid(self):
        req = _req(effects=[{"kind": "set_viewport",
                             "bbox": [103.93, 30.62, 104.20, 30.80]}])
        assert req.effects[0].bbox == [103.93, 30.62, 104.20, 30.80]


class TestPaintWhitelist:
    def test_opacity_family_accepted(self):
        req = _req(effects=[
            {"kind": "set_layer_paint", "target": "a", "paint": {"circle-opacity": 0.7}},
        ])
        assert req.effects[0].paint == {"circle-opacity": 0.7}

    def test_size_key_rejected_with_hint(self):
        with pytest.raises(ValueError) as ei:
            _req(effects=[
                {"kind": "set_layer_paint", "target": "a",
                 "paint": {"circle-radius": 5}},
            ])
        assert "set_layer_symbol" in str(ei.value)

    def test_opacity_range(self):
        with pytest.raises(ValueError):
            _req(effects=[
                {"kind": "set_layer_paint", "target": "a", "paint": {"opacity": 1.5}},
            ])


class TestSymbolWhitelist:
    def test_symbol_property_vocabulary(self):
        with pytest.raises(ValueError):
            _req(effects=[
                {"kind": "set_layer_symbol", "target": "a",
                 "symbol_property": "color", "symbol_value": "#f00"},
            ])

    def test_symbol_scale_bounds(self):
        with pytest.raises(ValueError):
            _req(effects=[
                {"kind": "set_layer_symbol", "target": "a",
                 "symbol_property": "point_size", "symbol_scale": 0},
            ])

    def test_value_or_scale_required_only_at_compile(self):
        req = _req(effects=[
            {"kind": "set_layer_symbol", "target": "a", "symbol_property": "point_size"},
        ])
        assert req.effects[0].symbol_value is None


class TestComponentPatch:
    def test_allowed_keys(self):
        req = _req(effects=[
            {"kind": "set_component", "target": "legend-1",
             "component_patch": {"position": "bottom-left"}},
        ])
        assert req.effects[0].component_patch == {"position": "bottom-left"}

    def test_forbidden_keys_rejected(self):
        with pytest.raises(ValueError):
            _req(effects=[
                {"kind": "set_component", "target": "legend-1",
                 "component_patch": {"options": {"inject": "js"}}},
            ])


class TestSensitive:
    def test_remove_is_sensitive(self):
        assert sensitive_request(
            _req(effects=[{"kind": "remove_layer", "target": "a"}]).effects
        )

    def test_visibility_not_sensitive(self):
        assert not sensitive_request(_req().effects)


class TestSchemaConstants:
    def test_version_and_kinds(self):
        assert SCHEMA_VERSION == "map_operation.v1"
        assert len(EFFECT_KINDS) == 8
