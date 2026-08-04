"""Compiler parity contract test (Python side).

Locks the Python MapSpec-to-SVG compiler against its TypeScript twin so the two
duplicated implementations do not silently drift (review Standards finding #1).
The same fixture file is consumed by
``frontend/lib/mapspec-compiler/mapspec-to-svg.parity.test.ts``; both sides
assert the same normalized invariants (element counts, DPI-scaled dimensions,
default colors, attribute shapes). If you change the compiler output, update
both tests together.
"""
import json
import re
from pathlib import Path

import pytest

from app.services.mapspec_to_svg import compile_mapspec_to_svg

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "compiler_parity_mapspec.json"


@pytest.fixture()
def mapspec():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _count(svg: str, tag: str) -> int:
    return len(re.findall(rf"<{tag}\b", svg))


def test_python_compiles_fixture_to_expected_element_counts(mapspec):
    """The fixture has 1 Point + 1 LineString + 1 Polygon + 3 text elements across 4 layers.
    MAPSPEC-02: Polygon fills are now rendered as <path d="..." fill-rule="evenodd" /> to support holes.
    """
    svg = compile_mapspec_to_svg(mapspec, target_dpi=72)
    assert _count(svg, "circle") == 1
    assert _count(svg, "path") == 2
    assert _count(svg, "polygon") == 0
    assert _count(svg, "text") == 3


def test_python_scales_by_dpi_factor(mapspec):
    """radius 5 * (300/72) = 20.83; line-width 2 * (300/72) = 8.33;
    outline 1.0 * (300/72) = 4.17; font-size 12 * (300/72) = 50.0.

    Canonical minimal form (_fmt_num strips trailing zeros): "20.83", "8.33",
    "4.17", "50". P0-2 fix: previously asserted ``'font-size="50.0"' in svg or
    'font-size="50"' in svg`` -- the ``or`` mask accepted BOTH forms so twin
    drift could never fail. Now both twins emit one canonical form, and this
    pins exactly that string.
    """
    svg = compile_mapspec_to_svg(mapspec, target_dpi=300)
    assert 'r="20.83"' in svg
    assert 'stroke-width="8.33"' in svg
    assert 'stroke-width="4.17"' in svg
    # ONE canonical string - no `or` mask. Both twins emit font-size="50".
    assert 'font-size="50"' in svg
    assert 'font-size="50.0"' not in svg
    assert "Beijing" in svg


def test_python_emits_default_colors_and_group_wrapper(mapspec):
    """Structural invariants shared with the TS twin: a single wrapping
    <g class="mapspec-vector-layers"> and the fixture's exact paint colors."""
    svg = compile_mapspec_to_svg(mapspec, target_dpi=72)
    assert '<g class="mapspec-vector-layers">' in svg
    assert "#de2d26" in svg  # circle-color
    assert "#2563eb" in svg  # line-color
    assert "#60a5fa" in svg  # fill-color
    assert "#1d4ed8" in svg  # fill-outline-color
    assert 'fill-opacity="0.6"' in svg
    # Default opacities are canonical minimal form: "1" not "1.0".
    assert 'fill-opacity="1"' in svg
    assert 'fill-opacity="1.0"' not in svg
    # Coordinate parity: the range bug (clamped small ranges to 1.0) is fixed;
    # both twins now project the Point (116.4, 39.9) to cx="413.33".
    assert 'cx="413.33"' in svg
    assert 'cy="400.26"' in svg


def test_python_wraps_in_svg_root_with_white_background(mapspec):
    """Both twins must emit the same root <svg> + background <rect> shape."""
    svg = compile_mapspec_to_svg(mapspec, target_dpi=72, width=800, height=600)
    assert svg.startswith('<svg width="800" height="600"')
    assert 'viewBox="0 0 800 600"' in svg
    assert '<rect width="100%" height="100%" fill="#ffffff" />' in svg


def test_python_compiles_raster_layer_with_oversample_boost():
    """Raster layers emit <image> with oversample boost calculated via log2(dpi / 96)."""
    raster_mapspec = {
        "sources": {
            "r1": {"type": "raster", "tiles": ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"]}
        },
        "layers": [
            {"id": "r-base", "type": "raster", "source": "r1", "paint": {"raster-opacity": 0.8}}
        ]
    }
    svg = compile_mapspec_to_svg(raster_mapspec, target_dpi=300)
    assert '<image' in svg
    assert 'data-oversample-boost="2"' in svg
    assert 'opacity="0.8"' in svg


def test_tile_grid_pixel_bounds_parity():
    """Verifies that Web Mercator Y tile grid pixel bounds match the TS twin in scaled viewBox space."""
    raster_mapspec = {
        "sources": {
            "v1": {
                "type": "geojson",
                "data": {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[116.3, 39.8], [116.6, 39.8], [116.6, 40.0], [116.3, 40.0], [116.3, 39.8]]]
                    }
                }
            },
            "r1": {"type": "raster", "tiles": ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"]}
        },
        "layers": [
            {"id": "r-base", "type": "raster", "source": "r1", "paint": {"raster-opacity": 0.8}}
        ]
    }
    svg = compile_mapspec_to_svg(raster_mapspec, target_dpi=300, width=1200, height=800, padding=40)
    images = re.findall(r'<image\s+x="([^"]+)"\s+y="([^"]+)"\s+width="([^"]+)"\s+height="([^"]+)"', svg)
    assert len(images) > 0
    assert images[0] == ("-155.38", "-501.09", "1367.19", "1011.4")


