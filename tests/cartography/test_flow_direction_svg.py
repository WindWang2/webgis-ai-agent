import re

from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed


def _spec(coordinates):
    edge_source = "flow-odf_demo-edges-source"
    multi = coordinates and isinstance(coordinates[0][0], list)
    return {
        "version": "1.10",
        "view": {"center": [179.95, 30], "zoom": 8},
        "sources": {edge_source: {"type": "geojson", "inlineData": {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature", "id": "e_demo",
                "geometry": {"type": "MultiLineString" if multi else "LineString", "coordinates": coordinates},
                "properties": {"id": "e_demo", "weight": 8, "weight_encoded": 1},
            }],
        }}},
        "layers": [{
            "id": "flow-odf_demo-edges", "type": "line", "source": edge_source,
            "paint": {"width": 4, "color": "#111111"},
        }, {
            "id": "flow-odf_demo-direction", "type": "symbol", "source": edge_source,
            "metadata": {"flow_part": "direction"},
            "layout": {
                "symbol-placement": "line", "symbol-spacing": 40,
                "text-field": "▶", "text-size": 10,
                "text-rotation-alignment": "map", "text-keep-upright": False,
            },
            "paint": {"text-color": "#111111"},
        }],
        "layout": {"components": []},
    }


def _rotation(svg: str) -> float:
    match = re.search(r"rotate\((-?\d+(?:\.\d+)?)", svg)
    assert match, svg[:500]
    return float(match.group(1))


def test_reverse_edges_have_reverse_local_tangent_arrows():
    forward = compile_mapspec_to_svg_detailed(_spec([[179.9, 30], [180.0, 30.1]]), target_dpi=96).svg
    backward = compile_mapspec_to_svg_detailed(_spec([[180.0, 30.1], [179.9, 30]]), target_dpi=96).svg
    assert abs(_rotation(forward) - _rotation(backward)) > 90


def test_antimeridian_multilinestring_keeps_direction_at_96_and_300_dpi():
    coordinates = [
        [[179.9, 30], [180.0, 30.05]],
        [[-180.0, 30.05], [-179.9, 30.1]],
    ]
    for dpi in (96, 300):
        result = compile_mapspec_to_svg_detailed(_spec(coordinates), target_dpi=dpi)
        assert "▶" in result.svg
        assert "rotate(" in result.svg
        assert "NaN" not in result.svg
