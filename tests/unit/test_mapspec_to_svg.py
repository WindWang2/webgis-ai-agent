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
            }
        ],
    }

    svg_72 = compile_mapspec_to_svg(mapspec, target_dpi=72)
    assert "<svg" in svg_72
    assert "<circle" in svg_72
    assert 'r="5.0"' in svg_72

    svg_300 = compile_mapspec_to_svg(mapspec, target_dpi=300)
    # 5 * (300 / 72) = 20.8333... -> 20.83
    assert 'r="20.83"' in svg_300
