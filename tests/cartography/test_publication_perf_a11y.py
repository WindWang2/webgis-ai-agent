"""V6（ADR-0120 W10/W11）资源包络结构断言 + 可访问性。

结构性预算（不依赖 wall-clock 绝对值）：
- 20k 要素编译：caps 生效（features_truncated）、标签预算 ≤ 400、
  诊断 ≤ 64、元素数量级有界。
- publication SVG 可访问性：role="img" + <title>（chrome 路径）。
"""
import pytest

from app.lib.cartography.label_collision import MAX_LABELS_PER_EXPORT
from app.lib.cartography.render_diagnostics import MAX_DIAGNOSTICS_PER_EXPORT
from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed


def _big_spec(n_features: int, with_labels: bool = True):
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [100.0 + (i % 200) * 0.01, 20.0 + (i // 200) * 0.01]},
            "properties": {"name": f"点{i}", "v": i % 7},
        }
        for i in range(n_features)
    ]
    layers = [
        {
            "id": "pts",
            "source": "pts",
            "type": "circle",
            "paint": {"circle-radius": 3, "circle-color": "#de2d26"},
        }
    ]
    if with_labels:
        layers.append(
            {
                "id": "pts_sym",
                "source": "pts",
                "type": "symbol",
                "layout": {"text-field": "{name}", "text-size": 10},
            }
        )
    return {
        "version": "1.1",
        "sources": {"pts": {"type": "geojson", "inlineData": {"type": "FeatureCollection", "features": features}}},
        "layers": layers,
        "layout": {"labels": {"collision": "deterministic"}},
        "thresholds": {"maxFeatures": 5000},
    }


class TestStructuralBudgets:
    @pytest.mark.parametrize("n", [20000])
    def test_20k_features_caps_enforced(self, n):
        result = compile_mapspec_to_svg_detailed(
            _big_spec(n), target_dpi=72, width=800, height=600, padding=10
        )
        # 要素 cap 生效：features_truncated 披露 + 编译不炸
        assert result.truncated_features is True
        assert any(d["code"] == "features_truncated" for d in result.diagnostics)
        # 标签预算：碰撞模式标签组内元素对数 ≤ MAX_LABELS_PER_EXPORT
        labels_group = result.svg.split('<g class="mapspec-labels">')[-1].split("</g>")[0]
        text_count = labels_group.count("<text")
        assert text_count <= MAX_LABELS_PER_EXPORT * 2  # halo + main 两元素
        # 诊断封顶
        assert len(result.diagnostics) <= MAX_DIAGNOSTICS_PER_EXPORT

    def test_publication_svg_has_a11y_title(self):
        spec = {
            "version": "1.1",
            "sources": {"g": {"type": "geojson", "inlineData": {"type": "FeatureCollection", "features": [
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.0, 39.0]}, "properties": {"name": "A"}},
            ]}}},
            "layers": [{"id": "l", "source": "g", "type": "circle", "paint": {"circle-radius": 4}}],
            "layout": {"components": [{"id": "t", "type": "title", "options": {"text": "无障碍标题"}}]},
        }
        result = compile_mapspec_to_svg_detailed(
            spec, target_dpi=72, width=400, height=300, padding=10, include_chrome=True
        )
        assert 'role="img"' in result.svg
        assert "<title>" in result.svg
        assert "无障碍标题" in result.svg
