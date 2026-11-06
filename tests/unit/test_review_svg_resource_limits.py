"""Export limits apply before hidden geometry traversal and across all layers."""
from types import SimpleNamespace

import pytest

from app.services.mapspec_expr import evaluate_map_libre_expression, parse_color
from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed, _resolve_export_thresholds


def _doc():
    return {"sources": {"s": {"type": "geojson", "inlineData": {
        "type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [x, 0]}, "properties": {}}
            for x in (0, 10, 20)]}}},
        "layers": [{"id": "first", "type": "circle", "source": "s"},
                   {"id": "second", "type": "circle", "source": "s"}]}


def test_total_rendered_features_are_bounded_across_layers():
    result = compile_mapspec_to_svg_detailed(_doc(), max_features=4)
    assert result.feature_count == 4
    assert result.svg.count("<circle") == 4
    assert result.truncated_features
    assert any(item.get("layer_id") == "second" for item in result.diagnostics)


def test_hidden_geometry_does_not_participate_in_export_extent():
    doc = _doc()
    baseline = compile_mapspec_to_svg_detailed(doc).svg
    doc["sources"]["hidden"] = {"type": "geojson", "inlineData": {
        "type": "FeatureCollection", "features": [{"type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-175, -80]}}]}}
    doc["layers"].append({"id": "hidden", "type": "circle", "source": "hidden", "visible": False})
    assert compile_mapspec_to_svg_detailed(doc).svg == baseline


def test_expression_match_is_type_strict_and_invalid_hex_is_safe():
    assert evaluate_map_libre_expression(["match", ["get", "key"], ["1", "2"], "yes", "no"], {"key": 1}, 8, "fallback") == "no"
    assert evaluate_map_libre_expression(["match", ["get", "key"], ["1", "2"], "yes", "no"], {"key": "2"}, 8, "fallback") == "yes"
    assert parse_color("#xx00ff") is None
    assert parse_color("#zzg") is None


def test_untrusted_thresholds_and_output_geometry_are_clamped():
    features, timeout = _resolve_export_thresholds({"thresholds": {"maxFeatures": 10**12, "timeoutMs": 10**12}}, None, None)
    assert features <= 50_000 and timeout <= 30_000
    result = compile_mapspec_to_svg_detailed(_doc(), width=10**12, height=10**12, target_dpi=10**12)
    assert 'width="16384"' in result.svg
    assert 'height="16384"' in result.svg


def test_publication_page_budget_counts_utf8_bytes_and_cover(monkeypatch):
    from app.services import publication_export as publication
    from app.lib.cartography.publication_ir import plan_publication_pages

    ir = plan_publication_pages(_doc())
    page = ir.pages[0]
    monkeypatch.setattr(publication, "compile_mapspec_to_svg_detailed", lambda *a, **kw: SimpleNamespace(svg="汉" * 4))
    with pytest.raises(publication.PageCompileFailure, match="budget"):
        publication.compile_publication_page(_doc(), page, ir, frames_lookup=[], target_dpi=72, max_labels=10, svg_budget_remaining=10)
    cover = page.model_copy(update={"cover": True})
    monkeypatch.setattr(publication, "_render_toc_svg", lambda *a: "汉" * 4)
    with pytest.raises(publication.PageCompileFailure, match="budget"):
        publication.compile_publication_page(_doc(), cover, ir, frames_lookup=[], target_dpi=72, max_labels=10, svg_budget_remaining=10)


def test_overflowing_integer_inputs_do_not_escape_export_caps():
    huge = 10**400
    result = compile_mapspec_to_svg_detailed(_doc(), width=huge, height=huge, target_dpi=huge, max_features=huge, timeout_ms=huge)
    assert "<circle" in result.svg
    assert 'width="1200"' in result.svg
    assert _resolve_export_thresholds({}, huge, huge)[0] <= 50_000
