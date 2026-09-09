"""V6（ADR-0120 W7）publication chrome 集成（后端孪生 include_chrome）。

- 组件驱动：enabled 组件 → 对应整饰；缺席/disabled → 不画（user-wins）。
- 图例条目来自 derive_legend_items 单源。
- legacy 路径（include_chrome=False）byte-stable。
"""
import pytest

from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed


def _pub_spec(components, **layer_overrides):
    layer = {
        "id": "regions",
        "source": "g",
        "type": "fill",
        "paint": {"fill-color": "#3b82f6"},
        "legend_spec": {
            "type": "categorical",
            "field": "zone",
            "title": "功能区",
            "categories": [
                {"key": "res", "color": "#fca5a5", "label": "居住"},
                {"key": "ind", "color": "#93c5fd", "label": "工业"},
            ],
            "nodata": {"color": "#e5e7eb", "label": "无数据"},
        },
    }
    layer.update(layer_overrides)
    return {
        "version": "1.1",
        "sources": {
            "g": {
                "type": "geojson",
                "inlineData": {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [116.0, 39.0]},
                            "properties": {"zone": "res"},
                        },
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [116.5, 39.5]},
                            "properties": {"zone": "ind"},
                        },
                    ],
                },
            },
        },
        "layers": [layer],
        "layout": {"components": components},
    }


_FULL_CHROME = [
    {"id": "t", "type": "title", "position": "top-center", "options": {"text": "城市功能区专题图"}},
    {"id": "st", "type": "subtitle", "position": "top-center", "options": {"text": "示例副标题"}},
    {"id": "n", "type": "north_arrow"},
    {"id": "sb", "type": "scale_bar"},
    {"id": "lg", "type": "legend", "position": "bottom-left"},
    {"id": "mb", "type": "map_border"},
    {"id": "gr", "type": "graticule"},
    {"id": "at", "type": "attribution", "options": {"text": "数据来源：示例"}},
    {"id": "ins", "type": "inset_map"},
]


class TestPublicationChrome:
    @pytest.fixture()
    def compilation(self):
        return compile_mapspec_to_svg_detailed(
            _pub_spec(_FULL_CHROME), target_dpi=72, width=800, height=600, padding=40,
            include_chrome=True,
        )

    def test_all_fragments_present(self, compilation):
        svg = compilation.svg
        for marker in (
            "mapspec-chrome",
            "chrome-title",
            "城市功能区专题图",
            "示例副标题",
            "chrome-north-arrow",
            "chrome-scale-bar",
            "chrome-legend",
            "chrome-graticule",
            "chrome-attribution",
            "chrome-inset",
        ):
            assert marker in svg, marker

    def test_legend_entries_from_single_source(self, compilation):
        svg = compilation.svg
        assert "功能区" in svg  # legend_spec.title
        assert "居住" in svg and "工业" in svg and "无数据" in svg  # 条目 + nodata

    def test_disabled_component_not_drawn(self):
        comps = [dict(c, enabled=False) if c["id"] == "n" else dict(c) for c in _FULL_CHROME]
        result = compile_mapspec_to_svg_detailed(
            _pub_spec(comps), target_dpi=72, width=800, height=600, padding=40, include_chrome=True
        )
        assert "chrome-north-arrow" not in result.svg

    def test_absent_component_not_drawn(self):
        result = compile_mapspec_to_svg_detailed(
            _pub_spec([c for c in _FULL_CHROME if c["id"] != "gr"]),
            target_dpi=72, width=800, height=600, padding=40, include_chrome=True,
        )
        assert "chrome-graticule" not in result.svg
        assert "chrome-legend" in result.svg  # 其余仍在

    def test_legacy_unchanged_without_chrome(self):
        spec = _pub_spec(_FULL_CHROME)
        r = compile_mapspec_to_svg_detailed(spec, target_dpi=72, width=800, height=600, padding=40)
        assert "mapspec-chrome" not in r.svg
        assert "chrome-title" not in r.svg

    def test_bounds_override_changes_projection(self):
        spec = _pub_spec([], type="circle", paint={"circle-radius": 4, "circle-color": "#de2d26"})
        r_default = compile_mapspec_to_svg_detailed(spec, target_dpi=72, width=400, height=400, padding=10)
        r_bounds = compile_mapspec_to_svg_detailed(
            spec, target_dpi=72, width=400, height=400, padding=10,
            bounds=[110.0, 30.0, 125.0, 45.0],
        )
        # 同一要素在显式 bounds 下投影位置不同（更大范围 → 更靠中心）
        assert r_default.svg != r_bounds.svg
        assert '<circle cx="' in r_bounds.svg  # 要素仍被编译
        # 非 4 元/非法 bounds 回退自动范围（不抛错）
        r_bad = compile_mapspec_to_svg_detailed(
            spec, target_dpi=72, width=400, height=400, padding=10, bounds=[1.0, 2.0]
        )
        assert r_bad.svg == r_default.svg
