"""导出 parity（方向 5 / C6）：Python 孪生 + expected_semantics 对账。"""
from __future__ import annotations

from app.lib.cartography.export_semantic_corpus import expected_semantics
from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed


def _points(n: int) -> dict:
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point",
                                         "coordinates": [104 + i * 0.01, 30.6]},
         "properties": {"v": float(i)}}
        for i in range(n)
    ]}


def _spec(layers: list, zoom: float = 5.0) -> dict:
    return {"version": "1.6", "view": {"center": [104, 30.6], "zoom": zoom},
            "sources": {"S": {"type": "geojson", "inlineData": _points(4)}},
            "layers": layers}


class TestTwinAdjudication:
    def test_gated_layer_omitted_with_diagnostic(self):
        spec = _spec([
            {"id": "G", "source": "S", "type": "circle",
             "visibility": {"min_zoom": 14.0}},
            {"id": "V", "source": "S", "type": "circle"},
        ])
        comp = compile_mapspec_to_svg_detailed(spec)
        codes = [d["code"] for d in comp.diagnostics]
        assert "layer_gated_by_zoom" in codes
        gated = next(d for d in comp.diagnostics if d["code"] == "layer_gated_by_zoom")
        assert gated["layer_id"] == "G"

    def test_representation_hidden_omitted_with_diagnostic(self):
        spec = _spec([
            {"id": "H", "source": "S", "type": "circle",
             "representation": {"windows": [
                 {"min_zoom": 0, "max_zoom": 8, "mode": "hidden"},
                 {"min_zoom": 8, "max_zoom": 24, "mode": "raw"}]}},
        ], zoom=5.0)
        comp = compile_mapspec_to_svg_detailed(spec)
        codes = [d["code"] for d in comp.diagnostics]
        assert "layer_representation_hidden" in codes

    def test_same_spec_at_higher_zoom_renders(self):
        layers = [{"id": "H", "source": "S", "type": "circle",
                   "representation": {"windows": [
                       {"min_zoom": 0, "max_zoom": 8, "mode": "hidden"},
                       {"min_zoom": 8, "max_zoom": 24, "mode": "raw"}]}}]
        comp = compile_mapspec_to_svg_detailed(_spec(layers, zoom=10.0))
        codes = [d["code"] for d in comp.diagnostics]
        assert "layer_representation_hidden" not in codes
        assert len(comp.svg) > 100

    def test_heatmap_window_renders_as_heatmap(self):
        layers = [{"id": "W", "source": "S", "type": "circle",
                   "representation": {"windows": [
                       {"min_zoom": 0, "max_zoom": 11, "mode": "visual_heatmap"},
                       {"min_zoom": 11, "max_zoom": 24, "mode": "raw"}]}}]
        comp = compile_mapspec_to_svg_detailed(_spec(layers, zoom=5.0))
        codes = [d["code"] for d in comp.diagnostics]
        assert "layer_gated_by_zoom" not in codes
        assert "layer_representation_hidden" not in codes

    def test_explicit_hidden_still_skipped_silently(self):
        spec = _spec([
            {"id": "X", "source": "S", "type": "circle",
             "layout": {"visibility": "none"}}])
        comp = compile_mapspec_to_svg_detailed(spec)
        assert not any(d["layer_id"] == "X" for d in comp.diagnostics)

    def test_diagnostic_codes_in_authoritative_vocab(self):
        from app.lib.cartography.render_diagnostics import RENDER_DIAGNOSTICS
        assert "layer_gated_by_zoom" in RENDER_DIAGNOSTICS
        assert "layer_representation_hidden" in RENDER_DIAGNOSTICS


class TestExpectedSemanticsScaleOmission:
    def test_scale_omitted_layers_accounted(self):
        spec = _spec([
            {"id": "G", "source": "S", "type": "circle",
             "visibility": {"min_zoom": 14.0}},
            {"id": "H", "source": "S", "type": "circle",
             "representation": {"windows": [
                 {"min_zoom": 0, "max_zoom": 8, "mode": "hidden"},
                 {"min_zoom": 8, "max_zoom": 24, "mode": "raw"}]}},
            {"id": "V", "source": "S", "type": "circle"},
        ], zoom=5.0)
        sem = expected_semantics(spec)
        assert set(sem["scale_omitted_layers"]) == {"G", "H"}
        assert sem["export_zoom"] == 5.0
        # 显式 hidden 面保持既有语义（layout.visibility 通道）。
        assert "V" not in sem["hidden_layers"]

    def test_plain_spec_omission_list_empty(self):
        spec = _spec([{"id": "V", "source": "S", "type": "circle"}])
        sem = expected_semantics(spec)
        assert sem["scale_omitted_layers"] == []
