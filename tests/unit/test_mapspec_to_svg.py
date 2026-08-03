"""Unit tests for Python MapSpec-to-SVG vector compiler target."""
import pytest
from app.services.mapspec_to_svg import compile_mapspec_to_svg


def test_compile_mapspec_to_svg_basic():
    mapspec = {
        "sources": {
            "s1": {
                "type": "geojson",
                "data": {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
                            "properties": {"name": "Beijing"},
                        },
                        {
                            "type": "Feature",
                            "geometry": {"type": "Polygon", "coordinates": [[[116.3, 39.8], [116.5, 39.8], [116.5, 40.0], [116.3, 40.0], [116.3, 39.8]]]},
                            "properties": {"name": "Area 1"},
                        }
                    ],
                },
            }
        },
        "layers": [
            {
                "id": "pts",
                "type": "circle",
                "source": "s1",
                "paint": {"circle-color": "#de2d26", "circle-radius": 5},
            },
            {
                "id": "polys",
                "type": "fill",
                "source": "s1",
                "paint": {"fill-color": "#60a5fa", "fill-outline-color": "#1d4ed8"},
            }
        ],
    }

    svg_72 = compile_mapspec_to_svg(mapspec, target_dpi=72)
    assert "<svg" in svg_72
    assert "<circle" in svg_72
    assert "<polygon" in svg_72
    assert 'r="5.0"' in svg_72
    assert 'stroke-width="1.0"' in svg_72

    svg_300 = compile_mapspec_to_svg(mapspec, target_dpi=300)
    # 5 * (300 / 72) = 20.83
    assert 'r="20.83"' in svg_300
    # 1.0 * (300 / 72) = 4.17
    assert 'stroke-width="4.17"' in svg_300
