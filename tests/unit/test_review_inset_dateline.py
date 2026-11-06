"""Canonical #1921 S7-004: authored wrapped inset extents survive SVG export."""
from xml.etree import ElementTree

from app.lib.cartography.svg_marginalia import render_inset_locator
from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed


def test_svg_locator_unwraps_context_and_aligns_main_world_copy():
    fragment = render_inset_locator(0, 0, (200, 100), [170, -20, -170, -10], [-179, -18, -175, -12])
    rect = ElementTree.fromstring(fragment).findall("rect")[1]
    assert {key: float(rect.attrib[key]) for key in ("x", "y", "width", "height")} == {
        "x": 110, "y": 20, "width": 40, "height": 60}


def test_svg_compiler_preserves_authored_wrapped_inset_instead_of_auto_extent():
    spec = {"version": "1.1", "sources": {}, "layers": [], "layout": {"components": [
        {"id": "inset", "type": "inset_map", "options": {"bbox": [170, -20, -170, -10]}}]}}
    result = compile_mapspec_to_svg_detailed(spec, target_dpi=72, width=640, height=480,
                                           include_chrome=True, bounds=[-179, -18, -175, -12])
    root = ElementTree.fromstring(result.svg)
    ns = {"s": "http://www.w3.org/2000/svg"}
    group = root.find(".//s:g[@class='chrome-inset']", ns)
    assert group is not None
    rect = group.findall("s:rect", ns)[1]
    assert float(rect.attrib["width"]) == 30
    assert float(rect.attrib["height"]) == 66
