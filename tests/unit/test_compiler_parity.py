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
    """The fixture has 1 Point + 1 LineString + 1 Polygon across 3 layers."""
    svg = compile_mapspec_to_svg(mapspec, target_dpi=72)
    assert _count(svg, "circle") == 1
    assert _count(svg, "path") == 1
    assert _count(svg, "polygon") == 1


def test_python_scales_by_dpi_factor(mapspec):
    """radius 5 * (300/72) = 20.83; line-width 2 * (300/72) = 8.33;
    outline 1.0 * (300/72) = 4.17."""
    svg = compile_mapspec_to_svg(mapspec, target_dpi=300)
    assert 'r="20.83"' in svg
    assert 'stroke-width="8.33"' in svg
    assert 'stroke-width="4.17"' in svg


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


def test_python_wraps_in_svg_root_with_white_background(mapspec):
    """Both twins must emit the same root <svg> + background <rect> shape."""
    svg = compile_mapspec_to_svg(mapspec, target_dpi=72, width=800, height=600)
    assert svg.startswith('<svg width="800" height="600"')
    assert 'viewBox="0 0 800 600"' in svg
    assert '<rect width="100%" height="100%" fill="#ffffff" />' in svg
