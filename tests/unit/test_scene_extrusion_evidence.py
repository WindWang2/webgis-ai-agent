"""Extrusion evidence passthrough tests (ADR-0201 M4).

Oracle anchors:
- 挤出层携带 elevation_ref 证据溯源（工具 → metadata.extrusion →
  MapSpecLayer.extrusion.elevation_ref → v1.4 schema 校验通过）。
- 降级披露词表注册（scene_extrusion_no_height_evidence /
  scene_terrain_unavailable 是 render_diagnostics 权威词表成员）。
"""
from __future__ import annotations

from app.lib.cartography.mapspec_schema import parse_mapspec
from app.lib.cartography.render_diagnostics import RENDER_DIAGNOSTICS
from app.services.analysis_cartography_converter import convert_analysis_to_mapspec_layer


def _make_polygon_fc(properties_list):
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [104.0 + i * 0.05, 30.6],
                            [104.0 + (i + 1) * 0.05, 30.6],
                            [104.0 + (i + 1) * 0.05, 30.65],
                            [104.0 + i * 0.05, 30.65],
                            [104.0 + i * 0.05, 30.6],
                        ]
                    ],
                },
                "properties": props,
            }
            for i, props in enumerate(properties_list)
        ],
    }


class TestElevationRefPassthrough:
    def test_converter_passes_elevation_ref_to_layer(self):
        fc = _make_polygon_fc(
            [{"gdp": v} for v in (100, 200, 300, 400, 500)]
        )
        payload = {
            "type_hint": "extrusion_3d",
            "geojson": fc,
            "metadata": {
                "extrusion": {
                    "height_field": "gdp",
                    "height_unit": "亿元",
                    "elevation_ref": "ref:geoai-analysis-0123456789abcdef",
                }
            },
        }
        layer, _, _ = convert_analysis_to_mapspec_layer(payload)
        assert layer["type"] == "fill-extrusion"
        assert layer["extrusion"]["elevation_ref"] == "ref:geoai-analysis-0123456789abcdef"

    def test_converter_omits_elevation_ref_when_absent(self):
        fc = _make_polygon_fc(
            [{"gdp": v} for v in (100, 200, 300, 400, 500)]
        )
        payload = {
            "type_hint": "extrusion_3d",
            "geojson": fc,
            "metadata": {"extrusion": {"height_field": "gdp"}},
        }
        layer, _, _ = convert_analysis_to_mapspec_layer(payload)
        assert "elevation_ref" not in layer["extrusion"]

    def test_evidenced_extrusion_layer_is_schema_valid(self):
        fc = _make_polygon_fc(
            [{"gdp": v} for v in (100, 200, 300, 400, 500)]
        )
        payload = {
            "type_hint": "extrusion_3d",
            "geojson": fc,
            "metadata": {
                "extrusion": {
                    "height_field": "gdp",
                    "elevation_ref": "ref:geoai-analysis-0123456789abcdef",
                }
            },
        }
        layer, _, _ = convert_analysis_to_mapspec_layer(payload)
        spec = {
            "version": "1.4",
            "sources": {"s1": {"type": "geojson", "inlineData": fc}},
            "layers": [layer],
        }
        result = parse_mapspec(spec)
        assert result.valid is True, [d.to_dict() for d in result.invalid_fields]


class TestSceneDegradationVocabulary:
    def test_scene_codes_registered(self):
        assert "scene_extrusion_no_height_evidence" in RENDER_DIAGNOSTICS
        assert "scene_terrain_unavailable" in RENDER_DIAGNOSTICS

    def test_scene_code_severities(self):
        assert (
            RENDER_DIAGNOSTICS["scene_extrusion_no_height_evidence"].severity == "info"
        )
        assert RENDER_DIAGNOSTICS["scene_terrain_unavailable"].severity == "warning"
