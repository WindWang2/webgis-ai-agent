"""SVG twins reject misleading extents and resolve authored label expressions."""
from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed
from app.services.mapspec_expr import evaluate_map_libre_expression


def test_authored_label_and_get_expression_render_property_text():
    result = compile_mapspec_to_svg_detailed({
        'sources': {'s': {'type': 'geojson', 'inlineData': {'type': 'FeatureCollection', 'features': [
            {'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [116, 40]}, 'properties': {'title': 'Harbor'}},
        ]}}},
        'layers': [{'id': 'p', 'type': 'circle', 'source': 's', 'label': {'field': 'title'}}],
    })
    assert '>Harbor</text>' in result.svg
    assert 'get,title' not in result.svg


def test_invalid_geographic_coordinates_are_skipped_and_diagnosed_once():
    result = compile_mapspec_to_svg_detailed({
        'sources': {'s': {'type': 'geojson', 'inlineData': {'type': 'FeatureCollection', 'features': [
            {'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [500000, 4000000]}},
            {'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [500001, 4000001]}},
        ]}}},
        'layers': [{'id': 'p', 'type': 'circle', 'source': 's'}],
    })
    assert '<circle ' not in result.svg
    assert sum(item['code'] == 'layer_coordinates_invalid' for item in result.diagnostics) == 1


def test_case_and_coerced_array_match_resolve_like_live_maplibre():
    assert evaluate_map_libre_expression(['match', ['to-string', ['get', 'k']], ['1', '2'], '#fff', '#000'], {'k': 1}, 8, '#888') == '#fff'
    assert evaluate_map_libre_expression(['case', ['==', ['get', 'k'], 1], '#fff', '#000'], {'k': 1}, 8, '#888') == '#fff'
