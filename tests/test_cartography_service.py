"""cartography_service.build_legend_spec 契约测试。"""
from app.services.cartography_service import CartographyService


def test_build_legend_spec_choropleth():
    style_def = {
        "type": "choropleth",
        "field": "pop",
        "breaks": [0.0, 100.0, 500.0, 1000.0],
        "colors": ["#fff", "#aaa", "#000"],
        "legend_labels": ["0-100", "100-500", "500-1000"],
    }
    spec = CartographyService.build_legend_spec(style_def, palette="YlOrRd")
    assert spec["type"] == "graduated"
    assert spec["field"] == "pop"
    assert spec["breaks"] == [0.0, 100.0, 500.0, 1000.0]
    assert spec["palette"] == "YlOrRd"
    assert spec["palette_colors"] == ["#fff", "#aaa", "#000"]


def test_build_legend_spec_lisa_to_categorical():
    style_def = {
        "type": "lisa",
        "field": "pop",
        "categories": ["HH", "LL", "HL", "LH", "NS"],
        "colors": {
            "HH": "#ff0000", "LL": "#0000ff",
            "HL": "#ffaaaa", "LH": "#aaaaff", "NS": "#cccccc",
        },
        "legend_labels": ["High-High", "Low-Low", "High-Low", "Low-High", "Not Significant"],
    }
    spec = CartographyService.build_legend_spec(style_def)
    assert spec["type"] == "categorical"
    assert spec["field"] == "pop"
    assert len(spec["categories"]) == 5
    hh = next(c for c in spec["categories"] if c["key"] == "HH")
    assert hh["color"] == "#ff0000"
    assert hh["label"] == "High-High"


def test_build_legend_spec_unknown_type_returns_none():
    assert CartographyService.build_legend_spec({"type": "what"}) is None
    assert CartographyService.build_legend_spec(None) is None
    assert CartographyService.build_legend_spec({}) is None
