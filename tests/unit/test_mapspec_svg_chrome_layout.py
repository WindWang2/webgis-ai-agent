"""The extracted SVG chrome helper preserves coordinates, escaping and coverage."""
from xml.etree import ElementTree as ET

import pytest

from app.lib.cartography.render_scene import resolve_component
from app.services.mapspec_svg_chrome_layout import (
    _constraint_chrome_xy,
    _constraint_layout_active,
    _render_title_chrome,
)


@pytest.mark.parametrize("mapspec", [None, [], {}, {"layout": []}, {"layout": {"constraint_layout": "not_evaluated"}}])
def test_constraint_helpers_require_explicit_evaluation(mapspec):
    component = resolve_component({"id": "n", "type": "north_arrow", "position": "bottom-left"})
    assert not _constraint_layout_active(mapspec)
    assert _constraint_chrome_xy(mapspec, component, "north_arrow", 800, 600, 20) is None


@pytest.mark.parametrize("kind, anchor, expected", [
    ("north_arrow", "bottom-left", (50.0, 550.0)),
    ("scale_bar", "top-left", (20.0, 40.0)),
    ("legend", "top-right", (620.0, 28.0)),
    ("categorical_legend", "top-right", (620.0, 28.0)),
    ("attribution", "top-right", (660.0, 34.0)),
    ("title", "bottom-left", (170.0, 580.0)),
    ("subtitle", "bottom-right", (630.0, 580.0)),
])
def test_constraint_anchor_coordinates_stay_unchanged(kind, anchor, expected):
    component = resolve_component({"id": "c", "type": kind, "position": anchor})
    assert _constraint_chrome_xy(
        {"layout": {"constraint_layout": "evaluated"}}, component, kind, 800, 600, 20,
    ) == expected


def test_title_markup_escapes_text_and_reports_only_nonempty_types():
    title_text = '<script>alert("x")</script>&'
    title = resolve_component({"id": "t", "type": "title", "options": {"text": title_text}})
    subtitle = resolve_component({"id": "s", "type": "subtitle", "options": {"text": ""}})
    parts, rendered = _render_title_chrome({}, title, subtitle, 800, 600, 20)
    root = ET.fromstring(parts[0])
    assert rendered == ["title"]
    assert root.find("text").text == title_text
    assert root.find("script") is None
    assert "&lt;script&gt;" in parts[0]


def test_temporal_title_keeps_coverage_without_title_components():
    parts, rendered = _render_title_chrome({
        "temporal": {
            "enabled": True,
            "cursor_frame": "f",
            "granularity": "day",
            "frames": [{"id": "f", "label": "Day <1>", "start": "2026-01-01", "end": "2026-01-02"}],
        },
    }, None, None, 800, 600, 20)
    root = ET.fromstring(parts[0])
    assert rendered == ["subtitle"]
    assert "时间: Day <1>" in "".join(root.itertext())
    assert "2026-01-01" in parts[0] and "2026-01-02" in parts[0]
    assert "&lt;1&gt;" in parts[0]
    assert _render_title_chrome({}, None, None, 800, 600, 20) == ([], [])
