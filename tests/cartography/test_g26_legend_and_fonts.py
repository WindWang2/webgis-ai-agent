"""G26 P4 — 图例/说明/中文字体面。

A08（全 nodata/常值说明）、A09（RGB 通道顺序）、A13（中文可读）、A14
（图例分级/单位来自同一数据表达）。
"""
from __future__ import annotations

import io

import pytest

from app.lib.cartography.render_scene import derive_legend_items
from app.lib.cartography.svg_marginalia import render_legend_box


def _rgb_legend():
    return {
        "schema_version": "raster_expression.v1",
        "type": "raster_rgb",
        "style_id": "st-rgb", "data_revision": "fp:t",
        "channels": [
            {"channel": "R", "band": 4, "band_label": "band 4: 近红外",
             "min": 0.0, "max": 320.0, "unit": None, "unit_status": "unknown"},
            {"channel": "G", "band": 3, "band_label": "band 3: 红",
             "min": 0.0, "max": 300.0, "unit": "reflectance", "unit_status": "declared"},
            {"channel": "B", "band": 2, "band_label": "band 2: 绿",
             "min": 0.0, "max": 280.0, "unit": "reflectance", "unit_status": "declared"},
        ],
        "colorbar": None,
        "notes": ["rgb_composite: channel combination is not a single physical quantity"],
    }


def _continuous_legend(*, constant=False, approximate=False):
    notes = []
    if constant:
        notes.append("constant_or_all_nodata: the resolved stretch has zero span")
    if approximate:
        notes.append("approximate_range: endpoints come from a bounded decimated sample")
    return {
        "schema_version": "raster_expression.v1", "type": "continuous",
        "style_id": "st-c", "data_revision": "fp:t",
        "band": 1, "band_label": "band 1: 高程", "unit": "m", "unit_status": "declared",
        "min": 0.0, "max": 0.0 if constant else 8848.0,
        "palette": "viridis", "palette_colors": ["#440154", "#31688e", "#35b779", "#fde725"],
        "constant": constant,
        "notes": notes,
        "stretch_provenance": {
            "source": "bounded_sample" if approximate else "manual",
            "method": "minmax_decimated_2048" if approximate else "manual",
            "sample_count": 4096 if approximate else None,
            "approximate": approximate,
        },
    }


class TestRasterRgbLegend:
    def test_channels_become_entries_in_R_G_B_order(self):
        model = derive_legend_items(_rgb_legend())
        assert model is not None
        labels = [e["label"] for e in model["entries"]]
        assert labels[0].startswith("R ←")
        assert labels[1].startswith("G ←")
        assert labels[2].startswith("B ←")
        assert "band 4: 近红外" in labels[0]
        assert "单位未知" in labels[0]
        assert "reflectance" in labels[1]
        # A09 前置：通道序 R/G/B 与冻结 stretch 值入标签
        assert "320" in labels[0]
        assert model["title"] == "RGB 波段复合"

    def test_no_continuous_colorbar_entries(self):
        """RGB 不伪造连续色标（无 min..max 渐变条目语义）。"""
        model = derive_legend_items(_rgb_legend())
        assert model["kind"] == "categorical"
        assert all(e["kind"] == "class" for e in model["entries"])

    def test_legend_box_renders_channels(self):
        model = derive_legend_items(_rgb_legend())
        box = render_legend_box(10.0, 10.0, model)
        assert "R ← band 4" in box
        assert "#dc2626" in box and "#16a34a" in box and "#2563eb" in box


class TestContinuousNotes:
    def test_constant_note_flows_to_footnote(self):
        model = derive_legend_items(_continuous_legend(constant=True))
        assert model is not None
        assert any("constant_or_all_nodata" in n for n in model.get("notes", []))
        box = render_legend_box(10.0, 10.0, model)
        assert "constant_or_all_nodata" in box, "A08：常值说明必须可见"

    def test_approximate_note_flows(self):
        model = derive_legend_items(_continuous_legend(approximate=True))
        assert any("approximate_range" in n for n in model.get("notes", []))

    def test_unit_row_present(self):
        model = derive_legend_items(_continuous_legend())
        assert model.get("unit") == "m"
        box = render_legend_box(10.0, 10.0, model)
        assert "单位：m" in box, "A14：单位来自同一数据表达"

    def test_notes_absent_byte_stable(self):
        """无 notes 的旧 legend：模型不含 notes 键（旧路径零变化）。"""
        spec = _continuous_legend()
        spec.pop("notes")
        model = derive_legend_items(spec)
        assert model is not None
        assert "notes" not in model


class TestCjkFontFetcherFix:
    def test_font_data_uri_fetcher_returns_response_object(self):
        """WeasyPrint 70 兼容：字体 data URI 经修复后的 fetcher 返回
        URLFetcherResponse（dict 会被 assert 吞掉 → 字体静默缺失）。"""
        import base64

        from app.services.publication_export import _deny_url_fetcher

        payload = base64.b64encode(b"\x00\x01fakefont").decode("ascii")
        res = _deny_url_fetcher(f"data:font/ttf;base64,{payload}")
        assert hasattr(res, "read") and hasattr(res, "close")
        assert res.read() == b"\x00\x01fakefont"
        assert res.content_type == "font/ttf"

    def test_image_data_uri_allowed_with_cap(self):
        import base64

        from app.services.publication_export import (
            _MAX_EMBEDDED_IMAGE_B64_BYTES,
            _deny_url_fetcher,
        )

        payload = base64.b64encode(b"\x89PNG fake").decode("ascii")
        res = _deny_url_fetcher(f"data:image/png;base64,{payload}")
        assert res is not None and res.content_type == "image/png"
        with pytest.raises(ValueError):
            _deny_url_fetcher(
                "data:image/png;base64," + "A" * (_MAX_EMBEDDED_IMAGE_B64_BYTES + 1))

    @pytest.mark.parametrize("bad", [
        "http://evil.example/x.png",
        "file:///etc/passwd",
        "https://evil.example/font.ttf",
        "data:image/svg+xml;base64,PHN2Zz4=",
    ])
    def test_external_and_non_png_schemes_denied(self, bad):
        from app.services.publication_export import _deny_url_fetcher

        with pytest.raises(ValueError):
            _deny_url_fetcher(bad)
