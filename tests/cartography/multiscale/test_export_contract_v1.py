"""M6 导出同合同测试 —— artifact 窗源覆盖（后端 SVG 面 + 裁决助手）。"""
from __future__ import annotations

import pytest
from shapely.geometry import shape

from app.lib.cartography.multiscale.export_adjudication import (
    active_representation_window,
    export_source_override,
)
from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed

pytestmark = pytest.mark.cartography


def _layer(**extra) -> dict:
    return {
        "id": "roads", "source": "roads_src", "type": "line",
        "paint": {"line-color": "#c00", "line-width": 1.5},
        **extra,
    }


def _spec(layer: dict, zoom: float = 6.0) -> dict:
    return {
        "version": "1.8",
        "view": {"center": [104.05, 30.6], "zoom": zoom},
        "sources": {
            "roads_src": {"type": "geojson", "inlineData": {
                "type": "FeatureCollection", "features": [
                    {"type": "Feature", "id": f"r{i}", "properties": {},
                     "geometry": {"type": "LineString", "coordinates": [
                         [104.0 + i * 0.05, 30.5], [104.0 + i * 0.05, 30.7]]}}
                    for i in range(5)]}},
            "roads__gen@0-8": {"type": "geojson", "inlineData": {
                "type": "FeatureCollection", "features": [
                    {"type": "Feature", "id": "trunk-0", "properties": {},
                     "geometry": {"type": "LineString", "coordinates": [
                         [104.02, 30.55], [104.02, 30.65]]}}]}},
        },
        "layers": [layer],
    }


class TestExportSourceOverrideHelper:
    WINDOWS = [
        {"min_zoom": 0, "max_zoom": 8, "mode": "generalized",
         "reason_codes": ["MSP.BAND.WORLD.GENERALIZED"], "source": "roads__gen@0-8"},
        {"min_zoom": 8, "max_zoom": 24, "mode": "raw", "reason_codes": []},
    ]

    def test_active_window_and_override(self):
        layer = _layer(representation={"windows": self.WINDOWS})
        assert export_source_override(layer, 6.0) == "roads__gen@0-8"
        assert export_source_override(layer, 12.0) is None  # raw 带：主源
        assert export_source_override(_layer(), 6.0) is None  # 无声明
        win = active_representation_window(layer, 6.0)
        assert win is not None and win["mode"] == "generalized"

    def test_override_requires_artifact_mode(self):
        layer = _layer(representation={"windows": [
            {"min_zoom": 0, "max_zoom": 24, "mode": "raw",
             "source": "x"}]})
        # raw 窗即使带 source 键也不覆盖（raw = 主层 source 语义）
        assert export_source_override(layer, 3.0) is None

    def test_out_of_domain_zoom_clamped(self):
        layer = _layer(representation={"windows": self.WINDOWS})
        assert export_source_override(layer, -5) == "roads__gen@0-8"
        assert export_source_override(layer, 99) is None


class TestSvgExportConsumesArtifactSource:
    WINDOWS = TestExportSourceOverrideHelper.WINDOWS

    def test_export_zoom_in_artifact_band_renders_artifact(self):
        spec = _spec(_layer(representation={"windows": self.WINDOWS}), zoom=6.0)
        result = compile_mapspec_to_svg_detailed(spec)
        svg = result.svg
        # 工件源只有 1 条线（主干）—— 主源 5 条线不得全部出现
        assert svg.count("<path") + svg.count("<line") < 5 + 2
        codes = [d.get("code") for d in result.diagnostics]
        assert "layer_generalized_artifact" in codes

    def test_export_zoom_in_raw_band_renders_main_source(self):
        spec = _spec(_layer(representation={"windows": self.WINDOWS}), zoom=12.0)
        result = compile_mapspec_to_svg_detailed(spec)
        assert "layer_generalized_artifact" not in [
            d.get("code") for d in result.diagnostics]

    def test_artifact_geometry_actually_from_artifact(self):
        spec = _spec(_layer(representation={"windows": self.WINDOWS}), zoom=6.0)
        result = compile_mapspec_to_svg_detailed(spec)
        # 工件线在 (104.02,30.55)-(104.02,30.65)；主源线 r0 在 104.0。
        # 导出画布内应存在与工件对应的一条近垂直线 —— 以要素计数断言：
        # 主源 5 条水平分布线，工件 1 条；若未覆盖源会画 5 条。
        assert result.svg.count("<path") + result.svg.count("<line") <= 3
