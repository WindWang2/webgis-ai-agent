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
